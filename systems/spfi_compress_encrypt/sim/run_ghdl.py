# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""System simulation of spfi_compress_encrypt with the behavioural model of the GTY adapter at both ends of the link
(GHDL).

Usage: python systems/spfi_compress_encrypt/sim/run_ghdl.py [--profile sim] [--seed N] [--each] [--hdl DIR]
       [TC-RS-HW-nn ...]

tools/sysbench.py describes the runs and their results (sim_out/ghdl/spfi_compress_encrypt, or RS_GHDL_OUT).
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "tools"))
sys.path.insert(0, str(HERE))

import sysbench  # noqa: E402
import vectors  # noqa: E402

if __name__ == "__main__":
    sys.exit(sysbench.main(sysbench.Simulation("spfi_compress_encrypt", vectors), "ghdl", __doc__.splitlines()[0]))
