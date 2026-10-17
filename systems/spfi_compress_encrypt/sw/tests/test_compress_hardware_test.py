"""Runs the hardware test of spfi_compress_encrypt against the software model of the system (system_model.py).

Until the system has its Composer file, the test runs on a stand-in of the system: the blocks and register windows of
spfi_encrypt, an OpenCompact channel in front of every framer with the four lab configurations at small image
dimensions, and status windows for the four compressors.
"""

import copy
from pathlib import Path

import pytest

from refsys import ocp, systest
from refsys.composer import SystemFile, instance_of
from refsys.regs import RegisterMap

import ocp_configs  # noqa: E402 - tools of cores/opencompact, on the path through refsys.ocp
import ocp_generics  # noqa: E402

HERE = Path(__file__).resolve().parent
ht, sm = systest.system_modules(HERE.parent, "hardware_test", "system_model")

SPFI = ht.ROOT / "systems" / "spfi_encrypt"
SPFI_FILE = SystemFile(SPFI / "spfi_encrypt.composer.json")

# The families of the lab configurations, one per channel (BIP sample-adaptive, BSQ, BIP block-adaptive, BIL
# near-lossless hybrid)
FAMILIES = ("c01_lossless_bip", "c25_tp_bsq_lossless", "c27_tp_bip_full", "c26_tp_bil_near_abs")
CHANNEL_GENERICS = {"InWidth_g": 128, "OutWidth_g": 128, "CoreWidth_g": 32, "UserWidth_g": 22,
                    "UserLastMask_g": 'x"3FC000"', "UserLastValue_g": 'x"004000"', "UserErrorMask_g": 'x"003000"',
                    "Speculation_g": True, "RamStyle_g": "auto"}
DIMS = (16, 8, 6)            # 768 samples, 1536 bytes: 8 SDUs of at most 200 bytes
SDU_BYTES = 200


def lab_generics(family: str, dims: tuple[int, int, int] = DIMS, **changes) -> dict:
    c = copy.deepcopy(ocp_configs.CONFIGS[family])
    bip = c["sample_encoding_order"] == 0 and c["large_m"] == c["large_n_z"]
    c.update(large_n_x=dims[0], large_n_y=dims[1], large_n_z=dims[2], **changes)
    if bip:
        c["large_m"] = dims[2]
    return ocp_generics.generics(c) | CHANNEL_GENERICS


class FixtureSystem(ht.System):
    """spfi_compress_encrypt as its Composer file will describe it, built on the Composer file of spfi_encrypt."""

    def __init__(self, generics: list[dict] | None = None):
        self.lab = generics or [lab_generics(f) for f in FAMILIES]
        systest.System.__init__(self, SPFI_FILE.path, RegisterMap(SPFI / "hdl" / "spfi_encrypt_regs.h",
                                                                  "SPFI_ENCRYPT"))
        for c in self.channels:
            self.windows[c.compressor] = 0x80000 + 0x10000 * c.port
            self.ids[c.compressor] = ht.OCP.REGMAP_ID

    def channel(self, f, port):
        framer = instance_of(f.connection_from(f"{self.node}.M_Rx[{port}]")["to"])
        return ht.make_channel(port, f"ocp{port}", self.lab[port], systest.encryption(f, framer))


SYSTEM = FixtureSystem()
LAB = ht.PROFILES["lab"]


def context(seed: int = 3, system=SYSTEM, profile=LAB):
    return ht.make_context(seed, profile, system=system, sdu_bytes=SDU_BYTES)


def model(system=SYSTEM, **kw):
    return sm.SystemModel(ht.Setup(), system=system, system_file=SPFI_FILE, **kw)


def run(transport, ctx, tests=None):
    results = ht.run(transport, ctx, tests)
    return dict(results)


def test_procedure_passes_against_the_model():
    m, ctx = model(), context()
    results = run(m, ctx)
    assert list(results) == [ht.test_name(t) for t in ht.TESTS] and not any(results.values())
    per_channel = len(LAB.kinds) + 5 + LAB.stream
    assert ctx.images == 4 * per_channel and not m.pending
    assert "Gbit/s of samples (limited by the test equipment" in ctx.rate


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


def test_the_system_facts_of_the_stand_in():
    assert [(c.port, c.compressor, c.framer, c.core, c.output_apid, c.end_mark) for c in SYSTEM.channels] == [
        (k, f"ocp{k}", f"frm{k}", f"ocr{k}", k, 0x01) for k in range(4)]
    assert [ocp.samples_per_image(c.cfg) for c in SYSTEM.channels] == [768] * 4
    assert SYSTEM.channels[0].marks == ocp.UserMarks(0x3FC000, 0x004000, 0x003000)


@pytest.mark.parametrize("mutation, test, message", [
    ("image", "TC-RS-HW-06", "compressed image 0: 1"),
    ("header", "TC-RS-HW-06", "user_defined_data 5, expected 0"),
    ("counter", "TC-RS-HW-07", "ocp1 IMAGES_DONE = 6, expected 5"),
    ("loss", "TC-RS-HW-08", "ocp0 SDU_ERRORS = 0, expected 2"),
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

    m = Faulty(ht.Setup(), system=SYSTEM, system_file=SPFI_FILE, corrupt=mutation == "corrupt")
    if mutation == "config":
        m.regs[SYSTEM.base("ocp2") + ht.OCP.PREDICTOR] ^= 1
    results = run(m, context(), ht.select_tests([test]))
    assert message in (results[test] or ""), results


def test_an_image_that_does_not_fit_tc08_is_reported():
    tiny = FixtureSystem([lab_generics(f, dims=(4, 2, 2)) for f in FAMILIES])
    results = run(model(tiny), context(system=tiny), ht.select_tests(["TC-RS-HW-08"]))
    assert "too short for the lost SDUs" in results["TC-RS-HW-08"]


def test_channels_that_the_test_cannot_drive_are_rejected():
    enc = {"framer": "frm0", "core": "ocr0", "output_apid": 0, "slot": 0, "key_slots": 4}
    periodic = lab_generics("c26_tp_bil_near_abs", periodic_updating_flag=1, u=1)
    with pytest.raises(ValueError, match="error limit stream"):
        ht.make_channel(0, "ocp0", periodic, enc)
    with pytest.raises(ValueError, match="User Application"):
        ht.make_channel(0, "ocp0", lab_generics(FAMILIES[0]) | {"UserLastMask_g": 'x"000001"'}, enc)


@pytest.mark.parametrize("value, expected", [('x"3FC000"', 0x3FC000), ('X"00_4000"', 0x4000), ('"0110"', 6),
                                             ("1", 1), (12, 12)])
def test_logic_vector_generics(value, expected):
    assert ht.logic_vector(value) == expected


def test_a_selected_test_case_brings_those_it_builds_on():
    names = [ht.test_name(t) for t in ht.select_tests(["TC-RS-HW-07", "TC-RS-HW-09"])]
    assert names == ["TC-RS-HW-05", "TC-RS-HW-06", "TC-RS-HW-07", "TC-RS-HW-09"]


def test_the_shared_modules_of_spfi_encrypt_stay_in_place():
    # the modules of this system are not left under the names of those of spfi_encrypt
    import sys
    assert sys.modules.get("hardware_test") is not ht and sys.modules.get("system_model") is not sm
