# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Hardware test of spfi_encrypt: RMAP over virtual channel 0, Space Packets over virtual channel 1.

The far end of the SpaceFibre link is a STAR-Ultra PCIe at port 1 of the device. Its channel 0 reaches virtual
channel 0 of port 1 through the routing switch of the device, channel 1 is virtual channel 1 of port 1. Packets for
virtual channel 0 therefore carry a SpaceWire path address (ECSS-E-ST-50-12C Rev.1 5.6.8.3): the leading data
character 3 selects port 1 at the routing switch, which discards it; the replies of the system carry the path
address 1, which the routing switch uses to deliver them to channel 0 (Reply Address of the RMAP commands).

The register banks (base address and identification of every block) and the channels of the ECC monitor come from
the header that the Composer exports with the top level (hdl/spfi_encrypt_regs.h), the encryption channels (receive
port, framer, AES-GCM core, send port with its APID) and the key slots from the Composer file of the system, the
register maps from the headers of the cores. The set-up and the procedure are described with the board target.

Before the first test case the script discards the packets that an earlier run left on both channels (drain).

Usage: python systems/spfi_encrypt/sw/hardware_test.py --device <STAR-Ultra device name> [--seed N] [--profile P]
       [--packets N] [--test TC-RS-HW-nn ...]
