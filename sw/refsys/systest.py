"""Parts of the system tests that every reference system with a SpaceFibre link and RMAP shares.

The hardware test of a system (systems/<system>/sw/hardware_test.py) tests the system from the far end of its
SpaceFibre link: RMAP over one channel of the test equipment, Space Packets over another. This module holds what does
not depend on the processing of the system:

- the facts that every such system has in its Composer file: the SpaceFibre port, the RMAP node, the Space Packet
  node, the ECC monitor, the register banks of the exported header and, in a system that encrypts, the framers and
  AES-GCM cores (System);
- the channels and addresses of the test set-up (Setup) and the register access over RMAP (Registers);
- the key slots of the AES-GCM cores (load_slot, query_slot);
- the test cases TC-RS-HW-01 to TC-RS-HW-03 (link, identification, RMAP), TC-RS-HW-04 (commands of several words)
  of the systems that encrypt, and the steps of TC-RS-HW-05 (configuration) and TC-RS-HW-07 (counters) that the
  systems take;
- the drain of the channels and the run of a list of test cases.

The register maps of the cores come from their headers (cores/<core>/sw/<prefix>_regs.h).
"""

import argparse
import contextvars
import importlib
import json
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from refsys import errorstate, rmap, steplog
from refsys.composer import SystemFile, instance_of
from refsys.regs import RegisterMap
from refsys.transport import TransportError

ROOT = Path(__file__).resolve().parents[2]
CORES = ROOT / "cores"
SPP = RegisterMap(CORES / "openspp" / "sw" / "spp_regs.h", "SPP")
OCR = RegisterMap(CORES / "opencrypto" / "sw" / "ocr_regs.h", "OCR")
OFB = RegisterMap(CORES / "openfibre" / "sw" / "ofb_regs.h", "OFB")
OMAP = RegisterMap(CORES / "openrmap" / "sw" / "omap_regs.h", "OMAP")
OCP = RegisterMap(CORES / "opencompact" / "sw" / "ocp_regs.h", "OCP")
# the register map of a register window, by the prefix of its instance name
WINDOW_MAPS = {"spp": SPP, "ocr": OCR, "ofb": OFB, "rmap": OMAP, "ocp": OCP}

# the step log of the run that is going on: checks and records go to it (steplog.NULL outside a run)
ACTIVE_LOG: contextvars.ContextVar = contextvars.ContextVar("steplog", default=steplog.NULL)

# The receive error codes of the Space Packet node with their counters (OpenSPP register map, LAST_ERROR and
# ERR_RX1 to ERR_RX6: two 16-bit counters per register, the lower code in bits 15:0)
SPP_RX_ERRORS = {0x10: ("ERR_RX1", "EEP"), 0x11: ("ERR_RX1", "PROTOCOL_ID"), 0x12: ("ERR_RX2", "RESERVED"),
                 0x13: ("ERR_RX2", "ADDRESS"), 0x14: ("ERR_RX3", "SHORT"), 0x15: ("ERR_RX3", "VERSION"),
                 0x16: ("ERR_RX4", "LENGTH"), 0x17: ("ERR_RX4", "OVERSIZE"), 0x18: ("ERR_RX5", "IDLE"),
                 0x19: ("ERR_RX5", "NO_PATH"), 0x1A: ("ERR_RX6", "FLAGS"), 0x1B: ("ERR_RX6", "BUFFER")}

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

# Error counters of the AES-GCM core, 0 after a run without errors
OCR_ERRORS = ("ERR_AUTH", "ERR_FORMAT", "ERR_KEY", "ERR_IV_MODE", "ERR_IV_EXHAUSTED", "ERR_LENGTH", "ERR_ECC",
              "ERR_OVERFLOW")


