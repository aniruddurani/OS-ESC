"""Command-line entry point the Makefile calls after each tool run.

Each subcommand parses tool output, writes the stage report and returns the
stage's exit code. Usage: python3 -m flowlib.cli <subcommand> [options]
"""
import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

from flowlib.deck import PORT_ORDER, make_deck
from flowlib.digital import parse_cocotb_results, parse_verilator_lint, preset, read_available, summarize_config
from flowlib.parsers import (
    count_subckt_devices,
    drc_status,
    parse_lyrdb,
    parse_magic_drc,
    parse_netgen,
    parse_mag_ports,
    parse_ngspice_meas,
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
    metrics = {}
    if text is None:
        status = "error"
        errors.append({"message": f"netlist not written: {a.netlist}"})
    elif "Error" in log_text:
        status = "error"
        errors += [{"message": l.strip()} for l in log_text.splitlines() if "Error" in l]
    else:
        ports = parse_subckt_ports(text, a.design)
        devices = count_subckt_devices(text, a.design)
        metrics = {"devices": devices}
        if ports != PORT_ORDER:
            status = "fail"
            errors.append({"message": f".subckt {a.design} ports are {ports}, expected {PORT_ORDER}"})
        elif not devices:
            # Pins without devices means xschem could not find the symbols
            # (wrong PDK or path); sim would then fail on the wrong thing.
            status = "error"
            errors.append({"message": f".subckt {a.design} contains no devices"})
        else:
            status = "pass"
    return write_report(a.out, "netlist", a.design, status, metrics, errors, a.log)


def cmd_layout(a):
    # Evidence comes from the saved .mag, not from what the generator printed.
    mag_text = _read(a.mag)
    labels = parse_mag_ports(mag_text) if mag_text is not None else None
    ports = [name for name, _ in labels] if labels is not None else []
    errors = []
    if not os.path.exists(a.gds) or os.path.getsize(a.gds) == 0:
        status = "error"
        errors.append({"message": f"GDS not written or empty: {a.gds}"})
    elif labels is None:
        status = "error"
        errors.append({"message": f"layout file not written: {a.mag}"})
    elif ports != PORT_ORDER:
        status = "fail"
        missing = [p for p in PORT_ORDER if p not in ports]
        detail = f"missing {missing}" if missing else "wrong order"
        errors.append({"message": f"ports are {ports}, expected {PORT_ORDER} ({detail})"})
    elif [n for n, layer in labels if layer == "space"]:
        status = "fail"
        floating = [n for n, layer in labels if layer == "space"]
        errors.append({"message": f"port label(s) {floating} sit on empty space, not on metal"})
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
    # Magic's DRC_COUNT counts rule hits; report the boxes a person fixes from.
    magic_listed = len(magic["violations"]) or magic["count"]
    metrics = {"magic_violations": magic_listed,
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


def cmd_dlint(a):
    text = _read(a.log)
    if text is None:
        return write_report(a.out, "dlint", a.design, "error", {},
                            [{"message": f"lint log missing: {a.log}"}], a.log)
    counts = parse_verilator_lint(text)
    clean = counts["errors"] == 0 and counts["warnings"] == 0
    errors = [] if clean else [{"message": l} for l in text.splitlines() if l.startswith("%")]
    return write_report(a.out, "dlint", a.design, "pass" if clean else "fail", counts, errors, a.log)


def cmd_dsim(a):
    xml_text = _read(a.results)
    manifest_text = _read(a.manifest)
    if xml_text is None or manifest_text is None:
        gone = a.results if xml_text is None else a.manifest
        return write_report(a.out, "dsim", a.design, "error", {},
                            [{"message": f"simulation evidence missing: {gone}"}], a.results)
    manifest = [l.strip() for l in manifest_text.splitlines() if l.strip()]
    if not manifest:
        return write_report(a.out, "dsim", a.design, "error", {},
                            [{"message": f"manifest lists no tests: {a.manifest}"}], a.results)
    try:
        r = parse_cocotb_results(xml_text, manifest)
    except ET.ParseError as e:
        return write_report(a.out, "dsim", a.design, "error", {},
                            [{"message": f"results unreadable: {e}"}], a.results)
    errors = [{"message": f"expected test did not run or was skipped: {n}"} for n in r["missing"]]
    if r["failed"]:
        errors.append({"message": f"{r['failed']} test(s) failed"})
    status = "pass" if not errors and r["passed"] >= 1 else "fail"
    return write_report(a.out, "dsim", a.design, status, r, errors, a.results)


def _synth_parameters(yaml_text):
    """NAME=value entries of a LibreLane config's SYNTH_PARAMETERS list."""
    params, inside = {}, False
    for line in yaml_text.splitlines():
        if re.match(r"^SYNTH_PARAMETERS\s*:", line):
            inside = True
        elif inside and re.match(r"^\s*-\s*\w+\s*=", line):
            name, value = line.split("-", 1)[1].split("=", 1)
            params[name.strip()] = int(value.strip())
        elif inside and line.strip() and not line.startswith((" ", "\t")):
            inside = False
    return params


def cmd_bbuild(a):
    """bbuild: a Quartus build of the FPGA board top, from Quartus's own reports."""
    from flowlib.board import evaluate_bbuild
    texts = {p: _read(p) for p in (a.sta_rpt, a.map_rpt, a.fit_rpt, a.params_from)}
    gone = [p for p, t in texts.items() if t is None]
    if gone:
        return write_report(a.out, "bbuild", a.design, "error", {},
                            [{"message": f"build evidence missing: {p}"} for p in gone], a.fit_rpt)
    params = _synth_parameters(texts[a.params_from])
    if not params:
        return write_report(a.out, "bbuild", a.design, "error", {},
                            [{"message": f"no SYNTH_PARAMETERS in {a.params_from}"}], a.fit_rpt)
    status, metrics, errors = evaluate_bbuild(texts[a.sta_rpt], texts[a.map_rpt], texts[a.fit_rpt],
                                              params, a.core_mhz)
    return write_report(a.out, "bbuild", a.design, status, metrics, errors, a.sta_rpt)


def _read_json(path):
    """A JSON file's value, or None if it is missing, empty or unreadable."""
    text = _read(path)
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def _read_build_id(path):
    """The build id bbuild wrote, or None."""
    text = (_read(path) or "").strip()
    return int(text) if text.isdigit() else None


def cmd_buildid(a):
    """buildid: the identity of a build, from its sources and settings, into a file."""
    from flowlib.board import build_id
    with open(a.out, "w") as f:
        f.write(str(build_id(a.dir, a.settings)))
    return 0


def cmd_bprog(a):
    """bprog: esc_board.sof loaded over JTAG, and the board's status read back."""
    from flowlib.board import evaluate_bprog
    log, build = _read(a.pgm_log), _read_build_id(a.build_id_file)
    if log is None or build is None:
        gone = a.pgm_log if log is None else a.build_id_file
        return write_report(a.out, "bprog", a.design, "error", {},
                            [{"message": f"evidence missing: {gone}"}], a.pgm_log)
    s, metrics, errors = evaluate_bprog(log, _read_json(a.status_json), build)
    return write_report(a.out, "bprog", a.design, s, metrics, errors, a.pgm_log)


def cmd_bnet(a):
    """bnet: the netsim scenarios run against the board (run_board.py's results.json)."""
    from flowlib.board import evaluate_bnet
    from flowlib.netsim import read_manifest
    stage = "bnet" if a.size == "quick" else f"bnet_{a.size}"
    text, manifest_text, build = _read(a.results), _read(a.manifest), _read_build_id(a.build_id_file)
    if text is None or manifest_text is None or build is None:
        gone = a.results if text is None else a.manifest if manifest_text is None else a.build_id_file
        return write_report(a.out, stage, a.design, "error", {},
                            [{"message": f"board run evidence missing: {gone}"}], a.results)
    try:
        results = json.loads(text)
    except ValueError as e:
        return write_report(a.out, stage, a.design, "error", {},
                            [{"message": f"results unreadable: {e}"}], a.results)
    status, metrics, errors = evaluate_bnet(results, read_manifest(manifest_text), a.size, build)
    return write_report(a.out, stage, a.design, status, metrics, errors, a.results)


def cmd_bsoem(a):
    """bsoem: the two SOEM programs' JSON results against the board."""
    from flowlib.board import evaluate_bsoem
    def program(path):
        line = next((s for s in (_read(path) or "").splitlines() if s.startswith("{")), None)
        try:
            return json.loads(line) if line else None
        except ValueError:
            return None
    build = _read_build_id(a.build_id_file)
    if build is None:
        return write_report(a.out, "bsoem", a.design, "error", {},
                            [{"message": f"evidence missing: {a.build_id_file}"}], a.result)
    status, metrics, errors = evaluate_bsoem(program(a.result), program(a.config_result),
                                             _read_json(a.before), _read_json(a.after), build,
                                             _read_json(a.between))
    return write_report(a.out, "bsoem", a.design, status, metrics, errors, a.result)


def cmd_dnet(a):
    """dnet: the network simulation's results.json against its manifest. The fault-evidence
    rule is the netsim's own (check.py beside the manifest), so the gate and the runner
    apply the same one."""
    import importlib.util
    from flowlib.netsim import evaluate, read_manifest
    stage = "dnet" if a.size == "quick" else f"dnet_{a.size}"
    text, manifest_text = _read(a.results), _read(a.manifest)
    if text is None or manifest_text is None:
        gone = a.results if text is None else a.manifest
        return write_report(a.out, stage, a.design, "error", {},
                            [{"message": f"simulation evidence missing: {gone}"}], a.results)
    try:
        results = json.loads(text)
    except ValueError as e:
        return write_report(a.out, stage, a.design, "error", {},
                            [{"message": f"results unreadable: {e}"}], a.results)
    check_py = a.check or os.path.join(os.path.dirname(os.path.abspath(a.manifest)), "check.py")
    spec = importlib.util.spec_from_file_location("netsim_check", check_py)
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    status, metrics, errors = evaluate(results, read_manifest(manifest_text), a.size,
                                       check.fault_evidence)
    return write_report(a.out, stage, a.design, status, metrics, errors, a.results)


def cmd_dmetrics(a):
    """dsynth / dpnr: every --zero metric must be 0 and every --nonneg metric
    >= 0, read from LibreLane's metrics.json."""
    stage = a.command
    text = _read(a.metrics)
    if text is None:
        return write_report(a.out, stage, a.design, "error", {},
                            [{"message": f"metrics file missing: {a.metrics}"}], a.metrics)
    pz, pn = preset(a.preset) if a.preset else ([], [])
    a.zero, a.nonneg = list(a.zero) + pz, list(a.nonneg) + pn
    names = list(a.zero) + list(a.nonneg)
    try:
        values, missing = read_available(text, names)
    except ValueError as e:
        return write_report(a.out, stage, a.design, "error", {},
                            [{"message": f"metrics file unreadable: {e}"}], a.metrics)
    errors = [{"metric": n, "message": f"{n} = {values[n]}, expected 0"}
              for n in a.zero if n in values and values[n] != 0]
    errors += [{"metric": n, "message": f"{n} = {values[n]}, expected >= 0"}
               for n in a.nonneg if n in values and values[n] < 0]
    # A flow that stopped early leaves later metrics unwritten: a recorded violation is
    # a "fail", and the gaps are listed beside it; gaps alone are an "error".
    gaps = [{"metric": n, "message": f"metric not in metrics file: {n}"} for n in missing]
    status = "fail" if errors else ("error" if gaps else "pass")
    return write_report(a.out, stage, a.design, status, values, errors + gaps, a.metrics)


def cmd_dconfigs(a):
    """Area and frequency per configuration (a report, not a gate). Each --metrics is
    NAME=path to that configuration's final metrics.json."""
    rows, errors = {}, []
    for item in a.metrics:
        name, _, path = item.partition("=")
        text = _read(path)
        if text is None:
            errors.append({"config": name, "message": f"metrics file missing: {path}"})
            continue
        try:
            rows[name] = summarize_config(json.loads(text), a.clock_period)
        except (KeyError, ValueError) as e:
            errors.append({"config": name, "message": f"metrics unreadable: {e!r}"})
    status = "error" if errors or not rows else "pass"
    return write_report(a.out, "dconfigs", a.design, status, {"configs": rows}, errors, "")


STAGE_ORDER = ["netlist", "sim", "layout", "drc", "lvs", "pex"]


def cmd_summary(a):
    """One status per stage, read from the reports that exist now. A stage with
    no report is 'missing' and an unreadable one 'error', never a pass."""
    stages = {}
    for stage in STAGE_ORDER:
        text = _read(os.path.join(a.reports, f"{stage}.json"))
        if text is None:
            stages[stage] = "missing"
            continue
        try:
            stages[stage] = json.loads(text)["status"]
        except (ValueError, KeyError, TypeError):
            stages[stage] = "error"
        # A pass is only current if this run stamped it; otherwise the report
        # is left over from an earlier run.
        if (a.stamps and stages[stage] == "pass"
                and not os.path.exists(os.path.join(a.stamps, f"{stage}.pass"))):
            stages[stage] = "stale"
    status = "pass" if all(s == "pass" for s in stages.values()) else "fail"
    errors = [{"stage": s, "message": f"{s}: {v}"} for s, v in stages.items() if v != "pass"]
    return write_report(a.out, "summary", a.design, status, {"stages": stages}, errors, "")


def cmd_deck(a):
    try:
        deck = make_deck(a.dut, a.tb, a.lib, a.corner, a.design, a.temp)
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
    s.add_argument("--mag", required=True)
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

    s = sub.add_parser("summary")
    s.add_argument("--reports", required=True)
    s.add_argument("--stamps", help="directory of <stage>.pass stamps; a pass without one is stale")
    s.add_argument("--design", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_summary)

    s = sub.add_parser("deck")
    s.add_argument("--dut", required=True)
    s.add_argument("--tb", required=True)
    s.add_argument("--lib", required=True)
    s.add_argument("--corner", required=True)
    s.add_argument("--temp", help="simulation temperature in C, written as a .temp card")
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

    s = sub.add_parser("dlint")
    s.add_argument("--log", required=True)
    s.add_argument("--design", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_dlint)

    s = sub.add_parser("dsim")
    s.add_argument("--results", required=True)
    s.add_argument("--manifest", required=True)
    s.add_argument("--design", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_dsim)

    s = sub.add_parser("dnet")
    s.add_argument("--results", required=True)
    s.add_argument("--manifest", required=True)
    s.add_argument("--size", choices=["quick", "soak"], required=True)
    s.add_argument("--check", help="netsim check.py (default: the one beside the manifest)")
    s.add_argument("--design", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_dnet)

    s = sub.add_parser("bbuild")
    s.add_argument("--sta-rpt", required=True)
    s.add_argument("--map-rpt", required=True)
    s.add_argument("--fit-rpt", required=True)
    s.add_argument("--params-from", required=True, help="LibreLane config whose SYNTH_PARAMETERS the build must match")
    s.add_argument("--core-mhz", type=float, required=True)
    s.add_argument("--design", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_bbuild)

    s = sub.add_parser("buildid")
    s.add_argument("--dir", action="append", required=True, help="source directory")
    s.add_argument("--settings", required=True, help="build settings that are not in a file")
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_buildid)

    s = sub.add_parser("bprog")
    s.add_argument("--pgm-log", required=True)
    s.add_argument("--status-json", required=True)
    s.add_argument("--build-id-file", required=True)
    s.add_argument("--design", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_bprog)

    s = sub.add_parser("bnet")
    s.add_argument("--results", required=True)
    s.add_argument("--manifest", required=True)
    s.add_argument("--size", choices=["quick", "soak"], required=True)
    s.add_argument("--build-id-file", required=True)
    s.add_argument("--design", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_bnet)

    s = sub.add_parser("bsoem")
    s.add_argument("--result", required=True, help="the SOEM test's stdout")
    s.add_argument("--config-result", required=True, help="soem_config's stdout")
    s.add_argument("--before", required=True, help="board status before the test")
    s.add_argument("--between", required=True, help="board status from the soft reset between the programs")
    s.add_argument("--after", required=True, help="board status after the test")
    s.add_argument("--build-id-file", required=True)
    s.add_argument("--design", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_bsoem)

    s = sub.add_parser("dconfigs")
    s.add_argument("--metrics", action="append", required=True, help="NAME=path to final metrics.json")
    s.add_argument("--clock-period", type=float, required=True, help="core clock period in ns")
    s.add_argument("--design", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_dconfigs)

    for name in ("dsynth", "dpnr"):
        s = sub.add_parser(name)
        s.add_argument("--metrics", required=True)
        s.add_argument("--zero", action="append", default=[], help="metric that must be 0")
        s.add_argument("--nonneg", action="append", default=[], help="metric that must be >= 0")
        s.add_argument("--preset", choices=["dsynth", "dpnr"],
                       help="also require the standard metric lists for that stage")
        s.add_argument("--design", required=True)
        s.add_argument("--out", required=True)
        s.set_defaults(func=cmd_dmetrics)

    return p


def main(argv):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
