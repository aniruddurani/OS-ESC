"""Command-line entry point the Makefile calls after each tool run.

Each subcommand parses tool output, writes the stage report and returns the
stage's exit code. Usage: python3 -m flowlib.cli <subcommand> [options]
"""
import argparse
import json
import os
import re
import sys

from flowlib.deck import PORT_ORDER, make_deck
from flowlib.parsers import (
    drc_status,
    parse_lyrdb,
    parse_magic_drc,
    parse_netgen,
    parse_ngspice_meas,
    parse_ports,
    parse_subckt_ports,
)
from flowlib.report import write_report
from flowlib.spec import check_measurements, load_spec


def _read(path):
    if not os.path.exists(path):
        return None
    with open(path, errors="replace") as f:
        return f.read()


def cmd_netlist(a):
    log_text = _read(a.log) or ""
    text = _read(a.netlist)
    errors = []
    if text is None:
        status = "error"
        errors.append({"message": f"netlist not written: {a.netlist}"})
    elif "Error" in log_text:
        status = "error"
        errors += [{"message": l.strip()} for l in log_text.splitlines() if "Error" in l]
    else:
        ports = parse_subckt_ports(text, a.design)
        if ports == PORT_ORDER:
            status = "pass"
        else:
            status = "fail"
            errors.append({"message": f".subckt {a.design} ports are {ports}, expected {PORT_ORDER}"})
    return write_report(a.out, "netlist", a.design, status, {}, errors, a.log)


def cmd_layout(a):
    log_text = _read(a.log) or ""
    ports = parse_ports(log_text)
    errors = []
    if not os.path.exists(a.gds) or os.path.getsize(a.gds) == 0:
        status = "error"
        errors.append({"message": f"GDS not written or empty: {a.gds}"})
    elif ports != PORT_ORDER:
        status = "fail"
        missing = [p for p in PORT_ORDER if p not in ports]
        detail = f"missing {missing}" if missing else "wrong order"
        errors.append({"message": f"ports are {ports}, expected {PORT_ORDER} ({detail})"})
    else:
        status = "pass"
    return write_report(a.out, "layout", a.design, status, {"ports": ports}, errors, a.log)


def cmd_drc(a):
    magic = parse_magic_drc(_read(a.magic_log) or "")
    lyrdb = _read(a.klayout_report)
    klayout = parse_lyrdb(lyrdb) if lyrdb is not None else None
    status = drc_status(magic["count"], magic["bbox"], klayout)
    errors = [{"tool": "magic", "rule": v["rule"], "where": list(v["box"])}
              for v in magic["violations"]]
    errors += [{"tool": "klayout", "rule": v["rule"], "where": v["where"]}
               for v in klayout or []]
    if status == "error":
        if magic["count"] is None or magic["bbox"] is None:
            errors.append({"message": "Magic DRC output has no DRC_COUNT/BBOX lines"})
        elif (magic["bbox"][2] - magic["bbox"][0]) * (magic["bbox"][3] - magic["bbox"][1]) <= 0:
            errors.append({"message": "layout is empty (zero-area bounding box)"})
        if klayout is None:
            errors.append({"message": f"KLayout report missing or unreadable: {a.klayout_report}"})
    if a.klayout_log is not None:
        # The sky130A deck runs no front/back-end rules unless feol and beol are
        # set, and then reports a clean database; require proof they were on.
        klog = _read(a.klayout_log) or ""
        off = [g for g in ("feol", "beol")
               if not re.search(rf"\b{g}:\s*(true|1)\b", klog)]
        if off:
            status = "error"
            errors.append({"message": "KLayout rule groups not enabled: " + ", ".join(off)})
    metrics = {"magic_violations": magic["count"],
               "klayout_violations": None if klayout is None else len(klayout)}
    return write_report(a.out, "drc", a.design, status, metrics, errors, a.magic_log)


