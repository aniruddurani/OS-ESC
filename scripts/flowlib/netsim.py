"""The dnet gate: turns the network simulation's results.json into a stage verdict.

pass needs every manifest scenario to have run its minimum frames (and the total its
minimum), no mismatched, missing or unexpected reply, no flag set within the Ethernet
clock limit, and diag counters that saw every injected fault. A crashed or desynced run,
or a run short of its frames, is an error, never a pass."""

FLAG_NAMES = {1: "rx_overflow", 2: "tx_overflow", 4: "tx_underrun", 8: "late"}
PPM_LIMIT = 100.0


def read_manifest(text):
    """[(name, quick minimum, soak minimum)]; a row named `total` bounds the sum."""
    rows = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            name, quick, soak = line.split()
            rows.append((name, int(quick), int(soak)))
    return rows


def evaluate(results, manifest, size, fault_evidence):
    col = 1 if size == "quick" else 2
    runs = results.get("runs") or []
    if not runs:
        return "error", {}, [{"message": "results hold no runs"}]
    errors, failures = [], []

    per = {}
    totals = dict(frames=0, replies=0, mismatches=0, missing=0, unexpected=0, flags_seen=0,
                  late=0, sim_ps=0, wall_s=0.0, cycles=0, runs=len(runs))
    for r in runs:
        name = f"{r['scenario']} k={r['k']} seed={r['seed']}"
        per[r["scenario"]] = per.get(r["scenario"], 0) + r.get("frames", 0)
        m = r.get("metrics", {})
        totals["frames"] += r.get("frames", 0)
        for key in ("replies", "mismatches", "missing", "unexpected", "late"):
            totals[key] += m.get(key, 0)
        totals["flags_seen"] |= m.get("flags_seen", 0)
        for key in ("sim_ps", "wall_s", "cycles"):
            totals[key] += r.get(key, 0)
        if r.get("error"):
            errors.append({"run": name, "message": f"{name}: {r['error']}"})
            continue
        bad = {k: m.get(k, 0) for k in ("mismatches", "missing", "unexpected") if m.get(k, 0)}
        if bad:
            first = ", ".join(f"{f['kind']} at index {f['index']}" for f in r.get("failures", [])[:3])
            failures.append({"run": name, "message": f"{name}: {bad}" + (f" (first: {first})" if first else "")})
        cfg = r.get("config", {})
        within = max(abs(cfg.get("rx_ppm", 0.0)), abs(cfg.get("tx_ppm", 0.0))) <= PPM_LIMIT
        flags = m.get("flags_seen", 0)
        if within and flags:
            names = [n for b, n in FLAG_NAMES.items() if flags & b]
            failures.append({"run": name, "message": f"{name}: flags {names} set at rx {cfg.get('rx_ppm')} "
                                                     f"/ tx {cfg.get('tx_ppm')} ppm"})
        for e in fault_evidence(r.get("injected", {}), r.get("diag", {}),
                                require_all=r["scenario"] == "faults"):
            failures.append({"run": name, "message": f"{name}: fault evidence: {e}"})

    for name, quick, soak in manifest:
        need = (quick, soak)[col - 1]
        got = totals["frames"] if name == "total" else per.get(name, 0)
        if got < need:
            errors.append({"message": f"{name}: ran {got} frames, needs at least {need}"})

    totals["frames_per_scenario"] = per
    totals["cycles_per_s"] = totals["cycles"] / totals["wall_s"] if totals["wall_s"] else 0
    status = "error" if errors else ("fail" if failures else "pass")
    return status, totals, errors + failures
