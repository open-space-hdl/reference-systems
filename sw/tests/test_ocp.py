"""Unit tests of refsys.ocp: the model configuration from the generics of ocp_core, the test images, the samples as
bytes, the header check, the model of an OpenCompact channel against the error cases of its specification and the
words of its status interface."""

import copy
import json
from dataclasses import replace

import pytest

from refsys import ocp
from refsys.ocp import Channel, Counts, UserMarks
from refsys.regs import RegisterMap

import ocp_configs  # noqa: E402 - tools of cores/opencompact, on the path through refsys.ocp
import ocp_generics  # noqa: E402

CONFIGS = ocp_configs.CONFIGS


def small(cfg: dict, nx: int, ny: int, nz: int) -> dict:
    """A configuration with other image dimensions (M follows NZ in BIP order)."""
    c = copy.deepcopy(cfg)
    bip = c["sample_encoding_order"] == 0 and c["large_m"] == c["large_n_z"]
    c.update(large_n_x=nx, large_n_y=ny, large_n_z=nz)
    if bip:
        c["large_m"] = nz
    return c


# --- configuration --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_the_generics_of_every_configuration_give_its_compressed_image(name):
    c = CONFIGS[name]
    cfg = ocp.model_config(ocp_generics.generics(c))
    assert ocp.ocp_model.header(cfg) == ocp.ocp_model.header(c)
    img = ocp_configs.make_image(c)
    limits = ocp_configs.error_limit_updates(c)
    assert ocp.ocp_model.compress(cfg, img, limits) == ocp.ocp_model.compress(c, img, limits)


@pytest.mark.parametrize("value, expected", [
    ("(0 => 4)", [4]), ("(1, 2, 3)", [1, 2, 3]), ("(others => 0)", [0]), ("(1 => 7, 0 => 5)", [5, 7]),
    ("1,2,3", [1, 2, 3]), ("12", [12]), (7, [7]), ([3, -2], [3, -2]), ("(-6, 5)", [-6, 5]), ("(0 => 16#1F#)", None)])
def test_vector_generics(value, expected):
    if expected is None:
        with pytest.raises(ValueError):
            ocp.vector(value)
    else:
        assert ocp.vector(value) == expected


@pytest.mark.parametrize("value", ["(1, 2 => 3)", "(1 => 3)", "()", True])
def test_malformed_vector_generics_are_rejected(value):
    with pytest.raises(ValueError):
        ocp.vector(value)


def test_composer_values_of_the_generics():
    g = ocp_generics.generics(CONFIGS["c26_tp_bil_near_abs"])
    g |= {"AbsErrorLimits_g": "(0 => 12)", "Damping_g": "(0 => 2)", "HybridAccuInit_g": "(0 => 300)",
          "HybridAccuInitHigh_g": "(0 => 0)", "SignedSamples_g": "false", "UseInLast_g": True}
    assert ocp.ocp_model.header(ocp.model_config(g)) == ocp.ocp_model.header(CONFIGS["c26_tp_bil_near_abs"])
    with pytest.raises(ValueError):
        ocp.flag("yes")


# --- images ---------------------------------------------------------------------------------------------------------
LAB_LIKE = [small(CONFIGS[n], 20, 9, 6) for n in ("c01_lossless_bip", "c25_tp_bsq_lossless", "c26_tp_bil_near_abs")]


def address_bits(n: int) -> range:
    return range(max(1, (n - 1).bit_length()))


@pytest.mark.parametrize("kind", ocp.KINDS)
@pytest.mark.parametrize("cfg", LAB_LIKE, ids=["bip", "bsq", "bil"])
def test_every_image_depends_on_every_address_bit(kind, cfg):
    img = ocp.test_image(cfg, kind, seed=3)
    nx, ny, nz = cfg["large_n_x"], cfg["large_n_y"], cfg["large_n_z"]
    lo, hi = ocp.sample_range(cfg)
    assert all(lo <= s <= hi for band in img for row in band for s in row)
    for axis, size in (("x", nx), ("y", ny), ("z", nz)):
        for bit in address_bits(size):
            def flipped(z, y, x):
                a = {"x": x, "y": y, "z": z}
                a[axis] ^= 1 << bit
                return a
            assert any(img[z][y][x] != img[f["z"]][f["y"]][f["x"]]
                       for z in range(nz) for y in range(ny) for x in range(nx)
                       if (f := flipped(z, y, x))[axis] < size), f"{kind}: {axis} bit {bit}"
    samples = ocp.input_order(cfg, img)
    for bit in address_bits(len(samples)):
        assert any(samples[a] != samples[a ^ (1 << bit)] for a in range(len(samples)) if a ^ (1 << bit) < len(samples))


