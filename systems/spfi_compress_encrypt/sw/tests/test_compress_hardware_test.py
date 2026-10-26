"""Runs the hardware test of spfi_compress_encrypt against the software model of the system (system_model.py).

The procedure runs on the system of the Composer file with smaller images: every compressor keeps its configuration,
with other image dimensions, so that the model compresses the images of all test cases in seconds. The Composer file
itself is checked against the configurations of OpenCompact that its channels use.
"""

import copy
from pathlib import Path

import pytest

from refsys import ocp, ocptest, systest
from refsys.composer import SystemFile, instance_of

import ocp_configs  # noqa: E402 - tools of cores/opencompact, on the path through refsys.ocp

HERE = Path(__file__).resolve().parent
ht, sm = systest.system_modules(HERE.parent, "hardware_test", "system_model")

SYSTEM_FILE = SystemFile(ht.SYSTEM_FILE)
LAB_SYSTEM = ht.System()

# The configurations of OpenCompact of the four channels, one per preset of the Composer (BIP sample-adaptive, BSQ,
# BIP block-adaptive, BIL near-lossless hybrid), and the lab images: 64 x 64 samples in 48 bands
FAMILIES = ("c01_lossless_bip", "c25_tp_bsq_lossless", "c27_tp_bip_full", "c26_tp_bil_near_abs")
LAB_DIMS = (64, 64, 48)
DIMS = (16, 8, 6)            # 768 samples, 1536 bytes: 8 SDUs of at most 200 bytes
SDU_BYTES = 200


SYSTEM = ht.System(dims=DIMS)
# OpenCompact gives the compressed image of an aborted image (compress_aborted of its model): the test then compares
# aborted images exactly and receives no packet until a channel is quiet
EXACT_ABORT = hasattr(ocp.ocp_model, "compress_aborted")
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
    per_channel = len(LAB.kinds) + 5 + LAB.stream
    # TC-RS-HW-10: the plan of the two channels with the runtime configuration, one image of each other channel
    runtime = 2 * (ocptest.LAB_IMAGES + 1) + 2
    assert ctx.images == 4 * per_channel + runtime and not m.pending
    assert "Gbit/s of samples (limited by the test equipment" in ctx.rate


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
    assert [(c.port, c.compressor, c.framer, c.core, c.output_apid, c.end_mark) for c in LAB_SYSTEM.channels] == [
        (k, f"ocp{k}", f"frm{k}", f"ocr{k}", k, 0x01) for k in range(4)]
    assert [LAB_SYSTEM.base(f"ocp{k}") for k in range(4)] == [0x80000 + 0x10000 * k for k in range(4)]
    assert all(LAB_SYSTEM.ids[f"ocp{k}"] == ht.OCP.REGMAP_ID for k in range(4))
    assert all(c.marks == ocp.UserMarks(0x3FC000, 0x004000, 0x003000) for c in LAB_SYSTEM.channels)
    assert [ocp.samples_per_image(c.cfg) for c in SYSTEM.channels] == [768] * 4


def test_register_banks_of_the_exported_header_match_the_system():
    s, f = LAB_SYSTEM, SYSTEM_FILE
    # bases: the windows of the AXI4-Lite decoder of the Composer file
    assert s.windows == f.decoder_windows(f.instance_of_block("olo/olo_axi_lite_decoder"))
    # identifications: the register map identifications of the pinned cores, so that a new version of a register map
    # shows up here before the system is tested
    core_ids = {s.node: systest.SPP.REGMAP_ID, s.rmap: systest.OMAP.REGMAP_ID, s.port: systest.OFB.REGMAP_ID}
    core_ids |= {c.core: systest.OCR.REGMAP_ID for c in s.channels}
    core_ids |= {c.compressor: ht.OCP.REGMAP_ID for c in s.channels}
    assert s.ids == core_ids
    # channels of the ECC monitor: the framers, then the compressors, then the FIFOs, as many as Channels_g
    assert s.ecc_names == ([f"FRM{k}_ECC_{m}" for k in range(4) for m in range(2)] + [f"OCP{k}_ECC" for k in range(4)]
                           + [f"RXF{k}_ECC" for k in range(4)])
    assert s.ecc_channels == f.generic(s.ecc, "Channels_g") == 16


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


