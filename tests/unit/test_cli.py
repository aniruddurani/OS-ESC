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

    DEVICE = "XM1 out in vdd vdd sky130_fd_pr__pfet_01v8 L=0.15 W=2\n"

    def test_good_netlist_passes(self):
        n = self.write("inv.spice", ".subckt inv in out vdd vss\n" + self.DEVICE + ".ends\n")
        self.assertEqual(self.run_netlist(n), 0)
        self.assertEqual(self.report()["status"], "pass")
        self.assertEqual(self.report()["metrics"], {"devices": 1})

    def test_subckt_without_devices_is_error(self):
        # Wrong PDK or symbol path: pins but no devices must not pass.
        n = self.write("inv.spice", ".subckt inv in out vdd vss\n*.ipin in\n.ends\n")
        self.assertEqual(self.run_netlist(n), 1)
        self.assertEqual(self.report()["status"], "error")

    def test_wrong_port_order_fails(self):
        n = self.write("inv.spice", ".subckt inv vdd vss in out\n.ends\n")
        self.assertEqual(self.run_netlist(n), 1)
        self.assertEqual(self.report()["status"], "fail")

    def test_missing_netlist_is_error(self):
        self.assertEqual(self.run_netlist(self.path("nope.spice")), 1)
        self.assertEqual(self.report()["status"], "error")

    def test_error_in_log_is_error(self):
        n = self.write("inv.spice", ".subckt inv in out vdd vss\n" + self.DEVICE + ".ends\n")
        self.assertEqual(self.run_netlist(n, "Error: symbol not found\n"), 1)
        self.assertEqual(self.report()["status"], "error")


class LayoutCommandTest(CliTestBase):
    GOOD = [("metal1", "in", 1), ("metal1", "out", 2), ("metal1", "vdd", 3), ("metal1", "vss", 4)]

    @staticmethod
    def mag(labels):
        """A .mag with (layer, name, port index) labels, as Magic writes them."""
        lines = ["magic", "tech sky130A", "<< labels >>"]
        for layer, name, idx in labels:
            lines += [f"rlabel {layer} 0 0 0 0 0 {name}", f"port {idx} nsew"]
        return "\n".join(lines) + "\n<< end >>\n"

    def run_layout(self, gds_bytes, labels):
        gds = self.path("inv.gds")
        if gds_bytes is not None:
            with open(gds, "wb") as f:
                f.write(gds_bytes)
        mag = self.write("inv.mag", self.mag(labels))
        log = self.write("layout.log", "")
        return main(["layout", "--gds", gds, "--mag", mag, "--log", log,
                     "--design", "inv", "--out", self.out])

    def test_gds_and_ports_pass(self):
        self.assertEqual(self.run_layout(b"GDSII", self.GOOD), 0)
        self.assertEqual(self.report()["status"], "pass")

    def test_missing_gds_is_error(self):
        self.assertEqual(self.run_layout(None, self.GOOD), 1)
        self.assertEqual(self.report()["status"], "error")

    def test_empty_gds_is_error(self):
        self.assertEqual(self.run_layout(b"", self.GOOD), 1)
        self.assertEqual(self.report()["status"], "error")

    def test_missing_port_fails(self):
        self.assertEqual(self.run_layout(b"GDSII", self.GOOD[:3]), 1)
        r = self.report()
        self.assertEqual(r["status"], "fail")
        self.assertIn("vss", r["errors"][0]["message"])

    def test_wrong_port_order_fails(self):
        labels = [("metal1", "out", 1), ("metal1", "in", 2)] + self.GOOD[2:]
        self.assertEqual(self.run_layout(b"GDSII", labels), 1)
        self.assertEqual(self.report()["status"], "fail")

    def test_port_label_on_empty_space_fails(self):
        # A label with no metal under it connects to nothing; the generator
        # printing the right names is not evidence the ports exist.
        labels = self.GOOD[:3] + [("space", "vss", 4)]
        self.assertEqual(self.run_layout(b"GDSII", labels), 1)
        r = self.report()
        self.assertEqual(r["status"], "fail")
        self.assertIn("vss", r["errors"][0]["message"])


class SummaryCommandTest(CliTestBase):
    STAGES = ["netlist", "sim", "layout", "drc", "lvs", "pex"]

    def write_reports(self, statuses):
        for stage, status in statuses.items():
            self.write(os.path.join("reports", f"{stage}.json"),
                       json.dumps({"stage": stage, "status": status}))

    def run_summary(self):
        return main(["summary", "--reports", self.path("reports"),
                     "--design", "inv", "--out", self.out])

    def setUp(self):
        super().setUp()
        os.makedirs(self.path("reports"), exist_ok=True)

    def test_all_stages_pass(self):
        self.write_reports({s: "pass" for s in self.STAGES})
        self.assertEqual(self.run_summary(), 0)
        r = self.report()
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["metrics"]["stages"], {s: "pass" for s in self.STAGES})

    def test_one_failing_stage_fails_the_summary(self):
        self.write_reports({**{s: "pass" for s in self.STAGES}, "lvs": "fail"})
        self.assertEqual(self.run_summary(), 1)
        r = self.report()
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["metrics"]["stages"]["lvs"], "fail")

    def test_stage_that_never_ran_is_missing_not_pass(self):
        # A stale or absent report must never read as a pass (Review Focus 4).
        self.write_reports({s: "pass" for s in self.STAGES if s != "pex"})
        self.assertEqual(self.run_summary(), 1)
        r = self.report()
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["metrics"]["stages"]["pex"], "missing")

    def test_pass_report_without_its_stamp_is_stale(self):
        # drc failed on this run; the lvs/pex reports on disk are from an
        # earlier run and must not read as passes.
        self.write_reports({s: "pass" for s in self.STAGES})
        os.makedirs(self.path("stamps"), exist_ok=True)
        for s in ["netlist", "sim", "layout"]:
            self.write(os.path.join("stamps", f"{s}.pass"), "")
        rc = main(["summary", "--reports", self.path("reports"), "--stamps", self.path("stamps"),
                   "--design", "inv", "--out", self.out])
        self.assertEqual(rc, 1)
        stages = self.report()["metrics"]["stages"]
        self.assertEqual(stages["layout"], "pass")
        self.assertEqual([stages[s] for s in ("drc", "lvs", "pex")], ["stale"] * 3)

    def test_unreadable_report_is_error(self):
        self.write_reports({s: "pass" for s in self.STAGES})
        self.write(os.path.join("reports", "drc.json"), "{not json")
        self.assertEqual(self.run_summary(), 1)
        self.assertEqual(self.report()["metrics"]["stages"]["drc"], "error")


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

    def test_magic_violation_metric_matches_the_listed_errors(self):
        # Magic's DRC_COUNT counts rule hits; the metric a person reads must
        # match the list they fix from.
        log = "DRC_COUNT 2\nBBOX 0 0 5 5\n" + "".join(
            f"DRC_ERR 0 0 1 {i} Metal1 spacing < 0.14 (met1.2)\n" for i in range(1, 5))
        self.assertEqual(self.run_drc(log, self.CLEAN_LYRDB), 1)
        r = self.report()
        self.assertEqual(r["metrics"]["magic_violations"], 4)
        self.assertEqual(len([e for e in r["errors"] if e["tool"] == "magic"]), 4)

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
