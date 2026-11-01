import json
import os
import tempfile
import unittest

from flowlib.cli import main
from flowlib.digital import (parse_cocotb_results, parse_verilator_lint, preset, read_metrics,
                             summarize_config)

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "fixtures", "digital")


def fixture(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read()


class LintParserTest(unittest.TestCase):
    def test_lint_clean_log_passes(self):
        self.assertEqual(parse_verilator_lint(fixture("lint_clean.log")),
                         {"errors": 0, "warnings": 0})

    def test_lint_warning_counts(self):
        counts = parse_verilator_lint(fixture("lint_dirty.log"))
        self.assertEqual(counts, {"errors": 1, "warnings": 2})


class CocotbParserTest(unittest.TestCase):
    def test_cocotb_all_expected_ran(self):
        r = parse_cocotb_results(fixture("results_ok.xml"), ["test_a", "test_b"])
        self.assertEqual(r, {"passed": 2, "failed": 0, "missing": []})

    def test_cocotb_missing_test_is_missing(self):
        r = parse_cocotb_results(fixture("results_ok.xml"), ["test_a", "test_b", "test_c"])
        self.assertEqual(r["missing"], ["test_c"])
        self.assertEqual(r["passed"], 2)

    def test_cocotb_failure_counted(self):
        r = parse_cocotb_results(fixture("results_fail.xml"), ["test_a", "test_b"])
        self.assertEqual((r["passed"], r["failed"]), (1, 1))

    def test_skipped_test_is_not_a_pass(self):
        r = parse_cocotb_results(fixture("results_skip.xml"), ["test_a", "test_b"])
        self.assertEqual(r["passed"], 1)
        self.assertEqual(r["missing"], ["test_b"])


class MetricsTest(unittest.TestCase):
    def test_metrics_missing_name_raises(self):
        with self.assertRaises(KeyError):
            read_metrics(fixture("metrics.json"), ["no__such__metric"])

    def test_metrics_values_returned(self):
        m = read_metrics(fixture("metrics.json"), ["magic__drc_error__count"])
        self.assertEqual(m, {"magic__drc_error__count": 0})


class DigitalCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "reports", "stage.json")

    def tearDown(self):
        self.tmp.cleanup()

    def report(self):
        with open(self.out) as f:
            return json.load(f)

    def write(self, name, text):
        p = os.path.join(self.tmp.name, name)
        with open(p, "w") as f:
            f.write(text)
        return p

    def fixture_path(self, name):
        return os.path.join(FIXTURES, name)

    def test_dlint_clean_passes(self):
        rc = main(["dlint", "--log", self.fixture_path("lint_clean.log"),
                   "--design", "ecat_esc", "--out", self.out])
        self.assertEqual(rc, 0)
        self.assertEqual(self.report()["status"], "pass")

    def test_dlint_warning_fails(self):
        rc = main(["dlint", "--log", self.fixture_path("lint_dirty.log"),
                   "--design", "ecat_esc", "--out", self.out])
        self.assertEqual(rc, 1)
        self.assertEqual(self.report()["status"], "fail")

    def test_dlint_missing_log_is_error(self):
        rc = main(["dlint", "--log", os.path.join(self.tmp.name, "nope.log"),
                   "--design", "ecat_esc", "--out", self.out])
        self.assertEqual(rc, 1)
        self.assertEqual(self.report()["status"], "error")

    def manifest(self, names):
        return self.write("manifest.txt", "\n".join(names) + "\n")

    def run_dsim(self, results, names):
        return main(["dsim", "--results", self.fixture_path(results),
                     "--manifest", self.manifest(names),
                     "--design", "ecat_esc", "--out", self.out])

    def test_dsim_all_pass(self):
        self.assertEqual(self.run_dsim("results_ok.xml", ["test_a", "test_b"]), 0)
        self.assertEqual(self.report()["status"], "pass")

    def test_dsim_failure_fails(self):
        self.assertEqual(self.run_dsim("results_fail.xml", ["test_a", "test_b"]), 1)
        self.assertEqual(self.report()["status"], "fail")

    def test_dsim_manifest_short_fails(self):
        # A test that is listed but never ran must not pass.
        self.assertEqual(self.run_dsim("results_ok.xml", ["test_a", "test_b", "test_c"]), 1)
        r = self.report()
        self.assertEqual(r["status"], "fail")
        self.assertIn("test_c", json.dumps(r["errors"]))

    def test_dsim_missing_results_is_error(self):
        rc = main(["dsim", "--results", os.path.join(self.tmp.name, "nope.xml"),
                   "--manifest", self.manifest(["test_a"]),
                   "--design", "ecat_esc", "--out", self.out])
        self.assertEqual(rc, 1)
        self.assertEqual(self.report()["status"], "error")

    def test_dsim_empty_manifest_is_error(self):
        # Nothing to prove is not a pass.
        self.assertEqual(self.run_dsim("results_ok.xml", []), 1)
        self.assertEqual(self.report()["status"], "error")

    def run_metrics(self, stage, zero=(), nonneg=(), metrics="metrics.json"):
        argv = [stage, "--metrics", self.fixture_path(metrics),
                "--design", "ecat_esc", "--out", self.out]
        for name in zero:
            argv += ["--zero", name]
        for name in nonneg:
            argv += ["--nonneg", name]
        return main(argv)

    def test_dpnr_all_good_passes(self):
        rc = self.run_metrics("dpnr", zero=["magic__drc_error__count"],
                              nonneg=["timing__setup__ws"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.report()["status"], "pass")

    def test_dpnr_nonzero_metric_fails(self):
        rc = self.run_metrics("dpnr", zero=["route__drc_errors__iter:0"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.report()["status"], "fail")

    def test_dpnr_negative_slack_fails(self):
        rc = self.run_metrics("dsynth", nonneg=["timing__setup__ws__corner:max_ss_100C_1v60"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.report()["status"], "fail")

    def test_dpnr_missing_metric_is_error(self):
        rc = self.run_metrics("dpnr", zero=["no__such__metric"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.report()["status"], "error")

    def test_dpnr_missing_metrics_file_is_error(self):
        rc = main(["dpnr", "--metrics", os.path.join(self.tmp.name, "nope.json"),
                   "--design", "ecat_esc", "--out", self.out, "--zero", "x"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.report()["status"], "error")

    # --- the gates themselves: the --preset lists ------------------------------

    def good_metrics(self, stage):
        zero, nonneg = preset(stage)
        m = {name: 0 for name in zero}
        m.update({name: 1.5 for name in nonneg})
        return m

    def run_preset(self, stage, metrics):
        path = self.write("m.json", json.dumps(metrics))
        return main([stage, "--metrics", path, "--preset", stage, "--design", "ecat_esc",
                     "--out", self.out])

    def test_preset_good_metrics_pass(self):
        for stage in ("dsynth", "dpnr"):
            self.assertEqual(self.run_preset(stage, self.good_metrics(stage)), 0, stage)

    def test_preset_covers_the_required_checks(self):
        zero, nonneg = preset("dpnr")
        for name in ("magic__drc_error__count", "klayout__drc_error__count",
                     "design__lvs_error__count", "antenna__violating__nets",
                     "timing__setup_vio__count", "timing__hold_vio__count"):
            self.assertIn(name, zero)
        for corner in ("nom_tt_025C_1v80", "max_ss_100C_1v60", "min_ff_n40C_1v95"):
            self.assertIn(f"timing__setup__ws__corner:{corner}", nonneg)
            self.assertIn(f"timing__hold__ws__corner:{corner}", nonneg)
        self.assertIn("design__inferred_latch__count", preset("dsynth")[0])

    def test_dpnr_fails_on_each_zero_metric(self):
        for name in preset("dpnr")[0]:
            m = self.good_metrics("dpnr")
            m[name] = 2
            self.assertEqual(self.run_preset("dpnr", m), 1, name)
            r = self.report()
            self.assertEqual(r["status"], "fail", name)
            self.assertIn(name, json.dumps(r["errors"]))

    def test_dpnr_fails_on_each_negative_slack(self):
        for name in preset("dpnr")[1]:
            m = self.good_metrics("dpnr")
            m[name] = -0.01
            self.assertEqual(self.run_preset("dpnr", m), 1, name)
            self.assertEqual(self.report()["status"], "fail", name)

    def test_dpnr_errors_on_each_missing_metric(self):
        for name in preset("dpnr")[0] + preset("dpnr")[1]:
            m = self.good_metrics("dpnr")
            del m[name]
            self.assertEqual(self.run_preset("dpnr", m), 1, name)
            self.assertEqual(self.report()["status"], "error", name)


class PartialRunTest(DigitalCliTest):
    """A flow that stops early (for example at a timing check) leaves the later
    metrics unwritten; the violation it did record must still be what is reported."""

    def partial(self, extra):
        m = {n: 0 for n in preset("dpnr")[0] if "drc" not in n and "lvs" not in n}
        m.update({n: 1.0 for n in preset("dpnr")[1]})
        m.update(extra)
        return m

    def test_violation_wins_over_missing_metrics(self):
        m = self.partial({"timing__setup__ws": -0.5, "timing__setup_vio__count": 12})
        rc = self.run_preset_dict(m)
        self.assertEqual(rc, 1)
        r = self.report()
        self.assertEqual(r["status"], "fail")
        text = json.dumps(r["errors"])
        self.assertIn("timing__setup__ws", text)
        self.assertIn("magic__drc_error__count", text)       # and the gap is still listed

    def test_only_missing_metrics_is_an_error(self):
        self.assertEqual(self.run_preset_dict(self.partial({})), 1)
        self.assertEqual(self.report()["status"], "error")

    def test_state_out_json_with_a_metrics_key_is_accepted(self):
        path = self.write("state_out.json", json.dumps({"metrics": self.good_metrics_full()}))
        rc = main(["dpnr", "--metrics", path, "--preset", "dpnr", "--design", "ecat_esc",
                   "--out", self.out])
        self.assertEqual(rc, 0)

    def run_preset_dict(self, metrics):
        path = self.write("m.json", json.dumps(metrics))
        return main(["dpnr", "--metrics", path, "--preset", "dpnr", "--design", "ecat_esc",
                     "--out", self.out])

    def good_metrics_full(self):
        zero, nonneg = preset("dpnr")
        m = {n: 0 for n in zero}
        m.update({n: 1.5 for n in nonneg})
        return m


class ConfigTableTest(DigitalCliTest):
    def metrics_file(self, name, area, ws):
        return self.write(name, json.dumps({
            "design__instance__count": 1000, "design__instance__area": area,
            "design__die__area": area * 2, "timing__setup__ws": ws, "timing__hold__ws": 0.3}))

    def test_summary_estimates_fmax_from_the_worst_setup_slack(self):
        info = summarize_config(json.loads(fixture("metrics.json")), clock_period_ns=10.0)
        self.assertEqual(info["area_um2"], 6277.27)
        self.assertAlmostEqual(info["fmax_mhz_est"], 1000.0 / (10.0 - 3.9856652439898355), places=3)

    def test_table_has_every_configuration(self):
        paths = [f"{n}={self.metrics_file(n + '.json', a, w)}"
                 for n, a, w in (("min", 50000, 3.0), ("default", 190000, 2.0), ("max", 600000, 0.5))]
        argv = ["dconfigs", "--design", "ecat_esc", "--out", self.out, "--clock-period", "10"]
        for p in paths:
            argv += ["--metrics", p]
        self.assertEqual(main(argv), 0)
        r = self.report()
        self.assertEqual(r["status"], "pass")
        self.assertEqual(sorted(r["metrics"]["configs"]), ["default", "max", "min"])
        self.assertEqual(r["metrics"]["configs"]["max"]["area_um2"], 600000)

    def test_table_errors_when_a_configuration_has_no_metrics(self):
        argv = ["dconfigs", "--design", "ecat_esc", "--out", self.out, "--clock-period", "10",
                "--metrics", "min=" + os.path.join(self.tmp.name, "nope.json")]
        self.assertEqual(main(argv), 1)
        self.assertEqual(self.report()["status"], "error")


if __name__ == "__main__":
    unittest.main()
