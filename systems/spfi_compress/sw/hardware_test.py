# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Hardware test of spfi_compress: RMAP over virtual channel 0, Space Packets over virtual channel 1.

The far end of the SpaceFibre link is a STAR-Ultra PCIe at port 1 of the device, set up as for spfi_encrypt: its
channel 0 reaches virtual channel 0 of port 1 through the routing switch of the device with the path address 3 in
front of every command and the reply address 1 in front of every reply; its channel 1 is virtual channel 1.

The test sends test images as Space Packets of the input APIDs: the samples of an image in the input order of the
compressor of its channel, every sample in ceil(D/8) bytes, most significant byte first, in Service Data Units of
whole samples; the last SDU of an image carries the end mark of the channel as the User Application value of its
CPTP packet. Every channel compresses its images with OpenCompact, its chunker cuts every compressed image into
chunks of a fixed size (the last chunk of an image is shorter or of the same size), and the Space Packet node sends
every chunk as one Space Packet with the APID of the send port of the channel. Every image starts in a new Space
Packet; the Space Packets carry no mark of the end of an image. The test computes the expected compressed images with
the reference model of OpenCompact from the generics of the compressor of each channel (sw/refsys/ocp.py), the
images, counters and errors of every channel with the model of an OpenCompact channel (ocp.Channel), and compares
the Space Packets of every channel one by one with the expected images cut into chunks, the aborted images included
(compress_aborted of the model of OpenCompact).

The register banks come from the header that the Composer exports with the top level (hdl/spfi_compress_regs.h),
the channels (receive port, compressor with its generics, chunker with its chunk size, send port with its APID) from
the Composer file of the system, the register maps from the headers of the cores. The test cases TC-RS-HW-01 to
TC-RS-HW-03, the register access and the run are those of sw/refsys/systest.py. The set-up and the procedure are
described with the board target.

TC-RS-HW-10 gives the compressors with the runtime configuration (RuntimeConfig_g) and the runtime parameters
(RuntimeParams_g) the image dimensions, error limit values and parameters of every image over RMAP and compares the
images with the model images of these values (sw/refsys/ocptest.py).

Every complete compressed image is also decoded with the decoder of the reference model of OpenCompact
(ocp_model.decompress) and compared with the image that was sent: equal for lossless compression, within the
maximum error of every sample for near-lossless compression. A model of OpenCompact without the decoder skips this
check and says so.

Before the first test case the script discards the packets that an earlier run left on both channels (drain).

Usage: python systems/spfi_compress/sw/hardware_test.py --device <STAR-Ultra device name> [--seed N] [--profile P]
       [--test TC-RS-HW-nn ...] [--records DIR]
       python systems/spfi_compress/sw/hardware_test.py --model [--profile P] ...   (against the system model)
       python systems/spfi_compress/sw/hardware_test.py --check-environment
