"""Parsers for tool output. Each returns None (or None fields) when the
expected evidence is absent, so callers can report status "error"."""
import re
import xml.etree.ElementTree as ET

_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


def parse_ngspice_meas(log_text, names):
    """Read `name = value` lines that ngspice prints for .meas results."""
    found = {}
    for line in log_text.splitlines():
        m = re.match(r"\s*(\w+)\s*=\s*(\S+)", line)
        if not m:
            continue
        name = m.group(1).lower()
        if name in found:
            continue
        found[name] = float(m.group(2)) if re.fullmatch(_NUMBER, m.group(2)) else None
    return {n: found.get(n.lower()) for n in names}


def parse_magic_drc(log_text):
    """Read the DRC_COUNT / BBOX / DRC_ERR lines printed by magic_drc.tcl."""
    count = None
    bbox = None
    violations = []
    for line in log_text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "DRC_COUNT" and len(parts) == 2:
            count = int(parts[1])
        elif parts[0] == "BBOX" and len(parts) == 5:
            bbox = tuple(float(p) for p in parts[1:])
        elif parts[0] == "DRC_ERR" and len(parts) >= 6:
            box = tuple(float(p) for p in parts[1:5])
            violations.append({"rule": " ".join(parts[5:]), "box": box})
    return {"count": count, "bbox": bbox, "violations": violations}


def drc_status(count, bbox, klayout_items):
    if count is None or bbox is None or klayout_items is None:
        return "error"
    x1, y1, x2, y2 = bbox
    if (x2 - x1) * (y2 - y1) <= 0:
        return "error"
    if count > 0 or klayout_items:
        return "fail"
    return "pass"


def parse_lyrdb(xml_text):
    """Read a KLayout report database; one entry per violation item."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    if root.tag != "report-database":
        return None
    items = []
    for item in root.iter("item"):
        rule = (item.findtext("category") or "").strip().strip("'")
        value = item.find("values/value")
        where = value.text.strip() if value is not None and value.text else ""
        items.append({"rule": rule, "where": where})
    return items


_NETGEN_BAD = (
    "failed pin matching",
    "Property errors were found",
    "Netlists do not match",
)


def parse_netgen(report_text):
    if "Final result" not in report_text:
        return {"match": None, "errors": []}
    errors = [
        line.strip()
        for line in report_text.splitlines()
        if any(bad in line for bad in _NETGEN_BAD)
    ]
    match = "Final result: Circuits match uniquely." in report_text and not errors
    return {"match": match, "errors": errors}


def parse_ports(log_text):
    ports = []
    for line in log_text.splitlines():
        m = re.match(r"PORT\s+(\d+)\s+(\S+)\s*$", line)
        if m:
            ports.append((int(m.group(1)), m.group(2)))
    return [name for _, name in sorted(ports)]


def parse_subckt_ports(spice_text, name):
    """Port list of `.subckt <name> ...`, following `+` continuation lines."""
    lines = spice_text.splitlines()
    for i, line in enumerate(lines):
        tokens = line.split()
        if len(tokens) >= 2 and tokens[0].lower() == ".subckt" and tokens[1].lower() == name.lower():
            words = tokens[2:]
            for cont in lines[i + 1:]:
                if not cont.startswith("+"):
                    break
                words += cont[1:].split()
            return [w.lower() for w in words if "=" not in w]
    return None