@pytest.mark.parametrize("mutation, test, message", [
    ("image", "TC-RS-HW-06", "compressed image 0: 1"),
    ("header", "TC-RS-HW-06", "user_defined_data 5, expected 0"),
    ("counter", "TC-RS-HW-07", "ocp1 IMAGES_DONE = 6, expected 5"),
    # without the Data Loss Indicator the first image goes on: its exact compressed image, or without it the counter
    ("loss", "TC-RS-HW-08", "compressed image 0: " if EXACT_ABORT else "ocp0 SDU_ERRORS = 0, expected 2"),
    ("config", "TC-RS-HW-02", "ocp2 PREDICTOR"),
    ("corrupt", "TC-RS-HW-06", "tag does not authenticate"),
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

    m = Faulty(ht.Setup(), system=SYSTEM, system_file=SYSTEM_FILE, corrupt=mutation == "corrupt")
    if mutation == "config":
        m.regs[SYSTEM.base("ocp2") + ht.OCP.PREDICTOR] ^= 1
    results = run(m, context(), ht.select_tests([test]))
    assert message in (results[test] or ""), results


@pytest.mark.skipif(not EXACT_ABORT, reason="the model of OpenCompact gives no aborted image")
def test_an_aborted_image_of_another_length_fails():
    class Longer(sm.SystemModel):
        def _image(self, channel, output):
            data = super()._image(channel, output)
            # one word of fill bits more than the core writes
            return data if output.complete or channel.port != 1 else data + bytes(channel.cfg["large_b"])

    m = Longer(ht.Setup(), system=SYSTEM, system_file=SYSTEM_FILE)
    results = run(m, context(), ht.select_tests(["TC-RS-HW-08"]))
    assert "APID 1: compressed image 0: " in (results["TC-RS-HW-08"] or ""), results


def test_an_image_that_does_not_fit_tc08_is_reported():
    tiny = ht.System(dims=(4, 2, 2))
    results = run(model(tiny), context(system=tiny), ht.select_tests(["TC-RS-HW-08"]))
    assert "too short for the lost SDUs" in results["TC-RS-HW-08"]


def test_channels_that_the_test_cannot_drive_are_rejected():
    enc = {"framer": "frm0", "core": "ocr0", "output_apid": 0, "slot": 0, "key_slots": 4}
    periodic = LAB_SYSTEM.channels[3].generics | {"PeriodicUpdate_g": True, "UpdatePeriodExp_g": 1}
    with pytest.raises(ValueError, match="error limit stream"):
        ht.make_channel(0, "ocp0", periodic, enc)
    with pytest.raises(ValueError, match="User Application"):
        ht.make_channel(0, "ocp0", LAB_SYSTEM.channels[0].generics | {"UserLastMask_g": '22x"000001"'}, enc)


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


def test_the_shared_modules_of_spfi_encrypt_stay_in_place():
    # the modules of this system are not left under the names of those of spfi_encrypt
    import sys
    assert sys.modules.get("hardware_test") is not ht and sys.modules.get("system_model") is not sm


# --- TC-RS-HW-10: runtime configuration ------------------------------------------------------------------------------
def test_ocp0_and_ocp3_have_the_runtime_configuration():
    runtime, fixed = ocptest.split_channels(LAB_SYSTEM)
    assert [c.compressor for c in runtime] == ["ocp0", "ocp3"] and [c.compressor for c in fixed] == ["ocp1", "ocp2"]
    # ocp0 is lossless (dimensions only), ocp3 near-lossless with absolute error limits and images of width 1
    assert [c.cfg["quantizer_fidelity_control_method"] for c in runtime] == [0, 1]
    assert [ocp_configs.width_one_allowed(c.cfg) for c in runtime] == [False, True]


def test_the_runtime_images_of_the_profiles():
    ch0, ch3 = (LAB_SYSTEM.channels[k] for k in (0, 3))
    sim = context(system=LAB_SYSTEM, profile=ht.PROFILES["sim"])
    assert ocptest.image_plan(sim, ch0) == [ocp.ImageConfig(2, 1, 1), ocp.ImageConfig(64, 1, 48),
                                            ocp.ImageConfig(2, 64, 1)]
    assert ocptest.image_plan(sim, ch3) == [ocp.ImageConfig(1, 1, 1), ocp.ImageConfig(64, 1, 48, 1023),
                                            ocp.ImageConfig(1, 64, 1)]
    lab = ocptest.image_plan(context(system=LAB_SYSTEM), ch3)
    assert len(lab) == ocptest.LAB_IMAGES + 1 and lab[-1] == ocp.ImageConfig(64, 64, 48, 12)
    assert all(ocp.commit_accepts(ch3.cfg, v) for v in lab)


def test_tc10_ends_with_the_values_of_the_reset():
    m = model()
    results = run(m, context(), ht.select_tests(["TC-RS-HW-10"]))
    assert not any(results.values()), results
    fresh = model()
    for c in SYSTEM.channels:
        base = SYSTEM.base(c.compressor)
        for name in ocp.RUNTIME_REGISTERS + ocp.TAKEN_REGISTERS:
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
    # the core keeps the configuration of its generics: the end mark of the small image ends a larger one early
    ("ignore", "compressed image 0: large_n_x 16, expected 2"),
    ("pending", "ocp0: COMMIT of"),
    ("fixed", "ocp1 without the runtime configuration"),
    ("taken", "ocp0 IMAGE_X"),
    # ocp3 keeps the error limit value of its generics: the header of the image differs
    ("limit", "compressed image 0"),
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
        for name in ocp.RUNTIME_REGISTERS:
            m.runtime[SYSTEM.base("ocp1") + getattr(ht.OCP, name)] = (stored, name)
    elif mutation == "taken":
        registers0.taken = lambda: {}
    elif mutation == "limit":
        ch3.commit = lambda values: commit3(values) and commit3(ocp.ImageConfig(values.nx, values.ny, values.nz, 12,
                                                                                 values.rel_limit))
    results = run(m, context(), ht.select_tests(["TC-RS-HW-10"]))
    assert message in (results["TC-RS-HW-10"] or ""), results
