import json
import os
import tempfile
import unittest

from flowlib.cli import main
from flowlib.digital import parse_cocotb_results, parse_verilator_lint, read_metrics

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


if __name__ == "__main__":
    unittest.main()