class System:
    """What every system test needs to know about its system, from its Composer file and the header of its register
    banks. A system adds its channels (channel()) from the receive ports of the Space Packet node."""

    def __init__(self, path: Path, banks: RegisterMap):
        f = self.file = SystemFile(path)
        self.node = f.instance_of_block("openspp/spp_core")
        self.rmap = f.instance_of_block("openrmap/omap_core")
        self.port = f.instance_of_block("openfibre/ofb_core")
        self.lanes = f.generic(self.port, "NumLanes_g")
        self.num_vcs = f.generic(self.port, "NumVc_g")
        # virtual channels of the SpaceFibre port: the RMAP node behind its N-Char bridge, the Space Packet node
        vcs = {instance_of(f.connection_from(f"{self.port}.M_Vc[{k}]")["to"]): k
               for k in range(f.generic(self.port, "NumVc_g"))}
        self.rmap_vc = vcs[f.instance_of_block("openspp/spp_nchar")]
        self.spp_vc = vcs[self.node]
        self.ecc = f.instance_of_block("olo/olo_ft_ecc_monitor_axi")
        constants = banks.constants()
        self.windows = {n.removesuffix("_BASE").lower(): v for n, v in constants.items() if n.endswith("_BASE")}
        self.ids = {n.removesuffix("_ID").lower(): v for n, v in constants.items() if n.endswith("_ID")}
        channel = f"{self.ecc.upper()}_CH_"
        # names of the channels of the ECC monitor in channel order, for example FRM0_ECC_0
        self.ecc_names = [n.removeprefix(channel) for n, _ in sorted(
            ((n, v) for n, v in constants.items() if n.startswith(channel)), key=lambda item: item[1])]
        # bytes of a chunk of the framers, in a system that encrypts
        framers = f.instances_of("opencrypto/ocr_framer")
        self.chunk_bytes = f.generic(framers[0], "ChunkBytes_g") if framers else None
        self.rx_buffer_bytes = f.generic(self.node, "RxBufferBytes_g")
        self.ecc_channels = len(self.ecc_names)
        self.ecc_info = f.generic(self.ecc, "Channels_g") | (f.generic(self.ecc, "CounterWidth_g") << 8)
        self.channels = [self.channel(f, port) for port in range(f.generic(self.node, "NumRx_g"))]

    def channel(self, f: SystemFile, port: int):
        """The processing channel behind receive port `port` of the Space Packet node."""
        raise NotImplementedError

    def id_offsets(self) -> dict[str, int]:
        """The offset of the identification register of every block with an identification, in its register map."""
        return {self.node: SPP.ID, self.rmap: OMAP.ID, self.port: OFB.ID} | {c.core: OCR.ID for c in self.cores()}

    def cores(self) -> list:
        """The channels with an AES-GCM core (attribute core): all channels of a system that encrypts, none of one
        that does not."""
        return [c for c in self.channels if getattr(c, "core", None)]

    def base(self, instance: str) -> int:
        return self.windows[instance]

    def decoded_bytes(self) -> int:
        """The bytes of addresses that the AXI-Lite decoder tells apart: it takes the port from the log2ceil(Ports_g)
        address bits above the window of a port and ignores the bits above them, so the map repeats every
        decoded_bytes() bytes (Open Logic, olo_axi_lite_decoder)."""
        decoder = self.file.instances_of("olo/olo_axi_lite_decoder")[0]
        ports = self.file.generic(decoder, "Ports_g")
        return 1 << (self.file.generic(decoder, "PortAddrWidth_g") + (ports - 1).bit_length())

    def unmapped_address(self) -> int | None:
        """The first address after the register windows of the system, where the AXI-Lite decoder answers DECERR;
        None when every port index of the decoder is a port (Ports_g a power of two): then every address reaches a
        register window (decoded_bytes)."""
        decoder = self.file.instances_of("olo/olo_axi_lite_decoder")[0]
        address = max(self.windows.values()) + (1 << self.file.generic(decoder, "PortAddrWidth_g"))
        return address if address < self.decoded_bytes() else None

    def error_registers(self) -> list:
        """The error and memory error registers of the SpaceFibre port, the RMAP target, the Space Packet node and the
        ECC monitor (refsys.errorstate); a system adds those of its own blocks."""
        return (errorstate.ofb_registers(OFB, self.port, self.base(self.port), self.lanes, self.num_vcs)
                + errorstate.omap_registers(OMAP, self.rmap, self.base(self.rmap))
                + errorstate.spp_registers(SPP, self.node, self.base(self.node))
                + errorstate.edac_registers(self.ecc, self.base(self.ecc), self.ecc_channels))


def generic_value(value, width: int = 32) -> int:
    """The value of a generic of the Composer file as a number: an integer, a boolean, a bit or a bit string literal
    ('1', x"FE", "0110") or an aggregate (others => '1') of width bits."""
    if isinstance(value, bool) or isinstance(value, int):
        return int(value)
    text = str(value).strip()
    if text.startswith("(others"):
        return (1 << width) - 1 if "'1'" in text else 0
    text = text.strip("'")
    if text[:1] in "xX" and text[1:2] == '"':
        return int(text[2:-1].replace("_", ""), 16)
    return int(text.strip('"').replace("_", ""), 2)


