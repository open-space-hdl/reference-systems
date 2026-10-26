"""Images of OpenCompact (CCSDS 123.0-B-2) for the system tests: the configuration of the reference model from the
generics of an ocp_core instance, seeded test images, their samples in the input order of the compressor and as the
bytes of Space Packets, the check of the header of a compressed image, the image framing of the core and its runtime
configuration.

The expected compressed image of a test image is that of the reference model of OpenCompact, tools/ocp_model.py of
the submodule cores/opencompact, which is byte-identical to the CNES reference software on the configurations of
OpenCompact (OpenCompact README). A configuration of the model is a dict with the parameter names of
ocp_model.default_config(); an image is img[z][y][x].
"""

import math
import random
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "cores" / "opencompact" / "tools"
sys.path.insert(0, str(TOOLS))

import ocp_configs  # noqa: E402
import ocp_model  # noqa: E402

CODERS = {"SAMPLE_ADAPTIVE": 0, "HYBRID": 1, "BLOCK_ADAPTIVE": 2}
FIDELITY = {"LOSSLESS": 0, "ABSOLUTE": 1, "RELATIVE": 2, "ABSOLUTE_RELATIVE": 3}
LOCAL_SUMS = {"WIDE_NEIGHBOR": 0, "NARROW_NEIGHBOR": 1, "WIDE_COLUMN": 2, "NARROW_COLUMN": 3}

KINDS = ("scene", "stripes", "noise", "flat", "spikes")

# Bytes of the essential subpart of the header (CCSDS 5.3.2.2); the supplementary information tables follow it, their
# number is in the low four bits of its last byte
ESSENTIAL_BYTES = 12


# ------------------------------------------------------------------------------------------------
# Configuration of the model from the generics of ocp_core
# ------------------------------------------------------------------------------------------------
_NAMED = re.compile(r"^\s*(\w+)\s*=>\s*(.+?)\s*$")


def vector(value) -> list[int]:
    """The integers of a vector generic: a list, an integer, a comma-separated string ("1,2,3") or a VHDL aggregate
    ("(0 => 4)", "(1, 2, 3)", "(others => 0)"). In OpenCompact the last element of a vector repeats for the remaining
    bands (user guide, generics), so "(others => v)" is the vector (v)."""
    if isinstance(value, bool):
        raise ValueError(f"vector generic {value!r}")
    if isinstance(value, int):
        return [value]
    if isinstance(value, (list, tuple)):
        return [int(v) for v in value]
    text = str(value).strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    positional, named, others = [], {}, None
    for item in (t for t in text.split(",") if t.strip()):
        if m := _NAMED.match(item):
            if m[1].lower() == "others":
                others = int(m[2], 0)
            else:
                named[int(m[1], 0)] = int(m[2], 0)
        else:
            positional.append(int(item.strip(), 0))
    if positional and (named or others is not None):
        raise ValueError(f"vector generic {value!r} mixes positional and named elements")
    if named:
        if sorted(named) != list(range(len(named))):
            raise ValueError(f"vector generic {value!r} does not name the elements 0 to {len(named) - 1}")
        positional = [named[k] for k in range(len(named))]
    if others is not None:
        positional.append(others)
    if not positional:
        raise ValueError(f"vector generic {value!r} has no element")
    return positional


def flag(value) -> int:
    """A boolean generic as 0 or 1 (JSON boolean or "true"/"false")."""
    if isinstance(value, str):
        if value.lower() not in ("true", "false"):
            raise ValueError(f"boolean generic {value!r}")
        return int(value.lower() == "true")
    return int(bool(value))


def sup_tables(count: int, data: str, nx: int, ny: int, nz: int) -> list[dict]:
    """The supplementary information tables of the model from SupTableCount_g and SupTableData_g, the hexadecimal
    Supplementary Information Tables subpart (CCSDS 5.3.2.3), read with the header parser of the model; the size of
    a table depends on the image dimensions."""
    if count == 0:
        return []
    raw = bytes.fromhex(str(data).strip().removeprefix("x").strip('"'))
    bare = ocp_model.header(dict(ocp_model.default_config(), large_n_x=nx, large_n_y=ny, large_n_z=nz))
    essential = bare[:ESSENTIAL_BYTES - 1] + bytes([(bare[ESSENTIAL_BYTES - 1] & 0xF0) | count])
    cfg, _ = ocp_model.parse_header(essential + raw + bare[ESSENTIAL_BYTES:])
    return cfg["supplementary_information_tables"]


