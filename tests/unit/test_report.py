import json
import os
import tempfile
import unittest

from flowlib.report import write_report


class WriteReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "reports", "drc.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_pass_returns_zero_and_writes_all_keys(self):
        rc = write_report(self.path, "drc", "inv", "pass",
                          {"magic_violations": 0}, [], "build/inv/logs/drc.log")
        self.assertEqual(rc, 0)
        with open(self.path) as f:
            data = json.load(f)
        self.assertEqual(data, {
            "stage": "drc",
            "design": "inv",
            "status": "pass",
            "metrics": {"magic_violations": 0},
            "errors": [],
            "log": "build/inv/logs/drc.log",
        })

    def test_fail_returns_one(self):
        rc = write_report(self.path, "drc", "inv", "fail", {}, [{"rule": "x"}], "l")
        self.assertEqual(rc, 1)

    def test_error_returns_one(self):
        rc = write_report(self.path, "drc", "inv", "error", {}, [], "l")
        self.assertEqual(rc, 1)

    def test_unknown_status_raises(self):
        with self.assertRaises(ValueError):
            write_report(self.path, "drc", "inv", "maybe", {}, [], "l")
        self.assertFalse(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()
