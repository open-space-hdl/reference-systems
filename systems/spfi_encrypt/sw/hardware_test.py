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
register maps from the headers of the cores. The set-up and the procedure are described with the board target. The
test cases TC-RS-HW-01 to TC-RS-HW-04, the steps of TC-RS-HW-05 and TC-RS-HW-07 that every reference system takes,
the register access and the run are those of sw/refsys/systest.py.

Before the first test case the script discards the packets that an earlier run left on both channels (drain).

Usage: python systems/spfi_encrypt/sw/hardware_test.py --device <STAR-Ultra device name> [--seed N] [--profile P]
       [--packets N] [--test TC-RS-HW-nn ...]
Exit code 0 when every test case passes.
"""

import random
import sys
from dataclasses import dataclass
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parents[1]
ROOT = SYSTEM_DIR.parents[1]
sys.path.insert(0, str(ROOT / "sw"))

from refsys import chunks, systest  # noqa: E402
from refsys.composer import SystemFile, instance_of  # noqa: E402
from refsys.regs import RegisterMap  # noqa: E402
from refsys.spp import PRIMARY_HEADER_BYTES, SpacePacket, cptp_decode, cptp_encode  # noqa: E402
# the shared parts under the names that the system model, the system simulation and the tests use
from refsys.systest import (  # noqa: E402, F401
    DRAIN_LIMIT, DRAIN_TIMEOUT, EDAC_CNT, EDAC_INFO, EDAC_IRQ_ENA, LANE_ACTIVE, LINK_INITIALISED, OCR, OCR_INFO,
    OCR_IV, OCR_SLOT, OFB, OMAP, SPP, Registers, Setup, TestFailed, check, consecutive, drain, hex_words,
    load_slot, loaded_slot, query_slot, tc01_link, tc02_identification, tc03_rmap, tc04_multi_word, test_name)

# register banks of the system and channels of its ECC monitor, exported by the Composer
BANKS = RegisterMap(SYSTEM_DIR / "hdl" / "spfi_encrypt_regs.h", "SPFI_ENCRYPT")


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


class System(systest.System):
    """What the test needs to know about spfi_encrypt, from its Composer file."""

    def __init__(self, path: Path = SYSTEM_DIR / "spfi_encrypt.composer.json"):
        super().__init__(path, BANKS)

    def channel(self, f: SystemFile, port: int) -> Channel:
        framer = instance_of(f.connection_from(f"{self.node}.M_Rx[{port}]")["to"])
        return Channel(port=port, **systest.encryption(f, framer))


@dataclass
class Context(systest.Context):
    packets: int
    chunks: int = 0


def tc05_configure(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-05: input APID i goes to receive port i; the key slot of every AES-GCM core is loaded with its own
    key and generated IVs."""
    s = ctx.system
    node = s.base(s.node)
    systest.route_inputs(regs, ctx)
    systest.load_keys(regs, ctx)
    systest.clear_counters(regs, [node + SPP.RX_PACKETS, node + SPP.TX_PACKETS]
                           + [s.base(c.core) + OCR.ENCRYPTIONS for c in s.channels])


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
    encryptions = systest.ocr_counters(regs, ctx)
    check(encryptions == ctx.chunks, f"{encryptions} encryptions, {ctx.chunks} chunks")
    systest.ecc_counters(regs, ctx)


def tc18_repeated_run(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-18: the test cases of the run before this one run again on the same device, without a reset or a new
    device image, as a second run of the script does (systest.repeat_run): every one passes again; its records go
    into those of the run."""
    systest.repeat_run(regs, ctx, make_context(ctx.seed, ctx.packets, ctx.setup, ctx.system))


TESTS = [tc01_link, tc02_identification, tc03_rmap, tc04_multi_word, tc05_configure, tc06_encryption,
         tc07_counters, tc18_repeated_run]

# Test cases that a test case builds on; they run before it when it is selected on its own
REQUIRES = {tc06_encryption: [tc05_configure], tc07_counters: [tc05_configure, tc06_encryption],
            tc18_repeated_run: [tc01_link, tc05_configure]}


def select_tests(names: list[str] | None = None) -> list:
    """The test cases of the given names (all without names) with the test cases they build on, in order."""
    return systest.select_tests(names, TESTS, REQUIRES)


def make_context(seed: int, packets: int, setup: Setup = Setup(), system: System | None = None) -> Context:
    system = system or System()
    systest.check_apids(system, setup)
    packet_lengths(system, packets, random.Random(seed))
    return Context(system=system, setup=setup, seed=seed, packets=packets,
                   **systest.key_material(random.Random(seed), len(system.channels)))


def run(transport, ctx: Context, tests: list | None = None,
        registers: type[Registers] = Registers) -> list[tuple[str, str | None]]:
    """Runs the test cases in order (all by default) and stops at the first failure; returns the name and the error
    (None when it passed) of every test case that ran."""
    return systest.run(transport, ctx, tests or TESTS, registers)


def main() -> int:
    parser = systest.arguments(__doc__.splitlines()[0])
    parser.add_argument("--profile", choices=PROFILES, default="lab", help="size of the run (default lab)")
    parser.add_argument("--packets", type=int, help="Space Packets sent in TC-RS-HW-06 (default: of the profile)")
    args = parser.parse_args()
    ctx = make_context(args.seed, args.packets or PROFILES[args.profile].packets)
    return systest.run_on_device(args.device, ctx, select_tests(args.test))


if __name__ == "__main__":
    sys.exit(main())
