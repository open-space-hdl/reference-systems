# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Tests of tools/sysbench.py: the sources, the far-end generics and the probes of the system testbench of every
system, from its Composer file."""

import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import sysbench  # noqa: E402
from composer_system import system_files  # noqa: E402

SYSTEMS = [f.parent.name for f in system_files()]


@pytest.mark.parametrize("name", SYSTEMS)
def test_the_bench_follows_the_system(name):
    sim = sysbench.Simulation(name, vectors=None)
    files = sim.files([("olo", Path("olo.vhd"))])
    assert files[0] == ("olo", Path("olo.vhd"))
    assert files[1:] == [("refsys_tb", f) for f in sysbench.FAR_END_FILES] + [
        (f"{name}_tb", sysbench.ROOT / "systems" / name / "sim" / "tb" / f"{name}_tb.vhd")]
    assert all(f.exists() for _, f in files[1:])
    # the far end has the generics of the SpaceFibre port of the system
    assert sim.far_end_generics() == {"NumLanes_g": 4, "NumVc_g": 2, "VcInDepth_g": 1024}
    probes = sim.probes()
    assert f"/{name}_tb/i_dut/i_pa0/Stat_TxReady" in probes and f"/{name}_tb/i_far/i_far_core/LaneState" in probes
