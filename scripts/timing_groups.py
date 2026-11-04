"""Rank the worst setup paths of a LibreLane run by RTL register group.

Usage (in the container, from the repo root):
    python3 scripts/timing_groups.py <run tag> [corner] [count]

`run tag` is a directory under designs/ecat_esc/librelane/runs (for example sta1 for the
fast pre-route run, pnr_default for a full one). The newest OpenROAD STA step of the run
and its max.rpt for `corner` (default nom_ss_100C_1v60) are read; flip-flops are named
from the synthesis netlist, with bus indices removed, so paths between the same two
registers fall into one row: worst slack, number of paths, start -> end. Only the paths
the report lists (the worst few hundred per corner) are counted.
"""
import collections
import glob
import re
import sys

RUNS = "designs/ecat_esc/librelane/runs/"


def flop_names(netlist):
    lines = [x.rstrip() for x in open(netlist).read().split("\n")]
    names = {}
    for i, line in enumerate(lines):
        t = line.split()
        if len(t) == 3 and t[0].startswith("sky130_fd_sc_hd__df") and t[2] == "(":
            for j in range(i + 1, i + 8):
                m = re.search(r"\.Q\(\\?(\S+)", lines[j])
                if m:
                    names[t[1]] = re.sub(r"\[\d+\]", "[]", m.group(1))
                    break
    return names


def main():
    tag = sys.argv[1]
    corner = sys.argv[2] if len(sys.argv) > 2 else "nom_ss_100C_1v60"
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 14
    run = RUNS + tag + "/"
    steps = sorted(glob.glob(run + "*-openroad-sta*"))
    if not steps:
        sys.exit("no OpenROAD STA step in " + run)
    names = flop_names(glob.glob(run + "*-yosys-synthesis/*.nl.v")[0])
    path = steps[-1] + "/" + corner + "/max.rpt"
    if not glob.glob(path):                 # the mid-flow STA steps report one corner only
        path, corner = steps[-1] + "/max.rpt", "single corner"
    report = open(path).read()
    groups = collections.defaultdict(lambda: [0, 0.0])
    worst = 0.0
    for block in re.split(r"\n(?=Startpoint:)", report):
        s = re.search(r"Startpoint: (\S+)", block)
        e = re.search(r"Endpoint: (\S+)", block)
        sl = re.search(r"(-?[0-9.]+)\s+slack", block)
        if not (s and e and sl):
            continue
        key = (names.get(s.group(1), s.group(1)), names.get(e.group(1), e.group(1)))
        slack = float(sl.group(1))
        groups[key][0] += 1
        groups[key][1] = min(groups[key][1], slack)
        worst = min(worst, slack)
    print("%s %s: worst setup slack %.2f ns, %d register groups with violations" % (
        steps[-1].split("/")[-1], corner, worst, sum(1 for g in groups.values() if g[1] < 0)))
    for key, (n, w) in sorted(groups.items(), key=lambda x: x[1][1])[:count]:
        print("%8.2f ns %4d paths  %s  ->  %s" % (w, n, key[0], key[1]))


main()
