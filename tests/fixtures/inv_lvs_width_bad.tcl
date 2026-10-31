# Known-bad layout for `make selftest`: the inverter with a 1.5 um wide pfet
# where the schematic says 2 um. The connections are all correct, so only a
# size check can catch it: lvs must fail.
set W_P 1.5
source designs/inv/layout/inv.tcl
