# Extract $OUT_DIR/$DESIGN.mag to a SPICE netlist for LVS: $OUT_DIR/$DESIGN_ext.spice
# (no parasitics; port order comes from the layout's port numbers).
cd $::env(OUT_DIR)
set design $::env(DESIGN)
snap internal
units microns
load $design
select top cell
extract do local
extract all
ext2spice lvs
ext2spice -o ${design}_ext.spice
quit -noprompt
