# Known-bad layout for `make selftest`: the inverter with a gap cut in the out
# bar, so the nfet drain is no longer connected to the pfet drain. Removing
# metal cannot break a design rule, so drc must pass and lvs must fail.
set ::post_wire_hook {
    box -0.475 1.0 -0.305 1.5
    erase metal1
}
source designs/inv/layout/inv.tcl
