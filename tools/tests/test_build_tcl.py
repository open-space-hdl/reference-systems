# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Tests of write_build_info and timing_verdict of tcl/build.tcl of the VCK190 targets. The procedures run in tclsh
with stand-ins of the Vivado commands they call (skipped where no tclsh is found): the file holds the text, with the
timing verdict after an implementation, a file that stays empty stops the script, and the verdict follows the worst
setup and hold slack. Every target has the same procedures."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted(ROOT.glob("systems/*/targets/vck190/tcl/build.tcl"))
TCLSH = shutil.which("tclsh") or shutil.which("tclsh8.6") or shutil.which("tclsh86")

STAND_INS = """
proc version {args} { return 2025.2 }
proc current_project {} { return prj }
proc get_ports {name} { return $name }
proc get_clocks {args} { return gt_refclk }
proc get_property {args} {
    switch -- [lindex $args end-1] {
        PART { return xcvc1902-vsva2197-2MP-e-S }
        PACKAGE_PIN { return [dict get {GtRefClk_P AB11 GtRefClk_N AB10} [lindex $args end]] }
        PERIOD { return 10.000 }
    }
}
set name demo_vck190
set step rtl
set refclk si570
set refclk_mhz 100
"""


def procedure(script: Path, name: str = "write_build_info") -> str:
    match = re.search(rf"^proc {name} \{{[^\n]*\}} \{{\n.*?^\}}\n", script.read_text(encoding="utf-8"),
                      re.MULTILINE | re.DOTALL)
    assert match, f"{script}: no procedure {name}"
    return match.group(0)


def run(tmp_path: Path, script: Path, extra: str = "", timing: str = "") -> tuple[subprocess.CompletedProcess, Path]:
    if not TCLSH:
        pytest.skip("no tclsh")
    out = tmp_path / "build_info.txt"
    # root: a directory that is no repository, so that the git commands fail as on a computer without git
    call = f"write_build_info {{{out.as_posix()}}}" + (f" {{{timing}}}" if timing else "") + "\n"
    body = (STAND_INS + f"set root {{{(tmp_path / 'no_repository').as_posix()}}}\n" + procedure(script) + extra
            + call)
    test = tmp_path / "test.tcl"
    test.write_text(body, encoding="utf-8")
    return subprocess.run([TCLSH, str(test)], capture_output=True, text=True, check=False), out


def test_every_target_has_the_same_procedures():
    assert len(SCRIPTS) == 3
    assert len({procedure(s) for s in SCRIPTS}) == 1
    assert len({procedure(s, "timing_verdict") for s in SCRIPTS}) == 1


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda s: s.parts[-5])
def test_build_info_holds_the_text_with_lf_line_ends(tmp_path, script):
    result, out = run(tmp_path, script)
    assert result.returncode == 0, result.stdout + result.stderr
    data = out.read_bytes()
    assert b"\r" not in data and data.endswith(b"\n")
    lines = data.decode("utf-8").splitlines()
    assert re.fullmatch(r"demo_vck190, step rtl, \d{4}-\d\d-\d\d \d\d:\d\d:\d\d", lines[0])
    refclk = "reference clock si570 at 100 MHz, gt_refclk period 10.000 ns: GtRefClk_P AB11, GtRefClk_N AB10"
    assert lines[1:] == ["Vivado 2025.2, part xcvc1902-vsva2197-2MP-e-S", refclk,
                         "repository unknown (git not available)"]


def test_a_build_info_that_stays_empty_stops_the_script(tmp_path):
    # the text does not reach the file (as once in an rtl step, when it went to vivado.log instead)
    result, out = run(tmp_path, SCRIPTS[0], "rename puts tcl_puts\nproc puts {args} {}\n")
    assert result.returncode != 0
    assert out.exists() and out.stat().st_size == 0
    assert re.search(r"build_info\.txt has 0 bytes after writing, expected \d+", result.stderr)


def test_build_info_ends_with_the_timing_verdict_after_an_implementation(tmp_path):
    result, out = run(tmp_path, SCRIPTS[0], timing="TIMING MET: WNS 0.778 ns, WHS 0.011 ns")
    assert result.returncode == 0, result.stdout + result.stderr
    assert out.read_text(encoding="utf-8").splitlines()[-1] == "TIMING MET: WNS 0.778 ns, WHS 0.011 ns"


@pytest.mark.parametrize("wns, whs, verdict", [
    ("0.778", "0.011", "TIMING MET: WNS 0.778 ns, WHS 0.011 ns"),
    ("0.000", "0.000", "TIMING MET: WNS 0.000 ns, WHS 0.000 ns"),
    ("-0.165", "0.002", "TIMING NOT MET: WNS -0.165 ns, WHS 0.002 ns"),
    ("0.079", "-0.039", "TIMING NOT MET: WNS 0.079 ns, WHS -0.039 ns"),
    ("", "0.011", "TIMING NOT MET: no timed setup or hold path (WNS '', WHS '0.011')"),
])
def test_timing_verdict(tmp_path, wns, whs, verdict):
    if not TCLSH:
        pytest.skip("no tclsh")
    test = tmp_path / "verdict.tcl"
    test.write_text(procedure(SCRIPTS[0], "timing_verdict") + f"puts [timing_verdict {{{wns}}} {{{whs}}}]\n",
                    encoding="utf-8")
    result = subprocess.run([TCLSH, str(test)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == verdict
