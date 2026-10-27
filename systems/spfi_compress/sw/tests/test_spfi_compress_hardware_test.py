"""Runs the hardware test of spfi_compress against the software model of the system (system_model.py).

The procedure runs on the system of the Composer file with smaller images and chunks: every compressor keeps its
configuration, with other image dimensions, so that the model compresses the images of all test cases in seconds,
and every compressed image takes several chunks. The Composer file itself is checked against the configurations of
OpenCompact that its channels use and against the bounds that the system README states.
"""

import copy
import json
import re
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from refsys import errorstate, ocp, ocptest, rmap, systest
from refsys.composer import SystemFile, instance_of

import ocp_configs  # noqa: E402 - tools of cores/opencompact, on the path through refsys.ocp

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "tools"))

import render_records  # noqa: E402 - tools/render_records.py
ht, sm = systest.system_modules(HERE.parent, "hardware_test", "system_model")

SYSTEM_FILE = SystemFile(ht.SYSTEM_FILE)
LAB_SYSTEM = ht.System()

# The configurations of OpenCompact of the four channels, one per preset of the Composer (BIP sample-adaptive, BSQ,
# BIP block-adaptive, BIL near-lossless hybrid), and the lab images: 64 x 64 samples in 48 bands
FAMILIES = ("c01_lossless_bip", "c25_tp_bsq_lossless", "c27_tp_bip_full", "c26_tp_bil_near_abs")
LAB_DIMS = (64, 64, 48)
DIMS = (16, 8, 6)            # 768 samples, 1536 bytes: 8 SDUs of at most 200 bytes
SDU_BYTES = 200
CHUNK_BYTES = 256            # compressed images of several chunks
# The upper bound of the compressed image of every lab channel (max_image_bytes of the model of OpenCompact) and the
# Space Packets of 4096 bytes that it takes, as the system README states them
BOUNDS = [(835496, 204), (835496, 204), (399380, 98), (1303256, 319)]
# On the channels with the runtime parameters, the maximum of that bound over the ranges of the parameters
RUNTIME_BOUNDS = {"ocp0": (1179476, 288), "ocp3": (1698668, 415)}

SYSTEM = ht.System(dims=DIMS, chunk_bytes=CHUNK_BYTES)
LAB = ht.PROFILES["lab"]


def context(seed: int = 3, system=SYSTEM, profile=LAB):
    return ht.make_context(seed, profile, system=system, sdu_bytes=SDU_BYTES)


def model(system=SYSTEM, **kw):
    return sm.SystemModel(ht.Setup(), system=system, system_file=SYSTEM_FILE, **kw)


def run(transport, ctx, tests=None):
    results = ht.run(transport, ctx, tests)
    return dict(results)


def test_procedure_passes_against_the_model():
    m, ctx = model(), context()
    results = run(m, ctx)
    assert list(results) == [ht.test_name(t) for t in ht.TESTS] and not any(results.values())
    # TC-RS-HW-06, 08 and 09, and one image per channel in each round of TC-RS-HW-12, 13 (two), 14 (three) and 16
    per_channel = len(LAB.kinds) + 5 + LAB.stream + 7
    # TC-RS-HW-10: the plan of the two channels with the runtime configuration, one image of each other channel
    runtime = sum(len(ocptest.image_plan(ctx, ch)) for ch in ocptest.split_channels(SYSTEM)[0]) + 2
    assert ctx.images == 4 * per_channel + runtime and not m.pending
    assert "Gbit/s of samples (limited by the test equipment" in ctx.rate


def test_the_compressed_images_take_several_chunks_in_the_tests():
    m, ctx = model(), context()
    run(m, ctx, ht.select_tests(["TC-RS-HW-06"]))
    assert ctx.traffic.packets > 2 * 4 * len(LAB.kinds)


def test_the_compressors_have_the_configurations_of_opencompact():
    # the presets of the Composer with the image of the lab are the configurations of OpenCompact at that image size
    for ch, family in zip(LAB_SYSTEM.channels, FAMILIES):
        expected = copy.deepcopy(ocp_configs.CONFIGS[family])
        if expected["sample_encoding_order"] == 0 and expected["large_m"] == expected["large_n_z"]:
            expected["large_m"] = LAB_DIMS[2]
        expected.update(large_n_x=LAB_DIMS[0], large_n_y=LAB_DIMS[1], large_n_z=LAB_DIMS[2])
        for key in ("seed", "image"):             # of the test images of OpenCompact, not of the configuration
            expected.pop(key, None)
        assert ch.cfg == expected, family
        assert ch.generics["OutWidth_g"] == 32 and ch.cfg["large_d"] == 16 and ch.cfg["large_b"] == 4
    assert {ocp.samples_per_image(c.cfg) for c in LAB_SYSTEM.channels} == {64 * 64 * 48}


