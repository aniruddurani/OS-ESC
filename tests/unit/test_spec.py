import unittest

from flowlib.spec import check_measurements

SPEC = {
    "measurements": {
        "vm": {"min": 0.70, "max": 1.10, "unit": "V"},
        "voh": {"min": 1.75, "unit": "V"},
        "vol": {"max": 0.05, "unit": "V"},
        "tphl": {"max": 1e-10, "unit": "s"},
        "tplh": {"max": 1e-10, "unit": "s"},
    }
}

GOOD = {"vm": 0.85, "voh": 1.79, "vol": 0.01, "tphl": 3e-11, "tplh": 4e-11}


class CheckMeasurementsTest(unittest.TestCase):
    def test_all_within_limits_passes(self):
        self.assertEqual(check_measurements(dict(GOOD), SPEC), ("pass", []))

    def test_value_above_max_fails_naming_measurement(self):
        status, errors = check_measurements(dict(GOOD, vm=1.2), SPEC)
        self.assertEqual(status, "fail")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["measurement"], "vm")
        self.assertEqual(errors[0]["value"], 1.2)
        self.assertEqual(errors[0]["max"], 1.10)

    def test_value_below_min_fails(self):
        status, errors = check_measurements(dict(GOOD, voh=1.70), SPEC)
        self.assertEqual(status, "fail")
        self.assertEqual([e["measurement"] for e in errors], ["voh"])

    def test_limit_is_inclusive(self):
        status, _ = check_measurements(dict(GOOD, tphl=1e-10, vm=0.70), SPEC)
        self.assertEqual(status, "pass")

    def test_none_value_is_error(self):
        # A measurement that never triggered must not pass (Review Focus 5).
        status, errors = check_measurements(dict(GOOD, tphl=None), SPEC)
        self.assertEqual(status, "error")
        self.assertEqual(errors[0]["measurement"], "tphl")

    def test_missing_measurement_is_error(self):
        measured = dict(GOOD)
        del measured["tplh"]
        status, errors = check_measurements(measured, SPEC)
        self.assertEqual(status, "error")
        self.assertEqual(errors[0]["measurement"], "tplh")

    def test_error_wins_over_fail(self):
        status, _ = check_measurements(dict(GOOD, vm=5.0, tphl=None), SPEC)
        self.assertEqual(status, "error")


if __name__ == "__main__":
    unittest.main()
