# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""System simulation of spfi_encrypt with the AMD transceiver model at both ends of the link (xsim).

Usage: python systems/spfi_encrypt/sim/run_xsim.py [--profile sim] [--seed N] [--each] [--hdl DIR]
       [TC-RS-HW-nn ...]

Without test cases, one simulation runs all test cases; with test cases, the simulation runs them and the test
cases they build on; --each runs one simulation per test case. Every simulation starts with the reset and the link
initialisation. Each simulation gets a directory in <out>/runs/ with the stimulus, the records of the bench and
report.txt (result, simulated and wall-clock time per test case). <out> is vivado_out/xsim/spfi_encrypt, or
RS_XSIM_OUT. --hdl takes the top level from another directory than systems/spfi_encrypt/hdl.
Exit code 0 when every simulation passes.
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
sys.path.insert(0, str(ROOT / "sw"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(HERE))

import vectors  # noqa: E402
import xsim  # noqa: E402
from refsys.composer import SystemFile  # noqa: E402

OUT = Path(os.environ.get("RS_XSIM_OUT", ROOT / "vivado_out" / "xsim" / "spfi_encrypt"))
TB_LIBRARY = "spfi_encrypt_tb"
TB_FILES = ["spfi_encrypt_tb_pkg.vhd", "spfi_encrypt_tb.vhd"]
SNAPSHOT = "spfi_encrypt_tb"
EVENT = re.compile(r"event (\d+) (\w+) (-?\d+)")
VCD = "probes.vcd"

# Signals written to probes.vcd for the analysis of a failed link initialisation: the reset and the ready states of
# the transceivers and the lane states of both ends. They are not used by the checks, which see the ports only.
PROBES = [
    "/spfi_encrypt_tb/Rst",
    "/spfi_encrypt_tb/FarRst",
    "/spfi_encrypt_tb/i_dut/i_pa0/LaneRst",
    "/spfi_encrypt_tb/i_dut/i_pa0/Stat_TxReady",
    "/spfi_encrypt_tb/i_dut/i_pa0/Stat_RxReady",
    "/spfi_encrypt_tb/i_dut/i_pa0/Stat_Aligned",
    "/spfi_encrypt_tb/i_dut/i_ofb0/LaneState",
    "/spfi_encrypt_tb/i_far_pa/LaneRst",
    "/spfi_encrypt_tb/i_far_pa/Stat_TxReady",
    "/spfi_encrypt_tb/i_far_pa/Stat_RxReady",
    "/spfi_encrypt_tb/i_far_core/LaneState",
]


def far_end_generics() -> dict[str, int]:
    """The far end has the generics of the SpaceFibre port of the system."""
    f = SystemFile(SYSTEM_DIR / "spfi_encrypt.composer.json")
    port = f.instance_of_block("openfibre/ofb_core")
    return {name: f.generic(port, name) for name in ("NumLanes_g", "NumVc_g", "VcInDepth_g")}


def build(hdl: Path) -> tuple[Path, list[str]]:
    """Generates the wizard instance, compiles everything and elaborates the bench; returns the directory of the
    simulation and the libraries."""
    export = xsim.generate_gtw(OUT / "ip")
    files = xsim.compile_order(hdl / "spfi_encrypt_compile_order.txt")
    files += [(TB_LIBRARY, HERE / "tb" / name) for name in TB_FILES]
    xsim.compile_all(export, files)
    libraries = list(dict.fromkeys(library for library, _ in files))
    elaborate(export, libraries)
    return export, libraries


def elaborate(export: Path, libraries: list[str], again: bool = False) -> None:
    xsim.elaborate(export, f"{TB_LIBRARY}.spfi_encrypt_tb", libraries, far_end_generics(), SNAPSHOT, trace=True,
                   again=again)


def simulate(export: Path, libraries: list[str], name: str, profile: str, seed: int, tests: list[str] | None) -> int:
    run_dir = OUT / "runs" / name
    shutil.rmtree(run_dir, ignore_errors=True)
    vectors.write(run_dir, profile, seed, tests)
    for attempt in range(1, xsim.MODEL_ATTEMPTS + 1):
        for f in [vectors.EVENTS, VCD, *(vectors.rx_file(vc) for vc in range(8))]:
            (export / f).unlink(missing_ok=True)
        shutil.copy(run_dir / vectors.STIMULUS, export / vectors.STIMULUS)
        wall: dict[int, float] = {}

        def on_line(line: str, seconds: float) -> None:
            m = EVENT.search(line)
            if m:
                wall[int(m.group(3))] = seconds
                print(f"  {seconds / 60:6.1f} min  {line.strip()}", flush=True)

        start = time.time()
        log = xsim.simulate(export, SNAPSHOT, f"simulate_{SNAPSHOT}.log", on_line, PROBES, VCD)
        if xsim.model_error(log) and attempt < xsim.MODEL_ATTEMPTS:
            print(f"{name}: power-up error of the transceiver model ({xsim.MODEL_ERROR}), attempt {attempt} of "
                  f"{xsim.MODEL_ATTEMPTS}, {time.time() - start:.0f} s; elaborating again", flush=True)
            elaborate(export, libraries, again=True)
            continue
        break
    for f in export.iterdir():
        if f.name in (vectors.EVENTS, VCD, f"simulate_{SNAPSHOT}.log") or f.name.startswith("rx_vc"):
            shutil.copy(f, run_dir / f.name)
    (run_dir / vectors.WALL).write_text("".join(f"{line} {s:.1f}\n" for line, s in sorted(wall.items())),
                                        encoding="utf-8")
    if re.search(r"FATAL|Fatal|Failure:", log):
        print(f"{name}: the simulator stopped with an error, see {run_dir / f'simulate_{SNAPSHOT}.log'}")
    note = f", attempt {attempt} of {xsim.MODEL_ATTEMPTS}" if attempt > 1 else ""
    print(f"{name}: simulation {time.time() - start:.0f} s{note}", flush=True)
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
    export, libraries = build(args.hdl)
    if args.each:
        runs = [(t, [t]) for t in (names or [vectors.ht.test_name(t) for t in vectors.ht.TESTS])]
    else:
        runs = [("_".join(names) if names else "all", names)]
    failed = [name for name, tests in runs if simulate(export, libraries, name, args.profile, args.seed, tests)]
    print(f"{len(runs) - len(failed)} of {len(runs)} simulations passed" + (f"; failed: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
