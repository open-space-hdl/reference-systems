# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Runner of the system simulations: the testbench of a system (systems/<system>/sim/tb/<system>_tb.vhd) with the
far end of sim/tb/, the stimuli and the checks of its sim/vectors.py, in xsim with the AMD transceiver model
(tools/xsim.py) or in GHDL with the behavioural model of the adapter (tools/ghdl.py).

Usage of the runner of a system:
  python systems/<system>/sim/run_xsim.py [--profile sim] [--seed N] [--each] [--hdl DIR] [--refclk-mhz F]
         [--sysclk-mhz F] [TC-RS-HW-nn ...]
  python systems/<system>/sim/run_ghdl.py [--profile sim] [--seed N] [--each] [--hdl DIR] [--sysclk-mhz F]
         [TC-RS-HW-nn ...]

Without test cases, one simulation runs all test cases; with test cases, the simulation runs them and the test
cases they build on; --each runs one simulation per test case. Every simulation starts with the reset and the link
initialisation. Each simulation gets a directory in <out>/runs/ with the stimulus, the records of the bench and
report.txt (result, simulated and wall-clock time per test case). <out> is vivado_out/xsim/<system> or RS_XSIM_OUT
(xsim), sim_out/ghdl/<system> or RS_GHDL_OUT (GHDL). --hdl takes the top level from another directory than
systems/<system>/hdl. --refclk-mhz (xsim) sets the frequency of the transceiver reference clock, 156.25 (default) or
100 MHz, for the wizard instance and the bench; the behavioural model of GHDL takes only 156.25 MHz, its lane clock
is the reference clock. A run at another frequency than 156.25 MHz has its own wizard instance (<out>/ip_refclk_<F>)
and its own directory (<out>/runs/<name>_refclk_<F>). --sysclk-mhz sets the frequency of the system clock of the
bench, 200 (default) or 150 MHz, the frequency of the hardware builds; a run at 150 MHz has its own directory
(<out>/runs/<name>[_refclk_<F>]_sysclk_150). Exit code 0 when every simulation passes.
"""

import argparse
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import ghdl
import xsim

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sw"))

from refsys.composer import SystemFile  # noqa: E402

# The far end of the system testbenches and its text helpers
FAR_END_LIBRARY = "refsys_tb"
FAR_END_FILES = [ROOT / "sim" / "tb" / "rs_tb_pkg.vhd", ROOT / "sim" / "tb" / "rs_far_end.vhd"]
EVENT = re.compile(r"event (\d+) (\w+) (-?\d+)")
VCD = "probes.vcd"
# System clock of the testbenches in MHz (SysClkKhz_g); the hardware builds run at 150 MHz
SYSCLK_MHZ = "200"


@dataclass
class Simulation:
    """The system simulation of one system; vectors is its sim/vectors.py."""

    name: str
    vectors: ModuleType
    refclk_mhz: str = xsim.REFCLK_MHZ   # reference clock of the transceivers (xsim)
    sysclk_mhz: str = SYSCLK_MHZ        # system clock

    @property
    def directory(self) -> Path:
        return ROOT / "systems" / self.name

    @property
    def top(self) -> str:
        return f"{self.name}_tb"

    @property
    def library(self) -> str:
        return f"{self.name}_tb"

    def system_file(self) -> SystemFile:
        return SystemFile(self.directory / f"{self.name}.composer.json")

    def far_end_generics(self) -> dict[str, int]:
        """The far end has the generics of the SpaceFibre port of the system."""
        f = self.system_file()
        port = f.instance_of_block("openfibre/ofb_core")
        return {name: f.generic(port, name) for name in ("NumLanes_g", "NumVc_g", "VcInDepth_g")}

    def bench_generics(self) -> dict[str, object]:
        """The generics of the bench: those of the far end, and the system clock when it is not the default."""
        generics: dict[str, object] = dict(self.far_end_generics())
        if self.sysclk_mhz != SYSCLK_MHZ:
            generics["SysClkKhz_g"] = round(float(self.sysclk_mhz) * 1000)
        return generics

    def probes(self) -> list[str]:
        """Signals written to probes.vcd for the analysis of a failed link initialisation: the reset and the ready
        states of the transceivers and the lane states of both ends. They are not used by the checks, which see the
        ports only."""
        f = self.system_file()
        pa, port = f.instance_of_block("openfibre/ofb_pa_gty"), f.instance_of_block("openfibre/ofb_core")
        tb = f"/{self.top}"
        return [f"{tb}/Rst", f"{tb}/i_far/FarTxReady",
                f"{tb}/i_dut/i_{pa}/LaneRst", f"{tb}/i_dut/i_{pa}/Stat_TxReady", f"{tb}/i_dut/i_{pa}/Stat_RxReady",
                f"{tb}/i_dut/i_{pa}/Stat_Aligned", f"{tb}/i_dut/i_{port}/LaneState",
                f"{tb}/i_far/i_far_pa/LaneRst", f"{tb}/i_far/i_far_pa/Stat_TxReady",
                f"{tb}/i_far/i_far_pa/Stat_RxReady", f"{tb}/i_far/i_far_core/LaneState"]

    def files(self, compile_order: list[tuple[str, Path]]) -> list[tuple[str, Path]]:
        """The sources of the system, then the far end and the bench."""
        return (compile_order + [(FAR_END_LIBRARY, f) for f in FAR_END_FILES]
                + [(self.library, self.directory / "sim" / "tb" / f"{self.top}.vhd")])


def follower(wall: dict[int, float]):
    """Line callback of the simulators: notes the wall-clock time of every event of the bench and prints it."""

    def on_line(line: str, seconds: float) -> None:
        m = EVENT.search(line)
        if m:
            wall[int(m.group(3))] = seconds
            print(f"  {seconds / 60:6.1f} min  {m.group(0)}", flush=True)

    return on_line


def finish(sim: Simulation, run_dir: Path, wall: dict[int, float], start: float, note: str = "") -> int:
    vectors = sim.vectors
    (run_dir / vectors.WALL).write_text("".join(f"{line} {s:.1f}\n" for line, s in sorted(wall.items())),
                                        encoding="utf-8")
    print(f"{run_dir.name}: simulation {time.time() - start:.0f} s{note}", flush=True)
    return vectors.check(run_dir)


# --- xsim -----------------------------------------------------------------------------------------------------------
def xsim_out(sim: Simulation) -> Path:
    return Path(os.environ.get("RS_XSIM_OUT", ROOT / "vivado_out" / "xsim" / sim.name))


def xsim_build(sim: Simulation, hdl: Path) -> tuple[Path, list[str]]:
    """Generates the wizard instance, compiles everything and elaborates the bench; returns the directory of the
    simulation and the libraries."""
    ip = "ip" if sim.refclk_mhz == xsim.REFCLK_MHZ else f"ip_refclk_{sim.refclk_mhz}"
    export = xsim.generate_gtw(xsim_out(sim) / ip, sim.refclk_mhz)
    files = sim.files(xsim.compile_order(hdl / f"{sim.name}_compile_order.txt"))
    xsim.compile_all(export, files)
    libraries = list(dict.fromkeys(library for library, _ in files))
    xsim_elaborate(sim, export, libraries)
    return export, libraries


def xsim_elaborate(sim: Simulation, export: Path, libraries: list[str], again: bool = False) -> None:
    generics = sim.bench_generics()
    if sim.refclk_mhz != xsim.REFCLK_MHZ:
        generics["RefClkKhz_g"] = round(float(sim.refclk_mhz) * 1000)
    xsim.elaborate(export, f"{sim.library}.{sim.top}", libraries, generics, sim.top, trace=True, again=again)


def xsim_simulate(sim: Simulation, export: Path, libraries: list[str], name: str, profile: str, seed: int,
                  tests: list[str] | None) -> int:
    vectors = sim.vectors
    run_dir = xsim_out(sim) / "runs" / name
    shutil.rmtree(run_dir, ignore_errors=True)
    vectors.write(run_dir, profile, seed, tests)
    log_name = f"simulate_{sim.top}.log"
    for attempt in range(1, xsim.MODEL_ATTEMPTS + 1):
        for f in [vectors.EVENTS, VCD, *(vectors.rx_file(vc) for vc in range(8))]:
            (export / f).unlink(missing_ok=True)
        shutil.copy(run_dir / vectors.STIMULUS, export / vectors.STIMULUS)
        wall: dict[int, float] = {}
        start = time.time()
        log = xsim.simulate(export, sim.top, log_name, follower(wall), sim.probes(), VCD)
        if xsim.model_error(log) and attempt < xsim.MODEL_ATTEMPTS:
            print(f"{name}: power-up error of the transceiver model ({xsim.MODEL_ERROR}), attempt {attempt} of "
                  f"{xsim.MODEL_ATTEMPTS}, {time.time() - start:.0f} s; elaborating again", flush=True)
            xsim_elaborate(sim, export, libraries, again=True)
            continue
        break
    for f in export.iterdir():
        if f.name in (vectors.EVENTS, VCD, log_name) or f.name.startswith("rx_vc"):
            shutil.copy(f, run_dir / f.name)
    if re.search(r"FATAL|Fatal|Failure:", log):
        print(f"{name}: the simulator stopped with an error, see {run_dir / log_name}")
    note = f", attempt {attempt} of {xsim.MODEL_ATTEMPTS}" if attempt > 1 else ""
    return finish(sim, run_dir, wall, start, note)


# --- GHDL -----------------------------------------------------------------------------------------------------------
def ghdl_out(sim: Simulation) -> Path:
    return Path(os.environ.get("RS_GHDL_OUT", ROOT / "sim_out" / "ghdl" / sim.name))


def ghdl_build(sim: Simulation, hdl: Path) -> Path:
    """Analyses everything; returns the directory of the libraries."""
    work = ghdl_out(sim) / "work"
    ghdl.analyse(work, sim.files(ghdl.compile_order(hdl / f"{sim.name}_compile_order.txt")))
    return work


def ghdl_simulate(sim: Simulation, work: Path, name: str, profile: str, seed: int, tests: list[str] | None) -> int:
    vectors = sim.vectors
    run_dir = ghdl_out(sim) / "runs" / name
    shutil.rmtree(run_dir, ignore_errors=True)
    vectors.write(run_dir, profile, seed, tests)
    wall: dict[int, float] = {}
    start = time.time()
    log_name = f"simulate_{sim.top}.log"
    log, code = ghdl.simulate(work, sim.library, sim.top, sim.bench_generics(), run_dir, log_name, follower(wall),
                              sim.probes(), VCD)
    if code != 0 or re.search(r"\((assertion|report) (error|failure)\)", log):
        print(f"{name}: the simulator stopped with an error (exit code {code}), see {run_dir / log_name}")
    return finish(sim, run_dir, wall, start)


# --- command line ---------------------------------------------------------------------------------------------------
def main(sim: Simulation, simulator: str, description: str) -> int:
    ht = sim.vectors.ht
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("tests", nargs="*", help="test cases (default: all)")
    parser.add_argument("--profile", choices=ht.PROFILES, default="sim")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--each", action="store_true", help="one simulation per test case")
    parser.add_argument("--hdl", type=Path, default=sim.directory / "hdl", help="directory of the generated top level")
    if simulator == "xsim":
        parser.add_argument("--refclk-mhz", choices=(xsim.REFCLK_MHZ, "100"), default=xsim.REFCLK_MHZ,
                            help="frequency of the transceiver reference clock in MHz")
    parser.add_argument("--sysclk-mhz", choices=(SYSCLK_MHZ, "150"), default=SYSCLK_MHZ,
                        help="frequency of the system clock in MHz (the hardware builds run at 150)")
    args = parser.parse_args()
    suffix = ""
    if simulator == "xsim" and args.refclk_mhz != xsim.REFCLK_MHZ:
        sim.refclk_mhz = args.refclk_mhz
        suffix = f"_refclk_{args.refclk_mhz}"
    if args.sysclk_mhz != SYSCLK_MHZ:
        sim.sysclk_mhz = args.sysclk_mhz
        suffix += f"_sysclk_{args.sysclk_mhz}"

    names = args.tests or None
    ht.select_tests(names)                  # unknown names stop here, before the build
    if args.each:
        runs = [(t + suffix, [t]) for t in (names or [ht.test_name(t) for t in ht.TESTS])]
    else:
        runs = [(("_".join(names) if names else "all") + suffix, names)]
    if simulator == "xsim":
        export, libraries = xsim_build(sim, args.hdl)
        failed = [name for name, tests in runs
                  if xsim_simulate(sim, export, libraries, name, args.profile, args.seed, tests)]
    else:
        work = ghdl_build(sim, args.hdl)
        failed = [name for name, tests in runs if ghdl_simulate(sim, work, name, args.profile, args.seed, tests)]
    print(f"{len(runs) - len(failed)} of {len(runs)} simulations passed" + (f"; failed: {failed}" if failed else ""))
    return 1 if failed else 0
