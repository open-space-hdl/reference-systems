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

The register windows, the encryption channels (receive port, framer, AES-GCM core, send port with its APID) and the
key slots come from the Composer file of the system, the register maps from the headers of the cores. The set-up
and the procedure are described with the board target.

Usage: python systems/spfi_encrypt/sw/hardware_test.py --device <STAR-Ultra device name> [--seed N] [--packets N]
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

CORES = ROOT / "cores"
SPP = RegisterMap(CORES / "openspp" / "sw" / "spp_regs.h", "SPP")
OCR = RegisterMap(CORES / "opencrypto" / "sw" / "ocr_regs.h", "OCR")
OFB = RegisterMap(CORES / "openfibre" / "sw" / "ofb_regs.h", "OFB")
OMAP = RegisterMap(CORES / "openrmap" / "sw" / "omap_regs.h", "OMAP")

# olo_ft_ecc_monitor_axi (register map of its Open Logic documentation): INFO, IRQ_ENA, counters of channel ch
EDAC_INFO, EDAC_IRQ_ENA, EDAC_CNT = 0x00, 0x0C, 0x10

LANE_ACTIVE = 7           # LANE_STATUS.STATE of an active lane (OpenFibre register map)
LINK_INITIALISED = 3      # DL_STATUS.LINK_STATE of an initialised link


@dataclass(frozen=True)
class Channel:
    """One encryption channel: receive port of the Space Packet node, framer, AES-GCM core, send port."""

    port: int
    framer: str
    core: str
    output_apid: int
    slot: int


class System:
    """What the test needs to know about spfi_encrypt, from its Composer file."""

    def __init__(self, path: Path = SYSTEM_DIR / "spfi_encrypt.composer.json"):
        f = SystemFile(path)
        self.node = f.instance_of_block("openspp/spp_core")
        self.rmap = f.instance_of_block("openrmap/omap_core")
        self.port = f.instance_of_block("openfibre/ofb_core")
        self.ecc = f.instance_of_block("olo/olo_ft_ecc_monitor_axi")
        self.windows = f.decoder_windows(f.instance_of_block("olo/olo_axi_lite_decoder"))
        self.chunk_bytes = f.generic(f.instances_of("opencrypto/ocr_framer")[0], "ChunkBytes_g")
        self.rx_buffer_bytes = f.generic(self.node, "RxBufferBytes_g")
        self.ecc_channels = f.generic(self.ecc, "Channels_g")
        self.ecc_info = self.ecc_channels | (f.generic(self.ecc, "CounterWidth_g") << 8)
        self.channels = []
        for port in range(f.generic(self.node, "NumRx_g")):
            framer = instance_of(f.connection_from(f"{self.node}.M_Rx[{port}]")["to"])
            core = instance_of(f.connection_from(f"{framer}.M_Out")["to"])
            send = f.connection_from(f"{core}.M_Out")
            self.channels.append(Channel(port=port, framer=framer, core=core, output_apid=send["sideband"]["apid"],
                                         slot=f.tie_off(f"{framer}_Cfg_Slot_s", "slot")))

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
        tid = self.next_tid()
        reply = self.command(rmap.read_command(self.target, address, 4, tid), tid, read=True)
        check(reply.ok, f"read 0x{address:08X}: {rmap.status_text(reply.status)}")
        # the first byte of the data is the lowest address: little-endian byte lanes of the AXI4 bus
        return int.from_bytes(reply.data, "little")

    def write(self, address: int, value: int) -> None:
        tid = self.next_tid()
        reply = self.command(rmap.write_command(self.target, address, value.to_bytes(4, "little"), tid), tid,
                             read=False)
        check(reply.ok, f"write 0x{address:08X}: {rmap.status_text(reply.status)}")

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
    chunks: int = 0


