# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""GHDL for system simulations with the behavioural model of the GTY adapter of OpenFibre.

The architecture mdl of ofb_pa_gty (OpenFibre user guide, "System simulation with the behavioural adapter") has the
ports and generics of the transceiver wrapper and drives the serial pins with the 8B/10B bit stream, so the generated
top level runs unchanged without the AMD transceiver model. The files of hdl/ofb_pa_gty/compile_order_sim.txt of
OpenFibre take the place of the adapter files of the compile order of the Composer, in the library of OpenFibre; Open
Logic FT, the cores and the top level are compiled as the compile order lists them.

GHDL comes from GHDL_PATH (the directory of the executable), or from the ghdl found on the PATH.
"""

import hashlib
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import xsim

CORES = xsim.CORES
PA_DIR = "openfibre/hdl/ofb_pa_gty/"
PA_SIM_LIST = CORES / "openfibre" / "hdl" / "ofb_pa_gty" / "compile_order_sim.txt"
# The RAMs of Open Logic use shared variables of non-protected types, which GHDL accepts with -frelaxed
ANALYSE_FLAGS = ["--std=08", "-frelaxed", "-Wno-hide", "-Wno-shared"]
# Large arrays in the processes of the benches exceed the default stack limit of GHDL. The warnings of the IEEE
# packages are off, as in run.py (disable_ieee_warnings)
SIM_FLAGS = ["--max-stack-alloc=0", "--ieee-asserts=disable"]


def ghdl() -> str:
    if "GHDL_PATH" in os.environ:
        exe = Path(os.environ["GHDL_PATH"]) / ("ghdl.exe" if os.name == "nt" else "ghdl")
        if not exe.exists():
            raise SystemExit(f"{exe} not found")
        return str(exe)
    found = shutil.which("ghdl")
    if not found:
        raise SystemExit("GHDL not found: set GHDL_PATH to the directory of ghdl or put ghdl on the PATH")
    return found


def compile_order(path: Path) -> list[tuple[str, Path]]:
    """The files of a compile order exported by the Composer, with the files of the behavioural adapter model in
    place of those of the GTY adapter."""
    if not PA_SIM_LIST.exists():
        raise SystemExit(f"{PA_SIM_LIST} not found: the system simulation in GHDL needs OpenFibre with the behavioural "
                         "model of ofb_pa_gty")
    root = CORES / "openfibre"
    model = [root / line.strip() for line in PA_SIM_LIST.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")]
    files, placed = [], False
    for library, file in xsim.compile_order(path):
        if file.resolve().as_posix().startswith((CORES / PA_DIR).resolve().as_posix()):
            if not placed:
                files += [(library, f) for f in model]
                placed = True
            continue
        files.append((library, file))
    if not placed:
        raise SystemExit(f"{path} lists no file of ofb_pa_gty")
    return files


def run(cmd: list[str], cwd: Path, log: Path) -> None:
    with open(log, "w", encoding="utf-8") as f:
        result = subprocess.run(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=xsim.tool_env(cwd), check=False)
    if result.returncode != 0:
        text = log.read_text(encoding="utf-8", errors="replace").splitlines()
        print("\n".join(text[-30:]), flush=True)
        raise SystemExit(f"ghdl failed, see {log}")


def analyse(work: Path, files: list[tuple[str, Path]]) -> None:
    """Analyses the files in their order, one call per run of files of the same library, all libraries in the
    directory work. When a file or the order changed, the libraries are analysed again from scratch."""
    digest = hashlib.sha1()
    for library, path in files:
        digest.update(f"{library} {path}\n".encode())
        digest.update(path.read_bytes())
    stamp = work / ".analysed"
    if stamp.exists() and stamp.read_text() == digest.hexdigest():
        return
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    groups: list[tuple[str, list[Path]]] = []
    for library, path in files:
        if groups and groups[-1][0] == library:
            groups[-1][1].append(path)
        else:
            groups.append((library, [path]))
    for index, (library, paths) in enumerate(groups):
        cmd = [ghdl(), "-a", *ANALYSE_FLAGS, f"--work={library}", f"--workdir={work}", f"-P{work}"]
        run(cmd + [str(p) for p in paths], work, work / f"analyse_{index:02d}_{library}.log")
    stamp.write_text(digest.hexdigest())


def simulate(work: Path, library: str, top: str, generics: dict[str, object], run_dir: Path, log: str,
             on_line: Callable[[str, float], None] | None = None, probes: list[str] | None = None,
             vcd: str = "probes.vcd") -> tuple[str, int]:
    """Elaborates and runs the top level in run_dir, the working directory of its files, and returns the log and the
    exit code; on_line sees every new line of the log with the wall-clock seconds since the start. probes:
    hierarchical names of signals whose changes are written to the VCD file vcd."""
    path = run_dir / log
    path.unlink(missing_ok=True)
    cmd = [ghdl(), "--elab-run", "--std=08", "-frelaxed", f"--work={library}", f"--workdir={work}", f"-P{work}", top]
    cmd += [f"-g{name}={value}" for name, value in generics.items()]
    cmd += SIM_FLAGS
    if probes:
        options = run_dir / "probes.opt"
        options.write_text("$ version 1.1\n" + "".join(p + "\n" for p in probes), encoding="utf-8")
        cmd += [f"--vcd={vcd}", f"--read-wave-opt={options.name}"]
    start = time.time()
    with open(path, "wb") as f:
        proc = subprocess.Popen(cmd, cwd=run_dir, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=xsim.tool_env(run_dir))
    seen = 0
    pending = ""

    def follow() -> None:
        nonlocal seen, pending
        with open(path, "rb") as f:
            f.seek(seen)
            data = f.read()
            seen += len(data)
        pending += data.decode("utf-8", errors="replace").replace("\r", "")
        *complete, pending = pending.split("\n")
        if on_line:
            for line in complete:
                on_line(line, time.time() - start)

    while proc.poll() is None:
        time.sleep(2)
        follow()
    follow()
    return path.read_text(encoding="utf-8", errors="replace"), proc.returncode
