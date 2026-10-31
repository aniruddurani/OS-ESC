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
set dev "nf 1 guard 1 diffcov 100 polycov 100 poverlap 0 \
         doports 1 full_metal 1 viasrc 100 viadrn 100 viagate 100 viagr 100"
# Gate contact only on the side facing the other device (topc / botc); an
# unused gate pad is an isolated piece of metal1 that fails the minimum-area rule.
set dev_n "$dev topc 1 botc 0"
set dev_p "$dev topc 0 botc 1"

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
set nfet [new_device sky130::sky130_fd_pr__nfet_01v8 xn $W_N $L $dev_n]
set pfet [new_device sky130::sky130_fd_pr__pfet_01v8 xp $W_P $L $dev_p]
# Gate pad centres, read from each device's G port so the gate bar still lands
# on the pads if the generator changes the device outline. findlabel does not
# work on cells that are not saved, so read the label out of the saved .mag
# (rlabel <layer> x1 y1 x2 y2 <pos> G; units are 5 nm).
writeall force
proc gate_y {cell} {
    set f [open $cell.mag]
    set text [read $f]
    close $f
    if {![regexp {rlabel \S+ (-?\d+) (-?\d+) (-?\d+) (-?\d+) \d+ G\n} $text -> x1 y1 x2 y2]} {
        error "no G label in $cell.mag"
    }
    return [expr {($y1 + $y2) / 2.0 * 0.005}]
}
set gate_n [gate_y $nfet]
set gate_p [expr {$YP + [gate_y $pfet]}]

load inv
place $nfet 0 0
place $pfet 0 $YP

# Source-to-guard-ring ties (nfet: vss, pfet: vdd).
metal1 0.105 -0.08 0.705 0.08
metal1 0.105 [expr {$YP - 0.08}] 0.705 [expr {$YP + 0.08}]
# Supply pads: each overlaps the right side of its device's guard-ring metal1
# (x 0.875..0.905). The labels need metal1 painted in this cell, and every shape
# painted here must meet the minimum area on its own: 0.425 x 0.22 = 0.094 um^2.
metal1 0.875 -0.11 1.3 0.11
metal1 0.875 [expr {$YP - 0.11}] 1.3 [expr {$YP + 0.11}]
# Drains: out. Gates: in.
metal1 -0.475 0.0 -0.305 $YP
metal1 -0.07 $gate_n 0.07 $gate_p

# Ports, numbered in the flow's pin order: in out vdd vss.
# vdd / vss labels sit on the supply pads above.
set ports [list \
    {in  1 0.0   2.0} \
    {out 2 -0.39 2.0} \
    [list vdd 3 1.0 $YP] \
    {vss 4 1.0 0.0} ]
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
