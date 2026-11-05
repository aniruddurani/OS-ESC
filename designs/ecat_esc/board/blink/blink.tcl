# Creates and compiles the blink project. Run from the build directory:
#   quartus_sh -t <repo>/designs/ecat_esc/board/blink/blink.tcl
# Pins from the DE2-115 user manual: CLOCK_50 PIN_Y2 (3.3 V), LEDR[0] PIN_G19 (2.5 V).
load_package flow

set src [file dirname [file normalize [info script]]]

project_new blink -overwrite
set_global_assignment -name FAMILY "Cyclone IV E"
set_global_assignment -name DEVICE EP4CE115F29C7
set_global_assignment -name TOP_LEVEL_ENTITY blink
set_global_assignment -name VERILOG_FILE $src/blink.v
set_global_assignment -name SDC_FILE $src/blink.sdc
set_global_assignment -name RESERVE_ALL_UNUSED_PINS_WEAK_PULLUP "AS INPUT TRI-STATED"

set_location_assignment PIN_Y2 -to CLOCK_50
set_instance_assignment -name IO_STANDARD "3.3-V LVTTL" -to CLOCK_50
set_location_assignment PIN_G19 -to LEDR0
set_instance_assignment -name IO_STANDARD "2.5 V" -to LEDR0

execute_flow -compile
project_close
