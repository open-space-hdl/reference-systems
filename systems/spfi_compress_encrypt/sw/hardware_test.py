# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Hardware test of spfi_compress_encrypt: RMAP over virtual channel 0, Space Packets over virtual channel 1.

The far end of the SpaceFibre link is a STAR-Ultra PCIe at port 1 of the device, set up as for spfi_encrypt: its
channel 0 reaches virtual channel 0 of port 1 through the routing switch of the device with the path address 3 in
front of every command and the reply address 1 in front of every reply; its channel 1 is virtual channel 1.

The test sends test images as Space Packets of the input APIDs: the samples of an image in the input order of the
compressor of its channel, every sample in ceil(D/8) bytes, most significant byte first, in Service Data Units of
whole samples; the last SDU of an image carries the end mark of the channel as the User Application value of its
CPTP packet. Every channel compresses its images with OpenCompact and sends every compressed image as authenticated
chunks of OpenCrypto with the APID of its send port. The test computes the expected compressed images with the
reference model of OpenCompact from the generics of the compressor of each channel (sw/refsys/ocp.py) and the
images, counters and errors of every channel with the model of an OpenCompact channel (ocp.Channel).

The register banks come from the header that the Composer exports with the top level
(hdl/spfi_compress_encrypt_regs.h), the channels (receive port, compressor with its generics, framer, AES-GCM core,
send port with its APID) and the key slots from the Composer file of the system, the register maps from the headers
of the cores. The test cases TC-RS-HW-01 to TC-RS-HW-04, the register access and the run are those of
sw/refsys/systest.py. The set-up and the procedure are described with the board target.

Before the first test case the script discards the packets that an earlier run left on both channels (drain).

Usage: python systems/spfi_compress_encrypt/sw/hardware_test.py --device <STAR-Ultra device name> [--seed N]
       [--profile P] [--test TC-RS-HW-nn ...]