Exit code 0 when every test case passes.
"""

import argparse
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parents[1]
ROOT = SYSTEM_DIR.parents[1]
sys.path.insert(0, str(ROOT / "sw"))

from refsys import chunks, rmap  # noqa: E402
from refsys.composer import SystemFile, instance_of  # noqa: E402
from refsys.regs import RegisterMap  # noqa: E402
from refsys.spp import PRIMARY_HEADER_BYTES, SpacePacket, cptp_decode, cptp_encode  # noqa: E402
from refsys.transport import TransportError  # noqa: E402

CORES = ROOT / "cores"
SPP = RegisterMap(CORES / "openspp" / "sw" / "spp_regs.h", "SPP")
OCR = RegisterMap(CORES / "opencrypto" / "sw" / "ocr_regs.h", "OCR")
OFB = RegisterMap(CORES / "openfibre" / "sw" / "ofb_regs.h", "OFB")
OMAP = RegisterMap(CORES / "openrmap" / "sw" / "omap_regs.h", "OMAP")
# register banks of the system and channels of its ECC monitor, exported by the Composer
BANKS = RegisterMap(SYSTEM_DIR / "hdl" / "spfi_encrypt_regs.h", "SPFI_ENCRYPT")

# olo_ft_ecc_monitor_axi (register map of its Open Logic documentation): INFO, IRQ_ENA, counters of channel ch
EDAC_INFO, EDAC_IRQ_ENA, EDAC_CNT = 0x00, 0x0C, 0x10

LANE_ACTIVE = 7           # LANE_STATUS.STATE of an active lane (OpenFibre register map)
LINK_INITIALISED = 3      # DL_STATUS.LINK_STATE of an initialised link

# Packets left on the channels by an earlier run: a channel is empty when no packet arrives within DRAIN_TIMEOUT
# seconds; one that still delivers after DRAIN_LIMIT packets does not empty
DRAIN_TIMEOUT = 0.5
DRAIN_LIMIT = 10000

# Registers of the AES-GCM core that the test accesses with one command each
OCR_INFO = ("ID", "GENERICS", "GENERICS2", "RELEASE_BYTES")
OCR_IV = ("IV_FIXED", "IV_COUNT_LO", "IV_COUNT_HI")
OCR_SLOT = ("SLOT_STATUS", "SLOT_FIXED", "SLOT_COUNT_LO", "SLOT_COUNT_HI")


@dataclass(frozen=True)
class Profile:
    """Size of a test run. The hardware test runs the lab profile; the system simulation runs the sim profile, with
    fewer Space Packets in TC-RS-HW-06 and the same boundary cases (packet_lengths)."""

    name: str
    packets: int


PROFILES = {p.name: p for p in (Profile("lab", 40), Profile("sim", 8))}


@dataclass(frozen=True)
class Channel:
    """One encryption channel: receive port of the Space Packet node, framer, AES-GCM core, send port."""

    port: int
    framer: str
    core: str
    output_apid: int
    slot: int
    key_slots: int


class System:
    """What the test needs to know about spfi_encrypt, from its Composer file."""

    def __init__(self, path: Path = SYSTEM_DIR / "spfi_encrypt.composer.json"):
        f = SystemFile(path)
        self.node = f.instance_of_block("openspp/spp_core")
        self.rmap = f.instance_of_block("openrmap/omap_core")
        self.port = f.instance_of_block("openfibre/ofb_core")
        self.lanes = f.generic(self.port, "NumLanes_g")
        # virtual channels of the SpaceFibre port: the RMAP node behind its N-Char bridge, the Space Packet node
        vcs = {instance_of(f.connection_from(f"{self.port}.M_Vc[{k}]")["to"]): k
               for k in range(f.generic(self.port, "NumVc_g"))}
        self.rmap_vc = vcs[f.instance_of_block("openspp/spp_nchar")]
        self.spp_vc = vcs[self.node]
        self.ecc = f.instance_of_block("olo/olo_ft_ecc_monitor_axi")
        banks = BANKS.constants()
        self.windows = {n.removesuffix("_BASE").lower(): v for n, v in banks.items() if n.endswith("_BASE")}
        self.ids = {n.removesuffix("_ID").lower(): v for n, v in banks.items() if n.endswith("_ID")}
        channel = f"{self.ecc.upper()}_CH_"
        # names of the channels of the ECC monitor in channel order, for example FRM0_ECC_0
        self.ecc_names = [n.removeprefix(channel) for n, _ in sorted(
            ((n, v) for n, v in banks.items() if n.startswith(channel)), key=lambda item: item[1])]
        self.chunk_bytes = f.generic(f.instances_of("opencrypto/ocr_framer")[0], "ChunkBytes_g")
        self.rx_buffer_bytes = f.generic(self.node, "RxBufferBytes_g")
        self.ecc_channels = len(self.ecc_names)
        self.ecc_info = f.generic(self.ecc, "Channels_g") | (f.generic(self.ecc, "CounterWidth_g") << 8)
        self.channels = []
        for port in range(f.generic(self.node, "NumRx_g")):
            framer = instance_of(f.connection_from(f"{self.node}.M_Rx[{port}]")["to"])
            core = instance_of(f.connection_from(f"{framer}.M_Out")["to"])
            send = f.connection_from(f"{core}.M_Out")
            self.channels.append(Channel(port=port, framer=framer, core=core, output_apid=send["sideband"]["apid"],
                                         slot=f.tie_off(f"{framer}_Cfg_Slot_s", "slot"),
                                         key_slots=f.generic(core, "NumKeys_g")))

    def base(self, instance: str) -> int:
        return self.windows[instance]


@dataclass(frozen=True)
class Setup:
    """Channels and addresses of the test set-up.

    The input Space Packets use the APIDs input_apid_base + i for encryption channel i, while the system sends the
    encrypted chunks with the APIDs of its send ports (0 to 3, side band constants of the Composer file). The two
    ranges are kept apart on purpose: every Space Packet on virtual channel 1 is an input or an output by its APID
    alone, in the test and in a trace of the link.
    """

    rmap_channel: int = 0
    rmap_path: bytes = b"\x03"
    rmap_reply_path: bytes = b"\x01"
    spp_channel: int = 1
    system_tla: int = 0xFE     # Tla_g of the Space Packet node and logical address of the RMAP target
    far_end_tla: int = 0xFE    # Target Logical Address of destination 0 of the Space Packet node (reset value)
    input_apid_base: int = 0x10


class TestFailed(Exception):
    pass


def check(condition: bool, text: str) -> None:
    if not condition:
        raise TestFailed(text)


def hex_words(words: list[int]) -> str:
    return " ".join(f"{w:08X}" for w in words)


def consecutive(regmap: RegisterMap, names: tuple[str, ...]) -> int:
    """Address of the first of several registers that one command accesses; they must follow each other."""
    addresses = [getattr(regmap, name) for name in names]
    if addresses != list(range(addresses[0], addresses[0] + 4 * len(addresses), 4)):
        raise ValueError(f"the registers {', '.join(names)} are not consecutive")
    return addresses[0]


class Registers:
    """Register access of the system over RMAP."""

    def __init__(self, transport, setup: Setup):
        self.transport = transport
        self.setup = setup
        self.target = rmap.Target(logical_address=setup.system_tla, path=setup.rmap_path,
                                  reply_path=setup.rmap_reply_path)
        self.tid = 0

    def next_tid(self) -> int:
        self.tid = (self.tid + 1) & 0xFFFF
        return self.tid

    def command(self, packet: bytes, tid: int, read: bool, target: rmap.Target | None = None) -> rmap.Reply:
        reply = self.transport.request(self.setup.rmap_channel, packet)
        return rmap.parse_reply(reply, target or self.target, tid, read=read)

    def read(self, address: int) -> int:
        return self.read_block(address, 1)[0]

    def read_block(self, address: int, words: int) -> list[int]:
        """Reads consecutive registers with one command (incrementing address)."""
        tid = self.next_tid()
        reply = self.command(rmap.read_command(self.target, address, 4 * words, tid), tid, read=True)
        check(reply.ok, f"read of {4 * words} bytes at 0x{address:08X}: {rmap.status_text(reply.status)}")
        # the first byte of the data is the lowest address: little-endian byte lanes of the AXI4 bus
        return [int.from_bytes(reply.data[i:i + 4], "little") for i in range(0, 4 * words, 4)]

    def write(self, address: int, value: int) -> None:
        self.write_block(address, [value])

    def write_block(self, address: int, words: list[int]) -> None:
        """Writes consecutive registers with one verified command (incrementing address)."""
        data = b"".join(w.to_bytes(4, "little") for w in words)
        tid = self.next_tid()
        reply = self.command(rmap.write_command(self.target, address, data, tid), tid, read=False)
        check(reply.ok, f"write of {len(data)} bytes at 0x{address:08X}: {rmap.status_text(reply.status)}")

    def wait(self, address: int, mask: int, value: int, timeout: float = 1.0) -> int:
        end = time.monotonic() + timeout
        while True:
            word = self.read(address)
            if word & mask == value:
                return word
            check(time.monotonic() < end, f"0x{address:08X} = 0x{word:08X}, waited for 0x{value:X} under 0x{mask:X}")


@dataclass
class Context:
    system: System
    setup: Setup
    seed: int
    packets: int
    keys: list[bytes]
    fixed: list[int]
    counter: list[int]
    test_key: bytes
    test_fixed: int
    test_counter: int
    chunks: int = 0


def load_slot(regs: Registers, core: int, key: bytes, fixed: int, counter: int, slot: int) -> None:
    """Loads a key slot with generated IVs (OpenCrypto user guide, programming sequence): the key words with one
    command, the fixed field and the first invocation counter with one command, then KEY_LOAD."""
    busy = OCR.field("STATUS", "BUSY", 1)
    regs.wait(core + OCR.STATUS, busy, 0)
    # key byte 4 n in bits 31:24 of KEY n
    regs.write_block(core + OCR.KEY(0), [int.from_bytes(key[i:i + 4], "big") for i in range(0, len(key), 4)])
    regs.write_block(core + consecutive(OCR, OCR_IV), [fixed, counter & 0xFFFFFFFF, counter >> 32])
    regs.write(core + OCR.KEY_LOAD, OCR.field("KEY_LOAD", "START", 1) | OCR.field("KEY_LOAD", "IV_GEN", 1)
               | OCR.field("KEY_LOAD", "SLOT", slot))
    regs.wait(core + OCR.STATUS, busy, 0)


def query_slot(regs: Registers, core: int, slot: int) -> list[int]:
    """SLOT_STATUS, SLOT_FIXED, SLOT_COUNT_LO and SLOT_COUNT_HI of a key slot, read with one command."""
    regs.write(core + OCR.SLOT_QUERY, slot)
    regs.wait(core + OCR.SLOT_STATUS, OCR.field("SLOT_STATUS", "PENDING", 1), 0)
    return regs.read_block(core + consecutive(OCR, OCR_SLOT), len(OCR_SLOT))


def loaded_slot(fixed: int, counter: int) -> list[int]:
    """What query_slot gives for a slot loaded with generated IVs that has not encrypted yet."""
    status = OCR.field("SLOT_STATUS", "VALID", 1) | OCR.field("SLOT_STATUS", "IV_GEN", 1)
    return [status, fixed, counter & 0xFFFFFFFF, counter >> 32]


def tc01_link(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-01: the link is up with all lanes, the SpaceFibre port answers over RMAP."""
    base = ctx.system.base(ctx.system.port)
    check(regs.read(base + OFB.ID) == ctx.system.ids[ctx.system.port], "ID of the SpaceFibre port")
    state = OFB.get("DL_STATUS", "LINK_STATE", regs.read(base + OFB.DL_STATUS))
    check(state == LINK_INITIALISED, f"DL_STATUS.LINK_STATE = {state}")
    for lane in range(ctx.system.lanes):
        state = OFB.get("LANE_STATUS", "STATE", regs.read(base + OFB.LANE_STATUS(lane)))
        check(state == LANE_ACTIVE, f"LANE_STATUS({lane}).STATE = {state}")