Exit code 0 when every test case passes (every item is there).
"""

import copy
import hashlib
import itertools
import random
import re
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parents[1]
ROOT = SYSTEM_DIR.parents[1]
sys.path.insert(0, str(ROOT / "sw"))

from refsys import ecctest, errorstate, ocp, ocptest, rmaptest, spptest, systest  # noqa: E402
from refsys.composer import SystemFile, instance_of  # noqa: E402
from refsys.regs import RegisterMap  # noqa: E402
from refsys.spp import PRIMARY_HEADER_BYTES, SEQ_UNSEGMENTED, SpacePacket, cptp_decode, cptp_encode  # noqa: E402
from refsys.systest import (  # noqa: E402, F401
    OFB, OMAP, SPP, Registers, Setup, TestFailed, check, consecutive, drain, hex_words, tc11_port_registers, test_name)
from refsys.transport import FAR_END_LINK_RESET  # noqa: E402

OCP = RegisterMap(systest.CORES / "opencompact" / "sw" / "ocp_regs.h", "OCP")
SYSTEM_FILE = SYSTEM_DIR / "spfi_compress.composer.json"
BANKS_HEADER = SYSTEM_DIR / "hdl" / "spfi_compress_regs.h"
COMPRESSOR = "opencompact/ocp_channel"
CHUNKER = "olo/olo_base_chunker"

# The receive port of the Space Packet node delivers the User Application value of the CPTP packet in bits 21:14 of
# its user signal and the Data Loss Indicator in bit 12 (OpenSPP user guide 4.2)
USER_APPLICATION_SHIFT = 14
USER_APPLICATION_MASK = 0xFF << USER_APPLICATION_SHIFT
DATA_LOSS = 1 << 12

# The configuration registers of the status interface of a compressor, read with one command in TC-RS-HW-04
OCP_CONFIG = ("ID", "GENERICS", "IMAGE_X", "IMAGE_Y", "IMAGE_Z", "SAMPLES", "ORDER", "CODER", "PREDICTOR", "WEIGHTS",
              "ADAPTIVE", "BLOCK")
# The fields of WIN_CTRL of the RMAP target that TC-RS-HW-04 writes: all but ENABLE (OpenRMAP register map)
WIN_CTRL_WRITTEN = (OMAP.field("WIN_CTRL", "LAST_EXT", 0xFF) | OMAP.field("WIN_CTRL", "BASE_EXT", 0xFF)
                    | sum(OMAP.field("WIN_CTRL", f, 1) for f in ("READ", "WRITE", "VERIFIED_ONLY", "RMW", "SINGLE")))
WIN_ENABLE = OMAP.field("WIN_CTRL", "ENABLE", 1)

# Assumed lowest rate of the test equipment, for the time-outs of the exchanges of images (bytes per second)
EQUIPMENT_RATE = 20e6


@dataclass(frozen=True)
class Profile:
    """Size of a test run: TC-RS-HW-06 sends one image of every kind of kinds per channel, TC-RS-HW-09 stream images
    per channel at once; TC-RS-HW-08 runs both cases of lost SDUs on every channel, or with split_lost case (a) on
    the channels of even and case (b) on those of odd number; TC-RS-HW-10 sends the full image plan of every channel
    with the runtime configuration and one image of every other channel, or without runtime_full three images of the
    bounds (refsys.ocptest.image_plan). The test cases that check the data path after a provoked event
    (traffic_round) send one image per channel, or with small only one image of at most small samples (NX, NY, NZ)
    on each channel with the runtime configuration. With clear_bw_flags TC-RS-HW-01 clears VC_BW_OVER and VC_BW_UNDER
    of the SpaceFibre port before their first read (BW_FLAGS). The hardware test runs the lab profile, the system
    simulation the sim profile."""

    name: str
    kinds: tuple[str, ...]
    stream: int
    split_lost: bool = False
    runtime_full: bool = True
    small: tuple[int, int, int] | None = None
    clear_bw_flags: bool = False


PROFILES = {p.name: p for p in (Profile("lab", ocp.KINDS, 8),
                                Profile("sim", ("scene",), 2, split_lost=True, runtime_full=False,
                                        small=(64, 16, 8), clear_bw_flags=True))}

# Known issue of OpenFibre v0.1.4 in simulation: VC_BW_OVER and VC_BW_UNDER hold 'U' until the status of the core has
# crossed into the management clock domain, which happens only after the lane clock of the simulated transceiver
# starts; a read puts 'U' into the reply and its CRC. Writing ones clears both flags (write-one-to-clear). On hardware
# the flags start at 0, and the lab profile reads them as they are.
BW_FLAGS = ("VC_BW_OVER", "VC_BW_UNDER")


@dataclass
class Channel:
    """One processing channel: receive port of the Space Packet node, OpenCompact channel (compressor) with its
    generics, the model configuration and the marks of its SDUs, chunker with the bytes of a chunk, send port with
    its APID."""

    port: int
    compressor: str
    generics: dict
    cfg: dict
    marks: ocp.UserMarks
    chunker: str
    chunk_bytes: int
    output_apid: int

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


def make_channel(port: int, compressor: str, generics: dict, output: dict) -> Channel:
    """A channel from the generics of its compressor and its output part: chunker, chunk_bytes, output_apid."""
    cfg = ocp.model_config(generics)
    if cfg["periodic_updating_flag"] and cfg["quantizer_fidelity_control_method"]:
        raise ValueError(f"{compressor}: periodic error limit updating needs the error limit stream, which the system "
                         "does not drive")
    if output["chunk_bytes"] % cfg["large_b"]:
        raise ValueError(f"{compressor}: a chunk of {output['chunk_bytes']} bytes is not a whole number of output "
                         f"words of {cfg['large_b']} bytes")
    marks = ocp.UserMarks(last_mask=logic_vector(generics["UserLastMask_g"]),
                          last_value=logic_vector(generics["UserLastValue_g"]),
                          error_mask=logic_vector(generics["UserErrorMask_g"]))
    if marks.last_mask != USER_APPLICATION_MASK:
        raise ValueError(f"{compressor}: the end mark is not the User Application value (UserLastMask_g)")
    return Channel(port=port, compressor=compressor, generics=generics, cfg=cfg, marks=marks, **output)


def with_dims(generics: dict, dims: tuple[int, int, int]) -> dict:
    """The generics of a compressor with other image dimensions (NX, NY, NZ); in BIP order the sub-frame depth
    follows the number of bands."""
    bip = generics["EncodingOrder_g"] == "BI" and generics["SubFrameDepth_g"] == generics["ZSize_g"]
    out = generics | {"XSize_g": dims[0], "YSize_g": dims[1], "ZSize_g": dims[2]}
    if bip:
        out["SubFrameDepth_g"] = dims[2]
    return out


def chunks_of(data: bytes, chunk_bytes: int) -> list[bytes]:
    """The chunks that the chunker makes of one compressed image, in order: chunk_bytes bytes each, the last one the
    rest (1 to chunk_bytes bytes)."""
    if not data:
        raise ValueError("a compressed image has at least one byte")
    return [data[k:k + chunk_bytes] for k in range(0, len(data), chunk_bytes)]


class System(systest.System):
    """What the test needs to know about spfi_compress, from its Composer file. dims: images of other dimensions
    than those of the Composer file, every compressor with its configuration otherwise; chunk_bytes: chunks of another
    size than those of the chunkers (both for the tests of the procedure against the software model, which compresses
    small images in a fraction of the time, into compressed images of several chunks)."""

    def __init__(self, path: Path = SYSTEM_FILE, banks: RegisterMap | None = None,
                 dims: tuple[int, int, int] | None = None, chunk_bytes: int | None = None):
        self.dims = dims
        self.chunk_override = chunk_bytes
        super().__init__(path, banks or RegisterMap(BANKS_HEADER, "SPFI_COMPRESS"))
        self.rmap_windows = SystemFile(path).generic(self.rmap, "Windows_g")

    def channel(self, f: SystemFile, port: int) -> Channel:
        # the receive port feeds the compressor through a FIFO (Open Logic, In to Out)
        compressor = instance_of(f.connection_from(f"{self.node}.M_Rx[{port}]")["to"])
        compressors = f.instances_of(COMPRESSOR)
        while compressor not in compressors:
            compressor = instance_of(f.connection_from(f"{compressor}.Out")["to"])
        chunker = instance_of(f.connection_from(f"{compressor}.Out")["to"])
        if chunker not in f.instances_of(CHUNKER):
            raise ValueError(f"{compressor}.Out feeds {chunker}, not a chunker")
        send = f.connection_from(f"{chunker}.Out")
        if instance_of(send["to"]) != self.node:
            raise ValueError(f"{chunker}.Out feeds {send['to']}, not a send port of {self.node}")
        generics = f.generics(compressor)
        if self.dims:
            generics = with_dims(generics, self.dims)
        chunk_bytes = self.chunk_override or f.generic(chunker, "ChunkBeats_g") * f.generic(chunker, "Width_g") // 8
        return make_channel(port, compressor, generics, {"chunker": chunker, "chunk_bytes": chunk_bytes,
                                                         "output_apid": send["sideband"]["apid"]})

    def id_offsets(self) -> dict[str, int]:
        return super().id_offsets() | {c.compressor: OCP.ID for c in self.channels}

    def error_registers(self) -> list:
        """With the memory error counters of every compressor (a write clears them). Its image and SDU error counters
        count the errors of the input on purpose (TC-RS-HW-08); every test case with images checks them against the
        model (check_status)."""
        return super().error_registers() + [
            errorstate.Register(f"{c.compressor}.{name}", self.base(c.compressor) + getattr(OCP, name))
            for c in self.channels for name in ocp.ECC_COUNTERS]


@dataclass
class Traffic:
    """What the system has taken and sent since its counters were cleared."""

    sdus: int = 0
    losses: int = 0
    packets: int = 0


@dataclass
class Context(systest.Context):
    profile: Profile
    sdu_bytes: int
    window_words: list[int]                                           # TC-RS-HW-04: WIN_CTRL, WIN_BASE, WIN_LAST
    models: dict[int, ocp.Channel] = field(default_factory=dict)      # port: model of the OpenCompact channel
    cleared: dict[int, ocp.Counts] = field(default_factory=dict)      # port: model counts when the counters cleared
    input_count: dict[int, int] = field(default_factory=dict)         # APID: next Packet Sequence Count
    output_count: dict[int, int] = field(default_factory=dict)        # APID: expected next Packet Sequence Count
    pending_loss: set[int] = field(default_factory=set)               # APIDs whose last SDU was left out
    traffic: Traffic = field(default_factory=Traffic)
    images: int = 0
    rate: str = ""
    decoded: int = 0                                                  # images decoded with the model decoder
    undecoded: int = 0                                                # complete images without it (no decoder)
    image_records: list[dict] = field(default_factory=list)          # every compressed image received


@dataclass(frozen=True)
class Sdu:
    """An SDU of an image for channel port: lost SDUs are left out by the sender, which skips their Packet Sequence
    Count, so that the Space Packet node sets the Data Loss Indicator of the next SDU of the APID; before an SDU with
    gap the sender skips one Packet Sequence Count and sends it (an APID without the continuity check)."""

    port: int
    data: bytes
    end: bool
    lost: bool = False
    gap: bool = False


def image_samples(ctx: Context, ch: Channel, kind: str, index: int) -> list[int]:
    """The samples of test image `index` of a channel in the input order of its compressor."""
    return ocp.input_order(ch.cfg, ocp.test_image(ch.cfg, kind, f"{ctx.seed} {ch.port} {index}"))


def image_sdus(ctx: Context, ch: Channel, samples: list[int], lost: tuple[int, ...] = ()) -> list[Sdu]:
    """The SDUs of an image; the SDUs at the indices `lost` (from 0) are left out."""
    parts = ocp.split_sdus(ch.cfg, ocp.encode_samples(ch.cfg, samples), ctx.sdu_bytes)
    return [Sdu(ch.port, data, end=k == len(parts) - 1, lost=k in lost) for k, data in enumerate(parts)]


def expected_image(ch: Channel, output: ocp.Output) -> bytes:
    """The compressed image of an output of the channel: that of the model for a complete image, for an aborted one
    that of the samples taken (ocp.aborted_image). The Space Packets carry no mark of the end of an image, so the test
    needs the length of every image, also of an aborted one."""
    cfg = output.cfg
    if output.complete:
        return ocp.compress(cfg, ocp.from_input_order(cfg, list(output.samples)))
    data = ocp.aborted_image(cfg, list(output.samples))
    if data is None:
        raise TestFailed("the model of OpenCompact gives no aborted image (compress_aborted): the test cannot tell "
                         "where an aborted image ends")
    return data


def send_images(regs: Registers, ctx: Context, streams: dict[int, list[Sdu]]) -> dict[int, list[bytes]]:
    """Sends the SDUs of every channel, the channels interleaved SDU by SDU, and returns per channel the compressed
    images that it sent back; checks every Space Packet against the model of the channel."""
    s, setup = ctx.system, ctx.setup
    order = [sdu for group in itertools.zip_longest(*(streams[p] for p in sorted(streams))) for sdu in group if sdu]
    packets, outputs = [], {p: [] for p in streams}
    for sdu in order:
        ch = s.channels[sdu.port]
        apid = setup.input_apid_base + ch.port
        count = (ctx.input_count.get(apid, 0) + sdu.gap) & 0x3FFF
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
    expected = sum(len(chunks_of(data, s.channels[p].chunk_bytes)) for p, outs in images.items() for _, data in outs)
    size = sum(len(p) for p in packets) + sum(len(data) for outs in images.values() for _, data in outs)
    # the channels work in parallel, each image after the other: a system simulation waits for the slowest channel
    busy = getattr(regs.transport, "expect_busy", None)
    if busy is not None:
        busy(max((sum(ocp.image_cycles(o.cfg) for o, _ in outs) for outs in images.values()), default=0))
    received = regs.transport.exchange(setup.spp_channel, packets, setup.spp_channel, expected,
                                       timeout=10.0 + size / EQUIPMENT_RATE)
    return check_images(ctx, received, images)


def check_images(ctx: Context, received: list[bytes], images: dict[int, list]) -> dict[int, list[bytes]]:
    """Checks the Space Packets of every channel (APID, Packet Sequence Count, length; the fields of the Packet
    Primary Header that the send port sets: telemetry, no secondary header, Sequence Flags '11'; the User Application
    value 0 of destination 0 in the CPTP packet) and every compressed image, put together from its chunks, against
    its expected bytes."""
    s, setup = ctx.system, ctx.setup
    by_apid: dict[int, list[SpacePacket]] = {c.output_apid: [] for c in s.channels}
    for raw in received:
        user_application, payload = cptp_decode(raw, setup.far_end_tla)
        packet = SpacePacket.decode(payload)
        check(packet.apid in by_apid, f"output packet with APID 0x{packet.apid:03X}")
        header = (packet.telecommand, packet.secondary_header, packet.sequence_flags, user_application)
        check(header == (False, False, SEQ_UNSEGMENTED, 0),
              f"APID {packet.apid}: Space Packet {packet.sequence_count} with type, secondary header flag, Sequence "
              f"Flags and User Application {header}, expected telemetry, 0, '11' and 0")
        by_apid[packet.apid].append(packet)
    ctx.traffic.packets += len(received)
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
        wanted = [chunks_of(want, ch.chunk_bytes) for _, want in expected]
        check(len(out) == sum(len(w) for w in wanted),
              f"APID {apid}: {len(out)} Space Packets, expected {sum(len(w) for w in wanted)}")
        back, k = [], 0
        for n, ((output, want), parts) in enumerate(zip(expected, wanted)):
            got = out[k:k + len(parts)]
            k += len(parts)
            data = b"".join(p.data for p in got)
            problems = ocp.header_problems(data, output.cfg)
            if not problems and data != want:
                first_byte = next((i for i, (a, b) in enumerate(zip(data, want)) if a != b), min(len(data),
                                                                                                len(want)))
                problems = [f"{len(data)} bytes, expected {len(want)}, first difference at byte {first_byte}"]
            lengths, want_lengths = [len(p.data) for p in got], [len(p) for p in parts]
            if not problems and lengths != want_lengths:
                problems = [f"Space Packets of {lengths} bytes, expected {want_lengths}"]
            decoded = "aborted, not decoded"
            if not problems and output.complete:
                if ocp.decoder_available():
                    problems = ocp.decode_problems(output.cfg, data, list(output.samples))
                    ctx.decoded += 1
                    lossless = output.cfg["quantizer_fidelity_control_method"] == 0
                    decoded = "equal" if lossless else "within the maximum error"
                else:
                    ctx.undecoded += 1
                    decoded = "skipped, no decoder"
            ctx.image_records.append(image_record(ctx, ch, output, data, "failed" if problems else decoded))
            if problems:
                ctx.image_records[-1]["data"] = data.hex()
            check(not problems, f"APID {apid}: compressed image {n}: {problems[0] if problems else ''}")
            back.append(data)
        result[port] = back
    return result


def image_record(ctx: Context, ch: Channel, output: ocp.Output, data: bytes, decoded: str) -> dict:
    """The record of a compressed image received: test case, channel, dimensions, length, SHA-256, whether it is
    complete and the result of the decoding check (the bytes only when a check failed)."""
    cfg = output.cfg
    return {"test": ctx.current, "port": ch.port, "compressor": ch.compressor, "nx": cfg["large_n_x"],
            "ny": cfg["large_n_y"], "nz": cfg["large_n_z"], "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "complete": output.complete, "decoded": decoded}


def record_decoding(ctx: Context, decoded: int, undecoded: int) -> None:
    """Records the decoding check of the images of a test case."""
    if decoded:
        systest.record(ctx, f"model decoder: {decoded} compressed images decode to the images sent (lossless: equal; "
                            "near-lossless: within the maximum error)")
    if undecoded:
        systest.record(ctx, f"model decoder: not in the pinned OpenCompact, decoding check skipped for {undecoded} "
                            "images")


def clear_status(regs: Registers, ctx: Context) -> None:
    """Clears the packet and loss counters of the Space Packet node and the image and SDU counters of every
    channel."""
    s = ctx.system
    node = s.base(s.node)
    addresses = [node + SPP.RX_PACKETS, node + SPP.TX_PACKETS, node + SPP.RX_LOSS]
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
    check(regs.read(node + SPP.TX_PACKETS) == t.packets, f"TX_PACKETS of the Space Packet node, expected {t.packets}")
    check(regs.read(node + SPP.RX_LOSS) == t.losses, f"RX_LOSS of the Space Packet node, expected {t.losses}")
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


def tc01_link(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-01: the link is up with all lanes, the SpaceFibre port answers over RMAP, the lanes are recorded
    (sw/refsys/systest.py); then the baseline of the error and memory error registers of every block: recorded,
    cleared and read again as 0 (refsys.errorstate). In a profile with clear_bw_flags VC_BW_OVER and VC_BW_UNDER are
    cleared before the baseline reads them (BW_FLAGS)."""
    systest.tc01_link(regs, ctx)
    if ctx.profile.clear_bw_flags:
        base = ctx.system.base(ctx.system.port)
        for name in BW_FLAGS:
            regs.write(base + getattr(OFB, name), 0xFFFFFFFF)
    systest.baseline(regs, ctx)


