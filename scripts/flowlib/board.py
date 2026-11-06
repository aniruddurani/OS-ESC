"""Evidence parsers and verdicts for the FPGA board stages (the ESC on a DE2-115).

Quartus writes its reports as tables:

    +----------------------+
    ; Title                ;
    +----------------------+
    ; Col A ; Col B        ;
    +----------------------+
    ; a     ; b            ;
    +----------------------+

A table the gate needs that is missing makes the stage `error`, never a pass."""
import hashlib
import os
import re

TIMING_MODELS = ("Slow 1200mV 85C", "Slow 1200mV 0C", "Fast 1200mV 0C")
TIMING_KINDS = {"Setup": "worst_setup_ns", "Hold": "worst_hold_ns", "Recovery": "worst_recovery_ns",
                "Removal": "worst_removal_ns", "Minimum Pulse Width": "worst_pulse_width_ns"}
ESC_INSTANCE = "esc_top:u_esc"
JTAG_ID = "0x020F70DD"                 # EP4CE115


def build_id(dirs, settings):
    """A 24-bit identity of a build: every *.v and *.sdc directly in `dirs`, and `settings`.
    The board reports it over JTAG, so a gate can tell that the image it is testing is the
    one bbuild judged."""
    h = hashlib.sha256(settings.encode())
    for d in dirs:
        for name in sorted(os.listdir(d)):
            if name.endswith((".v", ".sdc")):
                h.update(name.encode())
                with open(os.path.join(d, name), "rb") as f:
                    h.update(f.read())
    return int.from_bytes(h.digest()[:3], "big")


def _cells(line):
    return [c.strip() for c in line.strip().strip(";").split(";")]


