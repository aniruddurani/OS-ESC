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
import re

TIMING_MODELS = ("Slow 1200mV 85C", "Slow 1200mV 0C", "Fast 1200mV 0C")
ESC_INSTANCE = "esc_top:u_esc"
JTAG_ID = "0x020F70DD"                 # EP4CE115


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
        while j < len(lines) and not lines[j].lstrip().startswith(";"):
            j += 1
        if j >= len(lines):
            return None
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
    metrics = dict(core_mhz=None, worst_setup_ns=None, worst_hold_ns=None, unconstrained=None,
                   les=None, registers=None, memory_bits=None, latches=None)

    # Timing: every model, setup and hold, every clock.
    worst = {"Setup": None, "Hold": None}
    for model in TIMING_MODELS:
        for kind in ("Setup", "Hold"):
            rows = parse_table(sta_rpt, f"{model} Model {kind} Summary")
            if not rows:
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
    metrics["worst_setup_ns"], metrics["worst_hold_ns"] = worst["Setup"], worst["Hold"]

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


def evaluate_bprog(pgm_log, status):
    """Verdict on loading esc_board.sof over JTAG: the programmer configured the EP4CE115,
    and the board then reported the PHY link up at 100 Mbit full duplex with no FIFO flag
    (`status`: board_status.decode's dict, None if it could not be read)."""
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
        if status["state"] != "link_100fd":
            failures.append({"message": f"PHY state {status['state']}, expected link_100fd"})
        set_flags = [k for k, v in status["sticky"].items() if v]
        if set_flags:
            failures.append({"message": f"FIFO flags set after load: {set_flags}"})
    status_ = "error" if errors else ("fail" if failures else "pass")
    return status_, metrics, errors + failures


BNET_NOT_APPLICABLE = ["clocks", "faults"]


def evaluate_bnet(results, manifest, size):
    """Verdict on the netsim scenarios run against the board (run_board.py's results.json):
    the dnet gate's own rules (no mismatch, missing or unexpected reply, no FIFO flag, every
    scenario its minimum frames), with no fault injection to check, plus the board's own
    status around every run. Returns (status, metrics, errors)."""
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
        elif r["board_after"].get("state") != "link_100fd":
            failures.append({"message": f"{name}: PHY state {r['board_after'].get('state')} after the run"})
    if base == "error" or errors:
        status = "error"
    else:
        status = "fail" if base == "fail" or failures else "pass"
    return status, metrics, found + errors + failures


SOEM_CHECKS = ("brd_count", "station_address", "fmmu_config", "sm_config", "lrw_cycles")
SOEM_MIN_CYCLES = 10000


def evaluate_bsoem(result):
    """Verdict on the SOEM low-level test's JSON (None if it printed none): all five checks
    present and true, over at least SOEM_MIN_CYCLES LRW cycles."""
    if result is None:
        return "error", {}, [{"message": "the SOEM test printed no result"}]
    checks = result.get("checks") or {}
    cyc = result.get("cycle_us") or {}
    metrics = dict(cycles=result.get("cycles"), cycle_us_min=cyc.get("min"),
                   cycle_us_median=cyc.get("median"), cycle_us_max=cyc.get("max"), checks=checks)
    errors = [{"message": f"SOEM test did not report {name}"} for name in SOEM_CHECKS if name not in checks]
    if (result.get("cycles") or 0) < SOEM_MIN_CYCLES:
        errors.append({"message": f"{result.get('cycles')} LRW cycles, needs {SOEM_MIN_CYCLES}"})
    failures = [{"message": f"SOEM check {name} failed"} for name in SOEM_CHECKS
                if name in checks and checks[name] is not True]
    status = "error" if errors else ("fail" if failures else "pass")
    return status, metrics, errors + failures
