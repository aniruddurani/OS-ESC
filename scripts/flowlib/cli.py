"""Command-line entry point the Makefile calls after each tool run.

Each subcommand parses tool output, writes the stage report and returns the
stage's exit code. Usage: python3 -m flowlib.cli <subcommand> [options]
"""
import argparse
import os
import sys

from flowlib.deck import PORT_ORDER, make_deck
from flowlib.parsers import parse_ngspice_meas, parse_ports, parse_subckt_ports
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
    return write_report(a.out, a.stage, a.design, status, measured, errors, a.log)


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
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_sim)

    return p


def main(argv):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