def model_config(generics: dict) -> dict:
    """The configuration of the reference model that an ocp_core with these generics compresses with: the inverse of
    tools/ocp_generics.py of OpenCompact. generics holds every generic of the instance (the resolved generics of a
    Composer file, default values included)."""
    g = generics
    bsq = g["EncodingOrder_g"] == "BSQ"
    theta = g["SampleRepResolution_g"]
    high = vector(g["HybridAccuInitHigh_g"])
    low = vector(g["HybridAccuInit_g"])
    high += [high[-1]] * (len(low) - len(high))
    cfg = ocp_model.default_config()
    cfg.update({
        "user_defined_data": g["UserData_g"],
        "large_n_x": g["XSize_g"], "large_n_y": g["YSize_g"], "large_n_z": g["ZSize_g"],
        "sample_type": flag(g["SignedSamples_g"]), "large_d": g["DynamicRange_g"],
        "sample_encoding_order": int(bsq), "large_m": 0 if bsq else g["SubFrameDepth_g"],
        "large_b": g["OutputWordSize_g"],
        "entropy_coder_type": CODERS[g["EntropyCoder_g"]],
        "quantizer_fidelity_control_method": FIDELITY[g["Fidelity_g"]],
        "supplementary_information_tables": sup_tables(g["SupTableCount_g"], g["SupTableData_g"], g["XSize_g"],
                                                       g["YSize_g"], g["ZSize_g"]),
        "large_p": g["PredictionBands_g"], "prediction_mode": int(g["PredictionMode_g"] == "REDUCED"),
        "local_sum_type": LOCAL_SUMS[g["LocalSum_g"]],
        "large_r": g["RegisterSize_g"], "large_omega": g["WeightResolution_g"],
        "log_two_t_inc": g["WeightUpdateInterval_g"], "v_min": g["WeightUpdateVmin_g"],
        "v_max": g["WeightUpdateVmax_g"],
        "weight_exponent_offset_flag": flag(g["WeightExpOffsetsUsed_g"]),
        "weight_exponent_offset_table_flag": flag(g["WeightExpOffsetTable_g"]),
        "zeta_vector": vector(g["WeightExpOffsets_g"]),
        "weight_initialization_method": flag(g["CustomWeights_g"]),
        "weight_initialization_table_flag": flag(g["WeightInitTable_g"]),
        "large_q": g["WeightInitResolution_g"],
        "large_lambda_vector": vector(g["WeightInitValues_g"]),
        "periodic_updating_flag": flag(g["PeriodicUpdate_g"]), "u": g["UpdatePeriodExp_g"],
        "absolute_error_limit_assignment_method": flag(g["AbsBandDependent_g"]),
        "large_d_large_a": g["AbsBitDepth_g"], "a_vector": vector(g["AbsErrorLimits_g"]),
        "relative_error_limit_assignment_method": flag(g["RelBandDependent_g"]),
        "large_d_large_r": g["RelBitDepth_g"], "r_vector": vector(g["RelErrorLimits_g"]),
        # the core writes the Sample Representative subpart when Theta is above 0 (user guide, generics)
        "sample_representative_flag": int(theta > 0), "large_theta": theta,
        "band_varying_damping_flag": flag(g["DampingBandVarying_g"]), "damping_table_flag": flag(g["DampingTable_g"]),
        "phi_vector": vector(g["Damping_g"]),
        "band_varying_offset_flag": flag(g["OffsetBandVarying_g"]), "offset_table_flag": flag(g["OffsetTable_g"]),
        "psi_vector": vector(g["Offset_g"]),
        "large_u_max": g["UnaryLimit_g"], "gamma_star": g["RescaleCounterSize_g"], "gamma_0": g["InitialCountExp_g"],
        "band_varying_k_flag": flag(g["AccuInitBandVarying_g"]),
        "accumulator_initialization_table_flag": flag(g["AccuInitTable_g"]),
        "k_prime_prime_vector": vector(g["AccuInit_g"]),
        "hybrid_initial_accumulator": [lo | (hi << 31) for lo, hi in zip(low, high)],
        "large_j": g["BlockSize_g"], "restricted_code_options_flag": flag(g["RestrictedCodes_g"]),
        "r": g["RefInterval_g"],
    })
    return cfg


def samples_per_image(cfg: dict) -> int:
    return cfg["large_n_x"] * cfg["large_n_y"] * cfg["large_n_z"]


# ------------------------------------------------------------------------------------------------
# Test images
# ------------------------------------------------------------------------------------------------
def sample_range(cfg: dict) -> tuple[int, int]:
    d = cfg["large_d"]
    if cfg["sample_type"]:
        return -(1 << (d - 1)), (1 << (d - 1)) - 1
    return 0, (1 << d) - 1


