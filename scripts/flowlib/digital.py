"""Evidence parsers for the digital stages (lint, simulation, synthesis, signoff)."""
import json
import re
import xml.etree.ElementTree as ET


def parse_verilator_lint(text):
    """Count Verilator diagnostics. '%Error' and '%Warning-<CODE>' start a line."""
    errors = len(re.findall(r"^%Error", text, re.M))
    warnings = len(re.findall(r"^%Warning", text, re.M))
    return {"errors": errors, "warnings": warnings}


def parse_cocotb_results(xml_text, manifest):
    """Pass/fail counts from a cocotb results.xml, checked against the list of
    tests that must have run. A skipped test is not a pass: if it is in the
    manifest it is reported missing."""
    root = ET.fromstring(xml_text)
    passed_names = set()
    failed = 0
    for case in root.iter("testcase"):
        if case.find("skipped") is not None:
            continue
        if case.find("failure") is not None or case.find("error") is not None:
            failed += 1
        else:
            passed_names.add(case.get("name"))
    ran = passed_names | {c.get("name") for c in root.iter("testcase")
                          if c.find("failure") is not None or c.find("error") is not None}
    missing = [name for name in manifest if name not in ran]
    return {"passed": len(passed_names), "failed": failed, "missing": missing}


def _metrics_dict(json_text):
    """final/metrics.json is flat; a step's state_out.json keeps them under "metrics"."""
    d = json.loads(json_text)
    return d["metrics"] if isinstance(d.get("metrics"), dict) else d


def read_metrics(json_text, names):
    """Return {name: value} for each requested metric. A name the tool did not
    write raises KeyError: a missing number is never read as zero."""
    metrics = _metrics_dict(json_text)
    return {name: metrics[name] for name in names}


def read_available(json_text, names):
    """({name: value} for the metrics present, [names that are missing])."""
    metrics = _metrics_dict(json_text)
    return ({n: metrics[n] for n in names if n in metrics},
            [n for n in names if n not in metrics])


# --- signoff gates -----------------------------------------------------------------
# Metric names as LibreLane v3.1.0.dev3 writes them to final/metrics.json (read off a
# real run). The timing corners are the nine it analyses: {nom, min, max} x {tt, ss, ff}.
CORNERS = [f"{rc}_{pvt}" for rc in ("nom", "min", "max")
           for pvt in ("tt_025C_1v80", "ss_100C_1v60", "ff_n40C_1v95")]

SYNTH_ZERO = [
    "design__lint_error__count",
    "design__lint_warning__count",
    "design__inferred_latch__count",
    "design__instance_unmapped__count",
    "synthesis__check_error__count",
]

PNR_ZERO = SYNTH_ZERO + [
    "magic__drc_error__count",
    "klayout__drc_error__count",
    "design__lvs_error__count",
    "antenna__violating__nets",
    "timing__setup_vio__count",
    "timing__hold_vio__count",
]

PNR_NONNEG = ["timing__setup__ws", "timing__hold__ws"] + [
    f"timing__{kind}__ws__corner:{c}" for kind in ("setup", "hold") for c in CORNERS]


def preset(stage):
    """(metrics that must be 0, metrics that must be >= 0) for 'dsynth' or 'dpnr'."""
    if stage == "dsynth":
        return list(SYNTH_ZERO), []
    if stage == "dpnr":
        return list(PNR_ZERO), list(PNR_NONNEG)
    raise ValueError(f"no preset for stage {stage!r}")


def summarize_config(metrics, clock_period_ns):
    """One row of the configuration table from a run's metrics. The frequency is an
    estimate: the clock period minus the worst setup slack over all clocks (the core
    clock is the tightest one)."""
    ws = metrics["timing__setup__ws"]
    return {
        "instances": metrics["design__instance__count"],
        "area_um2": metrics["design__instance__area"],
        "die_area_um2": metrics["design__die__area"],
        "setup_ws_ns": ws,
        "hold_ws_ns": metrics["timing__hold__ws"],
        "fmax_mhz_est": 1000.0 / (clock_period_ns - ws),
    }
