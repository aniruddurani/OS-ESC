import json
import os
import tempfile
import unittest

from flowlib.cli import main

SPEC = {"measurements": {
    "vm": {"min": 0.70, "max": 1.10, "unit": "V"},
    "tphl": {"max": 1e-10, "unit": "s"},
}}


class CliTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = self.path("reports", "stage.json")

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, *parts):
        return os.path.join(self.tmp.name, *parts)

    def write(self, name, text):
        p = self.path(name)
        with open(p, "w") as f:
            f.write(text)
        return p

    def report(self):
        with open(self.out) as f:
            return json.load(f)


class NetlistCommandTest(CliTestBase):
    def run_netlist(self, netlist, log_text=""):
        log = self.write("netlist.log", log_text)
        return main(["netlist", "--netlist", netlist, "--log", log,
                     "--design", "inv", "--out", self.out])

    def test_good_netlist_passes(self):
        n = self.write("inv.spice", ".subckt inv in out vdd vss\n.ends\n")
        self.assertEqual(self.run_netlist(n), 0)
        self.assertEqual(self.report()["status"], "pass")

    def test_wrong_port_order_fails(self):
        n = self.write("inv.spice", ".subckt inv vdd vss in out\n.ends\n")
        self.assertEqual(self.run_netlist(n), 1)
        self.assertEqual(self.report()["status"], "fail")

    def test_missing_netlist_is_error(self):
        self.assertEqual(self.run_netlist(self.path("nope.spice")), 1)
        self.assertEqual(self.report()["status"], "error")

    def test_error_in_log_is_error(self):
        n = self.write("inv.spice", ".subckt inv in out vdd vss\n.ends\n")
        self.assertEqual(self.run_netlist(n, "Error: symbol not found\n"), 1)
        self.assertEqual(self.report()["status"], "error")


class DeckCommandTest(CliTestBase):
    def test_writes_deck(self):
        dut = self.write("inv.spice", ".subckt inv in out vdd vss\n.ends\n")
        deck = self.path("sim", "pre.spice")
        rc = main(["deck", "--dut", dut, "--tb", "tb.spice", "--lib", "lib.spice",
                   "--corner", "tt", "--design", "inv", "--out", deck])
        self.assertEqual(rc, 0)
        with open(deck) as f:
            self.assertIn(f'.include "{dut}"', f.read())

    def test_bad_ports_exit_nonzero_and_write_nothing(self):
        dut = self.write("inv.spice", ".subckt inv out in vdd vss\n.ends\n")
        deck = self.path("sim", "pre.spice")
        rc = main(["deck", "--dut", dut, "--tb", "tb.spice", "--lib", "lib.spice",
                   "--corner", "tt", "--design", "inv", "--out", deck])
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(deck))


class SimCommandTest(CliTestBase):
    def run_sim(self, log_text):
        log = self.write("sim.log", log_text)
        spec = self.write("spec.json", json.dumps(SPEC))
        return main(["sim", "--log", log, "--spec", spec, "--design", "inv",
                     "--stage", "sim", "--out", self.out])

    def test_in_spec_passes_with_metrics(self):
        rc = self.run_sim("vm = 8.5e-01\ntphl = 3.0e-11 targ= 1e-9 trig= 1e-9\n")
        self.assertEqual(rc, 0)
        r = self.report()
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["metrics"], {"vm": 0.85, "tphl": 3.0e-11})

    def test_out_of_spec_fails(self):
        self.assertEqual(self.run_sim("vm = 1.5\ntphl = 3.0e-11\n"), 1)
        self.assertEqual(self.report()["status"], "fail")

    def test_missing_measurement_is_error(self):
        self.assertEqual(self.run_sim("vm = 0.9\n"), 1)
        self.assertEqual(self.report()["status"], "error")

    def test_missing_log_is_error(self):
        spec = self.write("spec.json", json.dumps(SPEC))
        rc = main(["sim", "--log", self.path("none.log"), "--spec", spec,
                   "--design", "inv", "--stage", "sim", "--out", self.out])
        self.assertEqual(rc, 1)
        self.assertEqual(self.report()["status"], "error")


if __name__ == "__main__":
    unittest.main()
