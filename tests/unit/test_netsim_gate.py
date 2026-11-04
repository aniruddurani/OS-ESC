"""The dnet gate (flowlib.netsim.evaluate and `cli dnet`)."""
import copy
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "designs", "ecat_esc", "netsim"))

from check import fault_evidence  # noqa: E402
from flowlib import cli  # noqa: E402
from flowlib.netsim import evaluate, read_manifest  # noqa: E402

MANIFEST_TEXT = """\
# scenario  quick  soak
startup 1000 2000
faults  1000 2000
total   3000 5000
"""
MANIFEST = read_manifest(MANIFEST_TEXT)

CLEAN = dict(sent=10, replies=10, mismatches=0, missing=0, unexpected=0, flags_seen=0, late=0,
             latency_max_ps=1)
ALL_FAULTS = {"rx_err": 2, "crc": 2, "runt": 2, "oversize": 2, "non_ethercat": 2, "malformed": 2,
              "truncated": 2}
DIAG = {"rx_errors": 2, "crc_failures": 9, "runts": 3, "oversize": 2, "non_ethercat": 2,
        "malformed": 4}


def run(scenario, frames, **over):
    r = dict(scenario=scenario, k=3, seed=1, config=dict(rx_ppm=10.0, tx_ppm=-20.0), frames=frames,
             metrics=dict(CLEAN), injected={}, diag={}, failures=[], sim_ps=5, wall_s=1.0,
             cycles=100, error="")
    if scenario == "faults":
        r["injected"], r["diag"] = dict(ALL_FAULTS), dict(DIAG)
    r.update(over)
    return r


def results(*runs):
    return {"size": "quick", "runs": list(runs) or [run("startup", 1500), run("faults", 1500)]}


def ev(res, size="quick"):
    return evaluate(res, MANIFEST, size, fault_evidence)


class GateTest(unittest.TestCase):
    def test_clean_quick_run_passes(self):
        status, metrics, errors = ev(results())
        self.assertEqual((status, errors), ("pass", []))
        self.assertEqual(metrics["frames"], 3000)
        self.assertEqual(metrics["frames_per_scenario"], {"startup": 1500, "faults": 1500})

    def test_short_scenario_is_error(self):
        status, _, errors = ev(results(run("startup", 2001), run("faults", 999)))
        self.assertEqual(status, "error")
        self.assertTrue(any("faults" in e["message"] for e in errors))

    def test_total_below_minimum_is_error(self):
        status, _, errors = ev(results(run("startup", 1000), run("faults", 1000)))
        self.assertEqual(status, "error")
        self.assertTrue(any("total" in e["message"] for e in errors))

    def test_soak_uses_soak_minimums(self):
        self.assertEqual(ev(results(), size="soak")[0], "error")

    def test_mismatch_fails(self):
        bad = run("startup", 1500, metrics=dict(CLEAN, mismatches=1))
        self.assertEqual(ev(results(bad, run("faults", 1500)))[0], "fail")

    def test_missing_or_unexpected_fails(self):
        for key in ("missing", "unexpected"):
            bad = run("startup", 1500, metrics=dict(CLEAN, **{key: 2}))
            self.assertEqual(ev(results(bad, run("faults", 1500)))[0], "fail", key)

    def test_flag_within_100ppm_fails(self):
        for bits in (1, 2, 4, 8):
            bad = run("startup", 1500, metrics=dict(CLEAN, flags_seen=bits))
            status, _, errors = ev(results(bad, run("faults", 1500)))
            self.assertEqual(status, "fail", bits)

    def test_fault_evidence_mismatch_fails(self):
        f = run("faults", 1500)
        f["diag"]["crc_failures"] = 1
        status, _, errors = ev(results(run("startup", 1500), f))
        self.assertEqual(status, "fail")
        self.assertTrue(any("crc" in e["message"] for e in errors))

    def test_no_fault_injected_fails(self):
        f = run("faults", 1500, injected={}, diag={})
        self.assertEqual(ev(results(run("startup", 1500), f))[0], "fail")

    def test_run_error_is_error(self):
        bad = run("startup", 1500, error="bench exited")
        status, _, errors = ev(results(bad, run("faults", 1500)))
        self.assertEqual(status, "error")
        self.assertTrue(any("bench exited" in e["message"] for e in errors))

    def test_no_runs_is_error(self):
        self.assertEqual(ev({"size": "quick", "runs": []})[0], "error")

    def test_failures_listed(self):
        bad = run("startup", 1500, metrics=dict(CLEAN, mismatches=1),
                  failures=[dict(index=4, kind="mismatch", expected="00", got="01")])
        _, _, errors = ev(results(bad, run("faults", 1500)))
        self.assertTrue(any("index 4" in e["message"] for e in errors))

    def test_missing_results_is_error(self):
        with tempfile.TemporaryDirectory() as d:
            man = os.path.join(ROOT, "designs", "ecat_esc", "netsim", "manifest.txt")
            out = os.path.join(d, "dnet.json")
            rc = cli.main(["dnet", "--results", os.path.join(d, "none.json"), "--manifest", man,
                           "--size", "quick", "--design", "ecat_esc", "--out", out])
            self.assertNotEqual(rc, 0)
            self.assertEqual(json.load(open(out))["status"], "error")

    def test_cli_writes_pass_report(self):
        with tempfile.TemporaryDirectory() as d:
            man = os.path.join(d, "manifest.txt")
            with open(man, "w") as f:
                f.write(MANIFEST_TEXT)
            res = os.path.join(d, "results.json")
            with open(res, "w") as f:
                json.dump(results(), f)
            out = os.path.join(d, "dnet.json")
            check = os.path.join(ROOT, "designs", "ecat_esc", "netsim", "check.py")
            rc = cli.main(["dnet", "--results", res, "--manifest", man, "--size", "quick",
                           "--check", check, "--design", "ecat_esc", "--out", out])
            self.assertEqual((rc, json.load(open(out))["status"]), (0, "pass"))


if __name__ == "__main__":
    unittest.main()