def test_images_are_deterministic_and_differ_by_seed_and_kind():
    cfg = LAB_LIKE[0]
    assert ocp.test_image(cfg, "scene", 1) == ocp.test_image(cfg, "scene", 1)
    assert ocp.test_image(cfg, "scene", 1) != ocp.test_image(cfg, "scene", 2)
    images = [ocp.test_image(cfg, kind, 1) for kind in ocp.KINDS]
    assert all(a != b for i, a in enumerate(images) for b in images[i + 1:])
    with pytest.raises(ValueError):
        ocp.test_image(cfg, "stars", 1)


def test_the_kinds_span_the_compression_ratios():
    cfg = small(CONFIGS["c01_lossless_bip"], 24, 16, 8)
    raw = 2 * ocp.samples_per_image(cfg)
    ratio = {k: raw / len(ocp.compress(cfg, ocp.test_image(cfg, k, 1))) for k in ocp.KINDS}
    assert ratio["noise"] < 1.0 < ratio["scene"] < min(ratio["flat"], ratio["spikes"])


@pytest.mark.parametrize("name", ["c01_lossless_bip", "c03_lossless_bsq_narrow", "c05_near_rel_bi4_signed",
                                  "c02_lossless_bil_column"])
def test_input_order_is_that_of_the_opencompact_tools_and_reversible(name):
    c = CONFIGS[name]
    img = ocp_configs.make_image(c)
    samples = ocp.input_order(c, img)
    assert samples == ocp_configs.input_order(c, img)
    assert ocp.from_input_order(c, samples) == img
    with pytest.raises(ValueError):
        ocp.from_input_order(c, samples[1:])


def test_raw_image_files_are_read_in_input_order():
    c = CONFIGS["c05_near_rel_bi4_signed"]        # 14-bit signed samples in 16-bit containers
    samples = ocp.input_order(c, ocp_configs.make_image(c))
    data = b"".join(s.to_bytes(2, "little", signed=True) for s in samples)
    assert ocp.read_raw(c, data) == samples
    with pytest.raises(ValueError):
        ocp.read_raw(c, data[:-2])


def test_samples_are_big_endian_containers_in_whole_sdus():
    c12 = dict(CONFIGS["c05_near_rel_bi4_signed"], large_d=12)
    assert ocp.sample_bytes(c12) == 2
    assert ocp.encode_samples(c12, [-1, 2047, -2048, 5]) == bytes.fromhex("ffff07fff8000005")
    c24 = dict(CONFIGS["c01_lossless_bip"], large_d=24)
    assert ocp.sample_bytes(c24) == 3 and ocp.encode_samples(c24, [0x123456]) == bytes.fromhex("123456")
    data = bytes(range(20))
    assert ocp.split_sdus(c24, data, 8) == [data[0:6], data[6:12], data[12:18], data[18:20]]
    with pytest.raises(ValueError):
        ocp.split_sdus(c24, data, 2)


# --- compressed images ----------------------------------------------------------------------------------------------
def test_header_problems_name_the_differing_field():
    cfg = LAB_LIKE[0]
    data = ocp.compress(cfg, ocp.test_image(cfg, "scene", 1))
    assert ocp.header_problems(data, cfg) == []
    other = dict(cfg, large_n_y=cfg["large_n_y"] + 1)
    assert any(p.startswith("large_n_y 9, expected 10") for p in ocp.header_problems(data, other))
    assert ocp.header_problems(b"\xff\x01", cfg) == ["header differs from byte 0 on and does not parse"]


def test_an_aborted_image_is_the_start_of_the_model_output_of_the_samples_taken():
    cfg = LAB_LIKE[1]                              # BSQ, B = 4
    samples = ocp.input_order(cfg, ocp.test_image(cfg, "scene", 1))
    taken = samples[:500]
    full = ocp.compress(cfg, ocp.from_input_order(cfg, taken + [0] * (len(samples) - 500)))
    b = cfg["large_b"]
    good = full[:400] + bytes(b)
    assert ocp.aborted_problems(good, cfg, taken) == []
    bad = bytearray(good)
    bad[300] ^= 0x10
    assert ocp.aborted_problems(bytes(bad), cfg, taken) == ["byte 300 differs from the codewords of the samples taken"]
    assert "not a multiple" in ocp.aborted_problems(good + b"\x00", cfg, taken)[0]
    assert ocp.aborted_problems(b"\xff" + good[1:], cfg, taken)[0].startswith("user_defined_data 255")


