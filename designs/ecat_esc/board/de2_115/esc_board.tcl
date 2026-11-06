# Creates and compiles the esc_board project (the ESC on a DE2-115) in the current
# directory. Run from the build directory:
#   quartus_sh -t <repo>/designs/ecat_esc/board/de2_115/esc_board.tcl \
#       [-pll_mul 8] [-pll_div 5] [-rtl_dir <dir with esc_top and its blocks>] [-build_id N]
# Pins: LiteX terasic_de2_115.py (ENET0 in MII mode), checked against the Terasic manual.
load_package flow

set here [file dirname [file normalize [info script]]]
set repo [file normalize $here/../../../..]
array set opt [list -pll_mul 8 -pll_div 5 -rtl_dir $repo/designs/ecat_esc/rtl -build_id 1]
foreach {k v} $quartus(args) {
    if {![info exists opt($k)]} { error "unknown option $k" }
    set opt($k) $v
}

project_new esc_board -overwrite
set_global_assignment -name FAMILY "Cyclone IV E"
set_global_assignment -name DEVICE EP4CE115F29C7
set_global_assignment -name TOP_LEVEL_ENTITY esc_board
set_global_assignment -name NUM_PARALLEL_PROCESSORS ALL
set_global_assignment -name RESERVE_ALL_UNUSED_PINS_WEAK_PULLUP "AS INPUT TRI-STATED"
# The RTL goes to Quartus as a copy in rtl_fpga/, with one difference: pdram.v's
# `$error` elaboration check (an unselected generate branch for invalid RAM_BYTES), which
# Quartus Standard/Lite cannot parse, is removed. The copy fails loudly if that check is
# not found exactly once. The sky130 SRAM macro behind pdram_macro.v is never
# elaborated: RAM_BYTES=256 selects the flip-flop RAM.
file delete -force rtl_fpga
file mkdir rtl_fpga
foreach f [lsort [glob $opt(-rtl_dir)/*.v]] {
    set fh [open $f r]; set text [read $fh]; close $fh
    if {[file tail $f] eq "pdram.v"} {
        set check {\n[ \t]*if \(\(RAM_BYTES % 1024\) != 0 \|\| RAM_BYTES > 16384\) begin : g_bad\n[^\n]*\$error\([^\n]*\n[ \t]*end}
        if {[regsub -all $check $text "" text] != 1} {
            error "pdram.v: the RAM_BYTES \$error check was not found exactly once"
        }
    }
    set out rtl_fpga/[file tail $f]
    set fh [open $out w]; fconfigure $fh -translation lf; puts -nonewline $fh $text; close $fh
    set_global_assignment -name VERILOG_FILE [file normalize $out]
}
foreach f [lsort [glob $here/rtl/*.v]] { set_global_assignment -name VERILOG_FILE $f }
set_global_assignment -name SDC_FILE $here/esc_board.sdc
set_parameter -name PLL_MUL $opt(-pll_mul)
set_parameter -name PLL_DIV $opt(-pll_div)
set_parameter -name BUILD_ID $opt(-build_id)

proc pin {port loc std} {
    set_location_assignment PIN_$loc -to $port
    set_instance_assignment -name IO_STANDARD $std -to $port
}
pin CLOCK_50 Y2 "3.3-V LVTTL"
pin KEY0 M23 "2.5 V"
set i 0
foreach loc {G19 F19 E19 F21 F18 E18 J19 H19 J17 G17 J15 H16 J16 H17 F15 G15 G16 H15} {
    pin "LEDR\[$i\]" $loc "2.5 V"
    incr i
}
pin ENET0_TX_CLK B17 "2.5 V"
pin ENET0_RX_CLK A15 "2.5 V"
pin ENET0_RST_N C19 "2.5 V"
pin ENET0_MDIO B21 "2.5 V"
pin ENET0_MDC C20 "2.5 V"
pin ENET0_RX_DV C17 "2.5 V"
pin ENET0_RX_ER D18 "2.5 V"
set i 0
foreach loc {C16 D16 D17 C15} { pin "ENET0_RX_DATA\[$i\]" $loc "2.5 V"; incr i }
pin ENET0_TX_EN A18 "2.5 V"
pin ENET0_TX_ER B18 "2.5 V"
set i 0
foreach loc {C18 D19 A19 B19} { pin "ENET0_TX_DATA\[$i\]" $loc "2.5 V"; incr i }

execute_flow -compile
project_close
