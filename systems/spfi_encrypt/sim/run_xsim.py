# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""System simulation of spfi_encrypt with the AMD transceiver model at both ends of the link (xsim).

Usage: python systems/spfi_encrypt/sim/run_xsim.py [--profile sim] [--seed N] [--each] [--hdl DIR]
       [TC-RS-HW-nn ...]

tools/sysbench.py describes the runs and their results (vivado_out/xsim/spfi_encrypt, or RS_XSIM_OUT).
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "tools"))
sys.path.insert(0, str(HERE))

import sysbench  # noqa: E402
import vectors  # noqa: E402

if __name__ == "__main__":
    sys.exit(sysbench.main(sysbench.Simulation("spfi_encrypt", vectors), "xsim", __doc__.splitlines()[0]))
