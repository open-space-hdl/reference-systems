# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Steps of TC-RS-HW-10, the runtime configuration of the compressors (RuntimeConfig_g of OpenCompact), shared by the
hardware tests of spfi_compress and spfi_compress_encrypt.

A compressor with the runtime configuration takes the image dimensions and the band-independent error limit values
of every image from NEXT_X to NEXT_LIMITS: CONTROL.COMMIT checks them and passes them to the core for its next image
start (STATUS.CFG_PENDING), or refuses them with STATUS.CFG_ERROR and changes nothing; IMAGE_X to IMAGE_Z, ORDER and
LIMITS show the values of the last image that started (OpenCompact user guide 4.3 and 4.4, register map). With the
runtime parameters (RuntimeParams_g) the same COMMIT also takes the user-defined data, M, the weight update and the
entropy coder parameters from NEXT_SAMPLES to NEXT_BLOCK, and SAMPLES, ORDER, WEIGHTS, ADAPTIVE and BLOCK show the
values of the last image (user guide 4.5). Without them these registers read zero and ignore writes. The test
procedure keeps the model of every channel (refsys.ocp.Channel) in step with what it commits, and the model gives
the expected values and images.
"""

import random
from dataclasses import replace

from refsys import ocp
from refsys.regs import RegisterMap
from refsys.systest import CORES, Registers, check, consecutive, hex_words

OCP = RegisterMap(CORES / "opencompact" / "sw" / "ocp_regs.h", "OCP")
COMMIT = OCP.field("CONTROL", "COMMIT", 1)
CFG_ERROR = OCP.field("STATUS", "CFG_ERROR", 1)
CFG_PENDING = OCP.field("STATUS", "CFG_PENDING", 1)

# Images per channel of the lab profile: the seeded sequence of the runtime testbench of OpenCompact
# (ocp_configs.RUNTIME_IMAGES), then one image of the configuration of the generics
LAB_IMAGES = 20


def split_channels(system) -> tuple[list, list]:
    """The channels whose compressor has the runtime configuration, and the others."""
    runtime = [ch for ch in system.channels if ocp.runtime_config(ch.generics)]
    return runtime, [ch for ch in system.channels if ch not in runtime]


def image_plan(ctx, ch) -> list[ocp.ImageConfig]:
    """The images of TC-RS-HW-10 of a channel with the runtime configuration. Lab profile (full): the sequence of
    LAB_IMAGES images of OpenCompact (the bounds first, then seeded at random; with the runtime parameters the
    sequence of the runtime parameter testbench, which also takes every parameter to its bounds), then an image with
    the configuration of the generics, so that a run ends with the values of the reset. Sim profile: three images of
    the bounds of that sequence, which take every dimension to 1 (or 2) and to its maximum and every error limit
    value of a method in use to 0 and to its maximum; with the runtime parameters the first with every parameter at
    the high bound of its range, the second at the low bound, the third at random."""
    seed = 1000 * ctx.seed + ch.port
    params = ocp.runtime_params(ch.generics)
    if ctx.profile.runtime_full:
        images = (ocp.runtime_params_sequence(ch.cfg, LAB_IMAGES, seed) if params
                  else ocp.runtime_sequence(ch.cfg, LAB_IMAGES, seed))
        return images + [ocp.build_values(ch.cfg, params)]
    images = ocp.runtime_sequence(ch.cfg, 8, seed)[1:4]
    if not params:
        return images
    rng = random.Random(seed)
    return [replace(v, params=ocp.random_params(ch.cfg, v.nz, rng, edge))
            for v, edge in zip(images, ("high", "low", None))]


def read_config(regs: Registers, ctx, ch) -> list[int]:
    """CONTROL to NEXT_LIMITS and NEXT_SAMPLES to NEXT_BLOCK of a compressor (two commands)."""
    base = ctx.system.base(ch.compressor)
    return (regs.read_block(base + consecutive(OCP, ocp.RUNTIME_REGISTERS), len(ocp.RUNTIME_REGISTERS))
            + regs.read_block(base + consecutive(OCP, ocp.PARAM_REGISTERS), len(ocp.PARAM_REGISTERS)))


def write_next(regs: Registers, ctx, ch, values: ocp.ImageConfig) -> dict[str, int]:
    """Writes NEXT_X to NEXT_LIMITS (one command) and, with the runtime parameters in values, NEXT_SAMPLES to
    NEXT_BLOCK (one command); returns the words written."""
    base = ctx.system.base(ch.compressor)
    words = ocp.next_words(OCP, values)
    regs.write_block(base + consecutive(OCP, ocp.NEXT_REGISTERS), [words[n] for n in ocp.NEXT_REGISTERS])
    if values.params is not None:
        regs.write_block(base + consecutive(OCP, ocp.PARAM_REGISTERS), [words[n] for n in ocp.PARAM_REGISTERS])
    return words


def check_reset(regs: Registers, ctx, ch) -> None:
    """CONTROL, STATUS, NEXT_X to NEXT_LIMITS and NEXT_SAMPLES to NEXT_BLOCK hold their values after a reset (also
    after a complete run of the lab profile, which ends with the values of the generics)."""
    want = [ocp.runtime_words(OCP, ch.generics)[n] for n in ocp.CONFIG_REGISTERS]
    got = read_config(regs, ctx, ch)
    check(got == want, f"{ch.compressor} CONTROL to NEXT_LIMITS, NEXT_SAMPLES to NEXT_BLOCK: {hex_words(got)}, "
          f"expected {hex_words(want)}")


def check_fixed(regs: Registers, ctx, ch) -> None:
    """A compressor without the runtime configuration ignores the writes of NEXT_X to NEXT_BLOCK and a COMMIT: the
    eleven registers read zero, and IMAGE_X to LIMITS keep the values of its generics."""
    base = ctx.system.base(ch.compressor)
    write_next(regs, ctx, ch, ocp.ImageConfig(2, 1, 1, 1, 1, ocp.build_params(ch.cfg)))
    regs.write(base + OCP.CONTROL, COMMIT)
    got = read_config(regs, ctx, ch)
    check(got == [0] * len(ocp.CONFIG_REGISTERS), f"{ch.compressor} without the runtime configuration: CONTROL to "
          f"NEXT_BLOCK read {hex_words(got)}")
    check_taken(regs, ctx, ch)


def read_status(regs: Registers, ctx, ch) -> tuple[bool, bool]:
    """STATUS.CFG_ERROR and STATUS.CFG_PENDING of a compressor."""
    word = regs.read(ctx.system.base(ch.compressor) + OCP.STATUS)
    check(not word & ~(CFG_ERROR | CFG_PENDING), f"{ch.compressor} STATUS 0x{word:08X}")
    return bool(word & CFG_ERROR), bool(word & CFG_PENDING)


def commit(regs: Registers, ctx, ch, values: ocp.ImageConfig) -> None:
    """Writes the values of the next image into NEXT_X to NEXT_LIMITS (one command), with the runtime parameters
    into NEXT_SAMPLES to NEXT_BLOCK (one command), and COMMIT; the compressor passes them on (STATUS: CFG_PENDING, no
    CFG_ERROR) and the registers read back what was written."""
    base = ctx.system.base(ch.compressor)
    written = write_next(regs, ctx, ch, values)
    regs.write(base + OCP.CONTROL, COMMIT)
    check(ctx.models[ch.port].commit(values), f"{ch.compressor}: the values {values} are out of range")
    status = read_status(regs, ctx, ch)
    check(status == (False, True), f"{ch.compressor}: COMMIT of {values} gives STATUS (CFG_ERROR, CFG_PENDING) "
          f"{status}, expected (False, True)")
    words = [written[n] for n in ocp.NEXT_REGISTERS]
    got = regs.read_block(base + consecutive(OCP, ocp.NEXT_REGISTERS), len(words))
    check(got == words, f"{ch.compressor} NEXT_X to NEXT_LIMITS: {hex_words(got)}, expected {hex_words(words)}")
    if values.params is not None:
        words = [written[n] for n in ocp.PARAM_REGISTERS]
        got = regs.read_block(base + consecutive(OCP, ocp.PARAM_REGISTERS), len(words))
        check(got == words, f"{ch.compressor} NEXT_SAMPLES to NEXT_BLOCK: {hex_words(got)}, expected "
              f"{hex_words(words)}")


def refuse(regs: Registers, ctx, ch, valid: ocp.ImageConfig) -> None:
    """After the COMMIT of the valid values: every COMMIT with one value out of range (ocp.refused_values) sets
    CFG_ERROR and keeps the pending configuration; the register reads back the value written, a write of 1 to
    CFG_ERROR clears it, and the register gets its valid value again. The image sent next must be that of the valid
    values."""
    base = ctx.system.base(ch.compressor)
    good = ocp.next_words(OCP, valid)
    model = ctx.models[ch.port]
    for name, bad in ocp.refused_values(ch.cfg, valid):
        word = ocp.next_words(OCP, bad)[name]
        regs.write(base + getattr(OCP, name), word)
        regs.write(base + OCP.CONTROL, COMMIT)
        check(not model.commit(bad), f"{ch.compressor}: the model takes the values {bad}")
        status = read_status(regs, ctx, ch)
        check(status == (True, True), f"{ch.compressor}: COMMIT with {name} 0x{word:08X} gives STATUS (CFG_ERROR, "
              f"CFG_PENDING) {status}, expected (True, True)")
        got = regs.read(base + getattr(OCP, name))
        check(got == word, f"{ch.compressor} {name}: 0x{got:08X}, expected 0x{word:08X}")
        regs.write(base + OCP.STATUS, CFG_ERROR)
        regs.write(base + getattr(OCP, name), good[name])
    status = read_status(regs, ctx, ch)
    check(status == (False, True), f"{ch.compressor}: STATUS (CFG_ERROR, CFG_PENDING) after the refused COMMITs "
          f"{status}, expected (False, True)")


def check_taken(regs: Registers, ctx, ch) -> None:
    """IMAGE_X to IMAGE_Z, SAMPLES, ORDER, WEIGHTS, ADAPTIVE, BLOCK and LIMITS show the values of the last image that
    started, as the model of the channel took them, and no configuration is pending."""
    base = ctx.system.base(ch.compressor)
    model = ctx.models[ch.port]
    words = ocp.mib_words(OCP, ch.generics, model.image_cfg)
    for name in ocp.TAKEN_REGISTERS:
        got = regs.read(base + getattr(OCP, name))
        check(got == words[name], f"{ch.compressor} {name}: 0x{got:08X}, expected 0x{words[name]:08X}")
    if model.runtime:
        status = read_status(regs, ctx, ch)
        check(status == (False, False), f"{ch.compressor}: STATUS (CFG_ERROR, CFG_PENDING) {status} after the "
              "image, expected (False, False)")