def test_image(cfg: dict, kind: str, seed: int) -> list[list[list[int]]]:
    """A deterministic test image img[z][y][x] of the dimensions and the sample range of cfg.

    Every kind depends on every bit of the sample address, so that an address fault of a memory of the core changes
    the compressed image: each has a seeded component that differs from sample to sample (sensor noise, uniform
    noise, sparse steps or spikes at seeded positions), and no kind is periodic in an address.

    - scene: three materials with smooth spectra, spatial abundance fields with edges, sensor noise
    - stripes: the scene with a fixed offset per column (detector non-uniformity)
    - noise: uniform noise over the full range, which no predictor compresses (ratio below 1)
    - flat: one level per band with sparse steps of one count at seeded samples (low entropy)
    - spikes: one level per band with spikes of 1 % of the range at seeded samples (one in 1024, at least 16),
      one of them on the last sample
    """
    if kind not in KINDS:
        raise ValueError(f"unknown image kind {kind}; known: {', '.join(KINDS)}")
    nx, ny, nz = cfg["large_n_x"], cfg["large_n_y"], cfg["large_n_z"]
    lo, hi = sample_range(cfg)
    span = hi - lo
    rng = random.Random(f"{kind} {seed}")
    if kind == "noise":
        return [[[rng.randint(lo, hi) for _ in range(nx)] for _ in range(ny)] for _ in range(nz)]
    if kind in ("flat", "spikes"):
        levels = [lo + rng.randint(span // 4, 3 * span // 4) for _ in range(nz)]
        img = [[[levels[z]] * nx for _ in range(ny)] for z in range(nz)]
        n = nx * ny * nz
        if kind == "flat":
            positions = rng.sample(range(n), max(1, n // 32))
            amplitude = 1
        else:
            positions = rng.sample(range(n - 1), min(n, max(16, n // 1024)) - 1) + [n - 1]
            amplitude = max(1, span // 100)
        for p in positions:
            z, rest = divmod(p, nx * ny)
            y, x = divmod(rest, nx)
            step = amplitude if rng.random() < 0.5 else -amplitude
            img[z][y][x] = min(hi, max(lo, img[z][y][x] + step))
        return img
    spectra = []
    for _ in range(3):
        a, b, ph = rng.uniform(0.2, 0.7), rng.uniform(0.05, 0.25), rng.uniform(0, 6.28)
        spectra.append([a + b * math.sin(ph + 3.0 * z / max(nz, 1)) for z in range(nz)])
    stripe = [rng.gauss(0, 0.01) if kind == "stripes" else 0.0 for _ in range(nx)]
    img = [[[0] * nx for _ in range(ny)] for _ in range(nz)]
    for y in range(ny):
        for x in range(nx):
            u = 0.5 + 0.5 * math.sin(0.35 * x + 0.2 * y)
            v = 0.5 + 0.5 * math.cos(0.15 * x - 0.3 * y)
            if (x // 6 + y // 5) % 3 == 0:
                u, v = v, 1.0 - u
            w = max(0.0, 1.0 - u - v * 0.5)
            for z in range(nz):
                val = 0.08 + 0.55 * (u * spectra[0][z] + v * spectra[1][z] + w * spectra[2][z])
                val += stripe[x] * (1 + 0.1 * z) + rng.gauss(0, 0.002)
                img[z][y][x] = min(hi, max(lo, lo + int(round(val * span))))
    return img


def input_order(cfg: dict, img: list[list[list[int]]]) -> list[int]:
    """The samples in the input order of the compressor, which is the encoding order (CCSDS 5.4.2): BSQ band by band,
    BI row by row with groups of M bands per pixel."""
    nx, ny, nz = cfg["large_n_x"], cfg["large_n_y"], cfg["large_n_z"]
    if cfg["sample_encoding_order"] == 1:
        return [s for band in img for row in band for s in row]
    m = cfg["large_m"]
    out = []
    for y in range(ny):
        for first in range(0, nz, m):
            bands = range(first, min(first + m, nz))
            for x in range(nx):
                out += [img[z][y][x] for z in bands]
    return out


def from_input_order(cfg: dict, samples: list[int]) -> list[list[list[int]]]:
    """The image img[z][y][x] of samples in the input order of the compressor (the inverse of input_order)."""
    nx, ny, nz = cfg["large_n_x"], cfg["large_n_y"], cfg["large_n_z"]
    if len(samples) != nx * ny * nz:
        raise ValueError(f"{len(samples)} samples, the image has {nx * ny * nz}")
    img = [[[0] * nx for _ in range(ny)] for _ in range(nz)]
    it = iter(samples)
    if cfg["sample_encoding_order"] == 1:
        for z in range(nz):
            for y in range(ny):
                for x in range(nx):
                    img[z][y][x] = next(it)
        return img
    m = cfg["large_m"]
    for y in range(ny):
        for first in range(0, nz, m):
            bands = range(first, min(first + m, nz))
            for x in range(nx):
                for z in bands:
                    img[z][y][x] = next(it)
    return img


def read_raw(cfg: dict, data: bytes) -> list[int]:
    """The samples of an image.raw file of OpenCompact (tools/golden.py): input order of the compressor,
    little-endian samples of 8, 16 or 32 bits."""
    size = 1 if cfg["large_d"] <= 8 else 2 if cfg["large_d"] <= 16 else 4
    if len(data) != size * samples_per_image(cfg):
        raise ValueError(f"{len(data)} bytes, the image has {samples_per_image(cfg)} samples of {size} bytes")
    return [int.from_bytes(data[i:i + size], "little", signed=bool(cfg["sample_type"]))
            for i in range(0, len(data), size)]


def sample_bytes(cfg: dict) -> int:
    """Bytes of one sample in a Space Packet: the dynamic range rounded up to whole bytes."""
    return (cfg["large_d"] + 7) // 8


def encode_samples(cfg: dict, samples: list[int]) -> bytes:
    """Samples as bytes, every sample big-endian in sample_bytes(cfg) bytes, in two's complement when signed."""
    size, signed = sample_bytes(cfg), bool(cfg["sample_type"])
    return b"".join(s.to_bytes(size, "big", signed=signed) for s in samples)


def split_sdus(cfg: dict, data: bytes, max_bytes: int) -> list[bytes]:
    """The bytes of an image cut into Service Data Units of at most max_bytes bytes, each of whole samples."""
    size = sample_bytes(cfg)
    step = max_bytes - max_bytes % size
    if step <= 0:
        raise ValueError(f"an SDU of {max_bytes} bytes holds no sample of {size} bytes")
    return [data[k:k + step] for k in range(0, len(data), step)]


# ------------------------------------------------------------------------------------------------
# Compressed images
# ------------------------------------------------------------------------------------------------
def compress(cfg: dict, img: list[list[list[int]]]) -> bytes:
    """The compressed image (header, body, fill to a multiple of B bytes) of the reference model."""
    return ocp_model.compress(cfg, img)


# Tables that are not in the header read as zeros, on both sides of the comparison of header_problems
_NO_TABLES = bytes(1 << 16)


def header_problems(data: bytes, cfg: dict) -> list[str]:
    """Differences between the header at the start of a compressed image and the header of the configuration (empty
    when they are byte-identical), as the fields of the header parser of the model where it can parse both."""
    expected = ocp_model.header(cfg)
    if data[:len(expected)] == expected:
        return []
    first = next((i for i, (a, b) in enumerate(zip(data, expected)) if a != b), min(len(data), len(expected)))
    try:
        got, _ = ocp_model.parse_header(data, _NO_TABLES)
        want, _ = ocp_model.parse_header(expected, _NO_TABLES)
    except (IndexError, AssertionError):
        return [f"header differs from byte {first} on and does not parse"]
    fields = [f"{k} {got.get(k)!r}, expected {v!r}" for k, v in want.items() if got.get(k) != v]
    return fields or [f"header differs from byte {first} on"]


def aborted_image(cfg: dict, taken: list[int]) -> bytes | None:
    """The compressed image of an aborted image with these samples taken (in the input order): the header of the
    complete image, the codewords of the samples taken with the end of the image of the entropy coder after the last
    one, and the fill bits (compress_aborted of the model of OpenCompact, CO-FR-03). None with a version of
    OpenCompact whose model does not give it; the bytes are then checked with aborted_problems."""
    if not hasattr(ocp_model, "compress_aborted"):
        return None
    lo, _ = sample_range(cfg)
    # the prediction is causal in the input order: the samples after those taken do not change the codewords
    img = from_input_order(cfg, list(taken) + [lo] * (samples_per_image(cfg) - len(taken)))
    return ocp_model.compress_aborted(cfg, img, len(taken))


def aborted_problems(data: bytes, cfg: dict, taken: list[int]) -> list[str]:
    """Differences between the compressed image of an aborted image and what the core may send for it, where the
    model of OpenCompact does not give the aborted image (aborted_image): its header and a length of a multiple of B
    bytes up to that of the full image. With the sample-adaptive coder also the codewords already produced for the
    samples taken, which are a prefix of the codewords of every image that starts with these samples: the bytes
    before the last word are those of the model for the samples taken, completed with any samples. The hybrid coder
    writes pending codewords of earlier samples at the end of an image and the block-adaptive coder the incomplete
    block, so for them the bytes are not checked."""
    problems = header_problems(data, cfg)
    if problems:
        return problems
    b = cfg["large_b"]
    lo, _ = sample_range(cfg)
    full = compress(cfg, from_input_order(cfg, list(taken) + [lo] * (samples_per_image(cfg) - len(taken))))
    if len(data) % b or len(data) < len(ocp_model.header(cfg)) or len(data) > len(full) + b:
        return [f"{len(data)} bytes: not a multiple of {b} bytes between the header and the full image"]
    if cfg["entropy_coder_type"] != 0:
        return []
    head = len(data) - b
    if data[:head] != full[:head]:
        first = next(i for i, (x, y) in enumerate(zip(data[:head], full)) if x != y)
        return [f"byte {first} differs from the codewords of the samples taken"]
    return []


# ------------------------------------------------------------------------------------------------
# Runtime configuration (RuntimeConfig_g; OpenCompact user guide, sections 4.3 and 4.4) and runtime parameters
# (RuntimeParams_g, section 4.5)
# ------------------------------------------------------------------------------------------------
# The runtime parameters in the order of OpenCompact (ocp_configs.PARAM_NAMES): user-defined data, M, log2 t_inc,
# vmin, vmax, Umax, gamma0, gamma*, K and r
PARAM_NAMES = tuple(ocp_configs.PARAM_NAMES)


@dataclass(frozen=True)
class ImageConfig:
    """The values of the runtime configuration of one image, as in NEXT_X to NEXT_LIMITS: NX, NY, NZ and the
    band-independent absolute and relative error limit values; with the runtime parameters also params, the values
    of NEXT_SAMPLES to NEXT_BLOCK in the order of PARAM_NAMES (0 for a parameter that the configuration does not
    use)."""

    nx: int
    ny: int
    nz: int
    abs_limit: int = 0
    rel_limit: int = 0
    params: tuple[int, ...] | None = None


def runtime_config(generics: dict) -> bool:
    """Whether a channel with these generics has the runtime configuration (RuntimeConfig_g)."""
    return bool(flag(generics.get("RuntimeConfig_g", False)))


def runtime_params(generics: dict) -> bool:
    """Whether a channel with these generics has the runtime parameters (RuntimeParams_g, which needs
    RuntimeConfig_g)."""
    return runtime_config(generics) and bool(flag(generics.get("RuntimeParams_g", False)))


def used_params(cfg: dict) -> list[str]:
    """The runtime parameters that a configuration uses (ocp_configs.params_in_use): the user-defined data and the
    weight update parameters always, M in BI order, Umax, gamma0 and gamma* with the sample-adaptive and the hybrid
    coder, K with the sample-adaptive coder and an accumulator initialisation constant, r with the block-adaptive
    coder. The fields of the others read zero and ignore writes."""
    use = ocp_configs.params_in_use(cfg)
    return (["user", "tinc", "vmin", "vmax"] + ["m"] * use["m"] + ["umax", "g0", "gstar"] * use["adp"]
            + ["k"] * use["k"] + ["r"] * use["r"])


def build_params(cfg: dict) -> tuple[int, ...]:
    """The runtime parameters of the configuration of the generics (0 for a parameter not in use): NEXT_SAMPLES to
    NEXT_BLOCK hold them after a reset."""
    v = dict.fromkeys(PARAM_NAMES, 0)
    v.update(user=cfg["user_defined_data"], m=cfg["large_m"], tinc=cfg["log_two_t_inc"], vmin=cfg["v_min"],
             vmax=cfg["v_max"], umax=cfg["large_u_max"], g0=cfg["gamma_0"], gstar=cfg["gamma_star"],
             k=cfg["k_prime_prime_vector"][0], r=cfg["r"])
    used = used_params(cfg)
    return tuple(v[n] if n in used else 0 for n in PARAM_NAMES)


def build_values(cfg: dict, params: bool = False) -> ImageConfig:
    """The values of the configuration of the generics: the maxima and the first error limit values of the methods in
    use (0 for a method not in use), with params also the runtime parameters; NEXT_X to NEXT_BLOCK and the values
    taken hold them after a reset."""
    fid = cfg["quantizer_fidelity_control_method"]
    return ImageConfig(cfg["large_n_x"], cfg["large_n_y"], cfg["large_n_z"],
                       cfg["a_vector"][0] if fid in (1, 3) else 0, cfg["r_vector"][0] if fid in (2, 3) else 0,
                       build_params(cfg) if params else None)


def commit_accepts(cfg: dict, values: ImageConfig) -> bool:
    """Whether CONTROL.COMMIT of the channel passes these values to the core (user guide 4.3): NX, NY and NZ from 1
    to the maxima of cfg, NX from 2 where an image must be at least two samples wide, the error limit values of the
    methods in use below 2^DA and 2^DR. The value of a method not in use is not checked. A COMMIT that finds a value
    out of range passes nothing."""
    fid = cfg["quantizer_fidelity_control_method"]
    xmin = 1 if ocp_configs.width_one_allowed(cfg) else 2
    accepted = (xmin <= values.nx <= cfg["large_n_x"] and 1 <= values.ny <= cfg["large_n_y"]
                and 1 <= values.nz <= cfg["large_n_z"]
                and (fid not in (1, 3) or values.abs_limit < 1 << cfg["large_d_large_a"])
                and (fid not in (2, 3) or values.rel_limit < 1 << cfg["large_d_large_r"]))
    if not accepted or values.params is None:
        return accepted
    # The runtime parameters in use within their ranges (user guide 4.5), the dependent ones against the values they
    # depend on: vmax from vmin, gamma* above gamma0, M up to NZ. runtime_params_limit of OpenCompact gives the value
    # that the core takes, which equals a value in range.
    driven = dict(zip(PARAM_NAMES, values.params))
    taken = dict(zip(PARAM_NAMES, ocp_configs.runtime_params_limit(cfg, values.nz, values.params)))
    return all(driven[n] == taken[n] for n in used_params(cfg))


def image_config(cfg: dict, values: ImageConfig) -> dict:
    """The configuration of the reference model for an image of these values: runtime_image_cfg of OpenCompact, by
    which its runtime testbench builds the expected images (the dimensions, in BI order M = min(M, NZ), the error
    limit vectors of the methods in use)."""
    out = ocp_configs.runtime_image_cfg(dict(cfg, seed=0), values.nx, values.ny, values.nz, values.abs_limit,
                                        values.rel_limit, 0, params=values.params)
    del out["seed"]
    return out


def runtime_sequence(cfg: dict, n: int, seed: int) -> list[ImageConfig]:
    """n images within the maxima and the bit depths of cfg: the first eight at the bounds of every dimension and
    error limit value, the others seeded at random (runtime_sequence of OpenCompact)."""
    return [ImageConfig(*v) for v in ocp_configs.runtime_sequence(dict(cfg, seed=seed), n)]


def runtime_params_sequence(cfg: dict, n: int, seed: int) -> list[ImageConfig]:
    """n images with their runtime parameters (runtime_params_sequence of OpenCompact): the dimensions of
    runtime_sequence, the parameters of the first two images at the high and the low bound of every range, the
    others seeded at random, and a last image of the maximum dimensions with log2 t_inc 4 and vmin = vmax = -6. The
    values are those that the core takes (0 for a parameter not in use)."""
    return [ImageConfig(*taken, params=tuple(params))
            for _, taken, _, params in ocp_configs.runtime_params_sequence(dict(cfg, seed=seed), n)]


def random_params(cfg: dict, nz: int, rng: random.Random, edge: str | None = None) -> tuple[int, ...]:
    """Valid runtime parameters for an image of nz bands (random_params of OpenCompact): random, or with edge "low"
    or "high" every parameter at that bound of its range; 0 for a parameter not in use."""
    return tuple(ocp_configs.runtime_params_limit(cfg, nz, ocp_configs.random_params(cfg, nz, rng, edge)))


def refused_values(cfg: dict, valid: ImageConfig) -> list[tuple[str, ImageConfig]]:
    """Values that COMMIT refuses, each the valid values with one value out of range, with the register that holds
    it: NX = 1 where an image must be at least two samples wide, NX above the maximum, NY = 0, NZ above the maximum,
    the error limit values 2^DA and 2^DR of the methods in use where the field holds them. With the runtime
    parameters also log2 t_inc 3, vmax below vmin, gamma* not above gamma0, Umax 33, K above min(D - 2, 14), M above
    the NZ of the valid values and r = 0, for the parameters in use."""
    fid = cfg["quantizer_fidelity_control_method"]
    out = [] if ocp_configs.width_one_allowed(cfg) else [("NEXT_X", replace(valid, nx=1))]
    out += [("NEXT_X", replace(valid, nx=cfg["large_n_x"] + 1)), ("NEXT_Y", replace(valid, ny=0)),
            ("NEXT_Z", replace(valid, nz=cfg["large_n_z"] + 1))]
    if fid in (1, 3) and cfg["large_d_large_a"] < 16:
        out.append(("NEXT_LIMITS", replace(valid, abs_limit=1 << cfg["large_d_large_a"])))
    if fid in (2, 3) and cfg["large_d_large_r"] < 16:
        out.append(("NEXT_LIMITS", replace(valid, rel_limit=1 << cfg["large_d_large_r"])))
    if valid.params is None:
        return out
    v = dict(zip(PARAM_NAMES, valid.params))
    used = used_params(cfg)

    def params(**changed) -> ImageConfig:
        return replace(valid, params=tuple((v | changed)[n] for n in PARAM_NAMES))

    out += [("NEXT_WEIGHTS", params(tinc=3)), ("NEXT_WEIGHTS", params(vmin=5, vmax=4))]
    if "g0" in used:
        g0 = min(8, cfg["gamma_star"] - 1)
        out += [("NEXT_ADAPTIVE", params(g0=g0, gstar=g0)), ("NEXT_ADAPTIVE", params(umax=33))]
    if "k" in used:
        out.append(("NEXT_ADAPTIVE", params(k=min(cfg["large_d"] - 2, 14) + 1)))
    if "m" in used and valid.nz < cfg["large_m"]:
        out.append(("NEXT_ORDER", params(m=valid.nz + 1)))
    if "r" in used:
        out.append(("NEXT_BLOCK", params(r=0)))
    return out


def decoder_available() -> bool:
    """Whether the reference model of the pinned OpenCompact has the decoder (ocp_model.decompress, OpenCompact
    0.3.0)."""
    return hasattr(ocp_model, "decompress")


def decode_problems(cfg: dict, data: bytes, samples: list[int]) -> list[str]:
    """Decodes a complete compressed image with the decoder of the reference model and compares it with the image
    that was compressed (samples in the input order of the compressor): equal for lossless compression, every sample
    within the maximum error m_z(t) of the decoder for near-lossless compression. Empty when the image decodes to
    its samples; the decoder takes the per-band values that the header leaves out from cfg."""
    try:
        header, img, max_error = ocp_model.decompress(data, tables=cfg)
    except ocp_model.DecodeError as err:
        return [f"the decoder of the model refuses it: {err}"]
    dims = (header["large_n_x"], header["large_n_y"], header["large_n_z"])
    if dims != (cfg["large_n_x"], cfg["large_n_y"], cfg["large_n_z"]):
        return [f"the header gives {dims[0]} x {dims[1]} x {dims[2]} samples"]
    source = from_input_order(cfg, list(samples))
    worst = None
    for z, band in enumerate(img):
        for y, row in enumerate(band):
            for x, value in enumerate(row):
                error = abs(value - source[z][y][x])
                if error > max_error[z][y][x] and worst is None:
                    worst = (x, y, z, value, source[z][y][x], max_error[z][y][x])
    if worst:
        x, y, z, value, want, bound = worst
        return [f"decoded sample ({x}, {y}, {z}) is {value}, the image has {want}, maximum error {bound}"]
    return []


# ------------------------------------------------------------------------------------------------
# Compression channel: input adapter and image framing of the core
# ------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Output:
    """A compressed image that the core sends: samples, the samples of it that the core took (in input order);
    complete, whether these are all samples of an image (its compressed image is then that of the model of these
    samples; otherwise the image was aborted, see aborted_problems); error, the value of Out_Error on its last word,
    which IMAGES_ABORTED counts (IMAGES_DONE the others); cfg, the configuration of the model for the image (with the
    runtime configuration, that of the values the core took for it)."""

    samples: tuple[int, ...]
    complete: bool
    error: bool
    cfg: dict | None = field(default=None, compare=False)


@dataclass(frozen=True)
class UserMarks:
    """Layout of the user signal of the SDUs for the input adapter of the channel: the end mark (UserLastMask_g,
    UserLastValue_g) and the faulty SDU (UserErrorMask_g)."""

    last_mask: int
    last_value: int
    error_mask: int

    def end(self, user: int) -> bool:
        return user & self.last_mask == self.last_value

    def faulty(self, user: int) -> bool:
        return bool(user & self.error_mask)


@dataclass
class Counts:
    """The image and SDU counters of the status interface of the channel (ocp_mib)."""

    images_done: int = 0
    images_aborted: int = 0
    sdu_errors: int = 0
    sdus_discarded: int = 0


class Channel:
    """The images that an OpenCompact channel (ocp_channel: input adapter, ocp_core with UseInLast_g) makes of a
    stream of SDUs, and its counters, after the specifications of the OpenCompact channel and its input adapter:

    - Every SDU carries samples in containers of sample_bytes(cfg) bytes, most significant byte first, the value in
      the D least significant bits. The last sample of an SDU with the end mark carries In_Last.
    - An SDU is faulty when its user signal has a bit of the error mask set or when its length is not a multiple of
      the container (the incomplete container is completed with zero bytes). Its samples are forwarded, the last
      with In_Last and In_Error. After a faulty SDU without the end mark the following SDUs up to and including the
      next one with the end mark are discarded.
    - The core: In_Last with the last sample of an image gives a correct image, with In_Error an image with an
      error; In_Last before the last sample aborts the image; an image whose last sample has no In_Last is
      completed with an error, and the core discards its samples up to and including the next one with In_Last.
    - With the runtime configuration (runtime): a COMMIT passes valid values for the next image (commit), and the
      core takes them with the first sample of an image for the whole image (user guide 4.4); cfg holds the maxima.

    The state carries over from one SDU to the next, as in the channel.
    """

    def __init__(self, cfg: dict, marks: UserMarks, runtime: bool = False):
        self.cfg = cfg
        self.marks = marks
        self.runtime = runtime
        self.image_cfg = cfg                      # configuration of the last image that started
        self.next_cfg: dict | None = None         # committed, until the next image start
        self.counts = Counts()
        self.samples = samples_per_image(cfg)
        self.current: list[int] = []
        self.adapter_discards = False
        self.core_discards = False

    @property
    def pending(self) -> bool:
        """STATUS.CFG_PENDING: a committed configuration waits for the next image start."""
        return self.next_cfg is not None

    def commit(self, values: ImageConfig) -> bool:
        """CONTROL.COMMIT with these values in NEXT_X to NEXT_LIMITS: True when the channel passes them to the core
        for its next image start (they replace a configuration that is still pending), False when it refuses them
        (STATUS.CFG_ERROR), which changes nothing."""
        if not self.runtime:
            raise ValueError("the channel has no runtime configuration")
        if not commit_accepts(self.cfg, values):
            return False
        self.next_cfg = image_config(self.cfg, values)
        return True

    def _value(self, container: bytes) -> int:
        d = self.cfg["large_d"]
        value = int.from_bytes(container, "big") & ((1 << d) - 1)
        if self.cfg["sample_type"] and value >> (d - 1):
            value -= 1 << d
        return value

    def sdu(self, data: bytes, user: int) -> list[Output]:
        """The compressed images that the channel sends after taking one SDU."""
        if not data:
            raise ValueError("an SDU has at least one byte")
        end = self.marks.end(user)
        if self.adapter_discards:
            self.counts.sdus_discarded += 1
            self.adapter_discards = not end
            return []
        size = sample_bytes(self.cfg)
        faulty = self.marks.faulty(user) or len(data) % size != 0
        if faulty:
            self.counts.sdu_errors += 1
            self.adapter_discards = not end
            data = data + bytes(-len(data) % size)
        values = [self._value(data[k:k + size]) for k in range(0, len(data), size)]
        out = []
        for k, value in enumerate(values):
            last = k == len(values) - 1 and (end or faulty)
            out += self._sample(value, last, last and faulty)
        return out

    def _sample(self, value: int, last: bool, error: bool) -> list[Output]:
        if self.core_discards:
            self.core_discards = not last
            return []
        if not self.current and self.next_cfg is not None:
            self.image_cfg, self.next_cfg = self.next_cfg, None
            self.samples = samples_per_image(self.image_cfg)
        self.current.append(value)
        if len(self.current) == self.samples:
            image = self._output(complete=True, error=error or not last)
            self.core_discards = not last
            return [image]
        if last:
            return [self._output(complete=False, error=True)]
        return []

    def _output(self, complete: bool, error: bool) -> Output:
        image = Output(tuple(self.current), complete, error, self.image_cfg)
        self.current = []
        if error:
            self.counts.images_aborted += 1
        else:
            self.counts.images_done += 1
        return image


# ------------------------------------------------------------------------------------------------
# Status interface of the channel
# ------------------------------------------------------------------------------------------------
# Counters of the status interface of the channel (ocp_mib), cleared by a write, and their names in Counts
COUNTERS = {"IMAGES_DONE": "images_done", "IMAGES_ABORTED": "images_aborted", "SDU_ERRORS": "sdu_errors",
            "SDUS_DISCARDED": "sdus_discarded"}
ECC_COUNTERS = ("ECC_CORRECTED", "ECC_UNCORRECTABLE")
# The registers of the runtime configuration (zero without it), and those of the values that the core took for the
# last image that started
RUNTIME_REGISTERS = ("CONTROL", "STATUS", "NEXT_X", "NEXT_Y", "NEXT_Z", "NEXT_LIMITS")
NEXT_REGISTERS = RUNTIME_REGISTERS[2:]
# The registers of the runtime parameters (zero without them), consecutive from 0x07C
PARAM_REGISTERS = ("NEXT_SAMPLES", "NEXT_ORDER", "NEXT_WEIGHTS", "NEXT_ADAPTIVE", "NEXT_BLOCK")
CONFIG_REGISTERS = RUNTIME_REGISTERS + PARAM_REGISTERS
TAKEN_REGISTERS = ("IMAGE_X", "IMAGE_Y", "IMAGE_Z", "SAMPLES", "ORDER", "WEIGHTS", "ADAPTIVE", "BLOCK", "LIMITS")


def mib_words(regs, generics: dict, image: dict | None = None) -> dict[str, int]:
    """The configuration registers of the status interface (ocp_mib) of an OpenCompact channel with these generics,
    register name -> value, with the fields of its register map regs (sw/ocp_regs.h of OpenCompact), and LIMITS: after
    a reset, or with image (the configuration of the model for the last image that started, with the runtime
    configuration) IMAGE_X to IMAGE_Z, ORDER and LIMITS of that image."""
    g = generics
    cfg = model_config(g)
    taken = image or cfg

    def word(register: str, **fields) -> int:
        return sum(regs.field(register, name, value) for name, value in fields.items())

    coder = cfg["entropy_coder_type"]
    periodic = cfg["periodic_updating_flag"] and cfg["quantizer_fidelity_control_method"] != 0
    custom = cfg["weight_initialization_method"]
    fidelity = cfg["quantizer_fidelity_control_method"]
    absolute = fidelity in (1, 3) and not cfg["absolute_error_limit_assignment_method"]
    relative = fidelity in (2, 3) and not cfg["relative_error_limit_assignment_method"]
    words = {
        "GENERICS": word("GENERICS", IN_STREAM_BYTES=g["InStreamWidth_g"] // 8,
                         OUT_STREAM_BYTES=g["OutStreamWidth_g"] // 8,
                         OUT_BYTES=g["OutWidth_g"] // 8, SAMPLE_BYTES=sample_bytes(cfg),
                         SPECULATION=flag(g["Speculation_g"]),
                         RUNTIME_CONFIG=flag(g.get("RuntimeConfig_g", False)),
                         RUNTIME_PARAMS=flag(g.get("RuntimeParams_g", False))),
        "IMAGE_X": word("IMAGE_X", SIZE=taken["large_n_x"]),
        "IMAGE_Y": word("IMAGE_Y", SIZE=taken["large_n_y"]),
        "IMAGE_Z": word("IMAGE_Z", SIZE=taken["large_n_z"]),
        "SAMPLES": word("SAMPLES", USER_DATA=taken["user_defined_data"], SIGNED=cfg["sample_type"],
                        DYNAMIC_RANGE=cfg["large_d"]),
        "ORDER": word("ORDER", SUB_FRAME_DEPTH=taken["large_m"], ENCODING=cfg["sample_encoding_order"]),
        "CODER": word("CODER", SUP_TABLES=len(cfg["supplementary_information_tables"]),
                      FIDELITY=cfg["quantizer_fidelity_control_method"], ENTROPY_CODER=coder,
                      OUTPUT_WORD_SIZE=cfg["large_b"]),
        "PREDICTOR": word("PREDICTOR", UPDATE_PERIOD_EXP=cfg["u"] if periodic else 0, PERIODIC_UPDATE=int(periodic),
                          SAMPLE_REP_RESOLUTION=cfg["large_theta"], REGISTER_SIZE=cfg["large_r"],
                          LOCAL_SUM=cfg["local_sum_type"], REDUCED=cfg["prediction_mode"], BANDS=cfg["large_p"]),
        "WEIGHTS": word("WEIGHTS", INIT_RESOLUTION=cfg["large_q"] if custom else 0, CUSTOM_INIT=custom,
                        EXP_OFFSETS=cfg["weight_exponent_offset_flag"], VMAX=taken["v_max"] & 0x1F,
                        VMIN=taken["v_min"] & 0x1F, UPDATE_INTERVAL=taken["log_two_t_inc"],
                        RESOLUTION=cfg["large_omega"]),
        "ADAPTIVE": 0,
        "BLOCK": 0,
        # the band-independent error limit values of the generics, 0 for a method not in use or with band-dependent
        # limits; with the runtime configuration, the values of the last image
        "LIMITS": word("LIMITS", ABS=taken["a_vector"][0] if absolute else 0,
                       REL=taken["r_vector"][0] if relative else 0),
    }
    # with the runtime parameters, the values of the last image (taken); the other fields are those of the generics
    if coder == 2:
        words["BLOCK"] = word("BLOCK", REF_INTERVAL=taken["r"], RESTRICTED=cfg["restricted_code_options_flag"],
                              BLOCK_SIZE=cfg["large_j"])
    else:
        accu = 0 if coder == 1 else 15 if cfg["band_varying_k_flag"] else taken["k_prime_prime_vector"][0]
        words["ADAPTIVE"] = word("ADAPTIVE", ACCU_INIT=accu, INITIAL_COUNT=taken["gamma_0"],
                                 RESCALE_SIZE=taken["gamma_star"], UNARY_LIMIT=taken["large_u_max"])
    return words


# The fields of the registers of the runtime parameters and the parameters they hold (OpenCompact register map)
PARAM_FIELDS = {"NEXT_SAMPLES": {"USER_DATA": "user"}, "NEXT_ORDER": {"SUB_FRAME_DEPTH": "m"},
                "NEXT_WEIGHTS": {"VMAX": "vmax", "VMIN": "vmin", "UPDATE_INTERVAL": "tinc"},
                "NEXT_ADAPTIVE": {"ACCU_INIT": "k", "INITIAL_COUNT": "g0", "RESCALE_SIZE": "gstar",
                                  "UNARY_LIMIT": "umax"},
                "NEXT_BLOCK": {"REF_INTERVAL": "r"}}
SIGNED_PARAMS = ("vmin", "vmax")      # 5-bit two's complement


def next_words(regs, values: ImageConfig) -> dict[str, int]:
    """NEXT_X to NEXT_LIMITS with these values, and with the runtime parameters NEXT_SAMPLES to NEXT_BLOCK."""
    words = {"NEXT_X": regs.field("NEXT_X", "SIZE", values.nx), "NEXT_Y": regs.field("NEXT_Y", "SIZE", values.ny),
             "NEXT_Z": regs.field("NEXT_Z", "SIZE", values.nz),
             "NEXT_LIMITS": regs.field("NEXT_LIMITS", "ABS", values.abs_limit)
             | regs.field("NEXT_LIMITS", "REL", values.rel_limit)}
    if values.params is not None:
        v = dict(zip(PARAM_NAMES, values.params))
        for name, fields in PARAM_FIELDS.items():
            words[name] = sum(regs.field(name, f, v[n] & 0x1F if n in SIGNED_PARAMS else v[n])
                              for f, n in fields.items())
    return words


def next_values(regs, words: dict[str, int], params: bool = False) -> ImageConfig:
    """The values in NEXT_X to NEXT_LIMITS, with params also those of NEXT_SAMPLES to NEXT_BLOCK (the inverse of
    next_words)."""
    limits = words["NEXT_LIMITS"]
    values = ImageConfig(regs.get("NEXT_X", "SIZE", words["NEXT_X"]), regs.get("NEXT_Y", "SIZE", words["NEXT_Y"]),
                         regs.get("NEXT_Z", "SIZE", words["NEXT_Z"]), regs.get("NEXT_LIMITS", "ABS", limits),
                         regs.get("NEXT_LIMITS", "REL", limits))
    if not params:
        return values
    v = {}
    for name, fields in PARAM_FIELDS.items():
        for f, n in fields.items():
            v[n] = regs.get(name, f, words[name])
            if n in SIGNED_PARAMS and v[n] & 0x10:
                v[n] -= 0x20
    return replace(values, params=tuple(v[n] for n in PARAM_NAMES))


def runtime_words(regs, generics: dict) -> dict[str, int]:
    """CONTROL, STATUS, NEXT_X to NEXT_LIMITS and NEXT_SAMPLES to NEXT_BLOCK after a reset: with the runtime
    configuration NEXT_X to NEXT_LIMITS hold the values of the generics (build_values), with the runtime parameters
    also NEXT_SAMPLES to NEXT_BLOCK; the registers of what a channel does not have read zero."""
    words = dict.fromkeys(CONFIG_REGISTERS, 0)
    if runtime_config(generics):
        words |= next_words(regs, build_values(model_config(generics), runtime_params(generics)))
    return words


def param_word(regs, cfg: dict, name: str, value: int) -> int:
    """A write of value to a register of the runtime parameters: the fields of the parameters that the configuration
    uses keep what is written, the others read zero."""
    used = used_params(cfg)
    return sum(regs.field(name, f, regs.get(name, f, value)) for f, n in PARAM_FIELDS[name].items() if n in used)


class RuntimeRegisters:
    """The registers of the runtime configuration of one channel, for the software models of the systems: NEXT_X to
    NEXT_LIMITS read back what is written, CONTROL.COMMIT passes their values to the channel or sets
    STATUS.CFG_ERROR, writing 1 to STATUS.CFG_ERROR clears it, STATUS.CFG_PENDING shows a pending configuration;
    taken() gives IMAGE_X to IMAGE_Z, SAMPLES, ORDER, WEIGHTS, ADAPTIVE, BLOCK and LIMITS of the last image that
    started. With the runtime parameters NEXT_SAMPLES to NEXT_BLOCK take part in the COMMIT; the fields of the
    parameters that the configuration does not use read zero. Without the runtime configuration (or the runtime
    parameters) these registers read zero and ignore writes (OpenCompact register map)."""

    def __init__(self, regs, generics: dict, channel: Channel):
        self.regs = regs
        self.generics = generics
        self.channel = channel
        self.params = runtime_params(generics)
        self.words = runtime_words(regs, generics)
        self.error = False

    def read(self, name: str) -> int:
        if name == "STATUS" and self.channel.runtime:
            return (self.regs.field("STATUS", "CFG_ERROR", int(self.error))
                    | self.regs.field("STATUS", "CFG_PENDING", int(self.channel.pending)))
        return self.words[name]

    def write(self, name: str, value: int) -> None:
        r = self.regs
        if not self.channel.runtime:
            return
        if name == "CONTROL":
            if r.get("CONTROL", "COMMIT", value):
                self.error |= not self.channel.commit(next_values(r, self.words, self.params))
        elif name == "STATUS":
            self.error &= not r.get("STATUS", "CFG_ERROR", value)
        elif name in PARAM_REGISTERS:
            if self.params:
                self.words[name] = param_word(r, self.channel.cfg, name, value)
        elif name == "NEXT_LIMITS":
            self.words[name] = (r.field(name, "ABS", r.get(name, "ABS", value))
                                | r.field(name, "REL", r.get(name, "REL", value)))
        else:
            self.words[name] = r.field(name, "SIZE", r.get(name, "SIZE", value))

    def taken(self) -> dict[str, int]:
        words = mib_words(self.regs, self.generics, self.channel.image_cfg)
        return {name: words[name] for name in TAKEN_REGISTERS}