def tc02_identification(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-02: every register window answers with the identification of its block."""
    s = ctx.system
    # the identification register of a block in its own register map, its value in the exported header
    offsets = {s.node: SPP.ID, s.rmap: OMAP.ID, s.port: OFB.ID} | {c.core: OCR.ID for c in s.channels}
    expected = [(name, offsets[name], value) for name, value in s.ids.items()] + [(s.ecc, EDAC_INFO, s.ecc_info)]
    check(sorted(name for name, _, _ in expected) == sorted(s.windows), f"register windows {sorted(s.windows)}")
    for name, offset, value in expected:
        word = regs.read(s.base(name) + offset)
        check(word == value, f"{name}: 0x{word:08X}, expected 0x{value:08X}")


def tc03_rmap(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-03: verified write and read back; a command with a wrong key is rejected."""
    address = ctx.system.base(ctx.system.ecc) + EDAC_IRQ_ENA
    for value in (0x3, 0x1, 0x0):
        regs.write(address, value)
        check(regs.read(address) == value, f"IRQ_ENA of the ECC monitor does not read back 0x{value:X}")
    wrong = rmap.Target(logical_address=regs.target.logical_address, key=regs.target.key ^ 0x5A,
                        path=regs.target.path, reply_path=regs.target.reply_path)
    tid = regs.next_tid()
    packet = rmap.read_command(wrong, ctx.system.base(ctx.system.port) + OFB.ID, 4, tid)
    reply = regs.command(packet, tid, read=True, target=wrong)
    check(reply.status == 3, f"wrong key gives status {reply.status} ({rmap.status_text(reply.status)})")


def tc04_multi_word(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-04: commands of several words: a read gives the words of single reads, the words of a write arrive
    in their registers."""
    ch = ctx.system.channels[0]
    core = ctx.system.base(ch.core)
    # read: identification and generics of the AES-GCM core (constants) with one command and word by word
    single = [regs.read(core + getattr(OCR, name)) for name in OCR_INFO]
    block = regs.read_block(core + consecutive(OCR, OCR_INFO), len(OCR_INFO))
    check(block == single, f"read of {4 * len(block)} bytes at {ch.core}.ID gives {hex_words(block)}, single "
          f"reads {hex_words(single)}")
    # write: a key slot that the framer does not use (its own slot if the core has only one; TC-RS-HW-05 loads it
    # again), loaded with the key in one command and with the fixed field and the invocation counter in one command.
    # The key cannot be read back; the fixed field and the counter come back through the slot query.
    slot = (ch.slot + 1) % ch.key_slots
    load_slot(regs, core, ctx.test_key, ctx.test_fixed, ctx.test_counter, slot)
    block = query_slot(regs, core, slot)
    single = [regs.read(core + getattr(OCR, name)) for name in OCR_SLOT]
    check(block == single, f"read of {4 * len(block)} bytes at {ch.core}.SLOT_STATUS gives {hex_words(block)}, "
          f"single reads {hex_words(single)}")
    expected = loaded_slot(ctx.test_fixed, ctx.test_counter)
    check(single == expected, f"write of {4 * len(OCR_IV)} bytes at {ch.core}.IV_FIXED: slot {slot} holds "
          f"{hex_words(single)}, expected {hex_words(expected)}")


def tc05_configure(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-05: input APID i goes to receive port i; the key slot of every AES-GCM core is loaded with its own
    key and generated IVs."""
    s, setup = ctx.system, ctx.setup
    node = s.base(s.node)
    busy = SPP.field("TABLE_STATUS", "TX_BUSY", 1) | SPP.field("TABLE_STATUS", "RX_BUSY", 1)
    regs.wait(node + SPP.TABLE_STATUS, busy, 0)
    for ch in s.channels:
        apid = setup.input_apid_base + ch.port
        regs.write(node + SPP.RX_TABLE_APID, apid)
        regs.read(node + SPP.RX_TABLE_ENTRY)
        entry = SPP.field("RX_TABLE_ENTRY", "ENABLE", 1) | SPP.field("RX_TABLE_ENTRY", "PORT", ch.port)
        regs.write(node + SPP.RX_TABLE_ENTRY, entry)
        word = regs.read(node + SPP.RX_TABLE_ENTRY)
        check(SPP.get("RX_TABLE_ENTRY", "PORT", word) == ch.port and SPP.get("RX_TABLE_ENTRY", "ENABLE", word),
              f"receive APID table entry of APID 0x{apid:03X}: 0x{word:08X}")
    check(not regs.read(node + SPP.TABLE_STATUS) & SPP.field("TABLE_STATUS", "RX_OVERRUN", 1),
          "TABLE_STATUS.RX_OVERRUN")
    for k, ch in enumerate(s.channels):
        core = s.base(ch.core)
        load_slot(regs, core, ctx.keys[k], ctx.fixed[k], ctx.counter[k], ch.slot)
        state = query_slot(regs, core, ch.slot)
        expected = loaded_slot(ctx.fixed[k], ctx.counter[k])
        check(state == expected, f"{ch.core} slot {ch.slot} holds {hex_words(state)}, expected {hex_words(expected)}")
    counters = [node + SPP.RX_PACKETS, node + SPP.TX_PACKETS] + [s.base(c.core) + OCR.ENCRYPTIONS for c in s.channels]
    for address in counters:
        regs.write(address, 0)  # a write clears a counter


def packet_lengths(system: System, packets: int, rng: random.Random) -> list[int]:
    """Data lengths of the Space Packets of TC-RS-HW-06: first the boundary cases (1 byte, one byte less than, equal
    to and one byte more than a chunk, the longest packet), then random lengths."""
    # RxBufferBytes_g holds the longest Space Packet (OpenSPP user guide, integration constraints)
    max_bytes = system.rx_buffer_bytes - PRIMARY_HEADER_BYTES
    n = system.chunk_bytes
    boundary = [1, n - 1, n, n + 1, max_bytes]
    if packets < len(boundary):
        raise ValueError(f"TC-RS-HW-06 needs at least {len(boundary)} Space Packets for its boundary cases")
    return boundary + [rng.randint(1, max_bytes) for _ in range(packets - len(boundary))]


def tc06_encryption(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-06: Space Packets of the input APIDs come back as authenticated chunks with the APID of their
    channel; every chunk decrypts with the key of its channel and the chunks give the input packets."""
    s, setup = ctx.system, ctx.setup
    rng = random.Random(ctx.seed)
    n = s.chunk_bytes
    sent: list[list[bytes]] = [[] for _ in s.channels]
    packets = []
    for k, length in enumerate(packet_lengths(s, ctx.packets, rng)):
        ch = k % len(s.channels)
        data = rng.randbytes(length)
        sent[ch].append(data)
        packet = SpacePacket(apid=setup.input_apid_base + ch, data=data, sequence_count=k).encode()
        packets.append(cptp_encode(packet, setup.system_tla))
    expected = sum(len(chunks.split(d, n)) for ds in sent for d in ds)
    received = regs.transport.exchange(setup.spp_channel, packets, setup.spp_channel, expected, timeout=10.0)
    by_apid: dict[int, list[SpacePacket]] = {c.output_apid: [] for c in s.channels}
    for raw in received:
        _, payload = cptp_decode(raw, setup.far_end_tla)
        packet = SpacePacket.decode(payload)
        check(packet.apid in by_apid, f"output packet with APID 0x{packet.apid:03X}")
        by_apid[packet.apid].append(packet)
    ctx.chunks = expected
    for k, ch in enumerate(s.channels):
        out = by_apid[ch.output_apid]
        counts = [p.sequence_count for p in out]
        check(all(b == (a + 1) & 0x3FFF for a, b in zip(counts, counts[1:])),
              f"APID {ch.output_apid}: Packet Sequence Counts {counts[:8]}")
        decrypted = []
        for p in out:
            try:
                chunk = chunks.decrypt(ctx.keys[k], p.data)
            except chunks.ChunkError as err:
                raise TestFailed(f"APID {ch.output_apid}: {err}") from err
            check(chunk.fixed == ctx.fixed[k], f"APID {ch.output_apid}: fixed field 0x{chunk.fixed:08X}")
            decrypted.append(chunk)
        if decrypted:
            check(min(c.counter for c in decrypted) == ctx.counter[k], f"APID {ch.output_apid}: first counter")
        try:
            packets_back = chunks.reassemble(decrypted)
        except chunks.ChunkError as err:
            raise TestFailed(f"APID {ch.output_apid}: {err}") from err
        check(packets_back == sent[k], f"APID {ch.output_apid}: decrypted packets differ from the input")


def tc07_counters(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-07: counters match the traffic, no error and no memory error is counted."""
    s = ctx.system
    node = s.base(s.node)
    check(regs.read(node + SPP.RX_PACKETS) == ctx.packets, "RX_PACKETS of the Space Packet node")
    check(regs.read(node + SPP.TX_PACKETS) == ctx.chunks, "TX_PACKETS of the Space Packet node")
    encryptions = 0
    for ch in s.channels:
        core = s.base(ch.core)
        encryptions += regs.read(core + OCR.ENCRYPTIONS)
        for name in ("ERR_AUTH", "ERR_FORMAT", "ERR_KEY", "ERR_IV_MODE", "ERR_IV_EXHAUSTED", "ERR_LENGTH",
                     "ERR_ECC", "ERR_OVERFLOW"):
            check(regs.read(core + getattr(OCR, name)) == 0, f"{ch.core} {name}")
    check(encryptions == ctx.chunks, f"{encryptions} encryptions, {ctx.chunks} chunks")
    for channel, name in enumerate(s.ecc_names):
        check(regs.read(s.base(s.ecc) + EDAC_CNT + 4 * channel) == 0,
              f"ECC monitor counters of channel {channel} ({name})")


TESTS = [tc01_link, tc02_identification, tc03_rmap, tc04_multi_word, tc05_configure, tc06_encryption,
         tc07_counters]

# Test cases that a test case builds on; they run before it when it is selected on its own
REQUIRES = {tc06_encryption: [tc05_configure], tc07_counters: [tc05_configure, tc06_encryption]}


def test_name(test) -> str:
    return test.__doc__.split(":")[0]


def select_tests(names: list[str] | None = None) -> list:
    """The test cases of the given names (all without names) with the test cases they build on, in order."""
    if not names:
        return list(TESTS)
    by_name = {test_name(t): t for t in TESTS}
    unknown = [n for n in names if n not in by_name]
    if unknown:
        raise ValueError(f"unknown test cases {unknown}; known: {', '.join(by_name)}")
    chosen = {by_name[n] for n in names}
    chosen |= {r for t in list(chosen) for r in REQUIRES.get(t, [])}
    return [t for t in TESTS if t in chosen]


def make_context(seed: int, packets: int, setup: Setup = Setup(), system: System | None = None) -> Context:
    system = system or System()
    inputs = {setup.input_apid_base + c.port for c in system.channels}
    if inputs & {c.output_apid for c in system.channels}:
        raise ValueError("the input APIDs overlap the output APIDs of the system")
    packet_lengths(system, packets, random.Random(seed))
    rng = random.Random(seed)
    count = len(system.channels)
    keys = [rng.randbytes(32) for _ in range(count)]
    fixed = [rng.getrandbits(32) for _ in range(count)]
    counter = [rng.getrandbits(32) for _ in range(count)]
    # the counter of TC-RS-HW-04 fills both counter words and stays far from its last value
    return Context(system=system, setup=setup, seed=seed, packets=packets, keys=keys, fixed=fixed, counter=counter,
                   test_key=rng.randbytes(32), test_fixed=rng.getrandbits(32),
                   test_counter=rng.randrange(1 << 32, 1 << 63))


def run(transport, ctx: Context, tests: list | None = None,
        registers: type[Registers] = Registers) -> list[tuple[str, str | None]]:
    """Runs the test cases in order (all by default) and stops at the first failure; returns the name and the error
    (None when it passed) of every test case that ran."""
    tests = tests or TESTS
    regs = registers(transport, ctx.setup)
    results = []
    for test in tests:
        name = test_name(test)
        transport.begin(name)
        try:
            test(regs, ctx)
        except Exception as err:  # noqa: BLE001 - the failure is reported, the later test cases depend on it
            print(f"FAIL {name}: {err}")
            results.append((name, str(err)))
            break
        print(f"PASS {name}")
        results.append((name, None))
    passed = sum(error is None for _, error in results)
    print(f"{passed} of {len(tests)} test cases passed")
    return results


def drain(transport, channels: tuple[int, ...], timeout: float = DRAIN_TIMEOUT,
          limit: int = DRAIN_LIMIT) -> dict[int, int]:
    """Reads every channel until no packet arrives within timeout and discards what it reads: a run that stopped
    early, for example in TC-RS-HW-06, leaves chunks and late replies on the channels, which the next run would
    otherwise take for its own. Prints and returns the number of discarded packets per channel."""
    counts = {}
    for channel in channels:
        count = 0
        while transport.receive(channel, timeout) is not None:
            count += 1
            if count >= limit:
                raise TransportError(f"channel {channel} still delivers packets after {count} discarded ones")
        print(f"channel {channel}: {count} packets of an earlier run discarded")
        counts[channel] = count
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", required=True, help="name of the STAR-Ultra device in STAR-System")
    parser.add_argument("--seed", type=int, default=1, help="seed of keys and packets")
    parser.add_argument("--profile", choices=PROFILES, default="lab", help="size of the run (default lab)")
    parser.add_argument("--packets", type=int, help="Space Packets sent in TC-RS-HW-06 (default: of the profile)")
    parser.add_argument("--test", action="append", help="run only this test case and those it builds on")
    args = parser.parse_args()

    from refsys.transport import StarSystemTransport

    ctx = make_context(args.seed, args.packets or PROFILES[args.profile].packets)
    tests = select_tests(args.test)
    with StarSystemTransport(args.device) as transport:
        drain(transport, (ctx.setup.rmap_channel, ctx.setup.spp_channel))
        results = run(transport, ctx, tests)
    return 0 if len(results) == len(tests) and all(error is None for _, error in results) else 1


if __name__ == "__main__":
    sys.exit(main())