def tc02_identification(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-02: every register window answers with the identification of its block; the SpaceFibre port, the
    RMAP target and the Space Packet node report the generics of the Composer file, the SpaceFibre port the
    initialised link with all lanes; the status interface of every compressor reports the configuration of its
    generics."""
    systest.tc02_identification(regs, ctx)
    systest.check_generics(regs, ctx)
    systest.check_link_state(regs, ctx)
    for ch in ctx.system.channels:
        base = ctx.system.base(ch.compressor)
        for name, value in ocp.mib_words(OCP, ch.generics).items():
            word = regs.read(base + getattr(OCP, name))
            check(word == value, f"{ch.compressor} {name}: 0x{word:08X}, expected 0x{value:08X}")


def tc03_rmap(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-03: verified write and read back; a command with a wrong key is rejected (sw/refsys/systest.py). The
    RMAP target counts the command with the wrong key (TGT_CNT_ERR2.KEY 1, EVENTS.TGT_ERROR) and no other error;
    the error registers are cleared."""
    systest.tc03_rmap(regs, ctx)
    rmap = ctx.system.rmap
    systest.caused(regs, ctx, "TC-RS-HW-03", expected={
        f"{rmap}.TGT_CNT_ERR2": OMAP.field("TGT_CNT_ERR2", "KEY", 1),
        f"{rmap}.EVENTS": OMAP.field("EVENTS", "TGT_ERROR", 1)})


def tc04_multi_word(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-04: commands of several words: a read of the configuration registers of a compressor gives the words
    of single reads; the words of a write arrive in their registers: WIN_CTRL, WIN_BASE and WIN_LAST of the last
    window of the RMAP target, which stays disabled and is restored afterwards."""
    s = ctx.system
    ch = s.channels[0]
    base = s.base(ch.compressor)
    single = [regs.read(base + getattr(OCP, name)) for name in OCP_CONFIG]
    block = regs.read_block(base + consecutive(OCP, OCP_CONFIG), len(OCP_CONFIG))
    check(block == single, f"read of {4 * len(block)} bytes at {ch.compressor}.ID gives {hex_words(block)}, single "
          f"reads {hex_words(single)}")
    window = s.rmap_windows - 1
    address = s.base(s.rmap) + OMAP.WIN_CTRL(window)
    check([OMAP.WIN_BASE(window), OMAP.WIN_LAST(window)] == [OMAP.WIN_CTRL(window) + 4, OMAP.WIN_CTRL(window) + 8],
          "WIN_CTRL, WIN_BASE and WIN_LAST of a window are not consecutive")
    before = regs.read_block(address, 3)
    check(not before[0] & WIN_ENABLE, f"window {window} of {s.rmap} is enabled: the test does not change it")
    regs.write_block(address, ctx.window_words)
    single = [regs.read(address + 4 * k) for k in range(3)]
    check(single == ctx.window_words, f"write of 12 bytes at {s.rmap}.WIN_CTRL({window}): {hex_words(single)}, "
          f"expected {hex_words(ctx.window_words)}")
    regs.write_block(address, before)
    check(regs.read_block(address, 3) == before, f"window {window} of {s.rmap} is not restored")


def tc05_configure(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-05: input APID i goes to receive port i with the continuity check of its Packet Sequence Count; the
    counters are cleared."""
    systest.route_inputs(regs, ctx, continuity=True)
    clear_status(regs, ctx)


def tc06_compression(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-06: every channel compresses one test image of every kind of the profile; every compressed image
    comes back in Space Packets of its chunks and equals the image of the reference model for the generics of its
    compressor, header included."""
    streams = {}
    for ch in ctx.system.channels:
        sdus = []
        for kind in ctx.profile.kinds:
            sdus += image_sdus(ctx, ch, image_samples(ctx, ch, kind, ctx.images))
            ctx.images += 1
        streams[ch.port] = sdus
    decoded, undecoded = ctx.decoded, ctx.undecoded
    send_images(regs, ctx, streams)
    record_decoding(ctx, ctx.decoded - decoded, ctx.undecoded - undecoded)


def tc07_counters(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-07: the counters of the Space Packet node and of every compressor match the traffic of TC-RS-HW-06;
    no loss, no image error, no faulty SDU and no memory error is counted."""
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
    discards the rest up to the next end mark and compresses the following images normally. Images, Space Packets
    and counters equal the model of the channel."""
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


def tc10_runtime_config(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-10: the runtime configuration of the compressors with RuntimeConfig_g (refsys/ocptest.py). Their
    registers hold the values of the reset, and every other compressor ignores the writes of the runtime
    configuration. Before every image of its plan a channel gets the values of the image with a COMMIT, the first one
    followed by COMMITs with one value out of range, which the compressor refuses; every compressed image equals the
    model image of its values, header included, and the compressor shows the values it took. In the lab profile every
    other channel compresses one image of its generics alongside. The counters match."""
    runtime, fixed = ocptest.split_channels(ctx.system)
    check(bool(runtime), "no compressor has the runtime configuration (RuntimeConfig_g)")
    clear_status(regs, ctx)
    for ch in runtime:
        ocptest.check_reset(regs, ctx, ch)
    for ch in fixed:
        ocptest.check_fixed(regs, ctx, ch)
    plans = {ch.port: ocptest.image_plan(ctx, ch) for ch in runtime}
    decoded, undecoded = ctx.decoded, ctx.undecoded
    for k in range(max(len(plan) for plan in plans.values())):
        streams = {}
        for ch in (ch for ch in runtime if k < len(plans[ch.port])):
            ocptest.commit(regs, ctx, ch, plans[ch.port][k])
            if k == 0:
                ocptest.refuse(regs, ctx, ch, plans[ch.port][k])
            cfg = ctx.models[ch.port].next_cfg
            kind = ctx.profile.kinds[k % len(ctx.profile.kinds)]
            samples = ocp.input_order(cfg, ocp.test_image(cfg, kind, f"{ctx.seed} {ch.port} {ctx.images}"))
            streams[ch.port] = image_sdus(ctx, ch, samples)
            ctx.images += 1
        if k == 0 and ctx.profile.runtime_full:
            for ch in fixed:
                streams[ch.port] = image_sdus(ctx, ch, image_samples(ctx, ch, "scene", ctx.images))
                ctx.images += 1
        send_images(regs, ctx, streams)
        for ch in (ch for ch in runtime if k < len(plans[ch.port])):
            ocptest.check_taken(regs, ctx, ch)
    record_decoding(ctx, ctx.decoded - decoded, ctx.undecoded - undecoded)
    check_status(regs, ctx)


def traffic_round(regs: Registers, ctx: Context, gap: tuple[int, ...] = ()) -> list[Channel]:
    """One image per channel for the test cases that check the data path after a provoked event: in a profile without
    small images every channel compresses a scene image of its generics; with small images only the channels with the
    runtime configuration, each a scene image of at most Profile.small samples, committed before it, and the values of
    the generics committed after it (with the runtime parameters of the generics on a compressor that has them). gap:
    the ports whose sender skips one Packet Sequence Count before the second SDU of the image. The counters are
    cleared before and match the images after; returns the channels used."""
    clear_status(regs, ctx)
    runtime, _ = ocptest.split_channels(ctx.system)
    channels = runtime if ctx.profile.small else ctx.system.channels
    streams = {}
    for ch in channels:
        if ctx.profile.small:
            values = ocp.build_values(ch.cfg, ocp.runtime_params(ch.generics))
            nx, ny, nz = (min(a, b) for a, b in zip(ctx.profile.small, (values.nx, values.ny, values.nz)))
            ocptest.commit(regs, ctx, ch, ocp.resized(values, nx, ny, nz))
            cfg = ctx.models[ch.port].next_cfg
            samples = ocp.input_order(cfg, ocp.test_image(cfg, "scene", f"{ctx.seed} {ch.port} {ctx.images}"))
        else:
            samples = image_samples(ctx, ch, "scene", ctx.images)
        ctx.images += 1
        sdus = image_sdus(ctx, ch, samples)
        if ch.port in gap:
            check(len(sdus) > 1, f"{ch.compressor}: an image of one SDU has no second SDU for the gap")
            sdus[1] = replace(sdus[1], gap=True)
        streams[ch.port] = sdus
    send_images(regs, ctx, streams)
    if ctx.profile.small:
        for ch in channels:
            ocptest.commit(regs, ctx, ch, ocp.build_values(ch.cfg, ocp.runtime_params(ch.generics)))
    check_status(regs, ctx)
    return channels


def lane_names(ctx: Context, lanes: list[int], *names: str) -> dict[str, int]:
    """The error registers of these lanes of the SpaceFibre port that a test case may move, with any value."""
    port = ctx.system.port
    out = {}
    for lane in lanes:
        out |= {f"{port}.LANE_EVENTS({lane})": errorstate.ALL, f"{port}.LANE_TIMEOUT_COUNT({lane})": errorstate.ALL,
                f"{port}.LANE_STATUS({lane}).RXERR_COUNT": errorstate.ALL}
    return out | {f"{port}.{name}": errorstate.ALL for name in names}


def tc12_far_end_link_reset(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-12: link reset from the far end, no traffic in flight. The operator resets the link at the port of the
    test equipment (operator step); the script waits until the RMAP target answers again. The SpaceFibre port reports
    the far-end link reset (DL_ERRORS.FAR_END_LINK_RESET, DL-LR-03) and the initialised link with all lanes; the lanes
    are recorded. A round of images (traffic_round) equals the model, the counters match. Before the error registers
    are cleared: DL_ERRORS holds FAR_END_LINK_RESET only; the lane registers (events, timeouts and receiver resets,
    RXERR_COUNT), ML_MISALIGNED and DL_RETRIES of the restart may hold any value and are recorded; every other error
    register is still 0."""
    s = ctx.system
    regs.transport.operator(FAR_END_LINK_RESET)
    systest.await_rmap(regs, ctx)
    port = s.base(s.port)
    reset = OFB.field("DL_ERRORS", "FAR_END_LINK_RESET", 1)
    regs.wait(port + OFB.DL_ERRORS, reset, reset)
    systest.check_link_state(regs, ctx)
    systest.lane_records(regs, ctx, "after the far-end link reset: ")
    traffic_round(regs, ctx)
    systest.caused(regs, ctx, "TC-RS-HW-12", expected={f"{s.port}.DL_ERRORS": reset},
                   allowed=lane_names(ctx, list(range(s.lanes)), "ML_MISALIGNED", "DL_RETRIES"))


def tc13_lane_removal(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-13: lane removal and rejoin from the near end (ML-LM-12, ECSS-E-ST-50-11C 5.6.9.5), between rounds of
    images: with the link idle the last lane gets LANE_CTRL.TX_EN = RX_EN = 0 over RMAP (RMAP keeps the link on the
    other lanes); ML_STATUS shows the other lanes sending and receiving, aligned, and the lane leaves the Active
    state. A round of images (traffic_round) on these lanes equals the model. The lane gets TX_EN and RX_EN again and
    rejoins: Active, ML_STATUS with all lanes. Another round of images equals the model. Before the error registers
    are cleared: ML_MISALIGNED, DL_RETRIES and the registers of the lane may hold any value and are recorded; every
    other error register is still 0. A run that stops here may leave the lane out: program the device again."""
    s = ctx.system
    port = s.base(s.port)
    lane = s.lanes - 1
    address = port + OFB.LANE_CTRL(lane)
    ctrl = 0x62 | systest.generic_value(s.file.generic(s.port, "LaneStart_g"))
    check(regs.read(address) == ctrl, f"{s.port} LANE_CTRL({lane}) does not read its value of the reset 0x{ctrl:02X}")
    enable = OFB.field("LANE_CTRL", "TX_EN", 1) | OFB.field("LANE_CTRL", "RX_EN", 1)
    fields = (OFB.field("ML_STATUS", "DATA_SENDING", OFB.ML_STATUS_DATA_SENDING_MASK)
              | OFB.field("ML_STATUS", "DATA_RECEIVING", OFB.ML_STATUS_DATA_RECEIVING_MASK)
              | OFB.field("ML_STATUS", "ALIGN_STATE", OFB.ML_STATUS_ALIGN_STATE_MASK))

    def lanes_up(mask: int) -> int:
        return (OFB.field("ML_STATUS", "DATA_SENDING", mask) | OFB.field("ML_STATUS", "DATA_RECEIVING", mask)
                | OFB.field("ML_STATUS", "ALIGN_STATE", 2))

    all_lanes = (1 << s.lanes) - 1
    regs.write(address, ctrl & ~enable)
    regs.wait(port + OFB.ML_STATUS, fields, lanes_up(all_lanes & ~(1 << lane)))
    state = OFB.get("LANE_STATUS", "STATE", regs.read(port + OFB.LANE_STATUS(lane)))
    check(state != systest.LANE_ACTIVE, f"{s.port} lane {lane} without TX_EN and RX_EN is still Active")
    traffic_round(regs, ctx)
    regs.write(address, ctrl)
    active = OFB.field("LANE_STATUS", "STATE", OFB.LANE_STATUS_STATE_MASK)
    regs.wait(port + OFB.LANE_STATUS(lane), active, OFB.field("LANE_STATUS", "STATE", systest.LANE_ACTIVE))
    regs.wait(port + OFB.ML_STATUS, fields, lanes_up(all_lanes))
    traffic_round(regs, ctx)
    systest.lane_records(regs, ctx, "after the rejoin: ")
    systest.caused(regs, ctx, "TC-RS-HW-13", allowed=lane_names(ctx, [lane], "ML_MISALIGNED", "DL_RETRIES"))


def tc14_memory_errors(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-14: memory errors injected through ECC_INJECT of the SpaceFibre port, the RMAP target and the Space
    Packet node during rounds of images (sw/refsys/ecctest.py): single errors into every buffer that the traffic
    writes, then a round of images that equals the model, and the counters of the monitor channels; then double errors
    with an outcome that the test sees: the transmit and the receive row crossing of the SpaceFibre port, each followed
    by a round of images (the frame is sent again, the link stays up), a verified write of the RMAP target that ends
    with the status 1 and a sacrificial Space Packet that the node discards with the code 0x1B. Before each clear
    every other error register is still 0 (systest.caused)."""
    runtime, _ = ocptest.split_channels(ctx.system)
    channels = runtime if ctx.profile.small else ctx.system.channels
    ecctest.inject_singles(regs, ctx, channels[0].port)
    traffic_round(regs, ctx)
    ecctest.check_singles(regs, ctx)
    for channel in ecctest.OFB_DOUBLES:
        ecctest.inject_ofb_double(regs, ctx, channel)
        traffic_round(regs, ctx)
    systest.check_link_state(regs, ctx)
    ecctest.rmap_double(regs, ctx)
    ecctest.spp_double(regs, ctx)
    ecctest.check_doubles(regs, ctx)


def tc16_space_packet_errors(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-16: Space Packets that the Space Packet node discards before and after its APID table, each counted in
    ERR_RX1 to ERR_RX6 and shown in LAST_ERROR (sw/refsys/spptest.py), none received (RX_PACKETS) and none given to a
    compressor. Then one round of images (traffic_round) through the normal paths that the other test cases do not
    take: on the first channel of the round the Packet Sequence Count of its input APID wraps from 16383 to 0 without
    the Data Loss Indicator (RX_TABLE_ENTRY preset with LAST 16382) and the count of its output APID from 16383 to 0
    (TX_TABLE_ENTRY preset), on the last channel the input APID has no continuity check and the sender skips a count
    without the Data Loss Indicator, and the send ports take turns (CONFIG.ROUND_ROBIN). Every image equals the model;
    the tables and CONFIG are restored, the counters of the discards checked and cleared."""
    s, setup = ctx.system, ctx.setup
    node = s.base(s.node)
    clear_status(regs, ctx)
    expected = spptest.discards(regs, ctx)
    check_status(regs, ctx)
    runtime, _ = ocptest.split_channels(s)
    channels = runtime if ctx.profile.small else s.channels
    first, last = channels[0], channels[-1]
    entry = {ch.port: SPP.field("RX_TABLE_ENTRY", "ENABLE", 1) | SPP.field("RX_TABLE_ENTRY", "PORT", ch.port)
             for ch in (first, last)}
    check_on = SPP.field("RX_TABLE_ENTRY", "CHECK", 1)
    wrap = setup.input_apid_base + first.port
    spptest.write_entry(regs, node, wrap, entry[first.port] | check_on | SPP.field("RX_TABLE_ENTRY", "VALID", 1)
                        | SPP.field("RX_TABLE_ENTRY", "LAST", 16382))
    ctx.input_count[wrap] = 16383
    spptest.write_entry(regs, node, setup.input_apid_base + last.port, entry[last.port])
    tx = SPP.field("TX_TABLE_ENTRY", "ENABLE", 1) | SPP.field("TX_TABLE_ENTRY", "COUNT", 16383)
    regs.wait(node + SPP.TABLE_STATUS, SPP.field("TABLE_STATUS", "TX_BUSY", 1), 0)
    regs.write(node + SPP.TX_TABLE_APID, first.output_apid)
    regs.read(node + SPP.TX_TABLE_ENTRY)
    regs.write(node + SPP.TX_TABLE_ENTRY, tx)
    check(regs.read(node + SPP.TX_TABLE_ENTRY) == tx, f"send APID table entry of APID {first.output_apid}")
    ctx.output_count[first.output_apid] = 16383
    config = regs.read(node + SPP.CONFIG)
    check(not SPP.get("CONFIG", "ROUND_ROBIN", config), f"{s.node} CONFIG.ROUND_ROBIN is set: 0x{config:08X}")
    regs.write(node + SPP.CONFIG, config | SPP.field("CONFIG", "ROUND_ROBIN", 1))
    traffic_round(regs, ctx, gap=(last.port,))
    regs.write(node + SPP.CONFIG, config)
    for ch in (first, last):
        spptest.write_entry(regs, node, setup.input_apid_base + ch.port, entry[ch.port] | check_on)
    systest.caused(regs, ctx, "TC-RS-HW-16", expected=expected | {
        f"{s.node}.EVENTS": SPP.field("EVENTS", "RX_DISCARD", 1)})


def tc15_rmap_errors(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-15: the RMAP target against commands that it refuses, discards or treats specially: the reply, TGT_LAST
    and the counters of every case, window 0 restricted and restored through window 3 (sw/refsys/rmaptest.py). The
    decoder of spfi_compress has 8 ports, so every address reaches a register window and the map repeats every
    0x80000 bytes: a read at 0x80000 returns the identification of spp0."""
    rmaptest.tc15_rmap_errors(regs, ctx, ctx.system.unmapped_address())


def tc17_end_of_run(regs: Registers, ctx: Context) -> None:
    """TC-RS-HW-17: end of the run: every error and memory error register of the SpaceFibre port, the RMAP target,
    the Space Packet node, the ECC monitor and the compressors still reads 0 since the baseline of TC-RS-HW-01 (the
    test cases that provoke errors clear what they caused), the leaky RXERR_COUNT of every lane at most its baseline;
    the values, the lanes and the rate of TC-RS-HW-09 are recorded."""
    if ctx.rate:
        systest.record(ctx, f"TC-RS-HW-09: {ctx.rate}")
    systest.end_of_run(regs, ctx)


TESTS = [tc01_link, tc02_identification, tc03_rmap, tc04_multi_word, tc05_configure, tc06_compression,
         tc07_counters, tc08_lost_data, tc09_rate, tc10_runtime_config, tc11_port_registers,
         tc12_far_end_link_reset, tc14_memory_errors, tc15_rmap_errors, tc16_space_packet_errors, tc13_lane_removal,
         tc17_end_of_run]

# Test cases that a test case builds on; they run before it when it is selected on its own. The checks of the error
# registers build on the baseline of TC-RS-HW-01
REQUIRES = {tc03_rmap: [tc01_link], tc06_compression: [tc05_configure],
            tc07_counters: [tc05_configure, tc06_compression], tc08_lost_data: [tc05_configure],
            tc09_rate: [tc05_configure], tc10_runtime_config: [tc05_configure],
            tc12_far_end_link_reset: [tc01_link, tc05_configure], tc13_lane_removal: [tc01_link, tc05_configure],
            tc14_memory_errors: [tc01_link, tc05_configure],
            tc15_rmap_errors: [tc01_link], tc16_space_packet_errors: [tc01_link, tc05_configure],
            tc17_end_of_run: [tc01_link]}


def select_tests(names: list[str] | None = None) -> list:
    """The test cases of the given names (all without names) with the test cases they build on, in order."""
    return systest.select_tests(names, TESTS, REQUIRES)


def window_words(rng: random.Random) -> list[int]:
    """WIN_CTRL (every field but ENABLE), WIN_BASE and WIN_LAST of the write of TC-RS-HW-04."""
    return [rng.getrandbits(32) & WIN_CTRL_WRITTEN, rng.getrandbits(32), rng.getrandbits(32)]


def make_context(seed: int, profile: Profile, setup: Setup = Setup(), system: System | None = None,
                 sdu_bytes: int | None = None) -> Context:
    """sdu_bytes: the longest SDU (default: the longest Packet Data Field that the receive buffer holds)."""
    system = system or System()
    systest.check_apids(system, setup)
    longest = system.rx_buffer_bytes - PRIMARY_HEADER_BYTES
    ctx = Context(system=system, setup=setup, seed=seed, profile=profile, sdu_bytes=min(sdu_bytes or longest, longest),
                  window_words=window_words(random.Random(seed)))
    ctx.models = {c.port: ocp.Channel(c.cfg, c.marks, ocp.runtime_config(c.generics)) for c in system.channels}
    ctx.cleared = {port: ocp.Counts() for port in ctx.models}
    return ctx


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
        return systest.check_environment()
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    if args.model:
        # the module as the system model imports it (this script runs as __main__), so that both share its classes
        import hardware_test as ht
        import system_model

        ctx = ht.make_context(args.seed, ht.PROFILES[args.profile])
        results = ht.run(system_model.SystemModel(ht.Setup(), system=ctx.system), ctx, ht.select_tests(args.test))
        code = 0 if all(error is None for _, error in results) and len(results) == len(ht.select_tests(args.test)) \
            else 1
        target = "system model"
    else:
        ctx = make_context(args.seed, PROFILES[args.profile])
        results = []
        code = systest.run_on_device(args.device, ctx, select_tests(args.test), results_out=results)
        target = "device"
    if args.records:
        systest.write_records(args.records, "spfi_compress", target, args.profile, ctx, results, started,
                              {"images": ctx.image_records, "decoded": ctx.decoded, "undecoded": ctx.undecoded,
                               "rate": ctx.rate})
    return code


if __name__ == "__main__":
    sys.exit(main())
