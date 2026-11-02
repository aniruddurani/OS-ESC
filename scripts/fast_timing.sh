#!/bin/bash
# Fast timing loop for the digital flow (run inside the container, from the repo root):
#   scripts/fast_timing.sh <run tag> [corner] [last step]
# Synthesis, placement, clock tree and the post-CTS timing repair, with the placement and
# timing steps working at `corner` (default nom_ss_100C_1v60, the slowest of the nine), then
# the report of that corner, so the setup problems of the slow corner show in about five
# minutes instead of the hour a routed run takes. It does not replace `make dpnr`, which
# gates all nine corners after routing. Rank the result with
#   python3 scripts/timing_groups.py <run tag> single
# (the mid-flow STA steps report one corner, the one named here).
# The corner goes in through a throwaway copy of the configuration: `-c PNR_CORNERS=[...]`
# reaches LibreLane as one string, not a list, and no liberty file is then found.
set -u
tag=${1:?run tag}
corner=${2:-nom_ss_100C_1v60}
last=${3:-OpenROAD.STAMidPNR-2}
export PDK=sky130A PDK_ROOT=/foss/pdks STD_CELL_LIBRARY=sky130_fd_sc_hd
export PDKPATH=$PDK_ROOT/$PDK
cfg=${LLCFG:-default}
dir=designs/ecat_esc/librelane
tmp=$dir/_fast_$tag.yaml
trap 'rm -f "$tmp"' EXIT
cp "$dir/$cfg.yaml" "$tmp"
printf '\nPNR_CORNERS:\n  - %s\n' "$corner" >> "$tmp"
librelane "$tmp" --pdk $PDK --pdk-root $PDK_ROOT \
    --scl $STD_CELL_LIBRARY --overwrite --run-tag "$tag" --to "$last" \
    > "build/ecat_esc/logs/fast_$tag.log" 2>&1
echo "exit=$?"
tail -3 "build/ecat_esc/logs/fast_$tag.log"
