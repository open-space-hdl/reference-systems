#---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
#---------------------------------------------------------------------------------------------------
# Builds the system spfi_encrypt for the AMD VCK190: project in vivado_out/spfi_encrypt_vck190, sources of the
# compile order exported by the Composer, transceiver wizard instance, CIPS block design, constraints, synthesis,
# implementation and device image.
#
# Usage (from the repository root, with the submodules in cores/ checked out):
#   vivado -mode batch -source systems/spfi_encrypt/targets/vck190/tcl/build.tcl [-tclargs <step>]
#   <step>: project (create the project only), synth, impl, all (default: all, device image included)
#
# The environment variable RS_VIVADO_OUT selects another project directory. On Windows the device image step
# compiles the platform loader firmware in a deep directory below the project; with a project path of more than
# about 40 characters the compiler exceeds the Windows path length limit and write_device_image fails ("opening
# dependency file ... No such file or directory"). Use a short path there, for example RS_VIVADO_OUT=C:/rs.
#
# Documentation: systems/spfi_encrypt/targets/vck190/README.md
#---------------------------------------------------------------------------------------------------

set target_dir [file normalize [file join [file dirname [info script]] ..]]
set system_dir [file normalize [file join $target_dir .. ..]]
set root [file normalize [file join $system_dir .. ..]]
set system [file tail $system_dir]
set name ${system}_[file tail $target_dir]

if {[info exists env(RS_VIVADO_OUT)] && $env(RS_VIVADO_OUT) ne ""} {
    set out [file normalize $env(RS_VIVADO_OUT)]
} else {
    set out [file join $root vivado_out $name]
}
if {$tcl_platform(platform) eq "windows" && [string length $out] > 40} {
    puts "WARNING: project path $out has more than 40 characters, write_device_image may fail on Windows (path\
        length limit); set RS_VIVADO_OUT to a short directory, for example C:/rs"
}
set step [expr {[llength $argv] > 0 ? [lindex $argv 0] : "all"}]

# Frequency of the clock domain sys of the Composer file (port Clk_sys of the system)
set clk_sys_mhz 200

create_project -force $name $out -part xcvc1902-vsva2197-2MP-e-S
set_property target_language VHDL [current_project]

# Sources of the system: Open Logic FT, the cores and the generated top level, as the compile order of the Composer
# lists them (the repositories are the submodules in cores/)
source [file join $root tools vivado compile_order.tcl]
rs_add_compile_order [file join $system_dir hdl ${system}_compile_order.txt] [file join $root cores]

# Board top level
set f [add_files -norecurse [glob [file join $target_dir src *.vhd]]]
set_property file_type {VHDL 2008} $f

# Transceiver wizard instance of the GTY adapter ofb_pa_gty (156.25 MHz reference clock)
source [file join $root cores openfibre hdl ofb_pa_gty tcl ofb_gtw.tcl]
ofb_gtw_create ofb_gtw 156.25
generate_target all [get_ips ofb_gtw]

# Control, interfaces and processing system (CIPS): every Versal design needs it, the platform management
# controller configures the device (JTAG boot). One clock of the PMC clock generator for the programmable logic,
# pl0 at the frequency of the system clock, and the fabric reset pl0_resetn.
create_bd_design vck190_cips
set cips [create_bd_cell -type ip -vlnv [lindex [lsort [get_ipdefs -filter {NAME == versal_cips}]] end] cips]
set_property -dict [list \
    CONFIG.CLOCK_MODE {Custom} \
    CONFIG.PS_PL_CONNECTIVITY_MODE {Custom} \
    CONFIG.PS_PMC_CONFIG [list \
        CLOCK_MODE {Custom} \
        DESIGN_MODE {1} \
        PMC_CRP_PL0_REF_CTRL_FREQMHZ $clk_sys_mhz \
        PS_NUM_FABRIC_RESETS {1} \
        PS_PL_CONNECTIVITY_MODE {Custom} \
        PS_USE_PMCPL_CLK0 {1} \
    ] \
] $cips
create_bd_port -dir O -type clk pl0_clk
create_bd_port -dir O -type rst pl0_resetn
connect_bd_net [get_bd_pins cips/pl0_ref_clk] [get_bd_ports pl0_clk]
connect_bd_net [get_bd_pins cips/pl0_resetn] [get_bd_ports pl0_resetn]
validate_bd_design
save_bd_design
set bd [get_files vck190_cips.bd]
generate_target all $bd
add_files -norecurse [make_wrapper -files $bd -top]
close_bd_design [current_bd_design]
set_property generic {IncludeCips_g=true} [current_fileset]

# Constraints: board, timing exceptions of the system (scoped to its entity), Open Logic crossings (implementation
# only). Only the constraints of the Open Logic area base are loaded: the scoped constraints of olo_intf_sync
# constrain device pins, and the system uses olo_intf_sync for internal asynchronous signals only (the clock pair
# constraints of the system cover them).
add_files -fileset constrs_1 -norecurse [file join $target_dir constr ${name}.xdc]
set system_xdc [file join $system_dir hdl ${system}.xdc]
add_files -fileset constrs_1 -norecurse $system_xdc
set_property SCOPED_TO_REF $system [get_files $system_xdc]
source [file join $root cores open-logic-ft src base tcl olo_base_constraints_amd.tcl]

set_property top ${name}_top [current_fileset]
update_compile_order -fileset sources_1

# Implementation: placement and routing explore several directives, physical optimization also after routing
set_property strategy Performance_ExplorePostRoutePhysOpt [get_runs impl_1]

if {$step eq "project"} { return }

launch_runs synth_1 -jobs 8
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} { error "synthesis failed" }
if {$step eq "synth"} { return }

launch_runs impl_1 -to_step write_device_image -jobs 8
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} { error "implementation failed" }
open_run impl_1
report_utilization -file [file join $out utilization.rpt]
report_utilization -hierarchical -hierarchical_depth 3 -file [file join $out utilization_hierarchical.rpt]
report_timing_summary -file [file join $out timing_summary.rpt]
report_methodology -file [file join $out methodology.rpt]
report_drc -file [file join $out drc.rpt]
