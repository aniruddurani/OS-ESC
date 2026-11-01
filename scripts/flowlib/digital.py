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


def read_metrics(json_text, names):
    """Return {name: value} for each requested metric. A name the tool did not
    write raises KeyError: a missing number is never read as zero."""
    metrics = json.loads(json_text)
    return {name: metrics[name] for name in names}
