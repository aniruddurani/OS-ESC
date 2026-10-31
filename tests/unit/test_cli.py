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


class LayoutCommandTest(CliTestBase):
    GOOD_LOG = "PORT 1 in\nPORT 2 out\nPORT 3 vdd\nPORT 4 vss\n"

    def run_layout(self, gds_bytes, log_text):
        gds = self.path("inv.gds")
        if gds_bytes is not None:
            with open(gds, "wb") as f:
                f.write(gds_bytes)
        log = self.write("layout.log", log_text)
        return main(["layout", "--gds", gds, "--log", log,
                     "--design", "inv", "--out", self.out])

    def test_gds_and_ports_pass(self):
        self.assertEqual(self.run_layout(b"\x00\x06\x00\x02\x00\x07", self.GOOD_LOG), 0)
        self.assertEqual(self.report()["status"], "pass")

    def test_missing_gds_is_error(self):
        self.assertEqual(self.run_layout(None, self.GOOD_LOG), 1)
        self.assertEqual(self.report()["status"], "error")

    def test_empty_gds_is_error(self):
        self.assertEqual(self.run_layout(b"", self.GOOD_LOG), 1)
        self.assertEqual(self.report()["status"], "error")

    def test_missing_port_fails(self):
        self.assertEqual(self.run_layout(b"gds", "PORT 1 in\nPORT 2 out\nPORT 3 vdd\n"), 1)
        r = self.report()
        self.assertEqual(r["status"], "fail")
        self.assertIn("vss", r["errors"][0]["message"])

    def test_wrong_port_order_fails(self):
        log = "PORT 1 out\nPORT 2 in\nPORT 3 vdd\nPORT 4 vss\n"
        self.assertEqual(self.run_layout(b"gds", log), 1)
        self.assertEqual(self.report()["status"], "fail")


class PexCommandTest(CliTestBase):
    PRE = {"vm": 0.87, "tphl": 3.0e-11, "tplh": 4.0e-11}
    SPEC = {"measurements": {
        "vm": {"min": 0.70, "max": 1.10, "unit": "V"},
        "tphl": {"max": 1e-10, "unit": "s"},
        "tplh": {"max": 1e-10, "unit": "s"},
    }}

    def run_pex(self, log_text, pre=PRE):
        log = self.write("pex.log", log_text)
        spec = self.write("spec.json", json.dumps(self.SPEC))
        if pre is None:
            sim = self.path("missing_sim.json")
        else:
            sim = self.write("sim.json", json.dumps({"status": "pass", "metrics": pre}))
        return main(["sim", "--stage", "pex", "--log", log, "--spec", spec,
                     "--compare", sim, "--design", "inv", "--out", self.out])

    def test_slower_post_layout_passes_and_reports_pre_post_delta(self):
        rc = self.run_pex("vm = 0.86\ntphl = 4.5e-11\ntplh = 5.0e-11\n")
        self.assertEqual(rc, 0)
        r = self.report()
        self.assertEqual(r["status"], "pass")
        tphl = r["metrics"]["tphl"]
        self.assertEqual((tphl["pre"], tphl["post"]), (3.0e-11, 4.5e-11))
        self.assertAlmostEqual(tphl["delta_pct"], 50.0)

    def test_post_layout_out_of_spec_fails(self):
        self.assertEqual(self.run_pex("vm = 0.86\ntphl = 1.5e-10\ntplh = 5.0e-11\n"), 1)
        self.assertEqual(self.report()["status"], "fail")

    def test_faster_post_layout_delay_is_error_not_pass(self):
        # Parasitics only add load; a faster extracted circuit means the
        # extraction or the deck is wrong, however good the number looks.
        self.assertEqual(self.run_pex("vm = 0.86\ntphl = 2.0e-11\ntplh = 5.0e-11\n"), 1)
        r = self.report()
        self.assertEqual(r["status"], "error")
        self.assertIn("tphl", r["errors"][0]["message"])

    def test_missing_schematic_report_is_error(self):
        self.assertEqual(self.run_pex("vm = 0.86\ntphl = 4.5e-11\ntplh = 5.0e-11\n", pre=None), 1)
        self.assertEqual(self.report()["status"], "error")

    def test_missing_post_measurement_is_error(self):
        self.assertEqual(self.run_pex("vm = 0.86\ntphl = 4.5e-11\n"), 1)
        self.assertEqual(self.report()["status"], "error")