def generic_words(f: SystemFile, s: System) -> dict[str, dict[str, int]]:
    """The registers that report the generics of the SpaceFibre port, the RMAP target and the Space Packet node, from
    the generics of the Composer file (register maps of OpenFibre, OpenRMAP and OpenSPP): {instance: {register:
    value}}."""
    ofb, omap, spp = f.generics(s.port), f.generics(s.rmap), f.generics(s.node)
    initiator = generic_value(omap["Initiator_g"])
    rmap_generics = (OMAP.field("GENERICS", "TARGET", generic_value(omap["Target_g"]))
                     | OMAP.field("GENERICS", "INITIATOR", initiator)
                     | OMAP.field("GENERICS", "PASSTHROUGH", generic_value(omap["Passthrough_g"]))
                     | OMAP.field("GENERICS", "EXT_AUTH", generic_value(omap["ExtAuth_g"]))
                     | OMAP.field("GENERICS", "WINDOWS", omap["Windows_g"])
                     | OMAP.field("GENERICS", "WORD_BYTES", omap["AxiDataWidth_g"] // 8))
    if initiator:  # without the initiator omap_core reads 0 in these fields (OpenRMAP register map, GENERICS)
        rmap_generics |= (OMAP.field("GENERICS", "TRANSACTIONS", omap["Transactions_g"])
                          | OMAP.field("GENERICS", "TGT_ADDR_BYTES", omap["TgtAddrBytes_g"]))
    ports = (1 << spp["NumTx_g"]) - 1, (1 << spp["NumRx_g"]) - 1
    return {
        s.port: {"GENERICS": OFB.field("GENERICS", "NUM_LANES", ofb["NumLanes_g"])
                 | OFB.field("GENERICS", "NUM_VC", ofb["NumVc_g"])},
        s.rmap: {"GENERICS": rmap_generics,
                 "BUFFERS": OMAP.field("BUFFERS", "VERIFY_BYTES", omap["BufferBytes_g"])
                 | OMAP.field("BUFFERS", "CHUNK_BYTES", omap["ChunkBytes_g"])},
        s.node: {"GENERICS": SPP.field("GENERICS", "NUM_TX", spp["NumTx_g"])
                 | SPP.field("GENERICS", "NUM_RX", spp["NumRx_g"])
                 | SPP.field("GENERICS", "NUM_DEST", spp["NumDest_g"])
                 | SPP.field("GENERICS", "SPW_ADDR_BYTES", spp["SpwAddrBytes_g"])
                 | SPP.field("GENERICS", "DATA_BYTES", spp["DataWidth_g"] // 8)
                 | SPP.field("GENERICS", "ENCAPSULATION", generic_value(spp["Encapsulation_g"])),
                 "SERVICES": SPP.field("SERVICES", "TX_OCTET", generic_value(spp["TxOctetString_g"], 16) & ports[0])
                 | SPP.field("SERVICES", "RX_OCTET", generic_value(spp["RxOctetString_g"], 16) & ports[1]),
                 "TX_BUFFER": spp["TxBufferBytes_g"], "RX_BUFFER": spp["RxBufferBytes_g"]},
    }


def encryption(f: SystemFile, framer: str) -> dict:
    """AES-GCM core, APID of the send port and key slot of the framer `framer`, and the key slots of the core."""
    core = instance_of(f.connection_from(f"{framer}.M_Out")["to"])
    send = f.connection_from(f"{core}.M_Out")
    return {"framer": framer, "core": core, "output_apid": send["sideband"]["apid"],
            "slot": f.tie_off(f"{framer}_Cfg_Slot_s", "slot"), "key_slots": f.generic(core, "NumKeys_g")}


@dataclass(frozen=True)
class Setup:
    """Channels and addresses of the test set-up.

    The input Space Packets use the APIDs input_apid_base + i for processing channel i, while the system sends the
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
    """A pass criterion: logged (a passing check at DEBUG, since its text describes the failure), TestFailed when it
    does not hold."""
    ACTIVE_LOG.get().check(bool(condition), text)
    if not condition:
        raise TestFailed(text)


def register_names(system: "System") -> dict[int, str]:
    """The name of every register of the register windows of a system, by address: <instance>.<register>, an
    element of a register array <instance>.<register>(<index>) (RegisterMap.registers), the registers of the ECC
    monitor with their channel counters CNT(<channel>)."""
    names = {}
    for instance, base in system.windows.items():
        if instance == system.ecc:
            registers = {EDAC_INFO: "INFO", errorstate.EDAC_CTRL: "CTRL", errorstate.EDAC_IRQ_STATUS: "IRQ_STATUS",
                         EDAC_IRQ_ENA: "IRQ_ENA"}
            registers |= {EDAC_CNT + 4 * ch: f"CNT({ch})" for ch in range(system.ecc_channels)}
        else:
            regs = WINDOW_MAPS.get(instance.rstrip("0123456789"))
            registers = regs.registers() if regs else {}
        names.update({base + offset: f"{instance}.{name}" for offset, name in registers.items()})
    return names


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

    def __init__(self, transport, setup: Setup, log: steplog.StepLog = steplog.NULL, names: dict | None = None):
        self.transport = transport
        self.setup = setup
        self.log = log                                  # every access is logged (read, write, wait)
        self.names = names or {}                        # register names by address (register_names)
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
        values = [int.from_bytes(reply.data[i:i + 4], "little") for i in range(0, 4 * words, 4)]
        self.log.access("read", address, values, self.names.get(address, ""))
        return values

    def expect(self, address: int, expected: int, what: str = "", mask: int = 0xFFFFFFFF, quiet: bool = False) -> int:
        """Reads a register (one read, as read) and checks it under mask against the value expected; logged with
        both values. Returns the word read."""
        word = self.read(address)
        what = what or self.names.get(address, f"0x{address:08X}")
        self.log.expect(what, word & mask, expected & mask, quiet=quiet, address=address, mask=mask)
        if word & mask != expected & mask:
            raise TestFailed(f"{what}: 0x{word:08X}, expected 0x{expected:08X}"
                             + (f" under 0x{mask:08X}" if mask != 0xFFFFFFFF else ""))
        return word

    def write(self, address: int, value: int) -> None:
        self.write_block(address, [value])

    def write_block(self, address: int, words: list[int]) -> None:
        """Writes consecutive registers with one verified command (incrementing address)."""
        data = b"".join(w.to_bytes(4, "little") for w in words)
        tid = self.next_tid()
        reply = self.command(rmap.write_command(self.target, address, data, tid), tid, read=False)
        check(reply.ok, f"write of {len(data)} bytes at 0x{address:08X}: {rmap.status_text(reply.status)}")
        self.log.access("write", address, list(words), self.names.get(address, ""))

    def wait(self, address: int, mask: int, value: int, timeout: float = 1.0) -> int:
        end = time.monotonic() + timeout
        polls = 0
        while True:
            word = self.read(address)
            polls += 1
            if word & mask == value:
                self.log.access("wait", address, [word], self.names.get(address, ""), mask=mask, value=value,
                                polls=polls)
                return word
            check(time.monotonic() < end, f"0x{address:08X} = 0x{word:08X}, waited for 0x{value:X} under 0x{mask:X}")


@dataclass
class Context:
    """State of a test run that the test cases share: in a system that encrypts, the keys, fixed fields and first
    invocation counters of the channels and the key material of TC-RS-HW-04 (key_material). A system adds what its own
    test cases need."""

    system: System
    setup: Setup
    seed: int
    keys: list[bytes] = field(default_factory=list, kw_only=True)
    fixed: list[int] = field(default_factory=list, kw_only=True)
    counter: list[int] = field(default_factory=list, kw_only=True)
    test_key: bytes = field(default=b"", kw_only=True)
    test_fixed: int = field(default=0, kw_only=True)
    test_counter: int = field(default=0, kw_only=True)
    records: list[str] = field(default_factory=list, kw_only=True)
    baseline: dict[str, int] = field(default_factory=dict, kw_only=True)   # error registers after link-up
    current: str = field(default="", kw_only=True)                        # the test case that runs
    recorded: list[dict] = field(default_factory=list, kw_only=True)      # the records with their test case
    selected: list = field(default_factory=list, kw_only=True)            # the test cases of the run, in order
    log: steplog.StepLog = field(default_factory=steplog.StepLog, kw_only=True)  # the step log of the run
    requirements: dict = field(default_factory=dict, kw_only=True)        # test case: requirements it confirms


def record(ctx: Context, text: str) -> None:
    """Records a value that is no pass criterion: printed, and kept in the context for the report of a system
    simulation."""
    ctx.records.append(text)
    ctx.recorded.append({"test": ctx.current, "text": text})
    ctx.log.emit("record", text)
    print(f"RECORD {text}")


def key_material(rng: random.Random, count: int) -> dict:
    """Keys, fixed fields and first invocation counters of `count` channels, then the key material of TC-RS-HW-04,
    drawn from rng in this order (fields of Context)."""
    keys = [rng.randbytes(32) for _ in range(count)]
    fixed = [rng.getrandbits(32) for _ in range(count)]
    counter = [rng.getrandbits(32) for _ in range(count)]
    # the counter of TC-RS-HW-04 fills both counter words and stays far from its last value
    return {"keys": keys, "fixed": fixed, "counter": counter, "test_key": rng.randbytes(32),
            "test_fixed": rng.getrandbits(32), "test_counter": rng.randrange(1 << 32, 1 << 63)}


def check_apids(system: System, setup: Setup) -> None:
    """The input APIDs of the set-up must not overlap the output APIDs of the system."""
    inputs = {setup.input_apid_base + c.port for c in system.channels}
    if inputs & {c.output_apid for c in system.channels}:
        raise ValueError("the input APIDs overlap the output APIDs of the system")


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
    """TC-RS-HW-01: the link is up with all lanes, the SpaceFibre port answers over RMAP. Recorded for every lane: the
    receiver resets of the physical adapter and the initialisation timeouts since the reset, and the receiver status
    (OpenFibre user guide, receiver reset of ofb_pa_gty), which show how the lanes came up."""
    port = ctx.system.port
    base = ctx.system.base(port)
    regs.expect(base + OFB.ID, ctx.system.ids[port], "ID of the SpaceFibre port")
    state = OFB.get("DL_STATUS", "LINK_STATE", regs.read(base + OFB.DL_STATUS))
    check(state == LINK_INITIALISED, f"DL_STATUS.LINK_STATE = {state}")
    for lane, state in enumerate(lane_records(regs, ctx)):
        check(state == LANE_ACTIVE, f"LANE_STATUS({lane}).STATE = {state}")


def lane_records(regs: Registers, ctx: Context, when: str = "") -> list[int]:
    """Records for every lane the receiver resets of the physical adapter and the initialisation timeouts since they
    were last cleared, and the receiver status (OpenFibre user guide, receiver reset of ofb_pa_gty); returns the
    state of every lane. when: a prefix of the records."""
    port = ctx.system.port
    base = ctx.system.base(port)
    states = []
    for lane in range(ctx.system.lanes):
        status = regs.read(base + OFB.LANE_STATUS(lane))
        count = regs.read(base + OFB.LANE_TIMEOUT_COUNT(lane))
        states.append(OFB.get("LANE_STATUS", "STATE", status))
        record(ctx, f"{when}{port} lane {lane}: RX_RESETS {OFB.get('LANE_TIMEOUT_COUNT', 'RX_RESETS', count)}, "
               f"initialisation timeouts {OFB.get('LANE_TIMEOUT_COUNT', 'COUNT', count)}, "
               f"RXRESETDONE {OFB.get('LANE_STATUS', 'RX_RESET_DONE', status)}, "
               f"receiver reset in progress {OFB.get('LANE_STATUS', 'RX_RESET_BUSY', status)}, "
               f"CDR lock {OFB.get('LANE_STATUS', 'CDR_LOCK', status)}, "
               f"receiver electrical idle {OFB.get('LANE_STATUS', 'RX_ELEC_IDLE', status)}")
    return states


def baseline(regs: Registers, ctx: Context) -> None:
    """Step of TC-RS-HW-01: the baseline of the error registers of every block right after link-up (refsys.errorstate):
    the registers that hold a value are recorded, then all are cleared and read again: every error register reads 0,
    a leaky counter at most its first value, which stays its baseline."""
    items = ctx.system.error_registers()
    first = errorstate.read_state(regs, items)
    record(ctx, f"error registers after link-up: {errorstate.nonzero(first)}")
    errorstate.clear_state(regs, items)
    ctx.baseline = errorstate.read_state(regs, items)
    problems = errorstate.unexpected(items, ctx.baseline, baseline=first)
    check(not problems, f"error registers that do not clear: {', '.join(problems)}")


def caused(regs: Registers, ctx: Context, label: str, expected: dict[str, int] | None = None,
           allowed: dict[str, int] | None = None) -> dict[str, int]:
    """The end of a test case that provokes errors on purpose: the error registers it targets hold the expected
    values (expected: the error bits of the register, by name), those it may move hold any value in their allowed
    bits (allowed: bits by name, errorstate.ALL for any value), and every other error register is still 0 (a leaky
    counter at most its baseline). The values are recorded, then every error register is cleared. Returns the values
    read."""
    items = ctx.system.error_registers()
    by_name = {r.name: r for r in items if isinstance(r, errorstate.Register)}
    state = errorstate.read_state(regs, items)
    record(ctx, f"{label} error registers: {errorstate.nonzero(state)}")
    for name, value in (expected or {}).items():
        got = state[name] & by_name[name].errors
        check(got == value, f"{name} 0x{state[name]:08X}, expected 0x{value:08X} in its error bits")
    targeted = dict(allowed or {}) | {name: errorstate.ALL for name in expected or {}}
    problems = errorstate.unexpected(items, state, targeted, ctx.baseline)
    check(not problems, f"error registers that the test case does not target: {', '.join(problems)}")
    errorstate.clear_state(regs, items)
    return state


def check_link_state(regs: Registers, ctx: Context) -> None:
    """The SpaceFibre port reports the initialised link with all lanes: DL_STATUS (LINK_STATE, TX_READY), ML_STATUS
    (every lane sending and receiving data, aligned, no bypass) and the state of every lane (OpenFibre register
    map)."""
    s = ctx.system
    base = s.base(s.port)
    lanes = (1 << s.lanes) - 1
    status = regs.read(base + OFB.DL_STATUS)
    check(OFB.get("DL_STATUS", "LINK_STATE", status) == LINK_INITIALISED and OFB.get("DL_STATUS", "TX_READY", status),
          f"{s.port} DL_STATUS 0x{status:08X}: LINK_STATE 3 and TX_READY 1 expected")
    ml = regs.read(base + OFB.ML_STATUS)
    fields = {f: OFB.get("ML_STATUS", f, ml) for f in ("DATA_SENDING", "DATA_RECEIVING", "ALIGN_STATE", "BYPASS")}
    check(fields == {"DATA_SENDING": lanes, "DATA_RECEIVING": lanes, "ALIGN_STATE": 2, "BYPASS": 0},
          f"{s.port} ML_STATUS 0x{ml:08X}: {fields}")
    for lane in range(s.lanes):
        state = OFB.get("LANE_STATUS", "STATE", regs.read(base + OFB.LANE_STATUS(lane)))
        check(state == LANE_ACTIVE, f"{s.port} LANE_STATUS({lane}).STATE = {state}")


def await_rmap(regs: Registers, ctx: Context, attempts: int = 10) -> None:
    """Waits until the RMAP target answers again after a link reset: a request that times out or whose reply does not
    belong to it (a late reply of an earlier request) is followed by a drain of the channel and another request."""
    for attempt in range(attempts):
        try:
            regs.read(ctx.system.base(ctx.system.port) + OFB.DL_STATUS)
            return
        except (TransportError, rmap.RmapError, TestFailed) as err:
            print(f"RMAP request {attempt + 1} after the link reset: {err}")
            drain(regs.transport, (ctx.setup.rmap_channel,))
    raise TestFailed(f"the RMAP target does not answer after {attempts} requests")


def check_generics(regs: Registers, ctx: Context) -> None:
    """The SpaceFibre port, the RMAP target and the Space Packet node report the generics of the Composer file in
    their registers (generic_words)."""
    s = ctx.system
    for instance, words in generic_words(s.file, s).items():
        regmap = {s.port: OFB, s.rmap: OMAP, s.node: SPP}[instance]
        for name, value in words.items():
            word = regs.read(s.base(instance) + getattr(regmap, name))
            check(word == value, f"{instance} {name}: 0x{word:08X}, expected 0x{value:08X}")


# TC-RS-HW-11: registers of the SpaceFibre port written and read back, with their writable bits (OpenFibre register
# map; IRQ_MASK with its documented fields), each back at its value of the reset afterwards
PORT_REGISTERS = (
    ("IRQ_MASK", OFB.field("IRQ_MASK", "DL_ERRORS", 0x1FFF) | OFB.field("IRQ_MASK", "LANE_EVENTS", 0xFF)
     | OFB.field("IRQ_MASK", "ECC_DED", 1) | OFB.field("IRQ_MASK", "ECC_SEC", 1)),
    ("DL_BC_INTERVAL", 0xFFFF), ("VC_IDLE_LIMIT", 0xFFFFFFFF), ("ECC_SELECT", 0xF), ("LANE_INIT_TIMEOUT", 0xFFFFFF))
# the values that LANE_INIT_TIMEOUT takes in TC-RS-HW-11 keep bit 23 set: a lane that would start in the meantime
# waits at least 2^23 words (54 ms at 156.25 MHz), never less than with the value of the reset
LONG_TIMEOUT = 1 << 23


def port_reset_values(ctx: Context) -> dict[str, int]:
    """The values of the reset of the registers of PORT_REGISTERS (OpenFibre register map)."""
    return {"IRQ_MASK": 0, "DL_BC_INTERVAL": 0x28, "VC_IDLE_LIMIT": 0x2625A, "ECC_SELECT": 0,
            "LANE_INIT_TIMEOUT": ctx.system.file.generic(ctx.system.port, "InitTimeoutWords_g")}


def tc11_port_registers(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-11: registers of the SpaceFibre port over RMAP: every register of PORT_REGISTERS reads its value of
    the reset, takes a random value, its complement and all ones in its writable bits with a verified write and reads
    each back, then is restored; the virtual network number of every VC (VC_CFG.VN, the other fields at their reset
    values) reads back as written, that of VC 0 reads 0 (MG-VN-01), and is restored."""
    s = ctx.system
    base = s.base(s.port)
    rng = random.Random(f"{ctx.seed} TC-RS-HW-11")
    reset = port_reset_values(ctx)
    for name, mask in PORT_REGISTERS:
        address = base + getattr(OFB, name)
        regs.expect(address, reset[name], f"{s.port} {name}, value of the reset")
        p = rng.getrandbits(32) & mask
        values = [p, ~p & mask, mask]
        if name == "LANE_INIT_TIMEOUT":
            values = [v | LONG_TIMEOUT for v in values]
        for value in values + [reset[name]]:
            regs.write(address, value)
            word = regs.read(address)
            check(word == value, f"{s.port} {name}: wrote 0x{value:08X}, read 0x{word:08X}")
    for vc in range(s.num_vcs):
        address = base + OFB.VC_CFG(vc)
        cfg = OFB.field("VC_CFG", "VN", vc) | OFB.field("VC_CFG", "PRIORITY", 3)
        regs.expect(address, cfg, f"{s.port} VC_CFG({vc}), value of the reset")
        vn = rng.randrange(1, 64)
        regs.write(address, OFB.field("VC_CFG", "VN", vn) | OFB.field("VC_CFG", "PRIORITY", 3))
        got = OFB.get("VC_CFG", "VN", regs.read(address))
        check(got == (0 if vc == 0 else vn), f"{s.port} VC_CFG({vc}).VN: wrote {vn}, read {got}")
        regs.write(address, cfg)
        regs.expect(address, cfg, f"{s.port} VC_CFG({vc}), restored")


def end_of_run(regs: Registers, ctx: Context) -> None:
    """Step of TC-RS-HW-17: every error and memory error register of every block still reads 0 since the baseline of
    TC-RS-HW-01, the leaky counters at most their baseline; the values and the lanes are recorded."""
    items = ctx.system.error_registers()
    state = errorstate.read_state(regs, items)
    record(ctx, f"error registers at the end of the run: {errorstate.nonzero(state)}")
    lane_records(regs, ctx, "end of the run: ")
    problems = errorstate.unexpected(items, state, baseline=ctx.baseline)
    check(not problems, f"error registers that counted since the baseline: {', '.join(problems)}")


def tc02_identification(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-02: every register window answers with the identification of its block."""
    s = ctx.system
    # the identification register of a block in its own register map, its value in the exported header
    offsets = s.id_offsets()
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
        regs.expect(address, value, "IRQ_ENA of the ECC monitor, read back")
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


def route_inputs(regs: Registers, ctx: Context, continuity: bool = False) -> None:
    """Step of TC-RS-HW-05: input APID input_apid_base + i goes to receive port i; with continuity, the Space Packet
    node checks the Packet Sequence Count of every input APID (RX_TABLE_ENTRY.CHECK)."""
    s, setup = ctx.system, ctx.setup
    node = s.base(s.node)
    busy = SPP.field("TABLE_STATUS", "TX_BUSY", 1) | SPP.field("TABLE_STATUS", "RX_BUSY", 1)
    regs.wait(node + SPP.TABLE_STATUS, busy, 0)
    for ch in s.channels:
        apid = setup.input_apid_base + ch.port
        regs.write(node + SPP.RX_TABLE_APID, apid)
        regs.read(node + SPP.RX_TABLE_ENTRY)
        entry = SPP.field("RX_TABLE_ENTRY", "ENABLE", 1) | SPP.field("RX_TABLE_ENTRY", "PORT", ch.port)
        if continuity:
            entry |= SPP.field("RX_TABLE_ENTRY", "CHECK", 1)
        regs.write(node + SPP.RX_TABLE_ENTRY, entry)
        word = regs.read(node + SPP.RX_TABLE_ENTRY)
        check(SPP.get("RX_TABLE_ENTRY", "PORT", word) == ch.port and SPP.get("RX_TABLE_ENTRY", "ENABLE", word)
              and (not continuity or SPP.get("RX_TABLE_ENTRY", "CHECK", word)),
              f"receive APID table entry of APID 0x{apid:03X}: 0x{word:08X}")
    check(not regs.read(node + SPP.TABLE_STATUS) & SPP.field("TABLE_STATUS", "RX_OVERRUN", 1),
          "TABLE_STATUS.RX_OVERRUN")


def read_start_counts(regs: Registers, ctx: Context) -> None:
    """Step of TC-RS-HW-05: the Packet Sequence Count of the next Space Packet of every output APID, from the send
    APID table of the Space Packet node (TX_TABLE_ENTRY.COUNT, OpenSPP register map), recorded; the packets of the
    run must continue from it. The table keeps the counts until a reset of the node, so a run before this one on the
    same device leaves the counts after its last packets (SPP 4.1.3.4.3). The receive side needs no such step:
    route_inputs writes every entry of an input APID with VALID 0, and the first packet after that sets no loss
    indicator (OpenSPP RE-DM-03)."""
    s = ctx.system
    node = s.base(s.node)
    regs.wait(node + SPP.TABLE_STATUS, SPP.field("TABLE_STATUS", "TX_BUSY", 1), 0)
    for ch in s.channels:
        regs.write(node + SPP.TX_TABLE_APID, ch.output_apid)
        word = regs.read(node + SPP.TX_TABLE_ENTRY)
        check(not SPP.get("TX_TABLE_ENTRY", "DED", word),
              f"send APID table entry of APID {ch.output_apid}: double error, 0x{word:08X}")
        ctx.output_count[ch.output_apid] = SPP.get("TX_TABLE_ENTRY", "COUNT", word)
        record(ctx, f"{s.node} sends the next Space Packet of APID {ch.output_apid} with Packet Sequence Count "
               f"{ctx.output_count[ch.output_apid]}")


def load_keys(regs: Registers, ctx: Context) -> None:
    """Step of TC-RS-HW-05: the key slot of the framer of every channel holds the key of the channel with generated
    IVs."""
    for k, ch in enumerate(ctx.system.channels):
        core = ctx.system.base(ch.core)
        load_slot(regs, core, ctx.keys[k], ctx.fixed[k], ctx.counter[k], ch.slot)
        state = query_slot(regs, core, ch.slot)
        expected = loaded_slot(ctx.fixed[k], ctx.counter[k])
        check(state == expected, f"{ch.core} slot {ch.slot} holds {hex_words(state)}, expected {hex_words(expected)}")


def clear_counters(regs: Registers, addresses: list[int]) -> None:
    for address in addresses:
        regs.write(address, 0)  # a write clears a counter


def ocr_counters(regs: Registers, ctx: Context) -> int:
    """Step of TC-RS-HW-07: no AES-GCM core counted an error; returns the sum of their ENCRYPTIONS counters."""
    encryptions = 0
    for ch in ctx.system.channels:
        core = ctx.system.base(ch.core)
        encryptions += regs.read(core + OCR.ENCRYPTIONS)
        for name in OCR_ERRORS:
            regs.expect(core + getattr(OCR, name), 0, f"{ch.core} {name}")
    return encryptions


def ecc_counters(regs: Registers, ctx: Context) -> None:
    """Step of TC-RS-HW-07: no channel of the ECC monitor counted a memory error."""
    s = ctx.system
    for channel, name in enumerate(s.ecc_names):
        regs.expect(s.base(s.ecc) + EDAC_CNT + 4 * channel, 0, f"ECC monitor counters of channel {channel} ({name})")


def test_name(test) -> str:
    return test.__doc__.split(":")[0]


def select_tests(names: list[str] | None, tests: list, requires: dict) -> list:
    """The test cases of the given names (all without names) with the test cases they build on (requires), in the
    order of tests."""
    if not names:
        return list(tests)
    by_name = {test_name(t): t for t in tests}
    unknown = [n for n in names if n not in by_name]
    if unknown:
        raise ValueError(f"unknown test cases {unknown}; known: {', '.join(by_name)}")
    chosen = {by_name[n] for n in names}
    chosen |= {r for t in list(chosen) for r in requires.get(t, [])}
    return [t for t in tests if t in chosen]


def run(transport, ctx: Context, tests: list, registers: type[Registers] = Registers) -> list[tuple[str, str | None]]:
    """Runs the test cases in order and stops at the first failure; returns the name and the error (None when it
    passed) of every test case that ran."""
    regs = prepare(transport, ctx, tests, registers)
    token = ACTIVE_LOG.set(ctx.log)
    results = []
    try:
        for test in tests:
            name = test_name(test)
            try:
                run_one(regs, ctx, test)
            except Exception as err:  # noqa: BLE001 - the failure is reported, the later test cases depend on it
                print(f"FAIL {name}: {err}")
                results.append((name, str(err)))
                break
            print(f"PASS {name}")
            results.append((name, None))
    finally:
        ACTIVE_LOG.reset(token)
    passed = sum(error is None for _, error in results)
    print(f"{passed} of {len(tests)} test cases passed")
    return results


def prepare(transport, ctx: Context, tests: list, registers: type[Registers] = Registers) -> Registers:
    """The register access of a run of the test cases tests, logging into the step log of the context."""
    ctx.selected = list(tests)
    return registers(transport, ctx.setup, ctx.log, register_names(ctx.system))


def run_one(regs: Registers, ctx: Context, test) -> None:
    """Runs one test case: its mark for the transport, its name in the context and in the step log, and a step
    event with the description of the test case and the requirements it confirms (ctx.requirements)."""
    name = test_name(test)
    regs.transport.begin(name)
    ctx.current = name
    ctx.log.begin(name)
    ctx.log.step(" ".join(test.__doc__.split(":", 1)[1].split()), tuple(ctx.requirements.get(name, ())))
    test(regs, ctx)


def repeat_run(regs: Registers, ctx: Context, fresh: Context) -> None:
    """TC-RS-HW-18: the test cases of the run before this one run again against the same device, without a reset or
    a new device image, with fresh, the context that a second run of the script starts with: the second run finds
    what the first one left in the system (the Packet Sequence Counts of the Space Packet node, the configuration of
    the compressors, the counters) and must pass all the same. The test cases of the second run make no marks of their
    own; their records go into the records of the run, with the name of this test case and their own."""
    names = [test_name(t) for t in ctx.selected]
    check(ctx.current in names and names.index(ctx.current) > 0, f"{ctx.current} has no test case before it to repeat")
    tests = ctx.selected[:names.index(ctx.current)]
    fresh.selected = tests
    fresh.log = ctx.log
    second = type(regs)(regs.transport, fresh.setup)
    second.log, second.names = regs.log, regs.names
    try:
        for test in tests:
            name = test_name(test)
            fresh.current = f"{ctx.current} {name}"
            ctx.log.begin(fresh.current)
            ctx.log.step(f"second run: {' '.join(test.__doc__.split(':', 1)[1].split())}")
            try:
                test(second, fresh)
            except Exception as err:  # noqa: BLE001 - the failure of the second run is the failure of this test case
                raise TestFailed(f"second run, {name}: {err}") from err
            print(f"PASS {ctx.current}: second run of {name}")
    finally:
        ctx.records += fresh.records
        ctx.recorded += fresh.recorded


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


def arguments(description: str, model: bool = False) -> argparse.ArgumentParser:
    """The arguments that the hardware test of every system takes; a system adds the size of its run. With model the
    test also runs against the software model of the system (--model, the dry run before the hardware) and checks
    the test computer (--check-environment), and it can write the records of the run (--records)."""
    parser = argparse.ArgumentParser(description=description)
    if model:
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--device", help="name of the STAR-Ultra device in STAR-System")
        target.add_argument("--model", action="store_true",
                            help="run against the software model of the system, without the test equipment")
        target.add_argument("--check-environment", action="store_true",
                            help="check the Python of the test computer and the packages that the test needs")
        parser.add_argument("--records", type=Path,
                            help="directory for the records of the run (a JSON file per run); nothing is written "
                                 "without it")
    else:
        parser.add_argument("--device", required=True, help="name of the STAR-Ultra device in STAR-System")
    parser.add_argument("--seed", type=int, default=1, help="seed of keys and packets")
    parser.add_argument("--test", action="append", help="run only this test case and those it builds on")
    return parser


def pytest_main(system_dir: Path, args: argparse.Namespace) -> int:
    """hardware_test.py --device/--model as a run of the pytest tests of the system (systems/<system>/sw/hwtest,
    sw/refsys/hwplugin.py), with the same options; the outputs go into --records, or into a new temporary directory
    outside the repository. Returns the exit code of pytest."""
    import tempfile

    import pytest

    results = args.records or Path(tempfile.mkdtemp(prefix="refsys_"))
    argv = [str(system_dir / "sw" / "hwtest"), "--target", "model" if args.model else "device",
            "--profile", args.profile, "--seed", str(args.seed), "--results", str(results)]
    if not args.model:
        argv += ["--device", args.device]
    for name in args.test or ():
        argv += ["--tc", name]
    print(f"python -B -m pytest {' '.join(argv)}")
    return int(pytest.main(argv))


def check_environment() -> int:
    """--check-environment: Python 3.10 or later, the reference model of OpenCompact and the Python package of the
    far-end software that the transport uses. One line per item; exit code 0 when every item is there."""
    from refsys import transport

    def python() -> str:
        if sys.version_info < (3, 10):
            raise RuntimeError(f"Python {sys.version.split()[0]}, 3.10 or later needed")
        return sys.version.split()[0]

    def pytest_version() -> str:
        return importlib.import_module("pytest").__version__

    def model() -> str:
        module = importlib.import_module("ocp_model")
        return f"found, decoder {'included' if hasattr(module, 'decompress') else 'not included'}"

    items = [("Python", python), ("pytest", pytest_version), ("reference model of OpenCompact", model),
             ("Python package of the far-end software", transport.far_end_software)]
    ok = True
    for name, probe in items:
        try:
            print(f"OK   {name}: {probe()}")
        except Exception as err:  # noqa: BLE001 - every missing item is reported
            ok = False
            print(f"FAIL {name}: {err}")
    return 0 if ok else 1


def run_on_model(model, ctx: Context, tests: list, run_tests=None) -> int:
    """--model: runs the test cases against the software model of the system; returns the exit code of the script
    (0 when every test case passes)."""
    results = (run_tests or run)(model, ctx, tests)
    return 0 if len(results) == len(tests) and all(error is None for _, error in results) else 1


def write_records(directory: Path, system: str, target: str, profile: str, ctx: Context, results: list,
                  started: str, extra: dict | None = None) -> Path:
    """--records: one JSON file per run in directory, with the date and time of the run, the system, the target
    (device or model), the profile and seed, the result of every test case, the recorded values with their test case
    and what the system adds (extra). No host name and no path (tools/render_records.py renders the tables of the
    report from it)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"records_{system}_{started.replace(':', '').replace('-', '').replace(' ', '_')}.json"
    data = {"system": system, "started": started, "finished": time.strftime("%Y-%m-%d %H:%M:%S"), "target": target,
            "profile": profile, "seed": ctx.seed,
            "results": [{"test": name, "pass": error is None, "error": error} for name, error in results],
            "records": ctx.recorded} | (extra or {})
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"records written: {path.name}")
    return path


def open_device(device: str, ctx: Context):
    """The far end of a run on the device: the test equipment, opened, with both of its channels drained of what an
    earlier run left on them. The caller closes it (close())."""
    from refsys.transport import StarSystemTransport

    transport = StarSystemTransport(device).__enter__()
    drain(transport, (ctx.setup.rmap_channel, ctx.setup.spp_channel))
    return transport


def system_modules(directory: Path, *names: str) -> list:
    """Imports the modules `names` of the software of one system (systems/<system>/sw or sim, for example
    hardware_test and system_model, in this order) and returns them, without leaving them in sys.modules under their
    names: every system has a hardware_test.py, and the tests of all systems run in one process. The modules of
    another system that are imported under these names stay as they are."""
    saved = {name: sys.modules.pop(name) for name in names if name in sys.modules}
    sys.path.insert(0, str(directory))
    try:
        return [importlib.import_module(name) for name in names]
    finally:
        sys.path.remove(str(directory))
        for name in names:
            sys.modules.pop(name, None)
        sys.modules.update(saved)