Exit code 0 when every test case passes.
"""

import copy
import itertools
import random
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parents[1]
ROOT = SYSTEM_DIR.parents[1]
sys.path.insert(0, str(ROOT / "sw"))

from refsys import chunks, ocp, systest  # noqa: E402
from refsys.composer import SystemFile, instance_of  # noqa: E402
from refsys.regs import RegisterMap  # noqa: E402
from refsys.spp import PRIMARY_HEADER_BYTES, SpacePacket, cptp_decode, cptp_encode  # noqa: E402
from refsys.systest import (  # noqa: E402, F401
    DRAIN_TIMEOUT, OCR, SPP, Registers, Setup, TestFailed, check, drain, tc01_link, tc03_rmap, tc04_multi_word,
    test_name)

OCP = RegisterMap(systest.CORES / "opencompact" / "sw" / "ocp_regs.h", "OCP")
SYSTEM_FILE = SYSTEM_DIR / "spfi_compress_encrypt.composer.json"
BANKS_HEADER = SYSTEM_DIR / "hdl" / "spfi_compress_encrypt_regs.h"
COMPRESSOR = "opencompact/ocp_channel"

# The receive port of the Space Packet node delivers the User Application value of the CPTP packet in bits 21:14 of
# its user signal and the Data Loss Indicator in bit 12 (OpenSPP user guide 4.2)
USER_APPLICATION_SHIFT = 14
USER_APPLICATION_MASK = 0xFF << USER_APPLICATION_SHIFT
DATA_LOSS = 1 << 12

# Assumed lowest rate of the test equipment, for the time-outs of the exchanges of images (bytes per second)
EQUIPMENT_RATE = 20e6


@dataclass(frozen=True)
class Profile:
    """Size of a test run: TC-RS-HW-06 sends one image of every kind of kinds per channel, TC-RS-HW-09 stream images
    per channel at once; TC-RS-HW-08 runs both cases of lost SDUs on every channel, or with split_lost case (a) on
    the channels of even and case (b) on those of odd number. The hardware test runs the lab profile, the system
    simulation the sim profile."""

    name: str
    kinds: tuple[str, ...]
    stream: int
    split_lost: bool = False


PROFILES = {p.name: p for p in (Profile("lab", ocp.KINDS, 8), Profile("sim", ("scene",), 2, split_lost=True))}


@dataclass
class Channel:
    """One processing channel: receive port of the Space Packet node, OpenCompact channel (compressor) with its
    generics, the model configuration and the marks of its SDUs, framer, AES-GCM core, send port."""

    port: int
    compressor: str
    generics: dict
    cfg: dict
    marks: ocp.UserMarks
    framer: str
    core: str
    output_apid: int
    slot: int
    key_slots: int

    @property
    def end_mark(self) -> int:
        """User Application value of the last SDU of an image."""
        return self.marks.last_value >> USER_APPLICATION_SHIFT


_BIT_STRING = re.compile(r'^(\d*)([bBxX]?)"([0-9A-Fa-f_]+)"$')


def logic_vector(value) -> int:
    """The value of a std_logic_vector generic: an integer, a bit string literal with or without a length
    (22x"3FC000", x"3FC000", b"0110") or a string of bits ("0110")."""
    if isinstance(value, int):
        return value
    text = str(value).strip()
    m = _BIT_STRING.match(text if text.endswith('"') else f'"{text}"')
    if not m:
        raise ValueError(f"std_logic_vector generic {value!r}")
    width, base, digits = m[1], m[2].lower() or "b", m[3].replace("_", "")
    number = int(digits, 16 if base == "x" else 2)
    if width and number >> int(width):
        raise ValueError(f"std_logic_vector generic {value!r} does not fit into {width} bits")
    return number


def make_channel(port: int, compressor: str, generics: dict, encryption: dict) -> Channel:
    """A channel from the generics of its compressor and its encryption part (systest.encryption)."""
    cfg = ocp.model_config(generics)
    if cfg["periodic_updating_flag"] and cfg["quantizer_fidelity_control_method"]:
        raise ValueError(f"{compressor}: periodic error limit updating needs the error limit stream, which the system "
                         "does not drive")
    marks = ocp.UserMarks(last_mask=logic_vector(generics["UserLastMask_g"]),
                          last_value=logic_vector(generics["UserLastValue_g"]),
                          error_mask=logic_vector(generics["UserErrorMask_g"]))
    if marks.last_mask != USER_APPLICATION_MASK:
        raise ValueError(f"{compressor}: the end mark is not the User Application value (UserLastMask_g)")
    return Channel(port=port, compressor=compressor, generics=generics, cfg=cfg, marks=marks, **encryption)


def with_dims(generics: dict, dims: tuple[int, int, int]) -> dict:
    """The generics of a compressor with other image dimensions (NX, NY, NZ); in BIP order the sub-frame depth
    follows the number of bands."""
    bip = generics["EncodingOrder_g"] == "BI" and generics["SubFrameDepth_g"] == generics["ZSize_g"]
    out = generics | {"XSize_g": dims[0], "YSize_g": dims[1], "ZSize_g": dims[2]}
    if bip:
        out["SubFrameDepth_g"] = dims[2]
    return out


class System(systest.System):
    """What the test needs to know about spfi_compress_encrypt, from its Composer file. dims: images of other
    dimensions than those of the Composer file, every compressor with its configuration otherwise (for the tests of
    the procedure against the software model, which compresses small images in a fraction of the time)."""

    def __init__(self, path: Path = SYSTEM_FILE, banks: RegisterMap | None = None,
                 dims: tuple[int, int, int] | None = None):
        self.dims = dims
        super().__init__(path, banks or RegisterMap(BANKS_HEADER, "SPFI_COMPRESS_ENCRYPT"))

    def channel(self, f: SystemFile, port: int) -> Channel:
        # the receive port feeds the compressor through a FIFO (Open Logic, In to Out)
        compressor = instance_of(f.connection_from(f"{self.node}.M_Rx[{port}]")["to"])
        compressors = f.instances_of(COMPRESSOR)
        while compressor not in compressors:
            compressor = instance_of(f.connection_from(f"{compressor}.Out")["to"])
        framer = instance_of(f.connection_from(f"{compressor}.Out")["to"])
        generics = f.generics(compressor)
        if self.dims:
            generics = with_dims(generics, self.dims)
        return make_channel(port, compressor, generics, systest.encryption(f, framer))

    def id_offsets(self) -> dict[str, int]:
        return super().id_offsets() | {c.compressor: OCP.ID for c in self.channels}


@dataclass
class Traffic:
    """What the system has taken and sent since its counters were cleared."""

    sdus: int = 0
    losses: int = 0
    chunks: int = 0


@dataclass
class Context(systest.Context):
    profile: Profile
    sdu_bytes: int
    models: dict[int, ocp.Channel] = field(default_factory=dict)      # port: model of the OpenCompact channel
    cleared: dict[int, ocp.Counts] = field(default_factory=dict)      # port: model counts when the counters cleared
    input_count: dict[int, int] = field(default_factory=dict)         # APID: next Packet Sequence Count
    output_count: dict[int, int] = field(default_factory=dict)        # APID: expected next Packet Sequence Count
    next_counter: dict[int, int] = field(default_factory=dict)        # port: next invocation counter of its slot
    pending_loss: set[int] = field(default_factory=set)               # APIDs whose last SDU was left out
    traffic: Traffic = field(default_factory=Traffic)
    images: int = 0
    rate: str = ""


@dataclass(frozen=True)
class Sdu:
    """An SDU of an image for channel port: lost SDUs are left out by the sender, which skips their Packet Sequence
    Count, so that the Space Packet node sets the Data Loss Indicator of the next SDU of the APID."""

    port: int
    data: bytes
    end: bool
    lost: bool = False


def image_samples(ctx: Context, ch: Channel, kind: str, index: int) -> list[int]:
    """The samples of test image `index` of a channel in the input order of its compressor."""
    return ocp.input_order(ch.cfg, ocp.test_image(ch.cfg, kind, f"{ctx.seed} {ch.port} {index}"))


def image_sdus(ctx: Context, ch: Channel, samples: list[int], lost: tuple[int, ...] = ()) -> list[Sdu]:
    """The SDUs of an image; the SDUs at the indices `lost` (from 0) are left out."""
    parts = ocp.split_sdus(ch.cfg, ocp.encode_samples(ch.cfg, samples), ctx.sdu_bytes)
    return [Sdu(ch.port, data, end=k == len(parts) - 1, lost=k in lost) for k, data in enumerate(parts)]


def expected_image(ch: Channel, output: ocp.Output) -> bytes | None:
    """The compressed image of an output of the channel: that of the model for a complete image, for an aborted one
    that of the samples taken (ocp.aborted_image), or None with a version of OpenCompact whose model does not give
    it; the bytes of the aborted image are then checked with ocp.aborted_problems."""
    if output.complete:
        return ocp.compress(ch.cfg, ocp.from_input_order(ch.cfg, list(output.samples)))
    return ocp.aborted_image(ch.cfg, list(output.samples))


def send_images(regs: Registers, ctx: Context, streams: dict[int, list[Sdu]]) -> dict[int, list[bytes]]:
    """Sends the SDUs of every channel, the channels interleaved SDU by SDU, and returns per channel the compressed
    images that it sent back; checks every chunk and every image against the model of the channel."""
    s, setup = ctx.system, ctx.setup
    order = [sdu for group in itertools.zip_longest(*(streams[p] for p in sorted(streams))) for sdu in group if sdu]
    packets, outputs = [], {p: [] for p in streams}
    for sdu in order:
        ch = s.channels[sdu.port]
        apid = setup.input_apid_base + ch.port
        count = ctx.input_count.get(apid, 0)
        ctx.input_count[apid] = (count + 1) & 0x3FFF
        if sdu.lost:
            ctx.pending_loss.add(apid)
            continue
        loss = apid in ctx.pending_loss
        ctx.pending_loss.discard(apid)
        ua = ch.end_mark if sdu.end else 0
        packet = SpacePacket(apid=apid, data=sdu.data, sequence_count=count).encode()
        packets.append(cptp_encode(packet, setup.system_tla, user_application=ua))
        user = (ua << USER_APPLICATION_SHIFT) | (DATA_LOSS if loss else 0)
        outputs[sdu.port] += ctx.models[sdu.port].sdu(sdu.data, user)
        ctx.traffic.sdus += 1
        ctx.traffic.losses += loss
    images = {p: [(o, expected_image(s.channels[p], o)) for o in outs] for p, outs in outputs.items()}
    known = sum(len(chunks.split(data, s.chunk_bytes)) for outs in images.values() for _, data in outs if data)
    aborted = sum(data is None for outs in images.values() for _, data in outs)
    size = sum(len(p) for p in packets) + s.chunk_bytes * (known + aborted)
    transport = regs.transport
    # every chunk of the images that the model gives, and at least one chunk of every other aborted image
    received = transport.exchange(setup.spp_channel, packets, setup.spp_channel, known + aborted,
                                  timeout=10.0 + size / EQUIPMENT_RATE)
    if aborted:
        # the model gives no length of these aborted images: their chunks arrive until the channel is quiet
        while (packet := transport.receive(setup.spp_channel, DRAIN_TIMEOUT)) is not None:
            received.append(packet)
    return check_images(ctx, received, images)


def check_images(ctx: Context, received: list[bytes], images: dict[int, list]) -> dict[int, list[bytes]]:
    """Checks the chunks of every channel (APID, Packet Sequence Count, authentication, fixed field and invocation
    counter of its key slot) and every compressed image against its expected bytes."""
    s, setup = ctx.system, ctx.setup
    by_apid: dict[int, list[SpacePacket]] = {c.output_apid: [] for c in s.channels}
    for raw in received:
        _, payload = cptp_decode(raw, setup.far_end_tla)
        packet = SpacePacket.decode(payload)
        check(packet.apid in by_apid, f"output packet with APID 0x{packet.apid:03X}")
        by_apid[packet.apid].append(packet)
    ctx.traffic.chunks += len(received)
    result = {}
    for port, expected in images.items():
        ch = s.channels[port]
        apid = ch.output_apid
        out = by_apid[apid]
        counts = [p.sequence_count for p in out]
        first = ctx.output_count.get(apid, 0)
        check(counts == [(first + n) & 0x3FFF for n in range(len(out))],
              f"APID {apid}: Packet Sequence Counts {counts[:8]}, expected from {first}")
        ctx.output_count[apid] = (first + len(out)) & 0x3FFF
        decrypted = []
        for p in out:
            try:
                chunk = chunks.decrypt(ctx.keys[port], p.data)
            except chunks.ChunkError as err:
                raise TestFailed(f"APID {apid}: {err}") from err
            check(chunk.fixed == ctx.fixed[port], f"APID {apid}: fixed field 0x{chunk.fixed:08X}")
            decrypted.append(chunk)
        if decrypted:
            check(min(c.counter for c in decrypted) == ctx.next_counter[port], f"APID {apid}: first counter")
            ctx.next_counter[port] += len(decrypted)
        try:
            back = chunks.reassemble(decrypted)
        except chunks.ChunkError as err:
            raise TestFailed(f"APID {apid}: {err}") from err
        check(len(back) == len(expected), f"APID {apid}: {len(back)} compressed images, expected {len(expected)}")
        for n, (data, (output, want)) in enumerate(zip(back, expected)):
            if want is None:
                problems = ocp.aborted_problems(data, ch.cfg, list(output.samples))
            else:
                problems = ocp.header_problems(data, ch.cfg)
                if not problems and data != want:
                    first_byte = next((i for i, (a, b) in enumerate(zip(data, want)) if a != b), min(len(data),
                                                                                                    len(want)))
                    problems = [f"{len(data)} bytes, expected {len(want)}, first difference at byte {first_byte}"]
            check(not problems, f"APID {apid}: compressed image {n}: {problems[0] if problems else ''}")
        result[port] = back
    return result


def clear_status(regs: Registers, ctx: Context) -> None:
    """Clears the packet, loss and encryption counters and the image and SDU counters of every channel."""
    s = ctx.system
    node = s.base(s.node)
    addresses = [node + SPP.RX_PACKETS, node + SPP.TX_PACKETS, node + SPP.RX_LOSS]
    addresses += [s.base(c.core) + OCR.ENCRYPTIONS for c in s.channels]
    addresses += [s.base(c.compressor) + getattr(OCP, name) for c in s.channels
                  for name in list(ocp.COUNTERS) + list(ocp.ECC_COUNTERS)]
    systest.clear_counters(regs, addresses)
    ctx.cleared = {port: copy.copy(model.counts) for port, model in ctx.models.items()}
    ctx.traffic = Traffic()


def check_status(regs: Registers, ctx: Context) -> None:
    """The counters match the traffic since they were cleared and the model of every channel; no error is
    counted."""
    s = ctx.system
    node = s.base(s.node)
    t = ctx.traffic
    check(regs.read(node + SPP.RX_PACKETS) == t.sdus, f"RX_PACKETS of the Space Packet node, expected {t.sdus}")
    check(regs.read(node + SPP.TX_PACKETS) == t.chunks, f"TX_PACKETS of the Space Packet node, expected {t.chunks}")
    check(regs.read(node + SPP.RX_LOSS) == t.losses, f"RX_LOSS of the Space Packet node, expected {t.losses}")
    encryptions = systest.ocr_counters(regs, ctx)
    check(encryptions == t.chunks, f"{encryptions} encryptions, {t.chunks} chunks")
    for ch in s.channels:
        base = s.base(ch.compressor)
        now, before = ctx.models[ch.port].counts, ctx.cleared[ch.port]
        for name, attr in ocp.COUNTERS.items():
            want = getattr(now, attr) - getattr(before, attr)
            got = regs.read(base + getattr(OCP, name))
            check(got == want, f"{ch.compressor} {name} = {got}, expected {want}")
        for name in ocp.ECC_COUNTERS:
            check(regs.read(base + getattr(OCP, name)) == 0, f"{ch.compressor} {name}")
    systest.ecc_counters(regs, ctx)


def tc02_identification(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-02: every register window answers with the identification of its block; the status interface of
    every compressor reports the configuration of its generics."""
    systest.tc02_identification(regs, ctx)
    for ch in ctx.system.channels:
        base = ctx.system.base(ch.compressor)
        for name, value in ocp.mib_words(OCP, ch.generics).items():
            word = regs.read(base + getattr(OCP, name))
            check(word == value, f"{ch.compressor} {name}: 0x{word:08X}, expected 0x{value:08X}")