def cmd_lvs(a):
    text = _read(a.report)
    if text is None:
        return write_report(a.out, "lvs", a.design, "error", {"match": None},
                            [{"message": f"Netgen report missing: {a.report}"}], a.log)
    result = parse_netgen(text)
    if result["match"] is None:
        status, errors = "error", [{"message": "no 'Final result' line in the Netgen report"}]
    elif result["match"]:
        status, errors = "pass", []
    else:
        status = "fail"
        errors = [{"message": m} for m in result["errors"]] or [
            {"message": "Netgen did not report a unique match"}]
    return write_report(a.out, "lvs", a.design, status, {"match": result["match"]}, errors, a.log)


def cmd_deck(a):
    try:
        deck = make_deck(a.dut, a.tb, a.lib, a.corner, a.design)
    except (OSError, ValueError) as e:
        print(f"deck: {e}", file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as f:
        f.write(deck)
    return 0


def cmd_sim(a):
    spec = load_spec(a.spec)
    names = list(spec["measurements"])
    log_text = _read(a.log)
    if log_text is None:
        return write_report(a.out, a.stage, a.design, "error", {},
                            [{"message": f"simulation log missing: {a.log}"}], a.log)
    measured = parse_ngspice_meas(log_text, names)
    status, errors = check_measurements(measured, spec)
    metrics = measured
    if a.compare:
        status, errors, metrics = _compare_with_schematic(a, spec, measured, status, errors)
    return write_report(a.out, a.stage, a.design, status, metrics, errors, a.log)


# Post-layout delays may be at most this much faster than the schematic's before
# the result is treated as a broken extraction (numerical noise allowance).
DELAY_SPEEDUP_TOLERANCE = 0.01


def _compare_with_schematic(a, spec, post, status, errors):
    """Pair each post-layout value with the schematic's, and refuse a faster
    extracted delay: parasitics only add load."""
    pre_text = _read(a.compare)
    if pre_text is None:
        msg = f"schematic simulation report missing: {a.compare}"
        return "error", errors + [{"message": msg}], post
    pre = json.loads(pre_text).get("metrics", {})
    metrics = {}
    for name, value in post.items():
        before = pre.get(name)
        delta = None
        if value is not None and before:
            delta = (value - before) / before * 100.0
        metrics[name] = {"pre": before, "post": value, "delta_pct": delta}
        is_delay = spec["measurements"][name].get("unit") == "s"
        if is_delay and value is not None and before and value < before * (1 - DELAY_SPEEDUP_TOLERANCE):
            status = "error"
            errors = errors + [{"measurement": name, "message":
                f"{name}: post-layout {value:g} s is faster than schematic {before:g} s; "
                "parasitics cannot speed up a gate, so the extraction or deck is wrong"}]
    return status, errors, metrics


def build_parser():
    p = argparse.ArgumentParser(prog="flowlib.cli")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("netlist")
    s.add_argument("--netlist", required=True)
    s.add_argument("--log", required=True)
    s.add_argument("--design", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_netlist)

    s = sub.add_parser("layout")
    s.add_argument("--gds", required=True)
    s.add_argument("--log", required=True)
    s.add_argument("--design", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_layout)

    s = sub.add_parser("drc")
    s.add_argument("--magic-log", required=True)
    s.add_argument("--klayout-report", required=True)
    s.add_argument("--klayout-log")
    s.add_argument("--design", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_drc)

    s = sub.add_parser("lvs")
    s.add_argument("--report", required=True)
    s.add_argument("--log", required=True)
    s.add_argument("--design", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_lvs)

    s = sub.add_parser("deck")
    s.add_argument("--dut", required=True)
    s.add_argument("--tb", required=True)
    s.add_argument("--lib", required=True)
    s.add_argument("--corner", required=True)
    s.add_argument("--design", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_deck)

    s = sub.add_parser("sim")
    s.add_argument("--log", required=True)
    s.add_argument("--spec", required=True)
    s.add_argument("--design", required=True)
    s.add_argument("--stage", choices=["sim", "pex"], required=True)
    s.add_argument("--compare", help="schematic sim report to pair post-layout values with")
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_sim)

    return p


def main(argv):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
