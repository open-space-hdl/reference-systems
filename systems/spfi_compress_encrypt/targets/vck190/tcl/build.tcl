#---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
#---------------------------------------------------------------------------------------------------
# Builds the system spfi_compress_encrypt for the AMD VCK190: project in vivado_out/spfi_compress_encrypt_vck190,
# sources of the compile order exported by the Composer, transceiver wizard instance, CIPS block design, constraints,
# synthesis, implementation and device image.
#
# Usage (from the repository root, with the submodules in cores/ checked out):
#   vivado -mode batch -source systems/spfi_compress_encrypt/targets/vck190/tcl/build.tcl [-tclargs [<step>] [<refclk>]]
#   <step>: project (create the project only), rtl (project and RTL elaboration of the design), synth, impl,
#   all (default: all, device image included)
#   <refclk>: source of the 156.25 MHz transceiver reference clock, which selects its pins
#   (constr/spfi_compress_encrypt_vck190_refclk_<refclk>.xdc): 8a34001 (default: clock generator 8A34001, output Q1, to
#   MGTREFCLK1 of GTY quad 200) or si570 (oscillator Si570 through the clock buffer SI53340 to GTY_REFCLK0 of GTY
#   quad 201). Both words are optional, in either order, for example -tclargs all si570.
#
# The results of a complete build go to <project>/results: the device image, the reports (timing summary,
# utilisation, methodology, DRC), the constraints that do not apply (invalid_constraints.xdc) and build_info.txt
# with the step, the reference clock and its pins, the Vivado version and the commits of the repository and of its
# submodules. The step rtl writes invalid_constraints.xdc and build_info.txt of the elaborated design.
#
# The environment variable RS_VIVADO_OUT selects another project directory. On Windows the device image step
# compiles the platform loader firmware in a deep directory below the project; with a project path of more than
# about 40 characters the compiler exceeds the Windows path length limit and write_device_image fails ("opening
# dependency file ... No such file or directory"). Use a short path there, for example RS_VIVADO_OUT=C:/git/rs.
#
# Documentation: systems/spfi_compress_encrypt/targets/vck190/README.md
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
        length limit); set RS_VIVADO_OUT to a short directory, for example C:/git/rs"
}
# Arguments: the step and the source of the reference clock, plain words in either order
set step all
set refclk 8a34001
foreach arg $argv {
    if {$arg in {project rtl synth impl all}} {
        set step $arg
    } elseif {$arg in {8a34001 si570}} {
        set refclk $arg
    } else {
        error "unknown argument $arg (step: project, rtl, synth, impl, all; reference clock: 8a34001, si570)"
    }
}
puts "Step $step, reference clock $refclk"
if {![string match 2025.2* [version -short]]} {
    puts "WARNING: the build is made for Vivado 2025.2, this is [version -short]"
}
set results [file join $out results]

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

# Constraints: board, pins of the selected reference clock, timing exceptions of the system (scoped to its entity),
# Open Logic crossings (implementation only). Only the constraints of the Open Logic area base are loaded: the scoped
# constraints of olo_intf_sync constrain device pins, and the system uses olo_intf_sync for internal asynchronous
# signals only (the clock pair constraints of the system cover them).
add_files -fileset constrs_1 -norecurse [file join $target_dir constr ${name}.xdc]
add_files -fileset constrs_1 -norecurse [file join $target_dir constr ${name}_refclk_${refclk}.xdc]
set system_xdc [file join $system_dir hdl ${system}.xdc]
add_files -fileset constrs_1 -norecurse $system_xdc
set_property SCOPED_TO_REF $system [get_files $system_xdc]
source [file join $root cores open-logic-ft src base tcl olo_base_constraints_amd.tcl]

set_property top ${name}_top [current_fileset]
update_compile_order -fileset sources_1

# Synthesis from scratch: an incremental synthesis reuses the netlist of the previous run, and after a change of the
# CIPS it can leave the constraint file of the CIPS unread (no clk_pl_0)
set_property AUTO_INCREMENTAL_CHECKPOINT 0 [get_runs synth_1]

# Implementation: placement and routing explore several directives, physical optimization also after routing. The
# run stops before opt_design when a constraint did not apply (an object query that matched nothing, the constraint
# file of a module that is not in the netlist): tools/vivado/check_constraints.tcl.
set_property strategy Performance_ExplorePostRoutePhysOpt [get_runs impl_1]
set_property STEPS.OPT_DESIGN.TCL.PRE [file join $root tools vivado check_constraints.tcl] [get_runs impl_1]

# build_info.txt of the open design: the step, the reference clock with the pins of its ports in the design, the
# Vivado version and the commits of the repository and of its submodules
proc write_build_info {path} {
    global name step refclk root
    set fh [open $path w]
    puts $fh "$name, step $step, [clock format [clock seconds] -format {%Y-%m-%d %H:%M:%S}]"
    puts $fh "Vivado [version -short], part [get_property PART [current_project]]"
    set pins {}
    foreach port {GtRefClk_P GtRefClk_N} { lappend pins "$port [get_property PACKAGE_PIN [get_ports $port]]" }
    puts $fh "reference clock $refclk: [join $pins {, }]"
    if {[catch {exec git -C $root log -1 --format=%H} commit]} { set commit "unknown (git not available)" }
    puts $fh "repository $commit"
    if {![catch {exec git -C $root submodule status} status]} {
        puts $fh "submodules:\n$status"
    }
    set nested [file join $root cores open-logic-ft]
    if {![catch {exec git -C $nested submodule status 3rdParty/en_cl_fix} status]} {
        puts $fh "cores/open-logic-ft/$status"
    }
    close $fh
}

if {$step eq "project"} { return }
if {$step eq "rtl"} {
    synth_design -rtl -name rtl_1
    # Constraints that do not apply to the elaborated design: a dropped constraint shows up before a synthesis
    file mkdir $results
    write_xdc -force -constraints invalid [file join $results invalid_constraints.xdc]
    write_build_info [file join $results build_info.txt]
    return
}

launch_runs synth_1 -jobs 8
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} { error "synthesis failed" }
if {$step eq "synth"} { return }

if {$step eq "impl"} {
    launch_runs impl_1 -jobs 8
} else {
    launch_runs impl_1 -to_step write_device_image -jobs 8
}
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} { error "implementation failed" }

# Results: reports of the implemented design, the device image and what was built
file mkdir $results
open_run impl_1
report_utilization -file [file join $results utilization.rpt]
report_utilization -hierarchical -hierarchical_depth 3 -file [file join $results utilization_hierarchical.rpt]
report_timing_summary -file [file join $results timing_summary.rpt]
report_methodology -file [file join $results methodology.rpt]
report_drc -file [file join $results drc.rpt]
# Constraints that do not apply to the implemented design (expected: none, the file holds only its header)
write_xdc -force -constraints invalid [file join $results invalid_constraints.xdc]
foreach pdi [glob -nocomplain [file join [get_property DIRECTORY [get_runs impl_1]] *.pdi]] {
    file copy -force $pdi $results
}
write_build_info [file join $results build_info.txt]
puts "Results in $results (reference clock $refclk)"
