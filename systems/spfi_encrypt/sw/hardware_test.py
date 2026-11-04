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
the register access and the run are those of sw/refsys/systest.py, the steps of TC-RS-HW-19 (pipeline reset) those
of sw/refsys/resettest.py.

Before the first test case the script discards the packets that an earlier run left on both channels (drain).

Usage: python systems/spfi_encrypt/sw/hardware_test.py --device <STAR-Ultra device name> [--seed N] [--profile P]
       [--packets N] [--test TC-RS-HW-nn ...]
Exit code 0 when every test case passes.
"""

import itertools
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parents[1]
ROOT = SYSTEM_DIR.parents[1]
sys.path.insert(0, str(ROOT / "sw"))

from refsys import chunks, errorstate, resettest, systest  # noqa: E402
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
    fewer Space Packets in TC-RS-HW-06 and the same boundary cases (packet_lengths). With clear_bw_flags TC-RS-HW-19
    and 22 clear VC_BW_OVER and VC_BW_UNDER of the SpaceFibre port before their first read
    (systest.clear_bw_flags)."""

    name: str
    packets: int
    clear_bw_flags: bool = False


PROFILES = {p.name: p for p in (Profile("lab", 40), Profile("sim", 8, clear_bw_flags=True))}


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
    output_count: dict[int, int] = field(default_factory=dict)        # APID: expected next Packet Sequence Count


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
    rng = random.Random(ctx.seed)
    ctx.chunks = send_packets(regs, ctx, packet_lengths(ctx.system, ctx.packets, rng), rng)


def send_packets(regs: Registers, ctx: Context, lengths: list[int], rng: random.Random,
                 first_count: int | None = None) -> int:
    """Sends Space Packets of these data lengths, the channels in turn, and checks the chunks that come back: the
    APID of the channel, consecutive Packet Sequence Counts (from first_count, if given), authenticated with the key
    of the channel, its fixed field, the invocation counters from the next counter of its key slot (next_counter),
    and the chunks give the packets sent. Returns the number of chunks."""
    s, setup = ctx.system, ctx.setup
    n = s.chunk_bytes
    sent: list[list[bytes]] = [[] for _ in s.channels]
    packets = []
    for k, length in enumerate(lengths):
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
    for k, ch in enumerate(s.channels):
        out = by_apid[ch.output_apid]
        counts = [p.sequence_count for p in out]
        check(all(b == (a + 1) & 0x3FFF for a, b in zip(counts, counts[1:])),
              f"APID {ch.output_apid}: Packet Sequence Counts {counts[:8]}")
        check(first_count is None or not counts or counts[0] == first_count,
              f"APID {ch.output_apid}: Packet Sequence Counts {counts[:8]}, expected from {first_count}")
        decrypted = []
        for p in out:
            try:
                chunk = chunks.decrypt(ctx.keys[k], p.data)
            except chunks.ChunkError as err:
                raise TestFailed(f"APID {ch.output_apid}: {err}") from err
            check(chunk.fixed == ctx.fixed[k], f"APID {ch.output_apid}: fixed field 0x{chunk.fixed:08X}")
            decrypted.append(chunk)
        if decrypted:
            check(min(c.counter for c in decrypted) == ctx.next_counter[ch.port], f"APID {ch.output_apid}: first "
                  f"counter {min(c.counter for c in decrypted)}, expected {ctx.next_counter[ch.port]}")
            ctx.next_counter[ch.port] += len(decrypted)
        try:
            packets_back = chunks.reassemble(decrypted)
        except chunks.ChunkError as err:
            raise TestFailed(f"APID {ch.output_apid}: {err}") from err
        check(packets_back == sent[k], f"APID {ch.output_apid}: decrypted packets differ from the input")
        ctx.log.emit("packets", f"APID {ch.output_apid}: {len(out)} chunks, Packet Sequence Counts "
                     f"{counts[0] if counts else '-'} to {counts[-1] if counts else '-'}, every chunk authenticated "
                     "and "
                     f"decrypted with the key of its channel, {len(sent[k])} Space Packets of "
                     f"{sum(len(d) for d in sent[k])} bytes equal to those sent", "pass", apid=ch.output_apid,
                     chunks=len(out), counts=[counts[0], counts[-1]] if counts else [],
                     lengths=[len(p.data) for p in out], packets=[len(d) for d in sent[k]])
    return expected


