import json
import os

STATUSES = ("pass", "fail", "error")


def write_report(path, stage, design, status, metrics, errors, log):
    """Write a stage report as JSON. Returns the exit code for the stage."""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}; expected one of {STATUSES}")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    report = {
        "stage": stage,
        "design": design,
        "status": status,
        "metrics": metrics,
        "errors": errors,
        "log": log,
    }
    with open(path, "w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    return 0 if status == "pass" else 1
