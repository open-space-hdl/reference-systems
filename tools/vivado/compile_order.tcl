#---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
#---------------------------------------------------------------------------------------------------
# Reads the compile order of a system that the Open Space Composer exports (<system>_compile_order.txt, format
# "composer-compile-order 1") and adds its VHDL-2008 files with their libraries to the current Vivado project.
#
# The file names the repositories ("source <directory> <url> <commit> [tag <tag>] [recursive]") and the files in
# compile order ("file <library> <path>"): a path that starts with the directory of a source is relative to the
# sources root (cores/ of this repository), any other path (the generated top level) is relative to the compile
# order file. Lines starting with # are comments.
#
# Usage: source tools/vivado/compile_order.tcl
#        rs_add_compile_order <compile order file> <sources root>
#---------------------------------------------------------------------------------------------------

namespace eval rs_compile_order {
    variable format "format composer-compile-order 1"
}

# Checks that the checkout of a source is the commit of the compile order (only when git is available)
proc rs_compile_order::check_commit {dir commit} {
    if {[catch {exec git -C $dir rev-parse HEAD} head]} {
        puts "WARNING: cannot read the commit of $dir (git not available), expected $commit"
        return
    }
    if {[string trim $head] ne $commit} {
        error "$dir is checked out at [string trim $head], the compile order needs $commit (git submodule update)"
    }
}

proc rs_add_compile_order {file sources_root} {
    set file [file normalize $file]
    set sources_root [file normalize $sources_root]
    set base [file dirname $file]
    set fh [open $file r]
    set lines [split [read $fh] "\n"]
    close $fh

    if {[string trim [lindex $lines 0]] ne $::rs_compile_order::format} {
        error "$file: first line must be \"$::rs_compile_order::format\""
    }
    set sources {}
    set libraries [dict create]
    set number 1
    foreach line [lrange $lines 1 end] {
        incr number
        set line [string trim $line]
        if {$line eq "" || [string index $line 0] eq "#"} { continue }
        set words [regexp -all -inline {\S+} $line]
        switch -- [lindex $words 0] {
            source {
                if {[llength $words] < 4} { error "$file:$number: source line needs a directory, URL and commit" }
                set dir [lindex $words 1]
                set path [file join $sources_root $dir]
                if {![file isdirectory $path]} { error "$file:$number: source $dir is missing in $sources_root" }
                rs_compile_order::check_commit $path [lindex $words 3]
                lappend sources $dir
            }
            file {
                if {[llength $words] != 3} { error "$file:$number: file line needs a library and a path" }
                lassign $words - library rel
                if {[lindex [file split $rel] 0] in $sources} {
                    set path [file join $sources_root $rel]
                } else {
                    set path [file join $base $rel]
                }
                if {![file exists $path]} { error "$file:$number: $path does not exist" }
                dict lappend libraries $library $path
            }
            default {
                error "$file:$number: unknown line \"[lindex $words 0]\""
            }
        }
    }
    set count 0
    dict for {library paths} $libraries {
        set f [add_files -norecurse $paths]
        set_property library $library $f
        set_property file_type {VHDL 2008} $f
        incr count [llength $paths]
    }
    puts "Compile order: $count files in [dict size $libraries] libraries from $file"
}
