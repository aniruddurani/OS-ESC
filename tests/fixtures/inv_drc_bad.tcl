# Known-bad layout for `make selftest`: the inverter plus one metal1 rectangle
# 0.1 um from the out bar (minimum metal1 spacing is 0.14 um). It touches
# nothing, so the only fault is the spacing violation: drc must fail.
set ::post_wire_hook {
    box -0.775 2.0 -0.575 2.5
    paint metal1
}
source designs/inv/layout/inv.tcl
