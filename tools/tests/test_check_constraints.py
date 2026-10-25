# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Tests of tools/vivado/check_constraints.tcl in tclsh with a stand-in of get_msg_config, the message counts of
link_design (skipped where no tclsh is found): no message passes, every message of a constraint that did not apply
stops the implementation, the critical warning olo_base_ram_sdp-1 of the Open Logic constraint script included."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "vivado" / "check_constraints.tcl"
TCLSH = shutil.which("tclsh") or shutil.which("tclsh8.6") or shutil.which("tclsh86")


def run(tmp_path: Path, counts: dict[str, int]) -> subprocess.CompletedProcess:
    if not TCLSH:
        pytest.skip("no tclsh")
    pairs = " ".join("{" + k + "} " + str(v) for k, v in counts.items())
    test = tmp_path / "test.tcl"
    test.write_text(f"set counts [dict create {pairs}]\n"
                    "proc get_msg_config {-id id -count} {\n"
                    "    global counts\n"
                    "    if {[dict exists $counts $id]} { return [dict get $counts $id] }\n"
                    "    return 0\n"
                    "}\n"
                    f"source {{{SCRIPT.as_posix()}}}\n", encoding="utf-8")
    return subprocess.run([TCLSH, str(test)], capture_output=True, text=True, check=False)


def test_no_message_passes(tmp_path):
    result = run(tmp_path, {})
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "check_constraints: every constraint applied"


def test_a_ram_without_read_register_stops_the_implementation(tmp_path):
    result = run(tmp_path, {"olo_base_ram_sdp 1": 1})
    assert result.returncode != 0
    # get_msg_config counts the message olo_base_ram_sdp-1 of send_msg_id under the id "olo_base_ram_sdp 1"
    assert "olo_base_ram_sdp 1 (olo_base_ram_sdp-1, distributed RAM of Open Logic" in result.stderr
    assert "without its first read data register): 1" in result.stderr


@pytest.mark.parametrize("msg_id", ["Vivado 12-180", "Vivado 12-1008", "Vivado 12-4739", "Designutils 20-1280"])
def test_every_message_of_a_lost_constraint_stops_the_implementation(tmp_path, msg_id):
    result = run(tmp_path, {msg_id: 2})
    assert result.returncode != 0
    assert f"{msg_id} (" in result.stderr and "): 2" in result.stderr