def tc01_link(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-01: the link is up with four lanes, the SpaceFibre port answers over RMAP."""
    base = ctx.system.base(ctx.system.port)
    check(regs.read(base + OFB.ID) == OFB.REGMAP_ID, "ID of the SpaceFibre port")
    state = OFB.get("DL_STATUS", "LINK_STATE", regs.read(base + OFB.DL_STATUS))
    check(state == LINK_INITIALISED, f"DL_STATUS.LINK_STATE = {state}")
    for lane in range(4):
        state = OFB.get("LANE_STATUS", "STATE", regs.read(base + OFB.LANE_STATUS(lane)))
        check(state == LANE_ACTIVE, f"LANE_STATUS({lane}).STATE = {state}")


def tc02_identification(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-02: every register window answers with the identification of its block."""
    s = ctx.system
    expected = [(s.node, SPP.ID, SPP.REGMAP_ID), (s.ecc, EDAC_INFO, s.ecc_info), (s.rmap, OMAP.ID, OMAP.REGMAP_ID),
                (s.port, OFB.ID, OFB.REGMAP_ID)] + [(c.core, OCR.ID, OCR.REGMAP_ID) for c in s.channels]
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


def tc04_configure(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-04: input APID i goes to receive port i; the key slot of every AES-GCM core is loaded with its own
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
    busy = OCR.field("STATUS", "BUSY", 1)
    for k, ch in enumerate(s.channels):
        core = s.base(ch.core)
        regs.wait(core + OCR.STATUS, busy, 0)
        key = ctx.keys[k]
        for n in range(len(key) // 4):
            # key byte 4 n in bits 31:24 of KEY n
            regs.write(core + OCR.KEY(n), int.from_bytes(key[4 * n:4 * n + 4], "big"))
        regs.write(core + OCR.IV_FIXED, ctx.fixed[k])
        regs.write(core + OCR.IV_COUNT_LO, ctx.counter[k] & 0xFFFFFFFF)
        regs.write(core + OCR.IV_COUNT_HI, ctx.counter[k] >> 32)
        regs.write(core + OCR.KEY_LOAD, OCR.field("KEY_LOAD", "START", 1) | OCR.field("KEY_LOAD", "IV_GEN", 1)
                   | OCR.field("KEY_LOAD", "SLOT", ch.slot))
        regs.wait(core + OCR.STATUS, busy, 0)
    counters = [node + SPP.RX_PACKETS, node + SPP.TX_PACKETS] + [s.base(c.core) + OCR.ENCRYPTIONS for c in s.channels]
    for address in counters:
        regs.write(address, 0)  # a write clears a counter


def tc05_encryption(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-05: Space Packets of the input APIDs come back as authenticated chunks with the APID of their
    channel; every chunk decrypts with the key of its channel and the chunks give the input packets."""
    s, setup = ctx.system, ctx.setup
    rng = random.Random(ctx.seed)
    # RxBufferBytes_g holds the longest Space Packet (OpenSPP user guide, integration constraints)
    max_bytes = s.rx_buffer_bytes - PRIMARY_HEADER_BYTES
    n = s.chunk_bytes
    sent: list[list[bytes]] = [[] for _ in s.channels]
    packets = []
    for k in range(ctx.packets):
        ch = k % len(s.channels)
        length = rng.choice([1, n - 1, n, n + 1, rng.randint(1, max_bytes)])
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


def tc06_counters(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-06: counters match the traffic, no error and no memory error is counted."""
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
    for channel in range(s.ecc_channels):
        check(regs.read(s.base(s.ecc) + EDAC_CNT + 4 * channel) == 0, f"ECC monitor counters of channel {channel}")


TESTS = [tc01_link, tc02_identification, tc03_rmap, tc04_configure, tc05_encryption, tc06_counters]


def make_context(seed: int, packets: int, setup: Setup = Setup(), system: System | None = None) -> Context:
    system = system or System()
    inputs = {setup.input_apid_base + c.port for c in system.channels}
    if inputs & {c.output_apid for c in system.channels}:
        raise ValueError("the input APIDs overlap the output APIDs of the system")
    rng = random.Random(seed)
    count = len(system.channels)
    return Context(system=system, setup=setup, seed=seed, packets=packets,
                   keys=[rng.randbytes(32) for _ in range(count)], fixed=[rng.getrandbits(32) for _ in range(count)],
                   counter=[rng.getrandbits(32) for _ in range(count)])


def run(transport, ctx: Context) -> int:
    """Runs the test cases in order and stops at the first failure; returns the number of passed test cases."""
    regs = Registers(transport, ctx.setup)
    passed = 0
    for test in TESTS:
        name = test.__doc__.split(":")[0]
        try:
            test(regs, ctx)
        except Exception as err:  # noqa: BLE001 - the failure is reported, the later test cases depend on it
            print(f"FAIL {name}: {err}")
            break
        print(f"PASS {name}")
        passed += 1
    print(f"{passed} of {len(TESTS)} test cases passed")
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", required=True, help="name of the STAR-Ultra device in STAR-System")
    parser.add_argument("--seed", type=int, default=1, help="seed of keys and packets")
    parser.add_argument("--packets", type=int, default=40, help="Space Packets sent in TC-RS-HW-05")
    args = parser.parse_args()

    from refsys.transport import StarSystemTransport

    ctx = make_context(args.seed, args.packets)
    with StarSystemTransport(args.device) as transport:
        passed = run(transport, ctx)
    return 0 if passed == len(TESTS) else 1


if __name__ == "__main__":
    sys.exit(main())
