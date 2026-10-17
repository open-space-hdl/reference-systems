#---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
#---------------------------------------------------------------------------------------------------
# Pins of the transceiver reference clock of the system spfi_encrypt on the AMD VCK190, reference clock 8a34001 of
# tcl/build.tcl (default): MGTREFCLK1 of GTY quad 200, which receives output Q1 of the clock generator 8A34001 (U219)
# (VCK190 Evaluation Board User Guide UG1366 v1.1, section "GTY200: QSFP1"). Q1 must run at 156.25 MHz.
#
# Documentation: systems/spfi_encrypt/targets/vck190/README.md
#---------------------------------------------------------------------------------------------------

set_property PACKAGE_PIN AD11 [get_ports GtRefClk_P]
set_property PACKAGE_PIN AD10 [get_ports GtRefClk_N]
