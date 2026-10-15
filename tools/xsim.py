# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""The AMD Vivado simulator (xsim) for system simulations with the AMD transceiver model.

OpenFibre simulates its GTY adapter ofb_pa_gty in the same way (tools/run_xsim.py of OpenFibre): the transceiver
wizard instance ofb_gtw is created with the script of OpenFibre and its simulation sources are exported; Open Logic
FT, the cores and the generated top level are compiled as the compile order of the Composer lists them. The
transceiver model sometimes fails to power up in an elaborated snapshot (DIV_VAL_CHK, see the verification report of
ofb_pa_gty in OpenFibre); such a run is elaborated and simulated again.

The Vivado installation comes from VIVADO_PATH, or from the vivado found on the PATH.
"""

import hashlib
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORES = ROOT / "cores"
PART = "xcvc1902-vsva2197-2MP-e-S"
REFCLK_MHZ = "156.25"
GTW_TCL = CORES / "openfibre" / "hdl" / "ofb_pa_gty" / "tcl" / "ofb_gtw.tcl"
VENDOR_LIBRARIES = ("unisims_ver", "unisim", "unimacro_ver", "secureip", "xpm")
MODEL_ERROR = "DIV_VAL_CHK"
MODEL_ATTEMPTS = 3


def vivado_bin() -> Path:
    if "VIVADO_PATH" in os.environ:
        return Path(os.environ["VIVADO_PATH"]) / "bin"
    found = shutil.which("vivado")
    if found:
        return Path(found).parent
    raise SystemExit("Vivado not found: set VIVADO_PATH to the Vivado installation or put vivado on the PATH")


def tool(name: str) -> str:
    exe = vivado_bin() / (name + (".bat" if os.name == "nt" else ""))
    if not exe.exists():
        raise SystemExit(f"{exe} not found")
    return str(exe)


def run(cmd: list[str], cwd: Path, log: str) -> None:
    with open(cwd / log, "w", encoding="utf-8") as f:
        # stdin closed: with an inherited stdin, export_simulation writes vlog.prj to stdout
        result = subprocess.run(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                check=False)
    if result.returncode != 0:
        text = (cwd / log).read_text(encoding="utf-8", errors="replace").splitlines()
        print("\n".join(text[-30:]), flush=True)
        raise SystemExit(f"{Path(cmd[0]).stem} failed, see {cwd / log}")


def generate_gtw(out: Path) -> Path:
    """Creates ofb_gtw and exports its simulation sources once per version of the wizard script; returns the export
    directory, in which the libraries are compiled and the simulations run."""
    export = out / "export" / "ofb_gtw" / "xsim"
    prj = export / "vlog.prj"
    stamp = out / ".gtw_generated"
    digest = hashlib.sha1(GTW_TCL.read_bytes()).hexdigest()
    if prj.exists() and prj.stat().st_size > 0 and stamp.exists() and stamp.read_text() == digest:
        return export
    if export.exists():
        for name in (".ip_compiled", ".hdl_compiled"):
            (export / name).unlink(missing_ok=True)
        prj.unlink(missing_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    script = out / "generate.tcl"
    script.write_text(
        f"create_project -force ip {out.as_posix()}/project -part {PART}\n"
        f"source {GTW_TCL.as_posix()}\n"
        f"ofb_gtw_create ofb_gtw {REFCLK_MHZ}\n"
        "generate_target all [get_ips ofb_gtw]\n"
        "export_simulation -of_objects [get_files ofb_gtw.xci] -simulator xsim "
        f"-directory {(out / 'export').as_posix()} -force\n",
        encoding="utf-8")
    run([tool("vivado"), "-mode", "batch", "-nojournal", "-nolog", "-source", str(script)], out, "generate.log")
    if not prj.exists() or prj.stat().st_size == 0:
        # export_simulation sometimes prints the file list instead of writing vlog.prj: recover it from the log
        lines = (out / "generate.log").read_text(encoding="utf-8", errors="replace").splitlines()
        start = next((i for i, line in enumerate(lines) if line.startswith("verilog ")), None)
        end = next((i for i, line in enumerate(lines) if line.strip() == "nosort"), None)
        if start is None or end is None:
            raise SystemExit(f"{prj} is empty and the file list is not in the log")
        prj.write_text("\n".join(lines[start:end + 1]) + "\n", encoding="utf-8")
    stamp.write_text(digest)
    return export


def ip_libraries(export: Path) -> list[str]:
    """The libraries of the exported wizard sources, in the order of vlog.prj."""
    names = re.findall(r"^verilog (\S+)", (export / "vlog.prj").read_text(encoding="utf-8"), re.MULTILINE)
    return list(dict.fromkeys(names))


def compile_order(path: Path) -> list[tuple[str, Path]]:
    """Library and file of every "file" line of a compile order exported by the Composer: the sources relative to
    cores/ (the directory of a source is its repository name), the top level relative to the compile order."""
    path = path.resolve()
    sources, files = set(), []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "source":
            sources.add(parts[1])
        elif len(parts) == 3 and parts[0] == "file":
            library, rel = parts[1], parts[2]
            base = CORES if rel.split("/")[0] in sources else path.parent
            files.append((library, base / rel))
    return files


def compile_all(export: Path, files: list[tuple[str, Path]]) -> None:
    """Compiles the wizard sources once and the VHDL files in their order, one xvhdl call per run of files of one
    library; the VHDL files are compiled again when one of them changed."""
    if not (export / ".ip_compiled").exists():
        run([tool("xvlog"), "--incr", "--relax", "-prj", "vlog.prj"], export, "compile_ip.log")
        (export / ".ip_compiled").touch()
    digest = hashlib.sha1()
    for library, path in files:
        digest.update(f"{library} {path}\n".encode())
        digest.update(path.read_bytes())
    stamp = export / ".hdl_compiled"
    if stamp.exists() and stamp.read_text() == digest.hexdigest():
        return
    runs: list[tuple[str, list[str]]] = []
    for library, path in files:
        if runs and runs[-1][0] == library:
            runs[-1][1].append(str(path))
        else:
            runs.append((library, [str(path)]))
    for k, (library, paths) in enumerate(runs):
        run([tool("xvhdl"), "--2008", "--relax", "-work", library] + paths, export, f"compile_{k:02d}_{library}.log")
    stamp.write_text(digest.hexdigest())


def elaborate(export: Path, top: str, libraries: list[str], generics: dict[str, object], snapshot: str) -> None:
    libs = []
    for library in libraries + ip_libraries(export) + list(VENDOR_LIBRARIES):
        libs += ["-L", library]
    gens = []
    for name, value in generics.items():
        gens += ["--generic_top", f"{name}={value}"]
    # options in a file: the batch wrapper of xelab splits arguments at "="
    opts = ["--relax", "--mt", "4"] + libs + gens + ["--snapshot", snapshot, top, "xil_defaultlib.glbl"]
    (export / f"elaborate_{snapshot}.opt").write_text(" ".join(opts) + "\n", encoding="utf-8")
    run([tool("xelab"), "-f", f"elaborate_{snapshot}.opt"], export, f"elaborate_{snapshot}.log")


def simulate(export: Path, snapshot: str, log: str, on_line: Callable[[str, float], None] | None = None,
             probes: list[str] | None = None, vcd: str = "probes.vcd") -> str:
    """Runs a snapshot to its end and returns the log; on_line sees every new line of the log with the wall-clock
    seconds since the start, while the simulation runs. probes: hierarchical names of signals whose changes are
    written to the VCD file vcd (for the analysis of a failure; a name that the snapshot does not have is noted in
    the log)."""
    path = export / log
    path.unlink(missing_ok=True)
    cmd = [tool("xsim"), snapshot, "-log", log]
    if probes:
        script = export / f"{snapshot}_probes.tcl"
        lines = [f"open_vcd {vcd}"]
        lines += [f'if {{[catch {{log_vcd [get_objects {p}]}} msg]}} {{puts "probe {p}: $msg"}}' for p in probes]
        lines += ["run all", "close_vcd", "quit"]
        script.write_text("\n".join(lines) + "\n", encoding="utf-8")
        cmd += ["-tclbatch", script.name]
    else:
        cmd += ["-R"]
    start = time.time()
    proc = subprocess.Popen(cmd, cwd=export, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL)
    seen = 0
    pending = ""

    def follow() -> None:
        nonlocal seen, pending
        if not path.exists():
            return
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
    return path.read_text(encoding="utf-8", errors="replace")


def model_error(log: str) -> bool:
    return MODEL_ERROR in log
