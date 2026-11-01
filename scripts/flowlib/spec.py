import json


def load_spec(path):
    with open(path) as f:
        return json.load(f)


def check_measurements(measured, spec):
    """Compare measured values with spec limits (inclusive).

    Returns (status, errors). A missing or None measurement is an error,
    because a stage may only pass on positive evidence.
    """
    missing = []
    out_of_range = []
    for name, limits in spec["measurements"].items():
        lo = limits.get("min")
        hi = limits.get("max")
        value = measured.get(name)
        entry = {"measurement": name, "value": value, "min": lo, "max": hi}
        if value is None:
            missing.append(dict(entry, message=f"{name}: no value measured"))
        elif (lo is not None and value < lo) or (hi is not None and value > hi):
            unit = limits.get("unit", "")
            out_of_range.append(dict(
                entry,
                message=f"{name} = {value:g} {unit} outside [{lo}, {hi}]",
            ))
    if missing:
        return "error", missing + out_of_range
    if out_of_range:
        return "fail", out_of_range
    return "pass", []