def test_the_bounds_of_the_compressed_images_and_their_space_packets():
    for ch, (bound, packets) in zip(LAB_SYSTEM.channels, BOUNDS):
        assert ocp.ocp_model.max_image_bytes(ch.cfg) == bound, ch.compressor
        assert -(-bound // ch.chunk_bytes) == packets, ch.compressor
        # every image needs several Space Packets: one compressed image is longer than a Packet Data Field
        assert bound > 65536


def test_the_bounds_over_the_runtime_parameters_are_those_of_the_throughput_budget():
    # the bound grows with Umax and as gamma* falls (more rescalings of the hybrid coder): its maximum over the ranges
    # of CO-RP-02 is at Umax 32 and gamma* 4, the bound by which the Composer rates the channel
    brief = Path(ht.SYSTEM_FILE).read_text(encoding="utf-8")       # the build brief is a string of the system file
    channels = [ch for ch in LAB_SYSTEM.channels if ocp.runtime_params(ch.generics)]
    assert [ch.compressor for ch in channels] == list(RUNTIME_BOUNDS)
    for ch in channels:
        bound, packets = RUNTIME_BOUNDS[ch.compressor]
        hi = ocp_configs.param_bounds(ch.cfg, ch.cfg["large_n_z"])
        sizes = {(u, g): ocp.ocp_model.max_image_bytes(dict(ch.cfg, large_u_max=u, gamma_star=g))
                 for u in range(hi["umax"][0], hi["umax"][1] + 1) for g in range(hi["gstar"][0], hi["gstar"][1] + 1)}
        assert max(sizes.values()) == sizes[(32, 4)] == bound, ch.compressor
        assert -(-bound // ch.chunk_bytes) == packets, ch.compressor
        assert re.search(rf"limited by `{ch.compressor}\.Out` \(System clock, 150 MHz: \d+ cycles per image of {bound} B",
                         brief), ch.compressor


def test_every_chunk_fits_the_send_buffer_twice_and_a_packet_data_field():
    f, node = SYSTEM_FILE, LAB_SYSTEM.node
    for ch in LAB_SYSTEM.channels:
        assert ch.chunk_bytes == 4096
        # the send buffer of a port holds two chunks, so that the next chunk enters while one leaves
        assert 2 * ch.chunk_bytes <= f.generic(node, "TxBufferBytes_g") and f.generic(node, "TxMaxPackets_g") >= 2
        assert ch.chunk_bytes <= 65536
        assert ch.chunk_bytes % ch.cfg["large_b"] == 0


def test_lost_sdus_give_the_counter_steps_of_the_channel_specification():
    m, ctx = model(), context()
    run(m, ctx, ht.select_tests(["TC-RS-HW-08"]))
    n = 8                                        # SDUs per image; (a) loses SDU n // 2 (from 0), (b) the last one
    j = n // 2 + 1                               # the lost SDU of (a) counted from 1
    for port in range(4):
        before, now = ctx.cleared[port], ctx.models[port].counts
        steps = {k: getattr(now, k) - getattr(before, k) for k in vars(now)}
        assert steps == {"images_done": 2, "images_aborted": 2, "sdu_errors": 2,
                         "sdus_discarded": (n - j - 1) + (n - 1)}
    assert ctx.traffic.losses == 8


def test_the_sim_profile_splits_the_cases_of_lost_sdus_over_the_channels():
    m, ctx = model(), context(profile=ht.PROFILES["sim"])
    results = run(m, ctx, ht.select_tests(["TC-RS-HW-08"]))
    assert not any(results.values()), results
    n, j = 8, 8 // 2 + 1
    for port in range(4):
        before, now = ctx.cleared[port], ctx.models[port].counts
        steps = {k: getattr(now, k) - getattr(before, k) for k in vars(now)}
        # case (a) on channels 0 and 2, case (b) on channels 1 and 3
        discarded = n - j - 1 if port % 2 == 0 else n - 1
        assert steps == {"images_done": 1, "images_aborted": 1, "sdu_errors": 1, "sdus_discarded": discarded}
    assert ctx.traffic.losses == 4


def test_the_system_facts_of_the_composer_file():
    assert [(c.port, c.compressor, c.chunker, c.chunk_bytes, c.output_apid, c.end_mark)
            for c in LAB_SYSTEM.channels] == [(k, f"ocp{k}", f"chunk{k}", 4096, k, 0x01) for k in range(4)]
    assert [LAB_SYSTEM.base(f"ocp{k}") for k in range(4)] == [0x40000 + 0x10000 * k for k in range(4)]
    assert all(LAB_SYSTEM.ids[f"ocp{k}"] == ht.OCP.REGMAP_ID for k in range(4))
    assert all(c.marks == ocp.UserMarks(0x3FC000, 0x004000, 0x003000) for c in LAB_SYSTEM.channels)
    assert [ocp.samples_per_image(c.cfg) for c in SYSTEM.channels] == [768] * 4
    assert LAB_SYSTEM.chunk_bytes is None and LAB_SYSTEM.cores() == [] and LAB_SYSTEM.rmap_windows == 4


def test_register_banks_of_the_exported_header_match_the_system():
    s, f = LAB_SYSTEM, SYSTEM_FILE
    # bases: the windows of the AXI4-Lite decoder of the Composer file
    assert s.windows == f.decoder_windows(f.instance_of_block("olo/olo_axi_lite_decoder"))
    # identifications: the register map identifications of the pinned cores, so that a new version of a register map
    # shows up here before the system is tested
    core_ids = {s.node: systest.SPP.REGMAP_ID, s.rmap: systest.OMAP.REGMAP_ID, s.port: systest.OFB.REGMAP_ID}
    core_ids |= {c.compressor: ht.OCP.REGMAP_ID for c in s.channels}
    assert s.ids == core_ids
    # channels of the ECC monitor: the compressors, then the FIFOs, as many as Channels_g
    assert s.ecc_names == [f"OCP{k}_ECC" for k in range(4)] + [f"RXF{k}_ECC" for k in range(4)]
    assert s.ecc_channels == f.generic(s.ecc, "Channels_g") == 8


def test_a_fifo_of_two_space_packets_feeds_every_compressor():
    # the receive buffer of spp0 is shared by all receive ports and delivers in order: without the FIFO, the next
    # Space Packet of any channel waits until the compressor of the previous one has taken it
    f = SYSTEM_FILE
    for k in range(4):
        fifo = instance_of(f.connection_from(f"spp0.M_Rx[{k}]")["to"])
        assert fifo in f.instances_of("olo/olo_ft_fifo_sync")
        assert instance_of(f.connection_from(f"{fifo}.Out")["to"]) == f"ocp{k}"
        beat = f.generic(f"ocp{k}", "InStreamWidth_g") // 8
        assert f.generic(fifo, "Depth_g") * beat >= 2 * LAB_SYSTEM.rx_buffer_bytes
        # the FIFO carries the data, the byte enables, the last flag and the user signal of the SDUs
        assert f.generic(fifo, "Width_g") == 8 * beat + beat + 1 + f.generic(f"ocp{k}", "UserWidth_g")


def test_the_chunker_passes_the_compressed_images_at_the_width_of_the_send_ports():
    f = SYSTEM_FILE
    for k in range(4):
        width = f.generic(f"chunk{k}", "Width_g")
        assert width == f.generic(f"ocp{k}", "OutStreamWidth_g") == f.generic("spp0", "DataWidth_g") == 128
        assert f.connection_from(f"chunk{k}.Out")["to"] == f"spp0.S_Tx[{k}]"


@pytest.mark.parametrize("mutation, test, message", [
    ("image", "TC-RS-HW-06", "first difference at byte"),
    ("header", "TC-RS-HW-06", "user_defined_data 5, expected 0"),
    ("counter", "TC-RS-HW-07", "ocp1 IMAGES_DONE = 6, expected 5"),
    # without the Data Loss Indicator the first image goes on: longer than the aborted image of the model, it takes
    # more Space Packets
    ("loss", "TC-RS-HW-08", "APID 0: 20 Space Packets, expected 19"),
    ("config", "TC-RS-HW-02", "ocp2 PREDICTOR"),
    ("corrupt", "TC-RS-HW-06", "first difference at byte"),
    # the bytes of an image in other Space Packets: the cut points between the chunks are checked
    ("cut", "TC-RS-HW-06", f"Space Packets of [{CHUNK_BYTES - 4}, {CHUNK_BYTES + 4}"),
    ("missing", "TC-RS-HW-06", "packets"),
])
def test_procedure_detects_a_faulty_system(mutation, test, message):
    class Faulty(sm.SystemModel):
        def _image(self, channel, output):
            data = super()._image(channel, output)
            if mutation == "image" and channel.port == 0:
                data = data[:-1] + bytes([data[-1] ^ 1])
            if mutation == "header" and channel.port == 0:
                data = bytes([5]) + data[1:]
            return data

        def process(self, channel, data, user_application, loss):
            if mutation == "loss":
                loss = False
            out = super().process(channel, data, user_application, loss)
            if mutation == "counter" and channel.port == 1 and out:
                self._count(self.system.base(channel.compressor) + ht.OCP.IMAGES_DONE, 1)
                self.regs[self.system.base(channel.compressor) + ht.OCP.IMAGES_DONE] = 6
            return out

        def send(self, channel, data):
            parts = super().send(channel, data)
            if mutation == "cut" and channel.port == 0 and len(parts) > 2:
                parts = [parts[0][:-4], parts[0][-4:] + parts[1]] + parts[2:]
            if mutation == "missing" and channel.port == 2 and len(parts) > 1:
                parts = parts[:1] + parts[2:]
            return parts

    m = Faulty(ht.Setup(), system=SYSTEM, system_file=SYSTEM_FILE, corrupt=mutation == "corrupt")
    if mutation == "config":
        m.regs[SYSTEM.base("ocp2") + ht.OCP.PREDICTOR] ^= 1
    results = run(m, context(), ht.select_tests([test]))
    assert message in (results[test] or ""), results


def test_an_aborted_image_of_another_length_fails():
    class Longer(sm.SystemModel):
        def _image(self, channel, output):
            data = super()._image(channel, output)
            # one word of fill bits more than the core writes
            return data if output.complete or channel.port != 1 else data + bytes(channel.cfg["large_b"])

    m = Longer(ht.Setup(), system=SYSTEM, system_file=SYSTEM_FILE)
    results = run(m, context(), ht.select_tests(["TC-RS-HW-08"]))
    assert "APID 1: " in (results["TC-RS-HW-08"] or ""), results


def test_an_enabled_window_of_the_rmap_target_is_left_alone():
    m, ctx = model(), context()
    window = SYSTEM.base(SYSTEM.rmap) + systest.OMAP.WIN_CTRL(SYSTEM.rmap_windows - 1)
    m.regs[window] = ht.WIN_ENABLE
    results = run(m, ctx, ht.select_tests(["TC-RS-HW-04"]))
    assert "is enabled: the test does not change it" in results["TC-RS-HW-04"]
    assert m.regs[window] == ht.WIN_ENABLE


def test_tc04_restores_the_window_of_the_rmap_target():
    m, ctx = model(), context()
    window = SYSTEM.base(SYSTEM.rmap) + systest.OMAP.WIN_CTRL(SYSTEM.rmap_windows - 1)
    results = run(m, ctx, ht.select_tests(["TC-RS-HW-04"]))
    assert results == {"TC-RS-HW-04": None}
    assert [m.regs.get(window + 4 * k, 0) for k in range(3)] == [0, 0, 0]
    assert not ctx.window_words[0] & ht.WIN_ENABLE and any(ctx.window_words)


def test_an_image_that_does_not_fit_tc08_is_reported():
    tiny = ht.System(dims=(4, 2, 2), chunk_bytes=CHUNK_BYTES)
    results = run(model(tiny), context(system=tiny), ht.select_tests(["TC-RS-HW-08"]))
    assert "too short for the lost SDUs" in results["TC-RS-HW-08"]


def test_channels_that_the_test_cannot_drive_are_rejected():
    out = {"chunker": "chunk0", "chunk_bytes": 4096, "output_apid": 0}
    periodic = LAB_SYSTEM.channels[3].generics | {"PeriodicUpdate_g": True, "UpdatePeriodExp_g": 1}
    with pytest.raises(ValueError, match="error limit stream"):
        ht.make_channel(0, "ocp0", periodic, out)
    with pytest.raises(ValueError, match="User Application"):
        ht.make_channel(0, "ocp0", LAB_SYSTEM.channels[0].generics | {"UserLastMask_g": '22x"000001"'}, out)
    with pytest.raises(ValueError, match="whole number of output words"):
        ht.make_channel(0, "ocp0", LAB_SYSTEM.channels[0].generics, out | {"chunk_bytes": 4094})


def test_the_chunks_of_an_image():
    assert ht.chunks_of(bytes(10), 4) == [bytes(4), bytes(4), bytes(2)]
    assert ht.chunks_of(bytes(8), 4) == [bytes(4), bytes(4)]
    with pytest.raises(ValueError):
        ht.chunks_of(b"", 4)


@pytest.mark.parametrize("value, expected", [('22x"3FC000"', 0x3FC000), ('x"3FC000"', 0x3FC000),
                                             ('X"00_4000"', 0x4000), ('b"0110"', 6), ('"0110"', 6), ("1", 1),
                                             (12, 12), ('4x"1F"', None), ('x"3G"', None)])
def test_logic_vector_generics(value, expected):
    if expected is None:
        with pytest.raises(ValueError):
            ht.logic_vector(value)
    else:
        assert ht.logic_vector(value) == expected


def test_a_selected_test_case_brings_those_it_builds_on():
    names = [ht.test_name(t) for t in ht.select_tests(["TC-RS-HW-07", "TC-RS-HW-09"])]
    assert names == ["TC-RS-HW-05", "TC-RS-HW-06", "TC-RS-HW-07", "TC-RS-HW-09"]


def test_the_shared_modules_of_the_other_systems_stay_in_place():
    # the modules of this system are not left under the names of those of the other systems
    import sys
    assert sys.modules.get("hardware_test") is not ht and sys.modules.get("system_model") is not sm


# --- TC-RS-HW-10: runtime configuration ------------------------------------------------------------------------------
def test_ocp0_and_ocp3_have_the_runtime_configuration():
    runtime, fixed = ocptest.split_channels(LAB_SYSTEM)
    assert [c.compressor for c in runtime] == ["ocp0", "ocp3"] and [c.compressor for c in fixed] == ["ocp1", "ocp2"]
    # ocp0 is lossless (dimensions only), ocp3 near-lossless with absolute error limits and images of width 1
    assert [c.cfg["quantizer_fidelity_control_method"] for c in runtime] == [0, 1]
    assert [ocp_configs.width_one_allowed(c.cfg) for c in runtime] == [False, True]
    # both also take the runtime parameters (OpenCompact 0.3.0): ocp0 M, Umax, gamma0, gamma* and K, ocp3 the
    # parameters of the hybrid coder
    assert [ocp.runtime_params(c.generics) for c in LAB_SYSTEM.channels] == [True, False, False, True]
    assert [ocp.used_params(c.cfg) for c in runtime] == [
        ["user", "tinc", "vmin", "vmax", "m", "umax", "g0", "gstar", "k"],
        ["user", "tinc", "vmin", "vmax", "m", "umax", "g0", "gstar"]]


def test_the_runtime_images_of_the_profiles():
    ch0, ch3 = (LAB_SYSTEM.channels[k] for k in (0, 3))
    sim = context(system=LAB_SYSTEM, profile=ht.PROFILES["sim"])

    def dims(plan):
        return [(v.nx, v.ny, v.nz, v.abs_limit) for v in plan]

    assert dims(ocptest.image_plan(sim, ch0)) == [(2, 1, 1, 0), (64, 1, 48, 0), (2, 64, 1, 0)]
    assert dims(ocptest.image_plan(sim, ch3)) == [(1, 1, 1, 0), (64, 1, 48, 1023), (1, 64, 1, 0)]
    for ch in (ch0, ch3):
        plan = ocptest.image_plan(sim, ch)
        assert all(v.params is not None and ocp.commit_accepts(ch.cfg, v) for v in plan)
        # the first image takes every parameter to the high bound of its range, the second to the low bound
        first, second = (dict(zip(ocp.PARAM_NAMES, v.params)) for v in plan[:2])
        assert (first["tinc"], first["vmax"], first["umax"], second["tinc"], second["vmin"], second["umax"]) == (
            11, 9, 32, 4, -6, 8)
    lab = ocptest.image_plan(context(system=LAB_SYSTEM), ch3)
    assert len(lab) == ocptest.LAB_IMAGES + 2 and lab[-1] == ocp.build_values(ch3.cfg, params=True)
    assert lab[-1].abs_limit == 12 and all(ocp.commit_accepts(ch3.cfg, v) for v in lab)


def test_tc10_ends_with_the_values_of_the_reset():
    m = model()
    results = run(m, context(), ht.select_tests(["TC-RS-HW-10"]))
    assert not any(results.values()), results
    fresh = model()
    for c in SYSTEM.channels:
        base = SYSTEM.base(c.compressor)
        for name in ocp.CONFIG_REGISTERS + ocp.TAKEN_REGISTERS:
            address = base + getattr(ht.OCP, name)
            assert m._read(address) == fresh._read(address), (c.compressor, name)


class Storing:
    """Registers that read back what is written: a compressor without the runtime configuration that keeps the
    writes of NEXT_X to NEXT_LIMITS."""

    def __init__(self):
        self.words = {}

    def read(self, name):
        return self.words.get(name, 0)

    def write(self, name, value):
        self.words[name] = value


@pytest.mark.parametrize("mutation, message", [
    # the channel passes values out of range on, and the core takes the nearest valid ones (CO-RC-07)
    ("clamp", "ocp0: COMMIT with NEXT_X 0x00000001 gives STATUS (CFG_ERROR, CFG_PENDING) (False, True)"),
    # the core keeps the configuration of its generics: the first image (the maximum dimensions with the parameters
    # at their high bounds) gives another compressed image, of another number of chunks
    ("ignore", "packets"),
    ("pending", "ocp0: COMMIT of"),
    ("fixed", "ocp1 without the runtime configuration"),
    # the first image has the dimensions of the generics: the user-defined data of SAMPLES differ first
    ("taken", "ocp0 SAMPLES"),
    # ocp3 keeps the error limit value of its generics: the header of the image differs
    ("limit", "compressed image 0"),
    # ocp0 keeps the runtime parameters of its generics: the compressed image differs, here in its number of chunks
    ("params", "packets"),
    # NEXT_SAMPLES to NEXT_BLOCK of ocp0 do not read back what was written
    ("readback", "NEXT_SAMPLES to NEXT_BLOCK"),
    # SAMPLES to BLOCK of ocp3 keep the values of the generics
    ("taken_params", "ocp3 SAMPLES"),
])
def test_tc10_detects_a_faulty_runtime_configuration(mutation, message):
    m = model()
    ch0, ch3 = m.channels[0], m.channels[3]
    registers0, _ = m.runtime[SYSTEM.base("ocp0") + ht.OCP.CONTROL]
    commit0, commit3 = ch0.commit, ch3.commit

    def clamp(values):
        ch0.next_cfg = ocp.image_config(ch0.cfg, ocp.ImageConfig(*ocp_configs.runtime_limit(
            ch0.cfg, values.nx, values.ny, values.nz, values.abs_limit, values.rel_limit)))
        return True

    if mutation == "clamp":
        ch0.commit = clamp
    elif mutation == "ignore":
        ch0.commit = lambda values: commit0(values) and (setattr(ch0, "next_cfg", ch0.cfg) or True)
    elif mutation == "pending":
        read = registers0.read
        registers0.read = lambda name: read(name) & ~ocptest.CFG_PENDING
    elif mutation == "fixed":
        stored = Storing()
        for name in ocp.CONFIG_REGISTERS:
            m.runtime[SYSTEM.base("ocp1") + getattr(ht.OCP, name)] = (stored, name)
    elif mutation == "taken":
        registers0.taken = lambda: {}
    elif mutation == "limit":
        ch3.commit = lambda values: commit3(values) and commit3(replace(values, abs_limit=12))
    elif mutation == "params":
        ch0.commit = lambda values: commit0(values) and commit0(replace(values, params=ocp.build_params(ch0.cfg)))
    elif mutation == "readback":
        read0 = registers0.read
        registers0.read = lambda name: read0(name) ^ (1 << 8) if name == "NEXT_SAMPLES" else read0(name)
    elif mutation == "taken_params":
        registers3, _ = m.runtime[SYSTEM.base("ocp3") + ht.OCP.CONTROL]
        taken3 = registers3.taken
        generics = ocp.mib_words(ht.OCP, registers3.generics)
        registers3.taken = lambda: taken3() | {n: generics[n] for n in ("SAMPLES", "WEIGHTS", "ADAPTIVE")}
    results = run(m, context(), ht.select_tests(["TC-RS-HW-10"]))
    assert message in (results["TC-RS-HW-10"] or ""), results


# --- decoding check and the options of the hardware test ------------------------------------------------------------
class StubDecodeError(Exception):
    pass


@pytest.fixture
def stub_decoder(monkeypatch):
    """A decoder for an OpenCompact without one (ocp_model.decompress, 0.3.0): it gives back the image that the model
    compressed into the bytes, and an error for bytes that the model did not make. wrong: the decoded value of the
    first sample is off by one more than its maximum error."""
    known, compress = {}, ocp.ocp_model.compress
    state = {"wrong": False}

    def recording(cfg, img):
        data = compress(cfg, img)
        known[bytes(data)] = (cfg, copy.deepcopy(img))
        return data

    def decompress(data, optional_tables=None, tables=None):
        if bytes(data) not in known:
            raise StubDecodeError("not a compressed image of the model")
        cfg, img = known[bytes(data)]
        img = copy.deepcopy(img)
        max_error = [[[0] * len(row) for row in band] for band in img]
        if state["wrong"]:
            img[0][0][0] += 1
        return dict(cfg), img, max_error

    monkeypatch.setattr(ocp.ocp_model, "compress", recording)
    monkeypatch.setattr(ocp.ocp_model, "decompress", decompress, raising=False)
    monkeypatch.setattr(ocp.ocp_model, "DecodeError", StubDecodeError, raising=False)
    return state


def test_tc06_decodes_every_image_with_the_model_decoder(stub_decoder):
    m, ctx = model(), context()
    results = run(m, ctx, ht.select_tests(["TC-RS-HW-06"]))
    assert not any(results.values()), results
    assert ctx.decoded == 4 * len(LAB.kinds) and ctx.undecoded == 0
    assert any("model decoder: 20 compressed images decode" in r for r in ctx.records)


def test_the_decoding_check_reports_an_image_that_decodes_wrongly(stub_decoder):
    stub_decoder["wrong"] = True
    results = run(model(), context(), ht.select_tests(["TC-RS-HW-06"]))
    assert "decoded sample (0, 0, 0)" in (results["TC-RS-HW-06"] or ""), results


def test_without_the_decoder_the_check_is_skipped_and_recorded(monkeypatch):
    monkeypatch.setattr(ocp, "decoder_available", lambda: False)
    m, ctx = model(), context()
    results = run(m, ctx, ht.select_tests(["TC-RS-HW-06"]))
    assert not any(results.values()), results
    assert ctx.decoded == 0 and ctx.undecoded == 4 * len(LAB.kinds)
    assert any("decoding check skipped for 20 images" in r for r in ctx.records)


def test_the_records_of_a_run(tmp_path, monkeypatch):
    monkeypatch.setattr(ocp, "decoder_available", lambda: False)
    m, ctx = model(), context()
    results = ht.run(m, ctx, ht.select_tests(["TC-RS-HW-06"]))
    path = systest.write_records(tmp_path, "spfi_compress", "system model", "lab", ctx, results,
                                 "2026-10-27 09:00:00", {"images": ctx.image_records, "rate": ctx.rate})
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["started"] == "2026-10-27 09:00:00" and data["seed"] == ctx.seed
    assert [r["test"] for r in data["results"]] == ["TC-RS-HW-05", "TC-RS-HW-06"]
    assert all(r["pass"] for r in data["results"]) and str(tmp_path) not in text
    assert {"test": "TC-RS-HW-06", "text": "model decoder: not in the pinned OpenCompact, decoding check skipped for "
                                          "20 images"} in data["records"]
    # every compressed image with its hash, no bytes: the images passed
    images = data["images"]
    assert len(images) == 4 * len(LAB.kinds) and all(i["test"] == "TC-RS-HW-06" for i in images)
    assert all(len(i["sha256"]) == 64 and "data" not in i and i["decoded"] == "skipped, no decoder" for i in images)
    assert [i["port"] for i in images[:len(LAB.kinds)]] == [0] * len(LAB.kinds)
    tables = render_records.render(data)
    assert f"| TC-RS-HW-06 | ocp0 | {len(LAB.kinds)} |" in tables and "| TC-RS-HW-06 | Pass |  |" in tables


def test_the_records_keep_the_bytes_of_an_image_that_fails():
    class Wrong(sm.SystemModel):
        def _image(self, channel, output):
            data = super()._image(channel, output)
            return data[:-1] + bytes([data[-1] ^ 1]) if channel.port == 1 else data

    ctx = context()
    run(Wrong(ht.Setup(), system=SYSTEM, system_file=SYSTEM_FILE), ctx, ht.select_tests(["TC-RS-HW-06"]))
    failed = [i for i in ctx.image_records if i["decoded"] == "failed"]
    assert len(failed) == 1 and failed[0]["port"] == 1 and len(failed[0]["data"]) == 2 * failed[0]["bytes"]


# --- TC-RS-HW-01 to 03 and 11 to 17: the error registers, the port, the RMAP target, the node --------------------------
OFB, OMAP, SPP = systest.OFB, systest.OMAP, systest.SPP
PORT, TARGET, NODE = (SYSTEM.base(n) for n in ("ofb0", "rmap0", "spp0"))


class Faulty(sm.SystemModel):
    """The system model with one fault of a TRL 4 test case (mutation)."""

    def __init__(self, mutation: str):
        super().__init__(ht.Setup(), system=SYSTEM, system_file=SYSTEM_FILE)
        self.mutation = mutation
        self.images_sent = 0
        if mutation == "generics":
            self.regs[TARGET + OMAP.GENERICS] ^= OMAP.field("GENERICS", "WINDOWS", 1)
        if mutation == "baseline":
            self.regs[PORT + OFB.DL_RETRIES] = 3

    def _write(self, address, value):
        if self.mutation == "baseline" and address == PORT + OFB.DL_RETRIES:
            return                                       # a counter that a write does not clear
        if self.mutation == "vn" and address == PORT + OFB.VC_CFG(0):
            self.regs[address] = value                   # the VN of VC 0 reads back as written
            return
        if self.mutation == "irq_mask" and address == PORT + OFB.IRQ_MASK:
            value &= ~OFB.field("IRQ_MASK", "ECC_SEC", 1)
        if self.mutation == "far_end" and address == PORT + OFB.DL_ERRORS:
            return
        if self.mutation == "lane" and address == PORT + OFB.LANE_CTRL(SYSTEM.lanes - 1):
            self.regs[address] = value                   # the lane stays in the link
            return
        if self.mutation == "no_single" and address == PORT + OFB.ECC_INJECT and value == 2:
            return                                       # channel 2 does not count
        if self.mutation == "no_drop" and address == NODE + SPP.ECC_INJECT:
            super()._write(address, value)
            self.drop_next = False
            return
        super()._write(address, value)

    def operator(self, step):
        if self.mutation != "far_end":
            super().operator(step)

    def _bus_read(self, address):
        if self.mutation == "no_alias" and address >= self.decoded_bytes:
            return None                                  # a decoder that answers DECERR above its ports
        return super()._bus_read(address)

    def _rmap(self, packet):
        cmd = bytes(packet[len(self.setup.rmap_path):])
        size = 16 + 4 * (cmd[2] & 3) if len(cmd) > 2 else 0
        if self.mutation == "header_crc" and size and len(cmd) >= size and rmap.crc8(cmd[:size]) != 0:
            # a target that answers a command with a header CRC error
            fixed = cmd[:size - 1] + bytes([rmap.crc8(cmd[:size - 1])]) + cmd[size:]
            return super()._rmap(self.setup.rmap_path + fixed)
        return super()._rmap(packet)

    def _spp(self, packet):
        if self.mutation == "oversize" and len(packet) > self.system.rx_buffer_bytes:
            return []                                    # dropped without a code
        out = super()._spp(packet)
        if self.mutation == "crc_count" and out:
            self._count(PORT + OFB.DL_CRC16_COUNT)       # every round of images counts a CRC error
        return out


@pytest.mark.parametrize("mutation, test, message", [
    ("baseline", "TC-RS-HW-01", "error registers that do not clear: ofb0.DL_RETRIES"),
    ("generics", "TC-RS-HW-02", "rmap0 GENERICS"),
    ("vn", "TC-RS-HW-11", "ofb0 VC_CFG(0).VN"),
    ("irq_mask", "TC-RS-HW-11", "ofb0 IRQ_MASK: wrote"),
    ("far_end", "TC-RS-HW-12", "waited for 0x20"),
    ("lane", "TC-RS-HW-13", "waited for 0x277"),
    ("no_single", "TC-RS-HW-14", "ofb0.ECC_COUNT(2)"),
    ("no_drop", "TC-RS-HW-14", "waited for 0x1B"),
    ("header_crc", "TC-RS-HW-15", "header CRC error: TGT_LAST"),
    ("no_alias", "TC-RS-HW-15", "a read with an AXI error ends its reply with an EEP"),
    ("oversize", "TC-RS-HW-16", "Space Packet of 8193 bytes: LAST_ERROR"),
    ("crc_count", "TC-RS-HW-12", "error registers that the test case does not target: ofb0.DL_CRC16_COUNT"),
])
def test_the_trl4_test_cases_detect_a_faulty_system(mutation, test, message):
    results = run(Faulty(mutation), context(), ht.select_tests([test]))
    assert message in (results[test] or ""), results


def test_tc17_finds_an_error_counted_after_the_baseline():
    class Late(sm.SystemModel):
        def _rmap(self, packet):
            reply = super()._rmap(packet)
            if self.regs.get(PORT + OFB.ML_MISALIGNED, 0) == 0 and self.done:
                self.regs[PORT + OFB.ML_MISALIGNED] = 1
            return reply

    m = Late(ht.Setup(), system=SYSTEM, system_file=SYSTEM_FILE)
    m.done = False
    ctx = context()
    regs = systest.Registers(m, ctx.setup)
    ht.tc01_link(regs, ctx)
    m.done = True
    with pytest.raises(ht.TestFailed, match="counted since the baseline: ofb0.ML_MISALIGNED"):
        ht.tc17_end_of_run(regs, ctx)


def test_every_address_reaches_a_register_window_of_spfi_compress():
    """The decoder has 8 ports of 64 KiB and ignores the address bits above them: the map repeats every 0x80000
    bytes, as the address map states, and no address answers DECERR."""
    m = model()
    assert SYSTEM.decoded_bytes() == 0x80000 and SYSTEM.unmapped_address() is None
    address_map = (Path(ht.SYSTEM_FILE).parent / "hdl" / "spfi_compress_address_map.md").read_text(encoding="utf-8")
    assert "the map repeats every 0x00080000 bytes" in address_map
    assert m._bus_read(0x80000 + NODE + SPP.ID) == m._bus_read(0xFFF80000 + NODE + SPP.ID) == SYSTEM.ids["spp0"]
    scratch = TARGET + OMAP.WIN_BASE(SYSTEM.rmap_windows - 1)
    assert m._bus_write(0x80000 + scratch, 0x1234) and m._bus_read(scratch) == 0x1234


def test_the_error_registers_of_spfi_compress():
    names = [r.name for r in SYSTEM.error_registers() if isinstance(r, errorstate.Register)]
    assert len(names) == len(set(names))
    for prefix, count in (("ofb0.ECC_COUNT", 9), ("rmap0.ECC_COUNT", 3), ("spp0.ECC_COUNT", 8), ("edac0.CNT", 8),
                          ("ofb0.LANE_EVENTS", 4)):
        assert len([n for n in names if n.startswith(prefix)]) == count, prefix
    assert [n for n in names if n.startswith("ocp")] == [f"ocp{k}.{c}" for k in range(4) for c in ocp.ECC_COUNTERS]
    # the reads take few commands: runs of consecutive registers and one per counter behind a select register
    assert len(errorstate.reads(SYSTEM.error_registers())) <= 70


class Accesses(sm.SystemModel):
    """The system model that notes the writes and reads of the bandwidth flags of the SpaceFibre port."""

    def __init__(self):
        super().__init__(ht.Setup(), system=SYSTEM, system_file=SYSTEM_FILE)
        self.flags = {PORT + getattr(OFB, name): name for name in ht.BW_FLAGS}
        self.log = []

    def _bus_read(self, address):
        if address in self.flags:
            self.log.append(("read", self.flags[address]))
        return super()._bus_read(address)

    def _bus_write(self, address, value):
        if address in self.flags:
            self.log.append(("write", self.flags[address], value))
        return super()._bus_write(address, value)


@pytest.mark.parametrize("profile", ["sim", "lab"])
def test_the_sim_profile_clears_the_bandwidth_flags_before_their_first_read(profile):
    m = Accesses()
    results = run(m, context(profile=ht.PROFILES[profile]), ht.select_tests(["TC-RS-HW-01"]))
    assert not any(results.values()), results
    writes = [("write", name, 0xFFFFFFFF) for name in ht.BW_FLAGS]
    if profile == "sim":
        assert m.log[:2] == writes and ("read", "VC_BW_OVER") in m.log[2:]
    else:
        assert not [a for a in m.log if a[0] == "write" and a[2] == 0xFFFFFFFF] and m.log[0][0] == "read"
