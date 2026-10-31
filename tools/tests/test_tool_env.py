# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Tests of the environment of the tools that tools/xsim.py and tools/ghdl.py start: their temporary files go to the
directory tmp in the working directory of the tool, with the outputs of the run."""

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import ghdl  # noqa: E402
import xsim  # noqa: E402

# A stand-in for a tool: writes the three temporary directories of its environment to its log
TOOL = [sys.executable, "-c", "import os; print(os.environ['TEMP'], os.environ['TMP'], os.environ['TMPDIR'])"]


def test_a_tool_of_xsim_has_its_temporary_directory_in_its_working_directory(tmp_path):
    xsim.run(TOOL, tmp_path, "tool.log")
    assert (tmp_path / "tmp").is_dir()
    assert (tmp_path / "tool.log").read_text(encoding="utf-8").split() == [str(tmp_path / "tmp")] * 3


def test_a_tool_of_ghdl_has_its_temporary_directory_in_its_working_directory(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    ghdl.run(TOOL, work, tmp_path / "tool.log")
    assert (work / "tmp").is_dir()
    assert (tmp_path / "tool.log").read_text(encoding="utf-8").split() == [str(work / "tmp")] * 3