def tc05_configure(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-05: input APID i goes to receive port i with the continuity check of its Packet Sequence Count; the
    key slot of every AES-GCM core is loaded with its own key and generated IVs; the counters are cleared."""
    systest.route_inputs(regs, ctx, continuity=True)
    systest.load_keys(regs, ctx)
    clear_status(regs, ctx)


def tc06_compression(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-06: every channel compresses one test image of every kind of the profile; every compressed image
    comes back as authenticated chunks and equals the image of the reference model for the generics of its
    compressor, header included."""
    streams = {}
    for ch in ctx.system.channels:
        sdus = []
        for kind in ctx.profile.kinds:
            sdus += image_sdus(ctx, ch, image_samples(ctx, ch, kind, ctx.images))
            ctx.images += 1
        streams[ch.port] = sdus
    send_images(regs, ctx, streams)


def tc07_counters(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-07: the counters of the Space Packet node, of the AES-GCM cores and of every compressor match the
    traffic of TC-RS-HW-06; no loss, no image error, no faulty SDU and no memory error is counted."""
    check_status(regs, ctx)


def lost_plan(ctx: Context, port: int, n: int) -> list[tuple[int, ...]]:
    """The SDUs that TC-RS-HW-08 leaves out, per image of a channel with images of n SDUs (from 0): case (a) an image
    without an SDU in its middle, then an intact image; case (b) an image without its last SDU, then two intact
    images (the first SDU of the second one carries the Data Loss Indicator and completes the damaged image)."""
    case_a, case_b = [(n // 2,), ()], [(n - 1,), (), ()]
    if ctx.profile.split_lost:
        return case_a if port % 2 == 0 else case_b
    return case_a + case_b


def tc08_lost_data(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-08: lost SDUs, both cases of lost_plan per channel (or one case per channel in a profile with
    split_lost). The Space Packet node flags the SDU after each gap; the channel ends the damaged image with an error,
    discards the rest up to the next end mark and compresses the following images normally. Images, chunks and
    counters equal the model of the channel."""
    clear_status(regs, ctx)
    streams = {}
    for ch in ctx.system.channels:
        n = len(image_sdus(ctx, ch, [0] * ocp.samples_per_image(ch.cfg)))
        check(n >= 3, f"{ch.compressor}: an image of {n} SDUs is too short for the lost SDUs of TC-RS-HW-08")
        plan = lost_plan(ctx, ch.port, n)
        sdus = []
        for lost in plan:
            sdus += image_sdus(ctx, ch, image_samples(ctx, ch, "scene", ctx.images), lost)
            ctx.images += 1
        streams[ch.port] = sdus
    send_images(regs, ctx, streams)
    check_status(regs, ctx)


def tc09_rate(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-09: all channels compress a stream of images at once, as fast as the test equipment sends them; no
    loss, every image equals the model, the counters match. The rate is recorded; it is limited by the test
    equipment."""
    clear_status(regs, ctx)
    streams, raw = {}, 0
    for ch in ctx.system.channels:
        sdus = []
        for k in range(ctx.profile.stream):
            sdus += image_sdus(ctx, ch, image_samples(ctx, ch, ocp.KINDS[k % len(ocp.KINDS)], ctx.images))
            ctx.images += 1
        streams[ch.port] = sdus
        raw += sum(len(s.data) for s in sdus)
    start = time.monotonic()
    back = send_images(regs, ctx, streams)
    seconds = time.monotonic() - start
    compressed = sum(len(d) for images in back.values() for d in images)
    images = sum(len(images) for images in back.values())
    ctx.rate = (f"{images} images, {raw} bytes of samples and {compressed} bytes compressed in {seconds:.2f} s: "
                f"{8 * raw / seconds / 1e9:.3f} Gbit/s of samples (limited by the test equipment and the host)")
    print(ctx.rate)
    check_status(regs, ctx)


TESTS = [tc01_link, tc02_identification, tc03_rmap, tc04_multi_word, tc05_configure, tc06_compression,
         tc07_counters, tc08_lost_data, tc09_rate]

# Test cases that a test case builds on; they run before it when it is selected on its own
REQUIRES = {tc06_compression: [tc05_configure], tc07_counters: [tc05_configure, tc06_compression],
            tc08_lost_data: [tc05_configure], tc09_rate: [tc05_configure]}


def select_tests(names: list[str] | None = None) -> list:
    """The test cases of the given names (all without names) with the test cases they build on, in order."""
    return systest.select_tests(names, TESTS, REQUIRES)


def make_context(seed: int, profile: Profile, setup: Setup = Setup(), system: System | None = None,
                 sdu_bytes: int | None = None) -> Context:
    """sdu_bytes: the longest SDU (default: the longest Packet Data Field that the receive buffer holds)."""
    system = system or System()
    systest.check_apids(system, setup)
    longest = system.rx_buffer_bytes - PRIMARY_HEADER_BYTES
    ctx = Context(system=system, setup=setup, seed=seed, profile=profile, sdu_bytes=min(sdu_bytes or longest, longest),
                  **systest.key_material(random.Random(seed), len(system.channels)))
    ctx.models = {c.port: ocp.Channel(c.cfg, c.marks) for c in system.channels}
    ctx.cleared = {port: ocp.Counts() for port in ctx.models}
    ctx.next_counter = {c.port: ctx.counter[c.port] for c in system.channels}
    return ctx


def run(transport, ctx: Context, tests: list | None = None,
        registers: type[Registers] = Registers) -> list[tuple[str, str | None]]:
    """Runs the test cases in order (all by default) and stops at the first failure; returns the name and the error
    (None when it passed) of every test case that ran."""
    return systest.run(transport, ctx, tests or TESTS, registers)


def main() -> int:
    parser = systest.arguments(__doc__.splitlines()[0])
    parser.add_argument("--profile", choices=PROFILES, default="lab", help="size of the run (default lab)")
    args = parser.parse_args()
    ctx = make_context(args.seed, PROFILES[args.profile])
    return systest.run_on_device(args.device, ctx, select_tests(args.test))


if __name__ == "__main__":
    sys.exit(main())
