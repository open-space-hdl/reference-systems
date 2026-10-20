#---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
#---------------------------------------------------------------------------------------------------
# Stops the implementation run when a constraint did not apply: a constraint whose object query matched nothing (a
# cell, net, pin, port or clock that the netlist does not have), or the constraint file of a module that is missing in
# the netlist. Vivado reports these as warnings or critical warnings and continues; the timing analysis then lacks the
# constraint, and the timing summary can look clean although a clock or an exception is missing.
#
# The implementation run sources the script before opt_design (STEPS.OPT_DESIGN.TCL.PRE, set by the build script of a
# target), when link_design has read every constraint file; it counts the messages of link_design.
#
# Usage: set_property STEPS.OPT_DESIGN.TCL.PRE <path>/check_constraints.tcl [get_runs impl_1]
#---------------------------------------------------------------------------------------------------

namespace eval rs_check_constraints {
    # Message ids of a constraint that did not apply
    variable ids [dict create \
        {Vivado 12-180}       "no cells matched" \
        {Vivado 12-507}       "no nets matched" \
        {Vivado 12-508}       "no pins matched" \
        {Vivado 12-584}       "no ports matched" \
        {Vivado 12-1008}      "no clocks found" \
        {Vivado 12-4739}      "constraint without a valid object" \
        {Designutils 20-1280} "constraint file of a module that is not in the netlist" \
        {Common 17-1548}      "constraint script that failed" \
    ]
    variable found {}
    dict for {id what} $ids {
        set n [get_msg_config -id $id -count]
        if {$n > 0} { lappend found "$id ($what): $n" }
    }
    if {[llength $found] > 0} {
        error "constraints that did not apply (messages of link_design above):\n  [join $found "\n  "]"
    }
    puts "check_constraints: every constraint applied"
}
