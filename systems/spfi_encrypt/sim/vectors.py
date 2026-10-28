# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Stimuli and checks of the system simulation of spfi_encrypt.

The system testbench (sim/tb/spfi_encrypt_tb.vhd) joins the system to a far-end SpaceFibre node and plays the
hardware test (sw/hardware_test.py): write records the procedure against the software model of the system
(sw/system_model.py) as the operations of the far end in stimulus.txt, the bench plays them and records what the far
end receives, and check runs the procedure again on the records, with every pass criterion of the hardware test, the
decryption and the tag check of every chunk included. sw/refsys/sysvectors.py describes the steps, the operations and
the files.

Usage:
  python systems/spfi_encrypt/sim/vectors.py write <dir> [--profile sim] [--seed N] [--size PACKETS]
                                                   [--test TC-RS-HW-nn ...]
  python systems/spfi_encrypt/sim/vectors.py check <dir>
Exit code of check: 0 when every selected test case passes.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "sw"))

import hardware_test as ht  # noqa: E402
from refsys import sysvectors  # noqa: E402
from refsys.sysvectors import (  # noqa: E402,F401
    EVENTS, REPORT, REQUEST_TIMEOUT_NS, STIMULUS, VECTORS, WALL, Route, routes, rx_file)
from system_model import SystemModel  # noqa: E402

BENCH = sysvectors.Bench(
    name="spfi_encrypt",
    ht=ht,
    model=lambda ctx: SystemModel(ctx.setup),
    context=lambda seed, profile, size: ht.make_context(seed, size or ht.PROFILES[profile].packets),
    fields=lambda ctx: {"packets": ctx.packets},
    restore=lambda vectors: ht.make_context(vectors["seed"], vectors["packets"]),
    describe=lambda vectors: f"{vectors['packets']} Space Packets in TC-RS-HW-06",
)


def write(directory: Path, profile: str, seed: int, tests: list[str] | None, packets: int | None = None) -> None:
    """Records the procedure of the selected test cases against the model and writes stimulus.txt and
    vectors.json; packets: the Space Packets of TC-RS-HW-06 (default: of the profile)."""
    sysvectors.write(BENCH, directory, profile, seed, tests, packets)


def check(directory: Path, out: Path | None = None) -> int:
    """Replays the records of the bench with the procedure of the hardware test, writes report.txt and the step log
    (into out, default directory); returns the exit code."""
    return sysvectors.check(BENCH, directory, out)


if __name__ == "__main__":
    sys.exit(sysvectors.main(BENCH, __doc__.splitlines()[0], "Space Packets of TC-RS-HW-06 (default: of the profile)"))
