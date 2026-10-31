#---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
#---------------------------------------------------------------------------------------------------
# Pins of the transceiver reference clock of the system spfi_encrypt on the AMD VCK190, reference clock si570 of
# tcl/build.tcl (default): GTY_REFCLK0 of GTY quad 201, which receives output Q0 of the clock buffer SI53340 (U206);
# with header J306 off (default) the buffer passes the programmable oscillator Si570 (U205) (VCK190 Evaluation Board
# User Guide UG1366 v1.1, section "Programmable FMCP MGT SI570 Clock with Buffer"). The Si570 runs at 100.000 MHz
# after every power-up, the default frequency of tcl/build.tcl with this source; an image for 156.25 MHz needs the
# Si570 set to 156.25 MHz after every power-up. The transceivers of quad 200 use this reference clock of the adjacent
# quad 201.
#
# Documentation: systems/spfi_encrypt/targets/vck190/README.md
#---------------------------------------------------------------------------------------------------

set_property PACKAGE_PIN AB11 [get_ports GtRefClk_P]
set_property PACKAGE_PIN AB10 [get_ports GtRefClk_N]
