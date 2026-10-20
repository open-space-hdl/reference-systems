#---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
#---------------------------------------------------------------------------------------------------
# Board constraints of the system spfi_compress on the AMD VCK190 evaluation board (XCVC1902-VSVA2197):
# pins of GTY quad 200 (QSFP1) and of the LEDs, the transceiver reference clock. The pins are those of the OpenFibre
# reference design (cores/openfibre/hdl/ofb_vck190/constr/ofb_vck190.xdc). The pins of the reference clock are in
# spfi_compress_vck190_refclk_<source>.xdc, of which tcl/build.tcl loads the one of the selected reference
# clock. The timing exceptions of the system itself are in
# systems/spfi_compress/hdl/spfi_compress.xdc (scoped to the entity spfi_compress).
#
# Documentation: systems/spfi_compress/targets/vck190/README.md
#---------------------------------------------------------------------------------------------------

# System clock: the constraints of the CIPS IP create clk_pl_0 (150 MHz); the lane clock of the system is the
# transmit clock of the transceivers.

# Transceiver reference clock gt_refclk: tcl/build.tcl writes its clock constraint at the selected frequency
# into the project directory (spfi_compress_vck190_refclk_clock.xdc), the pins are in
# spfi_compress_vck190_refclk_<source>.xdc.

# QSFP1: GTY quad 200 (GTY_QUAD_X1Y0), channels 0 to 3 = lanes 0 to 3
set_property LOC GTY_QUAD_X1Y0 [get_cells -hierarchical -filter {REF_NAME == GTYE5_QUAD}]
set_property PACKAGE_PIN AF2 [get_ports {Qsfp_RxP[0]}]
set_property PACKAGE_PIN AF1 [get_ports {Qsfp_RxN[0]}]
set_property PACKAGE_PIN AF7 [get_ports {Qsfp_TxP[0]}]
set_property PACKAGE_PIN AF6 [get_ports {Qsfp_TxN[0]}]
set_property PACKAGE_PIN AE4 [get_ports {Qsfp_RxP[1]}]
set_property PACKAGE_PIN AE3 [get_ports {Qsfp_RxN[1]}]
set_property PACKAGE_PIN AE9 [get_ports {Qsfp_TxP[1]}]
set_property PACKAGE_PIN AE8 [get_ports {Qsfp_TxN[1]}]
set_property PACKAGE_PIN AD2 [get_ports {Qsfp_RxP[2]}]
set_property PACKAGE_PIN AD1 [get_ports {Qsfp_RxN[2]}]
set_property PACKAGE_PIN AD7 [get_ports {Qsfp_TxP[2]}]
set_property PACKAGE_PIN AD6 [get_ports {Qsfp_TxN[2]}]
set_property PACKAGE_PIN AC4 [get_ports {Qsfp_RxP[3]}]
set_property PACKAGE_PIN AC3 [get_ports {Qsfp_RxN[3]}]
set_property PACKAGE_PIN AC9 [get_ports {Qsfp_TxP[3]}]
set_property PACKAGE_PIN AC8 [get_ports {Qsfp_TxN[3]}]

# LEDs (GPIO_LED_0 and GPIO_LED_1)
set_property PACKAGE_PIN H34 [get_ports {Led[0]}]
set_property PACKAGE_PIN J33 [get_ports {Led[1]}]
set_property IOSTANDARD LVCMOS18 [get_ports {Led[*]}]
set_false_path -to [get_ports {Led[*]}]
