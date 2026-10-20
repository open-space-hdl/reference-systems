# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Stimuli and checks of the system simulation of spfi_compress.

The system testbench (sim/tb/spfi_compress_tb.vhd) joins the system to a far-end SpaceFibre node and plays
the hardware test (sw/hardware_test.py): write records the procedure against the software model of the system
(sw/system_model.py) as the operations of the far end in stimulus.txt, the bench plays them and records what the far
end receives, and check runs the procedure again on the records, with every pass criterion of the hardware test: the
Space Packets of every channel must be the chunks of its compressed images, and every compressed image must equal the
image of the reference model of OpenCompact. sw/refsys/sysvectors.py describes the steps, the operations and the
files.

Usage:
  python systems/spfi_compress/sim/vectors.py write <dir> [--profile sim] [--seed N] [--test TC-RS-HW-nn ...]
  python systems/spfi_compress/sim/vectors.py check <dir>
Exit code of check: 0 when every selected test case passes.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "sw"))

from refsys import systest, sysvectors  # noqa: E402
from refsys.sysvectors import (  # noqa: E402,F401
    EVENTS, REPORT, REQUEST_TIMEOUT_NS, STIMULUS, VECTORS, WALL, Route, routes, rx_file)

# the modules of the system, without their names in sys.modules: spfi_encrypt has modules of the same names
ht, sm = systest.system_modules(HERE.parent / "sw", "hardware_test", "system_model")


def make_bench(system=None, sdu_bytes: int | None = None) -> sysvectors.Bench:
    """The simulation of the system of the Composer file, or of another system (for the tests of the stimuli);
    sdu_bytes: the longest SDU of the images (default: the longest that the receive buffer holds)."""

    def context(seed: int, profile: str, size: int | None = None):
        if size is not None:
            raise ValueError("the profile gives the size of a run of spfi_compress")
        return ht.make_context(seed, ht.PROFILES[profile], system=system, sdu_bytes=sdu_bytes)

    def describe(vectors: dict) -> str:
        kinds = len(ht.PROFILES[vectors["profile"]].kinds)
        return f"{kinds} image{'s' if kinds > 1 else ''} per channel in TC-RS-HW-06"

    return sysvectors.Bench(
        name="spfi_compress",
        ht=ht,
        model=lambda ctx: sm.SystemModel(ctx.setup, system=ctx.system),
        context=context,
        fields=lambda ctx: {},
        restore=lambda vectors: context(vectors["seed"], vectors["profile"]),
        describe=describe,
    )


BENCH = make_bench()


def write(directory: Path, profile: str, seed: int, tests: list[str] | None, size: int | None = None) -> None:
    """Records the procedure of the selected test cases against the model and writes stimulus.txt and
    vectors.json."""
    sysvectors.write(BENCH, directory, profile, seed, tests, size)


def check(directory: Path) -> int:
    """Replays the records of the bench with the procedure of the hardware test, writes report.txt; returns the exit
    code."""
    return sysvectors.check(BENCH, directory)


if __name__ == "__main__":
    sys.exit(sysvectors.main(BENCH, __doc__.splitlines()[0]))