@pytest.mark.skipif(not hasattr(ocp.ocp_model, "compress_aborted"), reason="the model gives no aborted image")
@pytest.mark.parametrize("cfg", LAB_LIKE, ids=["bip", "bsq", "bil"])
def test_the_aborted_image_is_the_compressed_image_of_the_samples_taken(cfg):
    samples = ocp.input_order(cfg, ocp.test_image(cfg, "scene", 2))
    taken = samples[:len(samples) // 3]
    aborted = ocp.aborted_image(cfg, taken)
    # the header of the complete image, a multiple of B bytes, and the codewords that every image with these first
    # samples starts with
    assert ocp.header_problems(aborted, cfg) == [] and len(aborted) % cfg["large_b"] == 0
    assert ocp.aborted_problems(aborted, cfg, taken) == []
    # the samples after those taken do not matter, and all samples give the compressed image of the image
    other = ocp.ocp_model.compress_aborted(cfg, ocp.from_input_order(cfg, samples), len(taken))
    assert other == aborted
    assert ocp.aborted_image(cfg, samples) == ocp.compress(cfg, ocp.from_input_order(cfg, samples))


# --- channel ----------------------------------------------------------------------------------------------------------
# OpenSPP receive port: User Application in bits 21:14, Data Loss Indicator bit 12, receive buffer error bit 13
MARKS = UserMarks(last_mask=0x3FC000, last_value=0x004000, error_mask=0x003000)
END, DLI, DED = 0x004000, 0x001000, 0x002000
CFG = small(CONFIGS["c25_tp_bsq_lossless"], 4, 3, 2)        # 24 samples of 2 bytes
SDU_SAMPLES = 5                                              # SDUs of 5 samples: n = 5, the last with 4


def image_sdus(index: int) -> list[bytes]:
    samples = [(1000 * index + 7 * k) % 65536 for k in range(ocp.samples_per_image(CFG))]
    return ocp.split_sdus(CFG, ocp.encode_samples(CFG, samples), 2 * SDU_SAMPLES)


def stream(images: int, faults: dict[tuple[int, int], int] | None = None, lost: set[tuple[int, int]] = frozenset(),
           unmark: set[int] = frozenset(), mark: set[tuple[int, int]] = frozenset()):
    """SDUs (data, user) of images 0 to images - 1; SDU j (from 1) of image k gets the bits faults[(k, j)], is left
    out when (k, j) is in lost (the next SDU then carries the Data Loss Indicator), loses its end mark when it is the
    last SDU of an image in unmark, gets an end mark when in mark."""
    faults = faults or {}
    out, gap = [], False
    for k in range(images):
        sdus = image_sdus(k)
        for j, data in enumerate(sdus, 1):
            if (k, j) in lost:
                gap = True
                continue
            user = (END if (j == len(sdus) and k not in unmark) or (k, j) in mark else 0) | faults.get((k, j), 0)
            if gap:
                user |= DLI
                gap = False
            out.append((data, user))
    return out


def run_channel(sdus):
    channel = Channel(CFG, MARKS)
    images = [o for data, user in sdus for o in channel.sdu(data, user)]
    return images, channel.counts


def samples_of(k: int, sdus: range) -> tuple[int, ...]:
    """The samples of the SDUs `sdus` (from 1) of image k."""
    all_samples = [(1000 * k + 7 * i) % 65536 for i in range(ocp.samples_per_image(CFG))]
    return tuple(s for j in sdus for s in all_samples[SDU_SAMPLES * (j - 1):SDU_SAMPLES * j])


def test_images_without_fault():
    images, counts = run_channel(stream(3))
    assert [(o.complete, o.error) for o in images] == [(True, False)] * 3
    assert [o.samples for o in images] == [samples_of(k, range(1, 6)) for k in range(3)]
    assert counts == Counts(images_done=3)


@pytest.mark.parametrize("j", [1, 3])
def test_faulty_sdu_inside_an_image(j):
    images, counts = run_channel(stream(3, faults={(1, j): DED}))
    assert images[1] == ocp.Output(samples_of(1, range(1, j + 1)), complete=False, error=True)
    assert images[2] == ocp.Output(samples_of(2, range(1, 6)), complete=True, error=False)
    assert counts == Counts(images_done=2, images_aborted=1, sdu_errors=1, sdus_discarded=5 - j)


def test_faulty_last_sdu():
    images, counts = run_channel(stream(3, faults={(1, 5): DED}))
    assert images[1] == ocp.Output(samples_of(1, range(1, 6)), complete=True, error=True)
    assert counts == Counts(images_done=2, images_aborted=1, sdu_errors=1)


def test_sdu_of_a_partial_sample():
    sdus = stream(2)
    data, user = sdus[1]
    sdus[1] = (data[:-1], user)                  # the last sample of SDU 2 of image 0 loses a byte
    images, counts = run_channel(sdus)
    expected = samples_of(0, range(1, 2)) + samples_of(0, range(2, 3))[:-1] + (data[-2] << 8,)
    assert images[0] == ocp.Output(expected, complete=False, error=True)
    assert counts == Counts(images_done=1, images_aborted=1, sdu_errors=1, sdus_discarded=3)


@pytest.mark.parametrize("j", [2, 4])
def test_case_a_lost_sdu_inside_an_image(j):
    images, counts = run_channel(stream(3, lost={(1, j)}))
    taken = samples_of(1, range(1, j)) + samples_of(1, range(j + 1, j + 2))
    assert images[1] == ocp.Output(taken, complete=False, error=True)
    assert images[2] == ocp.Output(samples_of(2, range(1, 6)), complete=True, error=False)
    assert counts == Counts(images_done=2, images_aborted=1, sdu_errors=1, sdus_discarded=5 - j - 1)


def test_case_b_lost_end_sdu():
    # SDU 5 of image 1 (4 samples) is lost; SDU 1 of image 2 (5 samples) completes image 1 with its first 4 samples,
    # the core discards its last sample, the adapter SDUs 2 to 5 of image 2
    images, counts = run_channel(stream(4, lost={(1, 5)}))
    assert len(images) == 3
    assert images[1] == ocp.Output(samples_of(1, range(1, 5)) + samples_of(2, range(1, 2))[:4], complete=True,
                                   error=True)
    assert images[2] == ocp.Output(samples_of(3, range(1, 6)), complete=True, error=False)
    assert counts == Counts(images_done=2, images_aborted=1, sdu_errors=1, sdus_discarded=4)


def test_end_mark_too_early_and_missing():
    images, counts = run_channel(stream(3, mark={(0, 2)}))
    assert [(len(o.samples), o.complete, o.error) for o in images] == [(10, False, True), (14, False, True),
                                                                       (24, True, False), (24, True, False)]
    assert counts == Counts(images_done=2, images_aborted=2)
    images, counts = run_channel(stream(3, unmark={0}))
    assert [(o.complete, o.error) for o in images] == [(True, True), (True, False)]
    assert images[1].samples == samples_of(2, range(1, 6))
    assert counts == Counts(images_done=1, images_aborted=1)


# --- status interface -----------------------------------------------------------------------------------------------
OCP_REGS = RegisterMap(ocp.TOOLS.parent / "sw" / "ocp_regs.h", "OCP")
MIB_WORDS = json.loads((ocp.TOOLS / "ocp_mib_words.json").read_text(encoding="utf-8"))
# the configurations of the testbench of OpenCompact, those with the runtime configuration and the runtime parameters
# included
MIB_CONFIGS = ocp_configs.ALL | ocp_configs.RUNTIME | ocp_configs.RUNTIME_PARAMS


def test_the_mib_words_cover_every_configuration():
    assert sorted(MIB_WORDS) == sorted(MIB_CONFIGS)


@pytest.mark.parametrize("name", sorted(MIB_WORDS))
def test_mib_words_are_those_that_the_opencompact_testbench_reads(name):
    # the file lists the generics that the words depend on; the others (error limits, tables) come from the
    # configuration
    entry = MIB_WORDS[name]
    generics = ocp_generics.generics(MIB_CONFIGS[name]) | entry["generics"]
    expected = {register: int(word, 16) for register, word in entry["words"].items()}
    assert expected.pop("ID") == OCP_REGS.REGMAP_ID
    assert ocp.mib_words(OCP_REGS, generics) == expected


# --- runtime configuration ------------------------------------------------------------------------------------------
BOTH = ocp_configs.RUNTIME["rc04_bi_m4_sample_both"]       # BI, M = 4, absolute and relative error limits
COLUMN = ocp_configs.RUNTIME["rc05_bsq_column_hybrid"]     # BSQ, lossless, images of width 1
ImageConfig = ocp.ImageConfig


@pytest.mark.parametrize("cfg, values, accepted", [
    (BOTH, ImageConfig(16, 8, 12, 31, 511), True),             # the maxima and the largest error limit values
    (BOTH, ImageConfig(2, 1, 1, 0, 0), True),
    (BOTH, ImageConfig(1, 1, 1), False),                       # full prediction: images at least two samples wide
    (BOTH, ImageConfig(17, 8, 12), False),
    (BOTH, ImageConfig(16, 0, 12), False),
    (BOTH, ImageConfig(16, 8, 13), False),
    (BOTH, ImageConfig(16, 8, 12, 32, 0), False),              # 2^DA
    (BOTH, ImageConfig(16, 8, 12, 0, 512), False),             # 2^DR
    (COLUMN, ImageConfig(1, 1, 1), True),
    (COLUMN, ImageConfig(8, 8, 5, 0xFFFF, 0xFFFF), True),      # the values of methods not in use are not checked
    (COLUMN, ImageConfig(0, 8, 5), False),
])
def test_commit_accepts_the_ranges_of_the_user_guide(cfg, values, accepted):
    assert ocp.commit_accepts(cfg, values) == accepted


def test_the_refused_values_are_each_one_value_out_of_range():
    valid = ImageConfig(2, 1, 1, 0, 0)
    refused = ocp.refused_values(BOTH, valid)
    assert [name for name, _ in refused] == ["NEXT_X", "NEXT_X", "NEXT_Y", "NEXT_Z", "NEXT_LIMITS", "NEXT_LIMITS"]
    assert all(not ocp.commit_accepts(BOTH, v) for _, v in refused)
    assert [name for name, _ in ocp.refused_values(COLUMN, ImageConfig(1, 1, 1))] == ["NEXT_X", "NEXT_Y", "NEXT_Z"]


def test_the_image_configuration_is_that_of_the_runtime_testbench_of_opencompact():
    values = ImageConfig(5, 3, 2, 7, 100)
    cfg = ocp.image_config(BOTH, values)
    want = ocp_configs.runtime_image_cfg(BOTH, 5, 3, 2, 7, 100, 0)
    assert cfg == {k: v for k, v in want.items() if k != "seed"} | {"image": BOTH["image"]}
    assert (cfg["large_m"], cfg["a_vector"], cfg["r_vector"]) == (2, [7], [100])      # BI: M = min(M, NZ)
    assert ocp.ocp_model.header(cfg)[:len(ocp.ocp_model.header(BOTH))] != ocp.ocp_model.header(BOTH)


def test_a_committed_configuration_applies_from_the_next_image_start():
    cfg = small(CONFIGS["c25_tp_bsq_lossless"], 4, 3, 2)
    channel = Channel(cfg, MARKS, runtime=True)
    values = ImageConfig(2, 1, 2)
    first = [(1000 + 7 * k) % 65536 for k in range(ocp.samples_per_image(cfg))]
    data = ocp.encode_samples(cfg, first)
    # the image in progress keeps its configuration: COMMIT after its first SDU
    out = channel.sdu(data[:4], 0)
    assert channel.commit(values) and channel.pending and not out
    out = channel.sdu(data[4:], END)
    assert [o.cfg for o in out] == [cfg] and channel.pending
    out = channel.sdu(ocp.encode_samples(cfg, [1, 2, 3, 4]), END)
    assert not channel.pending and [o.complete for o in out] == [True]
    assert out[0].cfg == channel.image_cfg == ocp.image_config(cfg, values) and out[0].samples == (1, 2, 3, 4)


def test_a_refused_commit_changes_nothing():
    channel = Channel(BOTH, MARKS, runtime=True)
    assert channel.commit(ImageConfig(4, 2, 3, 1, 2))
    pending = channel.next_cfg
    assert not channel.commit(ImageConfig(1, 2, 3))
    assert channel.next_cfg is pending
    with pytest.raises(ValueError):
        Channel(BOTH, MARKS).commit(ImageConfig(4, 2, 3))


def generics_of(name: str, runtime: bool) -> dict:
    """The generics of an OpenCompact channel with a configuration of the runtime testbench of OpenCompact."""
    return ocp_generics.generics(MIB_CONFIGS[name]) | MIB_WORDS[name]["generics"] | {"RuntimeConfig_g": runtime}


def test_the_registers_of_the_runtime_configuration_after_a_reset():
    words = ocp.runtime_words(OCP_REGS, generics_of("rc04_bi_m4_sample_both", True))
    assert words == {"CONTROL": 0, "STATUS": 0, "NEXT_X": 16, "NEXT_Y": 8, "NEXT_Z": 12, "NEXT_LIMITS": (64 << 16) | 4,
                     "NEXT_SAMPLES": 0, "NEXT_ORDER": 0, "NEXT_WEIGHTS": 0, "NEXT_ADAPTIVE": 0, "NEXT_BLOCK": 0}
    assert set(ocp.runtime_words(OCP_REGS, generics_of("rc04_bi_m4_sample_both", False)).values()) == {0}


def test_the_model_of_the_runtime_registers():
    g = generics_of("rc04_bi_m4_sample_both", True)
    cfg = ocp.model_config(g)
    regs = ocp.RuntimeRegisters(OCP_REGS, g, Channel(cfg, MARKS, runtime=True))
    status = lambda: (OCP_REGS.get("STATUS", "CFG_ERROR", regs.read("STATUS")),  # noqa: E731
                      OCP_REGS.get("STATUS", "CFG_PENDING", regs.read("STATUS")))
    for name, word in ocp.next_words(OCP_REGS, ImageConfig(3, 2, 1, 5, 6)).items():
        regs.write(name, word)
    assert ocp.next_values(OCP_REGS, regs.words) == ImageConfig(3, 2, 1, 5, 6)
    regs.write("CONTROL", 1)
    assert status() == (0, 1) and regs.read("CONTROL") == 0
    regs.write("NEXT_X", 1)
    regs.write("CONTROL", 1)
    assert status() == (1, 1) and regs.read("NEXT_X") == 1
    regs.write("STATUS", OCP_REGS.field("STATUS", "CFG_ERROR", 1))
    assert status() == (0, 1)
    # without the runtime configuration the registers read zero and ignore writes
    fixed = ocp.RuntimeRegisters(OCP_REGS, generics_of("rc04_bi_m4_sample_both", False), Channel(cfg, MARKS))
    fixed.write("NEXT_X", 3)
    fixed.write("CONTROL", 1)
    assert [fixed.read(n) for n in ocp.RUNTIME_REGISTERS] == [0] * 6


def test_the_status_interface_shows_the_values_taken():
    g = generics_of("rc04_bi_m4_sample_both", True)
    cfg = ocp.model_config(g)
    image = ocp.image_config(cfg, ImageConfig(5, 3, 2, 7, 100))
    words = ocp.mib_words(OCP_REGS, g, image)
    reset = ocp.mib_words(OCP_REGS, g)
    assert [OCP_REGS.get(n, "SIZE", words[n]) for n in ("IMAGE_X", "IMAGE_Y", "IMAGE_Z")] == [5, 3, 2]
    assert OCP_REGS.get("ORDER", "SUB_FRAME_DEPTH", words["ORDER"]) == 2
    assert words["LIMITS"] == (100 << 16) | 7 and reset["LIMITS"] == (64 << 16) | 4
    assert {n: w for n, w in words.items() if n not in ocp.TAKEN_REGISTERS} == {
        n: w for n, w in reset.items() if n not in ocp.TAKEN_REGISTERS}


# --- runtime parameters (OpenCompact 0.3.0) ------------------------------------------------------------------------
PARAMS = ocp_configs.RUNTIME_PARAMS["rp01_bi_m4_sample"]   # BI, M up to 4, sample-adaptive coder with a constant K
HYBRID = ocp_configs.RUNTIME_PARAMS["rp02_bi_mnz_hybrid"]  # hybrid coder whose initial accumulators raise gamma0
BLOCK = ocp_configs.RUNTIME_PARAMS["rp03_bsq_block"]       # BSQ, block-adaptive coder, r up to 4096


def params(cfg, **changed):
    v = dict(zip(ocp.PARAM_NAMES, ocp.build_params(cfg))) | changed
    return tuple(v[n] for n in ocp.PARAM_NAMES)


def test_the_parameters_of_the_generics_are_valid_and_complete():
    for cfg in (PARAMS, HYBRID, BLOCK):
        values = ocp.build_values(cfg, params=True)
        assert ocp.commit_accepts(cfg, values)
        assert ocp.image_config(cfg, values) == ocp.image_config(cfg, ocp.build_values(cfg))
    assert ocp.used_params(BLOCK) == ["user", "tinc", "vmin", "vmax", "r"]
    assert dict(zip(ocp.PARAM_NAMES, ocp.build_params(BLOCK)))["g0"] == 0


@pytest.mark.parametrize("cfg, changed, accepted", [
    (PARAMS, dict(tinc=4), True), (PARAMS, dict(tinc=3), False), (PARAMS, dict(tinc=12), False),
    (PARAMS, dict(vmin=-6, vmax=-6), True), (PARAMS, dict(vmin=5, vmax=4), False), (PARAMS, dict(vmin=-7), False),
    (PARAMS, dict(m=4), True), (PARAMS, dict(m=5), False), (PARAMS, dict(m=0), False),
    (PARAMS, dict(g0=5, gstar=6), True), (PARAMS, dict(g0=5, gstar=5), False), (PARAMS, dict(umax=33), False),
    (PARAMS, dict(k=14), True), (PARAMS, dict(k=15), False),
    (PARAMS, dict(r=4097), True),                   # not in use: not checked
    (HYBRID, dict(g0=1), False),                    # below the smallest gamma0 of the initial accumulators
    (BLOCK, dict(r=4096), True), (BLOCK, dict(r=0), False), (BLOCK, dict(g0=0, umax=0), True),
])
def test_commit_checks_the_runtime_parameters_in_use(cfg, changed, accepted):
    values = ocp.build_values(cfg, params=True)
    assert ocp.commit_accepts(cfg, replace(values, params=params(cfg, **changed))) == accepted


def test_m_is_checked_against_the_nz_of_the_same_commit():
    values = replace(ocp.build_values(PARAMS, params=True), nz=2)
    assert ocp.commit_accepts(PARAMS, replace(values, params=params(PARAMS, m=2)))
    assert not ocp.commit_accepts(PARAMS, replace(values, params=params(PARAMS, m=3)))


def test_the_refused_parameters_are_refused():
    valid = replace(ocp.build_values(PARAMS, params=True), nz=1)
    refused = ocp.refused_values(PARAMS, replace(valid, params=params(PARAMS, m=1)))
    names = [name for name, _ in refused]
    assert {"NEXT_WEIGHTS", "NEXT_ADAPTIVE", "NEXT_ORDER"} <= set(names)
    assert all(not ocp.commit_accepts(PARAMS, v) for _, v in refused)


def test_the_image_configuration_takes_the_parameters():
    values = replace(ocp.build_values(PARAMS, params=True), nx=4, ny=3, nz=2,
                     params=params(PARAMS, user=7, m=2, tinc=5, vmin=-2, vmax=3, umax=20, g0=2, gstar=6, k=3))
    cfg = ocp.image_config(PARAMS, values)
    assert (cfg["user_defined_data"], cfg["large_m"], cfg["log_two_t_inc"], cfg["v_min"], cfg["v_max"]) == (
        7, 2, 5, -2, 3)
    assert (cfg["large_u_max"], cfg["gamma_0"], cfg["gamma_star"], cfg["k_prime_prime_vector"]) == (20, 2, 6, [3])


def test_the_parameter_registers_round_trip():
    g = generics_of("rp01_bi_m4_sample", True) | {"RuntimeParams_g": True}
    values = replace(ocp.build_values(PARAMS, params=True),
                     params=params(PARAMS, user=255, m=1, tinc=11, vmin=-6, vmax=9, umax=8, g0=1, gstar=4, k=0))
    words = ocp.next_words(OCP_REGS, values)
    assert set(ocp.PARAM_REGISTERS) <= set(words)
    assert ocp.next_values(OCP_REGS, words, params=True) == values
    reset = ocp.runtime_words(OCP_REGS, g)
    assert ocp.next_values(OCP_REGS, reset, params=True) == ocp.build_values(PARAMS, params=True)


def test_the_model_of_the_parameter_registers():
    g = generics_of("rp03_bsq_block", True) | {"RuntimeParams_g": True}
    cfg = ocp.model_config(g)
    regs = ocp.RuntimeRegisters(OCP_REGS, g, Channel(cfg, MARKS, runtime=True))
    # the fields of the parameters that the block-adaptive coder does not use read zero
    regs.write("NEXT_ADAPTIVE", 0xFFFFFFFF)
    assert regs.read("NEXT_ADAPTIVE") == 0
    regs.write("NEXT_BLOCK", OCP_REGS.field("NEXT_BLOCK", "REF_INTERVAL", 0))
    regs.write("CONTROL", 1)
    assert OCP_REGS.get("STATUS", "CFG_ERROR", regs.read("STATUS")) == 1        # r = 0 is refused
    regs.write("STATUS", OCP_REGS.field("STATUS", "CFG_ERROR", 1))
    regs.write("NEXT_BLOCK", OCP_REGS.field("NEXT_BLOCK", "REF_INTERVAL", 7))
    regs.write("CONTROL", 1)
    assert OCP_REGS.get("STATUS", "CFG_PENDING", regs.read("STATUS")) == 1
    assert regs.channel.next_cfg["r"] == 7
    # without RuntimeParams_g the registers read zero and ignore writes
    fixed = ocp.RuntimeRegisters(OCP_REGS, g | {"RuntimeParams_g": False}, Channel(cfg, MARKS, runtime=True))
    fixed.write("NEXT_BLOCK", OCP_REGS.field("NEXT_BLOCK", "REF_INTERVAL", 7))
    assert [fixed.read(n) for n in ocp.PARAM_REGISTERS] == [0] * 5


def test_the_status_interface_shows_the_parameters_taken():
    g = generics_of("rp01_bi_m4_sample", True) | {"RuntimeParams_g": True}
    values = replace(ocp.build_values(PARAMS, params=True),
                     params=params(PARAMS, user=9, m=3, tinc=6, vmin=1, vmax=2, umax=10, g0=3, gstar=7, k=5))
    words = ocp.mib_words(OCP_REGS, g, ocp.image_config(PARAMS, values))
    assert OCP_REGS.get("SAMPLES", "USER_DATA", words["SAMPLES"]) == 9
    assert OCP_REGS.get("ORDER", "SUB_FRAME_DEPTH", words["ORDER"]) == 3
    assert (OCP_REGS.get("WEIGHTS", "UPDATE_INTERVAL", words["WEIGHTS"]), OCP_REGS.get("WEIGHTS", "VMIN",
            words["WEIGHTS"]), OCP_REGS.get("WEIGHTS", "VMAX", words["WEIGHTS"])) == (6, 1, 2)
    assert [OCP_REGS.get("ADAPTIVE", f, words["ADAPTIVE"]) for f in
            ("UNARY_LIMIT", "INITIAL_COUNT", "RESCALE_SIZE", "ACCU_INIT")] == [10, 3, 7, 5]
    assert OCP_REGS.get("GENERICS", "RUNTIME_PARAMS", words["GENERICS"]) == 1


# --- decoding check (decoder of the model of OpenCompact) ----------------------------------------------------------
needs_decoder = pytest.mark.skipif(not ocp.decoder_available(), reason="the pinned OpenCompact has no decoder")


@needs_decoder
@pytest.mark.parametrize("name", ["c01_lossless_bip", "c26_tp_bil_near_abs"])
def test_a_compressed_image_decodes_to_its_samples(name):
    cfg = small(CONFIGS[name], 6, 4, 5)
    samples = [(37 * k + 11) % 4096 for k in range(ocp.samples_per_image(cfg))]
    data = ocp.compress(cfg, ocp.from_input_order(cfg, samples))
    assert ocp.decode_problems(cfg, data, samples) == []


@needs_decoder
def test_the_decoding_check_finds_a_wrong_sample_and_a_broken_image():
    cfg = small(CONFIGS["c01_lossless_bip"], 6, 4, 5)
    samples = [(37 * k + 11) % 4096 for k in range(ocp.samples_per_image(cfg))]
    data = ocp.compress(cfg, ocp.from_input_order(cfg, samples))
    wrong = samples[:7] + [samples[7] + 1] + samples[8:]
    assert "decoded sample" in ocp.decode_problems(cfg, data, wrong)[0]
    assert ocp.decode_problems(cfg, data[:len(data) // 2], samples)
