"""Unit tests of refsys.ocp: the model configuration from the generics of ocp_core, the test images, the samples as
bytes, the header check, the model of an OpenCompact channel against the error cases of its specification and the
words of its status interface."""

import copy
import json

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
# the configurations of the testbench of OpenCompact, those with the runtime configuration included
MIB_CONFIGS = ocp_configs.ALL | ocp_configs.RUNTIME


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
