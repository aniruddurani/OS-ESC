# Magic DRC of $OUT_DIR/$DESIGN.mag with the PDK's full rule set.
# Prints, for scripts/flowlib/parsers.parse_magic_drc:
#   DRC_COUNT <total violations>
#   BBOX <llx> <lly> <urx> <ury>        (cell bounding box, microns)
#   DRC_ERR <llx> <lly> <urx> <ury> <rule text>   (one per violating box)
cd $::env(OUT_DIR)
snap internal
units microns
load $::env(DESIGN)
select top cell
puts "BBOX [box values]"
drc style drc(full)
drc check
drc catchup
puts "DRC_COUNT [drc list count total]"
foreach {why boxes} [drc listall why] {
    foreach b $boxes {
        puts "DRC_ERR $b $why"
    }
}
quit -noprompt