def parse_table(text, title):
    """Rows of the table whose title matches `title` (a string, or a compiled regex
    matched against the whole title), as dicts keyed by the header; None if absent."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if not (s.startswith(";") and s.endswith(";")):
            continue
        name = s[1:-1].strip()
        if not (name == title if isinstance(title, str) else title.fullmatch(name)):
            continue
        j = i + 1
        while j < len(lines) and lines[j].lstrip().startswith(("+", "-")):
            j += 1
        if j >= len(lines) or not lines[j].lstrip().startswith(";"):
            return []                  # "No paths to report."
        header = _cells(lines[j])
        rows, k = [], j + 1
        while k < len(lines) and lines[k].lstrip().startswith("+"):
            k += 1
        while k < len(lines) and lines[k].lstrip().startswith(";"):
            rows.append(dict(zip(header, _cells(lines[k]))))
            k += 1
        return rows
    return None


def _number(s):
    m = re.match(r"\s*([-\d,]+(?:\.\d+)?)", s or "")
    return float(m.group(1).replace(",", "")) if m else None


def _fit_value(fit_rpt, name):
    m = re.search(r"^;\s*" + re.escape(name) + r"\s*;\s*([^;]+);", fit_rpt, re.M)
    return _number(m.group(1)) if m else None


def evaluate_bbuild(sta_rpt, map_rpt, fit_rpt, expected_params, core_mhz):
    """Verdict on a Quartus build of esc_board from its timing (sta.rpt), synthesis
    (map.rpt) and fitter (fit.rpt) reports. Returns (status, metrics, errors)."""
    errors, failures = [], []
    metrics = dict(core_mhz=None, unconstrained=None, les=None, registers=None, memory_bits=None,
                   latches=None, **{key: None for key in TIMING_KINDS.values()})

    # Timing: every model, every kind of check, every clock. A kind may report no paths
    # (recovery and removal need asynchronous resets), but its table must be there, and
    # setup and hold must have paths.
    worst = {kind: None for kind in TIMING_KINDS}
    for model in TIMING_MODELS:
        for kind in TIMING_KINDS:
            rows = parse_table(sta_rpt, f"{model} Model {kind} Summary")
            if rows is None or (not rows and kind in ("Setup", "Hold")):
                errors.append({"message": f"timing report has no {model} {kind.lower()} summary"})
                continue
            for r in rows:
                slack = _number(r.get("Slack"))
                if slack is None:
                    errors.append({"message": f"{model} {kind.lower()}: unreadable slack for {r.get('Clock')}"})
                    continue
                if worst[kind] is None or slack < worst[kind]:
                    worst[kind] = slack
                if slack < 0:
                    failures.append({"message": f"{model} {kind.lower()} slack {slack} ns on {r.get('Clock')}"})
    metrics.update({key: worst[kind] for kind, key in TIMING_KINDS.items()})

    ucp = parse_table(sta_rpt, "Unconstrained Paths Summary")
    if ucp is None:
        errors.append({"message": "timing report has no unconstrained paths summary"})
    else:
        metrics["unconstrained"] = int(sum((_number(r.get("Setup")) or 0) + (_number(r.get("Hold")) or 0)
                                           for r in ucp))
        if metrics["unconstrained"]:
            bad = [f"{r['Property']} {r.get('Setup')}/{r.get('Hold')}" for r in ucp
                   if (_number(r.get("Setup")) or 0) + (_number(r.get("Hold")) or 0)]
            failures.append({"message": "unconstrained: " + ", ".join(bad)})

    clocks = parse_table(sta_rpt, "Clocks")
    if clocks is None:
        errors.append({"message": "timing report has no clocks table"})
    else:
        pll = [r for r in clocks if "pll" in r.get("Clock Name", "").lower()]
        if len(pll) != 1:
            errors.append({"message": f"expected one PLL clock, found {[r.get('Clock Name') for r in pll]}"})
        else:
            mhz = _number(pll[0].get("Frequency"))
            metrics["core_mhz"] = mhz
            if mhz is None or abs(mhz - core_mhz) > 1e-3:
                failures.append({"message": f"core clock {pll[0].get('Frequency')}, expected {core_mhz} MHz"})

    # Synthesis: the ESC's parameters, and no latches.
    params = parse_table(map_rpt, re.compile(r"Parameter Settings for User Entity Instance: (?:\S*\|)?"
                                             + re.escape(ESC_INSTANCE)))
    if params is None:
        errors.append({"message": f"synthesis report has no parameter table for {ESC_INSTANCE}"})
    else:
        got = {r.get("Parameter Name"): r.get("Value") for r in params}
        for name, want in expected_params.items():
            if _number(got.get(name)) != float(want):
                failures.append({"message": f"esc_top {name} = {got.get(name)}, expected {want}"})
    metrics["latches"] = len(re.findall(r"^Warning \(10240\).*inferring latch", map_rpt, re.M))
    if metrics["latches"]:
        failures.append({"message": f"{metrics['latches']} inferred latch warning(s) (10240)"})

    # Fitter: resources (a report, not a criterion), and the evidence the fit ran.
    for key, name in (("les", "Total logic elements"), ("registers", "Total registers"),
                      ("memory_bits", "Total memory bits")):
        v = _fit_value(fit_rpt, name)
        metrics[key] = int(v) if v is not None else None
        if v is None:
            errors.append({"message": f"fitter report has no '{name}'"})

    status = "error" if errors else ("fail" if failures else "pass")
    return status, metrics, errors + failures


def _other_image(status, build):
    """Why the board's status does not show the build `build`, or None if it does."""
    if status.get("build_id") != build:
        return f"the board holds build {status.get('build_id')}, not the build {build} that bbuild judged"
    return None


def evaluate_bprog(pgm_log, status, build):
    """Verdict on loading esc_board.sof over JTAG: the programmer configured the EP4CE115,
    and the board then reported the build `build` with the PHY link up at 100 Mbit full
    duplex and no FIFO flag (`status`: board_status.decode's dict, None if unreadable)."""
    errors, failures = [], []
    metrics = dict(jtag_id=None, state=None, sticky=None, build_id=None)
    m = re.search(r"JTAG ID code (0x[0-9A-Fa-f]{8})", pgm_log)
    metrics["jtag_id"] = m.group(1).upper().replace("0X", "0x") if m else None
    if metrics["jtag_id"] != JTAG_ID:
        errors.append({"message": f"programmer saw JTAG ID {metrics['jtag_id']}, expected {JTAG_ID}"})
    if not re.search(r"Configuration succeeded -- 1 device\(s\) configured", pgm_log):
        errors.append({"message": "programmer log does not show the configuration succeeding"})
    if status is None:
        errors.append({"message": "board status could not be read over JTAG"})
    else:
        metrics.update(state=status["state"], sticky=status["sticky"], build_id=status["build_id"])
        other = _other_image(status, build)
        if other:
            errors.append({"message": other})
        if status["state"] != "link_100fd":
            failures.append({"message": f"PHY state {status['state']}, expected link_100fd"})
        set_flags = [k for k, v in status["sticky"].items() if v]
        if set_flags:
            failures.append({"message": f"FIFO flags set after load: {set_flags}"})
    status_ = "error" if errors else ("fail" if failures else "pass")
    return status_, metrics, errors + failures


