# Extract $OUT_DIR/$DESIGN.mag with parasitic capacitance (to substrate and
# coupling) to a flat SPICE netlist: $OUT_DIR/$DESIGN_pex.spice.
# Resistance is not extracted (out of scope for the first milestone).
cd $::env(OUT_DIR)
set design $::env(DESIGN)
snap internal
units microns
load $design
select top cell
extract do local
extract do capacitance
extract do coupling
extract all
ext2spice cthresh 0
# Wrap the netlist in .subckt <design> <ports in port-number order>; the
# testbench connects to the ports by position.
ext2spice subcircuit top on
ext2spice -o ${design}_pex.spice
quit -noprompt
