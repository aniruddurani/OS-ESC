# sky130A inverter layout generator. Run headless by Magic:
#   OUT_DIR=build/inv magic -dnull -noconsole -rcfile <sky130A.magicrc> inv.tcl
# Writes $OUT_DIR/inv.mag, the generated device cells, and $OUT_DIR/inv.gds,
# then prints "PORT <index> <name>" lines for the flow to check.
#
# Layout: nfet at the origin, pfet above it. Each device's source is tied to
# its own guard ring with a metal1 bar (nfet ring = vss, pfet ring = vdd).
# Metal1 bars join the two gates (in) and the two drains (out).
# Units are microns; magic's internal grid is 5 nm.

set W_N 1.0
set W_P 2.0
set L   0.15
set YP  4.0

cd $::env(OUT_DIR)
snap internal
units microns
load inv

# Device generator options: metal1 on source/drain/gate contacts and on the
# guard ring's right side (viagr), which is where the supply ties land.
set dev "nf 1 guard 1 diffcov 100 polycov 100 topc 1 botc 1 poverlap 0 \
         doports 1 full_metal 1 viasrc 100 viadrn 100 viagate 100 viagr 100"

proc new_device {gencell inst w l opts} {
    set before [cellname list allcells]
    box position 0 0
    eval magic::gencell $gencell $inst w $w l $l $opts
    foreach c [cellname list allcells] {
        if {[lsearch $before $c] < 0} { return $c }
    }
    error "gencell $gencell created no cell"
}

proc place {cell x y} {
    box position $x $y
    getcell $cell child 0 0 parent $x $y
}

proc metal1 {x1 y1 x2 y2} {
    box $x1 $y1 $x2 $y2
    paint metal1
}

# gencell drops an instance of the new device into whatever cell is being
# edited, so generate in a scratch cell and keep it out of the layout.
load scratch
set nfet [new_device sky130::sky130_fd_pr__nfet_01v8 xn $W_N $L $dev]
set pfet [new_device sky130::sky130_fd_pr__pfet_01v8 xp $W_P $L $dev]
load inv
place $nfet 0 0
place $pfet 0 $YP

# Source-to-guard-ring ties (nfet: vss, pfet: vdd).
metal1 0.105 -0.08 0.705 0.08
metal1 0.105 [expr {$YP - 0.08}] 0.705 [expr {$YP + 0.08}]
# Repaint the top edge of each guard ring's metal1 in this cell (it merges with
# the identical metal inside the device), so the supply labels sit on metal1.
metal1 0.675 1.2 0.905 1.26
metal1 0.675 [expr {$YP + 1.745}] 0.905 [expr {$YP + 1.805}]
# Drains: out. Gates: in.
metal1 -0.475 0.0 -0.305 $YP
metal1 -0.07 0.8 0.07 [expr {$YP - 1.3}]

# Ports, numbered in the flow's pin order: in out vdd vss.
# The guard ring's metal1 is an outline; its top edge is the solid piece to label.
set ports [list \
    {in  1 0.0   2.0} \
    {out 2 -0.39 2.0} \
    [list vdd 3 0.89 [expr {$YP + 1.775}]] \
    {vss 4 0.89  1.23} ]
foreach p $ports {
    lassign $p name idx x y
    box $x $y $x $y
    label $name c metal1
    port make $idx
}

save inv
writeall force
gds write inv.gds
file delete scratch.mag

foreach p $ports {
    lassign $p name idx x y
    puts "PORT $idx $name"
}
quit -noprompt
