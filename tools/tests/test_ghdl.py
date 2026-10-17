# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Tests of the compile order of tools/ghdl.py: the files of the behavioural model of the GTY adapter take the place
of the adapter files of a compile order exported by the Composer, on a stand-in for cores/."""

import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import ghdl  # noqa: E402
import xsim  # noqa: E402

MODEL = ["tb/ofb_tb_8b10b_pkg.vhd", "hdl/ofb_pa_gty/sim/ofb_pa_model_pkg.vhd", "hdl/ofb_pa_gty/src/ofb_pa_gty.vhd",
         "hdl/ofb_pa_gty/sim/ofb_pa_gty_model.vhd"]


@pytest.fixture
def cores(tmp_path, monkeypatch):
    root = tmp_path / "cores"
    pa = root / "openfibre" / "hdl" / "ofb_pa_gty"
    pa.mkdir(parents=True)
    (pa / "compile_order_sim.txt").write_text("".join(f + "\n" for f in MODEL), encoding="utf-8")
    monkeypatch.setattr(xsim, "CORES", root)
    monkeypatch.setattr(ghdl, "CORES", root)
    monkeypatch.setattr(ghdl, "PA_SIM_LIST", pa / "compile_order_sim.txt")
    return root


def order(directory: Path, pa_files: list[str]) -> Path:
    lines = ["format composer-compile-order 1",
             "source olo https://github.com/open-space-hdl/open-logic-ft 0", "",
             "source openfibre https://github.com/open-space-hdl/openfibre 0",
             "source opencrypto https://github.com/open-space-hdl/opencrypto 0", "",
             "file olo olo/src/base/vhdl/olo_base_pkg_math.vhd",
             "file openfibre openfibre/hdl/ofb_core/src/ofb_core.vhd"]
    lines += [f"file openfibre openfibre/{f}" for f in pa_files]
    lines += ["file opencrypto opencrypto/hdl/ocr_core/src/ocr_core.vhd", "file system system.vhd"]
    path = directory / "system_compile_order.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("pa_files", [
    ["hdl/ofb_pa_gty/src/ofb_pa_gty.vhd"],
    ["hdl/ofb_pa_gty/src/ofb_pa_gty.vhd", "hdl/ofb_pa_gty/src/ofb_pa_gty_struct.vhd"],
])
def test_the_model_takes_the_place_of_the_gty_adapter(tmp_path, cores, pa_files):
    files = ghdl.compile_order(order(tmp_path, pa_files))
    of = cores / "openfibre"
    assert files == [("olo", cores / "olo/src/base/vhdl/olo_base_pkg_math.vhd"),
                     ("openfibre", of / "hdl/ofb_core/src/ofb_core.vhd"),
                     *[("openfibre", of / f) for f in MODEL],
                     ("opencrypto", cores / "opencrypto/hdl/ocr_core/src/ocr_core.vhd"),
                     ("system", tmp_path / "system.vhd")]


def test_a_compile_order_without_the_adapter_is_refused(tmp_path, cores):
    with pytest.raises(SystemExit, match="lists no file of ofb_pa_gty"):
        ghdl.compile_order(order(tmp_path, []))


def test_openfibre_without_the_model_is_refused(tmp_path, cores, monkeypatch):
    monkeypatch.setattr(ghdl, "PA_SIM_LIST", tmp_path / "missing.txt")
    with pytest.raises(SystemExit, match="behavioural model"):
        ghdl.compile_order(order(tmp_path, ["hdl/ofb_pa_gty/src/ofb_pa_gty.vhd"]))