class DrcCommandTest(CliTestBase):
    CLEAN_MAGIC = "DRC_COUNT 0\nBBOX 0 0 5 5\n"
    CLEAN_LYRDB = "<report-database><items/></report-database>"
    ONE_LYRDB = ("<report-database><items><item><category>'m1.2'</category>"
                 "<values><value>edge-pair: (0,0;1,0)/(0,0.1;1,0.1)</value></values>"
                 "</item></items></report-database>")

    def run_drc(self, magic_log, lyrdb):
        m = self.write("drc_magic.log", magic_log)
        args = ["drc", "--magic-log", m, "--design", "inv", "--out", self.out]
        if lyrdb is not None:
            args += ["--klayout-report", self.write("drc.lyrdb", lyrdb)]
        else:
            args += ["--klayout-report", self.path("missing.lyrdb")]
        return main(args)

    def test_both_clean_passes(self):
        self.assertEqual(self.run_drc(self.CLEAN_MAGIC, self.CLEAN_LYRDB), 0)
        r = self.report()
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["metrics"], {"magic_violations": 0, "klayout_violations": 0})

    def test_magic_violation_fails_with_rule_and_location(self):
        log = "DRC_COUNT 1\nBBOX 0 0 5 5\nDRC_ERR 1.0 2.0 1.5 2.5 Metal1 spacing < 0.14um\n"
        self.assertEqual(self.run_drc(log, self.CLEAN_LYRDB), 1)
        r = self.report()
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["metrics"]["magic_violations"], 1)
        self.assertEqual(r["errors"], [{
            "tool": "magic", "rule": "Metal1 spacing < 0.14um", "where": [1.0, 2.0, 1.5, 2.5]}])

    def test_klayout_violation_fails(self):
        self.assertEqual(self.run_drc(self.CLEAN_MAGIC, self.ONE_LYRDB), 1)
        r = self.report()
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["metrics"]["klayout_violations"], 1)
        self.assertEqual(r["errors"][0]["tool"], "klayout")
        self.assertEqual(r["errors"][0]["rule"], "m1.2")

    def test_empty_layout_is_error_not_pass(self):
        self.assertEqual(self.run_drc("DRC_COUNT 0\nBBOX 0 0 0 0\n", self.CLEAN_LYRDB), 1)
        self.assertEqual(self.report()["status"], "error")

    def test_missing_klayout_report_is_error(self):
        self.assertEqual(self.run_drc(self.CLEAN_MAGIC, None), 1)
        self.assertEqual(self.report()["status"], "error")

    def test_magic_crash_is_error(self):
        self.assertEqual(self.run_drc("magic crashed\n", self.CLEAN_LYRDB), 1)
        self.assertEqual(self.report()["status"], "error")

    def run_drc_with_klayout_log(self, klayout_log):
        m = self.write("drc_magic.log", self.CLEAN_MAGIC)
        r = self.write("drc.lyrdb", self.CLEAN_LYRDB)
        k = self.write("drc_klayout.log", klayout_log)
        return main(["drc", "--magic-log", m, "--klayout-report", r,
                     "--klayout-log", k, "--design", "inv", "--out", self.out])

    def test_klayout_rule_groups_enabled_passes(self):
        # The sky130A deck runs no front/back-end rules unless asked to.
        log = "Args:\n\tfeol: true\n\tbeol: true\n\toffgrid: true\n"
        self.assertEqual(self.run_drc_with_klayout_log(log), 0)

    def test_klayout_rule_groups_disabled_is_error_not_pass(self):
        log = "Args:\n\tfeol: false\n\tbeol: false\n\toffgrid: false\n"
        self.assertEqual(self.run_drc_with_klayout_log(log), 1)
        r = self.report()
        self.assertEqual(r["status"], "error")
        self.assertIn("feol", r["errors"][-1]["message"])

    def test_klayout_log_missing_args_is_error(self):
        self.assertEqual(self.run_drc_with_klayout_log("KLayout crashed\n"), 1)
        self.assertEqual(self.report()["status"], "error")


class LvsCommandTest(CliTestBase):
    MATCH = ("Subcircuit summary:\nCircuit 1: inv  |Circuit 2: inv\n"
             "Netlists match uniquely.\nFinal result: Circuits match uniquely.\n")

    def run_lvs(self, report_text):
        log = self.write("lvs.log", "")
        if report_text is None:
            rep = self.path("missing.out")
        else:
            rep = self.write("lvs_netgen.out", report_text)
        return main(["lvs", "--report", rep, "--log", log,
                     "--design", "inv", "--out", self.out])

    def test_unique_match_passes(self):
        self.assertEqual(self.run_lvs(self.MATCH), 0)
        r = self.report()
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["metrics"], {"match": True})

    def test_mismatch_fails_and_lists_the_problem(self):
        text = "Netlists do not match.\nFinal result: Netlists do not match.\n"
        self.assertEqual(self.run_lvs(text), 1)
        r = self.report()
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["metrics"], {"match": False})
        self.assertIn("Netlists do not match.", r["errors"][0]["message"])

    def test_pin_mismatch_fails_even_with_match_line(self):
        self.assertEqual(self.run_lvs(self.MATCH + "Top level cell failed pin matching.\n"), 1)
        self.assertEqual(self.report()["status"], "fail")

    def test_no_final_result_is_error(self):
        self.assertEqual(self.run_lvs("netgen crashed\n"), 1)
        self.assertEqual(self.report()["status"], "error")

    def test_missing_report_is_error(self):
        self.assertEqual(self.run_lvs(None), 1)
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