BNET_NOT_APPLICABLE = ["clocks", "faults"]


def evaluate_bnet(results, manifest, size, build):
    """Verdict on the netsim scenarios run against the board (run_board.py's results.json):
    the dnet gate's own rules (no mismatch, missing or unexpected reply, no FIFO flag, every
    scenario its minimum frames), with no fault injection to check, plus the board's own
    status around every run, which must show the build `build`. Returns (status, metrics,
    errors)."""
    from flowlib.netsim import evaluate
    base, metrics, found = evaluate(results, manifest, size, lambda *a, **k: [])
    errors, failures = [], []
    if results.get("not_applicable") != BNET_NOT_APPLICABLE:
        errors.append({"message": f"not_applicable is {results.get('not_applicable')}, "
                                  f"expected {BNET_NOT_APPLICABLE}"})
    for r in results.get("runs", []):
        name = f"{r['scenario']} k={r['k']} seed={r['seed']}"
        if r.get("error"):
            continue                       # already an error from evaluate
        if "board_before" not in r or "board_after" not in r:
            errors.append({"message": f"{name}: no board status around the run"})
            continue
        other = _other_image(r["board_before"], build)
        if other:
            errors.append({"message": f"{name}: {other}"})
        if r["board_after"].get("state") != "link_100fd":
            failures.append({"message": f"{name}: PHY state {r['board_after'].get('state')} after the run"})
    if base == "error" or errors:
        status = "error"
    else:
        status = "fail" if base == "fail" or failures else "pass"
    return status, metrics, found + errors + failures


SOEM_CHECKS = ("brd_count", "station_address", "fmmu_config", "sm_config", "lrw_cycles")
SOEM_CONFIG_CHECKS = ("found_one", "identity", "name", "mapping", "safe_op", "op", "inputs_seen",
                      "cycles_ok", "outputs_ram", "back_to_init")
SOEM_MIN_CYCLES = 10000


def _soem_program(label, result, names):
    """(metrics, errors, failures) of one SOEM program's JSON (None if it printed none): every
    check present and true, over at least SOEM_MIN_CYCLES cycles."""
    if result is None:
        return {}, [{"message": f"{label} printed no result"}], []
    checks = result.get("checks") or {}
    cyc = result.get("cycle_us") or {}
    metrics = dict(cycles=result.get("cycles"), cycle_us_min=cyc.get("min"),
                   cycle_us_median=cyc.get("median"), cycle_us_max=cyc.get("max"), checks=checks)
    errors = [{"message": f"{label} did not report {name}"} for name in names if name not in checks]
    if (result.get("cycles") or 0) < SOEM_MIN_CYCLES:
        errors.append({"message": f"{label}: {result.get('cycles')} cycles, needs {SOEM_MIN_CYCLES}"})
    failures = [{"message": f"{label} check {name} failed"} for name in names
                if name in checks and checks[name] is not True]
    return metrics, errors, failures


def evaluate_bsoem(result, config_result, before, after, build):
    """Verdict on the two SOEM programs' JSON: the low-level test and SOEM's standard start-up.
    `before` and `after` are the board's status around them (None if unreadable): a board that
    was not there, not linked or not the build `build` is an error whatever the checks say,
    and a FIFO flag or a lost link after them is a failure."""
    if before is None or after is None:
        return "error", {}, [{"message": "board status could not be read around the SOEM test"}]
    other = _other_image(before, build)
    if other:
        return "error", {}, [{"message": other}]
    if before["state"] != "link_100fd":
        return "error", {}, [{"message": f"board not ready: PHY state {before['state']}"}]
    metrics, errors, failures = _soem_program("the SOEM test", result, SOEM_CHECKS)
    config, config_errors, config_failures = _soem_program("soem_config", config_result, SOEM_CONFIG_CHECKS)
    metrics.update({f"config_{k}": v for k, v in config.items()})
    errors += config_errors
    failures += config_failures
    if after["state"] != "link_100fd":
        failures.append({"message": f"PHY state {after['state']} after the test"})
    set_flags = [k for k, v in after["sticky"].items() if v]
    if set_flags:
        failures.append({"message": f"FIFO flags set by the test: {set_flags}"})
    status = "error" if errors else ("fail" if failures else "pass")
    return status, metrics, errors + failures
