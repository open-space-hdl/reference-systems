# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""System simulation of spfi_encrypt with the behavioural model of the GTY adapter at both ends of the link (GHDL).

Usage: python systems/spfi_encrypt/sim/run_ghdl.py [--profile sim] [--seed N] [--each] [--hdl DIR]
       [TC-RS-HW-nn ...]

The bench, the stimuli and the checks are those of run_xsim.py; the architecture mdl of ofb_pa_gty of OpenFibre
replaces the transceiver wizard at both ends. Without test cases, one simulation runs all test cases; with test cases,
the simulation runs them and the test cases they build on; --each runs one simulation per test case. Each simulation
gets a directory in <out>/runs/ with the stimulus, the records of the bench and report.txt. <out> is
sim_out/ghdl/spfi_encrypt, or RS_GHDL_OUT. --hdl takes the top level from another directory than
systems/spfi_encrypt/hdl. Exit code 0 when every simulation passes.
"""

import argparse
import os
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SYSTEM_DIR = HERE.parent
ROOT = SYSTEM_DIR.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(HERE))

import ghdl  # noqa: E402
import run_xsim  # noqa: E402
import vectors  # noqa: E402

OUT = Path(os.environ.get("RS_GHDL_OUT", ROOT / "sim_out" / "ghdl" / "spfi_encrypt"))
TOP = "spfi_encrypt_tb"


def build(hdl: Path) -> Path:
    """Analyses everything; returns the directory of the libraries."""
    files = ghdl.compile_order(hdl / "spfi_encrypt_compile_order.txt")
    files += [(run_xsim.TB_LIBRARY, HERE / "tb" / name) for name in run_xsim.TB_FILES]
    work = OUT / "work"
    ghdl.analyse(work, files)
    return work


def simulate(work: Path, name: str, profile: str, seed: int, tests: list[str] | None) -> int:
    run_dir = OUT / "runs" / name
    shutil.rmtree(run_dir, ignore_errors=True)
    vectors.write(run_dir, profile, seed, tests)
    wall: dict[int, float] = {}

    def on_line(line: str, seconds: float) -> None:
        m = run_xsim.EVENT.search(line)
        if m:
            wall[int(m.group(3))] = seconds
            print(f"  {seconds / 60:6.1f} min  {m.group(0)}", flush=True)

    start = time.time()
    log, code = ghdl.simulate(work, run_xsim.TB_LIBRARY, TOP, run_xsim.far_end_generics(), run_dir,
                              f"simulate_{TOP}.log", on_line, run_xsim.PROBES, run_xsim.VCD)
    (run_dir / vectors.WALL).write_text("".join(f"{line} {s:.1f}\n" for line, s in sorted(wall.items())),
                                        encoding="utf-8")
    if code != 0 or re.search(r"\(assertion (error|failure)\)|\(report (error|failure)\)", log):
        print(f"{name}: the simulator stopped with an error (exit code {code}), see {run_dir / f'simulate_{TOP}.log'}")
    print(f"{name}: simulation {time.time() - start:.0f} s", flush=True)
    return vectors.check(run_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tests", nargs="*", help="test cases (default: all)")
    parser.add_argument("--profile", choices=vectors.ht.PROFILES, default="sim")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--each", action="store_true", help="one simulation per test case")
    parser.add_argument("--hdl", type=Path, default=SYSTEM_DIR / "hdl", help="directory of the generated top level")
    args = parser.parse_args()

    names = args.tests or None
    vectors.ht.select_tests(names)          # unknown names stop here, before the build
    work = build(args.hdl)
    if args.each:
        runs = [(t, [t]) for t in (names or [vectors.ht.test_name(t) for t in vectors.ht.TESTS])]
    else:
        runs = [("_".join(names) if names else "all", names)]
    failed = [name for name, tests in runs if simulate(work, name, args.profile, args.seed, tests)]
    print(f"{len(runs) - len(failed)} of {len(runs)} simulations passed" + (f"; failed: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