def tc07_counters(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-07: counters match the traffic, no error and no memory error is counted."""
    s = ctx.system
    node = s.base(s.node)
    regs.expect(node + SPP.RX_PACKETS, ctx.packets, "RX_PACKETS of the Space Packet node")
    regs.expect(node + SPP.TX_PACKETS, ctx.chunks, "TX_PACKETS of the Space Packet node")
    encryptions = systest.ocr_counters(regs, ctx)
    check(encryptions == ctx.chunks, f"{encryptions} encryptions, {ctx.chunks} chunks")
    systest.ecc_counters(regs, ctx)


def after_pipeline_reset(regs: Registers, ctx: Context, counters: dict[int, int]) -> None:
    """The configuration of TC-RS-HW-05 again after a pipeline reset, which returned it to its reset values: input
    APID i goes to receive port i, every key is loaded again with the invocation counter of its channel in counters
    (PR-12), the counters are cleared."""
    s = ctx.system
    systest.route_inputs(regs, ctx)
    systest.load_keys(regs, ctx, counters)
    systest.clear_counters(regs, [s.base(s.node) + SPP.RX_PACKETS, s.base(s.node) + SPP.TX_PACKETS]
                           + [s.base(c.core) + OCR.ENCRYPTIONS for c in s.channels])


def tc19_pipeline_reset(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-19: pipeline reset at rest (sw/refsys/resettest.py). One Space Packet per channel leaves counts in
    the Space Packet node and the AES-GCM cores; an entry of each APID table and CONFIG.ROUND_ROBIN are left in the
    node, the last window of the RMAP target, IRQ_ENA of the ECC monitor and DL_BC_INTERVAL of the SpaceFibre port
    outside the processing path. The writes that the pipeline reset controller ignores change neither STATUS.COUNT
    nor the table entry. With the channel of the Space Packets quiet and every key slot at the counter after its last
    chunk (systest.quiet_counters), the command resets the processing path: the Space Packet node holds its reset
    values, every key slot is erased and every AES-GCM core counts 0, nothing outside changed; a second command counts
    once more. After the configuration of TC-RS-HW-05 with every key loaded again at the counter after its last chunk
    (PR-12), one Space Packet per channel comes back as chunks whose Packet Sequence Counts start at 0 and whose
    first invocation counter is greater than the last one before the reset."""
    s = ctx.system
    rng = random.Random(f"{ctx.seed} TC-RS-HW-19")
    lengths = [s.chunk_bytes + 1] * len(s.channels)
    send_packets(regs, ctx, lengths, rng)
    resettest.leave_in_node(regs, ctx)
    left = resettest.leave_outside(regs, ctx)
    resettest.ignored_writes(regs, ctx)
    counters = systest.quiet_counters(regs, ctx)
    systest.pipeline_reset(regs, ctx, " of TC-RS-HW-19")
    resettest.check_node_reset(regs, ctx)
    systest.erased_slots(regs, ctx)
    resettest.check_members_errors(regs, ctx)
    resettest.check_outside(regs, ctx, left)
    systest.pipeline_reset(regs, ctx, " of TC-RS-HW-19, second command")
    resettest.check_node_reset(regs, ctx)
    resettest.restore_outside(regs, ctx, left)
    after_pipeline_reset(regs, ctx, counters)
    send_packets(regs, ctx, lengths, rng, first_count=0)
    for ch in s.channels:
        systest.record(ctx, f"{ch.core}: first invocation counter after the reload {counters[ch.port]}, last before "
                       f"the pipeline reset {counters[ch.port] - 1}")


def link_packets(ctx: Context, ch: Channel, packets: list[bytes]) -> list[bytes]:
    """The link packets in which the system sends the chunks of these input Space Packets of a channel: the
    authenticated chunks of its framer and AES-GCM core, the invocation counters from the next one of its key slot
    (ctx.next_counter), each in a CPTP packet of a Space Packet with the APID of the channel, the Packet Sequence
    Counts from the next one of the APID (ctx.output_count), as the model of the system builds them."""
    apid = ch.output_apid
    count, counter = ctx.output_count.get(apid, 0), ctx.next_counter[ch.port]
    out = []
    for pos, part in itertools.chain.from_iterable(chunks.split(data, ctx.system.chunk_bytes) for data in packets):
        chunk = chunks.encrypt(ctx.keys[ch.port], ctx.fixed[ch.port], counter, pos, part)
        out.append(cptp_encode(SpacePacket(apid=apid, data=chunk, sequence_count=count).encode(),
                               ctx.setup.far_end_tla))
        count, counter = (count + 1) & 0x3FFF, counter + 1
    return out


def tc22_reset_during_traffic(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-22: pipeline reset during traffic (sw/refsys/resettest.py). The Packet Sequence Counts of the output
    APIDs are read; of the Space Packets of TC-RS-HW-06 (the channels in turn) the far end sends the first k, k from
    the seed, the slots are queried and the command follows, while the channels encrypt; the reload counter of every
    key is the number of chunks of the packets sent (systest.bound_counters, PR-12). The far end then sends nothing
    until the configuration is written again (PR-11) and reads the channel of the Space Packets until it is quiet:
    what came back is per APID a prefix of the chunks of the packets sent, the last one possibly cut by the reset
    (recorded). RMAP answers and the link is up; the counters of the Space Packet node are recorded. A second pipeline
    reset with the link quiet: the reset values as in TC-RS-HW-19; after the configuration of TC-RS-HW-05 with every
    key loaded again at its bound, one Space Packet per channel comes back as chunks with Packet Sequence Counts from
    0 and a first invocation counter greater than every counter before the first reset; every error register outside
    the processing path holds its value from before the traffic."""
    s, setup = ctx.system, ctx.setup
    systest.clear_bw_flags(regs, ctx)
    errors = errorstate.read_state(regs, resettest.outside_items(ctx))
    systest.read_start_counts(regs, ctx)
    rng = random.Random(f"{ctx.seed} TC-RS-HW-22")
    lengths = packet_lengths(s, ctx.packets, rng)
    k = rng.randrange(1, len(lengths))
    sent: dict[int, list[bytes]] = {c.port: [] for c in s.channels}
    packets = []
    for n, length in enumerate(lengths[:k]):
        ch = s.channels[n % len(s.channels)]
        data = rng.randbytes(length)
        sent[ch.port].append(data)
        packet = SpacePacket(apid=setup.input_apid_base + ch.port, data=data, sequence_count=n).encode()
        packets.append(cptp_encode(packet, setup.system_tla))
    systest.record(ctx, f"TC-RS-HW-22: the pipeline reset after {k} of {len(lengths)} Space Packets")
    regs.transport.transmit(setup.spp_channel, packets)
    expected = {ch.output_apid: link_packets(ctx, ch, sent[ch.port]) for ch in s.channels}
    counters = systest.bound_counters(regs, ctx, {port: sum(len(chunks.split(d, s.chunk_bytes)) for d in datas)
                                                  for port, datas in sent.items()}, " during the traffic")
    systest.pipeline_reset(regs, ctx, " of TC-RS-HW-22, during traffic")
    resettest.judge_traffic(ctx, systest.receive_quiet(regs, ctx), expected, "TC-RS-HW-22")
    systest.check_link_state(regs, ctx)
    node = s.base(s.node)
    systest.record(ctx, f"after the pipeline reset during traffic: {s.node} " + ", ".join(
        f"{name} {regs.read(node + getattr(SPP, name))}" for name in ("RX_PACKETS", "TX_PACKETS", "RX_LOSS")))
    systest.pipeline_reset(regs, ctx, " of TC-RS-HW-22, second command")
    resettest.check_node_reset(regs, ctx)
    systest.erased_slots(regs, ctx)
    resettest.check_members_errors(regs, ctx)
    after_pipeline_reset(regs, ctx, counters)
    send_packets(regs, ctx, [s.chunk_bytes + 1] * len(s.channels), rng, first_count=0)
    resettest.check_outside_errors(regs, ctx, errors, "at the end of TC-RS-HW-22")


def tc23_fresh_after_reset(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-23: fresh after a pipeline reset: with the channel of the Space Packets quiet and every key slot at the
    counter after its last chunk (systest.quiet_counters), the pipeline reset, then TC-RS-HW-02 to 07 again as a run
    that starts from the reset values (systest.fresh_run), its keys loaded at these counters (PR-12): the Packet
    Sequence Counts of the output APIDs start at 0, every Space Packet comes back as its chunks and the counters
    match, as after a new device image."""
    counters = systest.quiet_counters(regs, ctx)
    systest.pipeline_reset(regs, ctx, " of TC-RS-HW-23")
    fresh = make_context(ctx.seed, ctx.packets, ctx.setup, ctx.system, ctx.clear_bw_flags)
    tests = [tc02_identification, tc03_rmap, tc04_multi_word, tc05_configure, tc06_encryption, tc07_counters]
    try:
        systest.fresh_run(regs, ctx, fresh, tests, counters)
    finally:
        ctx.next_counter = fresh.next_counter


def tc18_repeated_run(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-18: the test cases of the run before this one run again on the same device, without a reset or a new
    device image, as a second run of the script does (systest.repeat_run): every one passes again; its records go
    into those of the run."""
    systest.repeat_run(regs, ctx, make_context(ctx.seed, ctx.packets, ctx.setup, ctx.system, ctx.clear_bw_flags))


TESTS = [tc01_link, tc02_identification, tc03_rmap, tc04_multi_word, tc05_configure, tc06_encryption,
         tc07_counters, tc19_pipeline_reset, tc22_reset_during_traffic, tc23_fresh_after_reset, tc18_repeated_run]

# The procedure names no requirement of the cores (it has no coverage section): the test cases carry none.
REQUIREMENTS: dict = {}
REQUIREMENTS_NOTE = ("The procedure of spfi_encrypt has no coverage section and names no requirement of the cores: the "
                     "test cases carry no requirement IDs.")

# Test cases that a test case builds on; they run before it when it is selected on its own
REQUIRES = {tc06_encryption: [tc05_configure], tc07_counters: [tc05_configure, tc06_encryption],
            tc19_pipeline_reset: [tc01_link, tc05_configure], tc22_reset_during_traffic: [tc01_link, tc05_configure],
            tc23_fresh_after_reset: [tc01_link, tc05_configure],
            tc18_repeated_run: [tc01_link, tc05_configure]}


def select_tests(names: list[str] | None = None) -> list:
    """The test cases of the given names (all without names) with the test cases they build on, in order."""
    return systest.select_tests(names, TESTS, REQUIRES)


def make_context(seed: int, packets: int, setup: Setup = Setup(), system: System | None = None,
                 clear_bw_flags: bool = False) -> Context:
    """clear_bw_flags: that of the profile (Profile)."""
    system = system or System()
    systest.check_apids(system, setup)
    packet_lengths(system, packets, random.Random(seed))
    return Context(system=system, setup=setup, seed=seed, packets=packets, clear_bw_flags=clear_bw_flags,
                   **systest.key_material(random.Random(seed), len(system.channels)))


def run(transport, ctx: Context, tests: list | None = None,
        registers: type[Registers] = Registers) -> list[tuple[str, str | None]]:
    """Runs the test cases in order (all by default) and stops at the first failure; returns the name and the error
    (None when it passed) of every test case that ran."""
    return systest.run(transport, ctx, tests or TESTS, registers)


def main() -> int:
    parser = systest.arguments(__doc__.splitlines()[0], model=True)
    parser.add_argument("--profile", choices=PROFILES, default="lab", help="size of the run (default lab)")
    args = parser.parse_args()
    if args.check_environment:
        return systest.check_environment(args.target or "device", compressor=False, cipher=True)
    if args.target:
        parser.error("--target goes with --check-environment")
    return systest.pytest_main(SYSTEM_DIR, args)


if __name__ == "__main__":
    sys.exit(main())
