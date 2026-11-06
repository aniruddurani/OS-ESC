"""The FPGA board gates (flowlib/board.py), on report text in Quartus's own table format."""
import unittest

from flowlib.board import evaluate_bbuild

MODELS = ("Slow 1200mV 85C", "Slow 1200mV 0C", "Fast 1200mV 0C")
CORE = "u_pll|altpll_component|auto_generated|pll1|clk[0]"
PARAMS = dict(N_FMMU=3, N_SM=4, RAM_BYTES=256, DIAG_EN=1, FIFO_DEPTH=16)


def table(title, header, rows):
    line = "+" + "-" * 40 + "+"
    out = [line, f"; {title} ;", line, "; " + " ; ".join(header) + " ;", line]
    out += ["; " + " ; ".join(str(c) for c in r) + " ;" for r in rows]
    return "\n".join(out + [line, ""])


def empty_table(title):
    line = "-" * 40
    return "\n".join([line, f"; {title} ;", line, "No paths to report.", ""])


KINDS = ("Setup", "Hold", "Recovery", "Removal", "Minimum Pulse Width")


def sta_rpt(slack=None, models=MODELS, core_mhz="80.0", unconstrained=0, skip=(), no_paths=()):
    """slack: {(model, kind, clock): value} overriding the default 1.0. skip: kinds left out
    of the report; no_paths: kinds that report no paths, as Quartus prints them."""
    slack = slack or {}
    clocks = ["CLOCK_50", CORE, "ENET0_RX_CLK", "ENET0_TX_CLK"]
    parts = [table("Clocks", ["Clock Name", "Type", "Period", "Frequency"],
                   [["CLOCK_50", "Base", "20.000", "50.0 MHz"],
                    [CORE, "Generated", "12.500", f"{core_mhz} MHz"],
                    ["ENET0_RX_CLK", "Base", "40.000", "25.0 MHz"],
                    ["ENET0_TX_CLK", "Base", "40.000", "25.0 MHz"]])]
    for m in models:
        for kind in KINDS:
            if kind in skip:
                continue
            if kind in no_paths:
                parts.append(empty_table(f"{m} Model {kind} Summary"))
                continue
            parts.append(table(f"{m} Model {kind} Summary", ["Clock", "Slack", "End Point TNS"],
                               [[c, f"{slack.get((m, kind, c), 1.0):.3f}", "0.000"] for c in clocks]))
    parts.append(table("Unconstrained Paths Summary", ["Property", "Setup", "Hold"],
                       [["Illegal Clocks", 0, 0], ["Unconstrained Clocks", 0, 0],
                        ["Unconstrained Input Ports", 0, 0],
                        ["Unconstrained Input Port Paths", unconstrained, unconstrained],
                        ["Unconstrained Output Ports", 0, 0],
                        ["Unconstrained Output Port Paths", 0, 0]]))
    return "\n".join(parts)


def map_rpt(params=None, latch=False):
    params = params or PARAMS
    text = table("Parameter Settings for User Entity Instance: esc_top:u_esc",
                 ["Parameter Name", "Value", "Type"],
                 [[k, v, "Signed Integer"] for k, v in params.items()])
    if latch:
        text += 'Warning (10240): Verilog HDL Always Construct warning at x.v(3): inferring latch(es) for variable "q"\n'
    return text


FIT = """Fitter Status : Successful
; Total logic elements               ; 9,876 / 114,480 ( 9 % )  ;
; Total registers                    ; 7,321                    ;
; Total memory bits                  ; 4,096 / 3,981,312 ( < 1 % ) ;
"""


def run(sta=None, mp=None, fit=FIT, core_mhz=80.0):
    return evaluate_bbuild(sta_rpt() if sta is None else sta, map_rpt() if mp is None else mp,
                           fit, PARAMS, core_mhz)


class BbuildTest(unittest.TestCase):
    def test_all_slacks_nonneg_is_pass(self):
        status, metrics, errors = run()
        self.assertEqual(status, "pass", errors)
        self.assertEqual(metrics["core_mhz"], 80.0)
        self.assertEqual(metrics["les"], 9876)
        self.assertEqual(metrics["registers"], 7321)
        self.assertEqual(metrics["memory_bits"], 4096)
        self.assertEqual(metrics["worst_setup_ns"], 1.0)
        self.assertEqual(metrics["unconstrained"], 0)

    def test_negative_setup_on_one_model_is_fail(self):
        status, metrics, errors = run(sta=sta_rpt({("Slow 1200mV 85C", "Setup", CORE): -0.25}))
        self.assertEqual(status, "fail")
        self.assertEqual(metrics["worst_setup_ns"], -0.25)
        msg = " ".join(e["message"] for e in errors)
        self.assertIn("Slow 1200mV 85C", msg)
        self.assertIn(CORE, msg)

    def test_negative_hold_is_fail(self):
        status, _, _ = run(sta=sta_rpt({("Fast 1200mV 0C", "Hold", "ENET0_RX_CLK"): -0.1}))
        self.assertEqual(status, "fail")

    def test_negative_recovery_is_fail(self):
        status, metrics, errors = run(sta=sta_rpt({("Fast 1200mV 0C", "Recovery", "ENET0_TX_CLK"): -0.2}))
        self.assertEqual(status, "fail")
        self.assertEqual(metrics["worst_recovery_ns"], -0.2)
        self.assertIn("recovery slack", " ".join(e["message"] for e in errors))

    def test_negative_removal_is_fail(self):
        status, metrics, _ = run(sta=sta_rpt({("Slow 1200mV 0C", "Removal", "CLOCK_50"): -0.1}))
        self.assertEqual((status, metrics["worst_removal_ns"]), ("fail", -0.1))

    def test_negative_pulse_width_is_fail(self):
        status, metrics, _ = run(sta=sta_rpt({("Slow 1200mV 85C", "Minimum Pulse Width", CORE): -0.3}))
        self.assertEqual((status, metrics["worst_pulse_width_ns"]), ("fail", -0.3))

    def test_missing_recovery_table_is_error(self):
        status, _, _ = run(sta=sta_rpt(skip=("Recovery",)))
        self.assertEqual(status, "error")

    def test_recovery_with_no_paths_is_not_a_failure(self):
        status, metrics, errors = run(sta=sta_rpt(no_paths=("Recovery", "Removal")))
        self.assertEqual(status, "pass", errors)
        self.assertIsNone(metrics["worst_recovery_ns"])

    def test_setup_with_no_paths_is_error(self):
        status, _, _ = run(sta=sta_rpt(no_paths=("Setup",)))
        self.assertEqual(status, "error")

    def test_missing_timing_model_is_error(self):
        status, _, errors = run(sta=sta_rpt(models=MODELS[:2]))
        self.assertEqual(status, "error")
        self.assertIn("Fast 1200mV 0C", errors[0]["message"])

    def test_unconstrained_paths_fail(self):
        status, metrics, _ = run(sta=sta_rpt(unconstrained=3))
        self.assertEqual(status, "fail")
        self.assertEqual(metrics["unconstrained"], 6)

    def test_core_clock_not_80_is_fail(self):
        status, _, errors = run(sta=sta_rpt(core_mhz="79.9"))
        self.assertEqual(status, "fail")
        self.assertIn("79.9", errors[0]["message"])

    def test_inferred_latch_fails(self):
        status, metrics, _ = run(mp=map_rpt(latch=True))
        self.assertEqual(status, "fail")
        self.assertEqual(metrics["latches"], 1)

    def test_params_mismatch_fails(self):
        status, _, errors = run(mp=map_rpt(dict(PARAMS, RAM_BYTES=1024)))
        self.assertEqual(status, "fail")
        self.assertIn("RAM_BYTES", errors[0]["message"])

    def test_no_params_table_is_error(self):
        status, _, _ = run(mp="nothing here\n")
        self.assertEqual(status, "error")

    def test_no_fit_report_is_error(self):
        status, _, _ = run(fit="")
        self.assertEqual(status, "error")

    def test_no_clocks_table_is_error(self):
        status, _, _ = run(sta=sta_rpt().replace("; Clocks ;", "; Something ;"))
        self.assertEqual(status, "error")


class BbuildCliTest(unittest.TestCase):
    def _run(self, files, yaml_text):
        import json
        import os
        import tempfile
        from flowlib.cli import main
        d = tempfile.mkdtemp()
        paths = {}
        for name, text in files.items():
            paths[name] = os.path.join(d, name)
            with open(paths[name], "w") as f:
                f.write(text)
        yml = os.path.join(d, "default.yaml")
        with open(yml, "w") as f:
            f.write(yaml_text)
        out = os.path.join(d, "bbuild.json")
        rc = main(["bbuild", "--sta-rpt", paths.get("sta", os.path.join(d, "none.sta.rpt")),
                   "--map-rpt", paths["map"], "--fit-rpt", paths["fit"], "--params-from", yml,
                   "--core-mhz", "80", "--design", "ecat_esc", "--out", out])
        with open(out) as f:
            return rc, json.load(f)

    YAML = "SYNTH_PARAMETERS:\n  - N_FMMU=3\n  - N_SM=4\n  - RAM_BYTES=256\n  - DIAG_EN=1\n  - FIFO_DEPTH=16\nCLOCK_PERIOD: 10\n"

    def test_cli_pass_reads_params_from_librelane_yaml(self):
        rc, rep = self._run(dict(sta=sta_rpt(), map=map_rpt(), fit=FIT), self.YAML)
        self.assertEqual((rc, rep["status"], rep["stage"]), (0, "pass", "bbuild"))

    def test_cli_missing_timing_report_is_error(self):
        rc, rep = self._run(dict(map=map_rpt(), fit=FIT), self.YAML)
        self.assertEqual((rc, rep["status"]), (1, "error"))


PGM_OK = """Info (209016): Configuring device index 1
Info (209017): Device 1 contains JTAG ID code 0x020F70DD
Info (209007): Configuration succeeded -- 1 device(s) configured
Info: Quartus Prime Programmer was successful. 0 errors, 0 warnings
"""
LINK = {"build_id": 1, "state": "link_100fd",
        "sticky": {"rx_overflow": 0, "tx_overflow": 0, "tx_underrun": 0}}


class CliBuildIdTest(unittest.TestCase):
    def test_buildid_writes_a_decimal_id(self):
        import os
        import tempfile
        from flowlib.cli import main
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "a.v"), "w") as f:
            f.write("module a; endmodule\n")
        out = os.path.join(d, "build_id.txt")
        self.assertEqual(main(["buildid", "--dir", d, "--settings", "8/5", "--out", out]), 0)
        with open(out) as f:
            self.assertTrue(f.read().isdigit())


class BprogTest(unittest.TestCase):
    def test_bprog_pass_needs_jtag_id_and_link(self):
        from flowlib.board import evaluate_bprog
        status, metrics, errors = evaluate_bprog(PGM_OK, LINK, 1)
        self.assertEqual(status, "pass", errors)
        self.assertEqual(metrics["state"], "link_100fd")

    def test_bprog_wrong_link_is_fail(self):
        from flowlib.board import evaluate_bprog
        status, _, errors = evaluate_bprog(PGM_OK, dict(LINK, state="wrong_link"), 1)
        self.assertEqual(status, "fail")
        self.assertIn("wrong_link", errors[0]["message"])

    def test_bprog_sticky_flag_is_fail(self):
        from flowlib.board import evaluate_bprog
        status, _, _ = evaluate_bprog(PGM_OK, dict(LINK, sticky=dict(LINK["sticky"], tx_underrun=1)), 1)
        self.assertEqual(status, "fail")

    def test_bprog_no_status_is_error(self):
        from flowlib.board import evaluate_bprog
        status, _, _ = evaluate_bprog(PGM_OK, None, 1)
        self.assertEqual(status, "error")

    def test_bprog_other_jtag_id_is_error(self):
        from flowlib.board import evaluate_bprog
        status, _, _ = evaluate_bprog(PGM_OK.replace("0x020F70DD", "0x020F30DD"), LINK, 1)
        self.assertEqual(status, "error")

    def test_bprog_other_image_on_the_board_is_error(self):
        from flowlib.board import evaluate_bprog
        status, _, errors = evaluate_bprog(PGM_OK, LINK, 77)
        self.assertEqual(status, "error")
        self.assertIn("77", errors[0]["message"])

    def test_bprog_configuration_not_succeeded_is_error(self):
        from flowlib.board import evaluate_bprog
        status, _, _ = evaluate_bprog(PGM_OK.replace("Configuration succeeded", "Configuration failed"), LINK, 1)
        self.assertEqual(status, "error")


BOARD_MANIFEST = [("startup", 10, 100), ("cyclic", 10, 100), ("total", 20, 200)]


def board_results(**over):
    def one(scenario, **kw):
        r = dict(scenario=scenario, k=3, seed=1, frames=11, error="", failures=[], injected={},
                 diag={}, config=dict(rx_ppm=0.0, tx_ppm=0.0), sim_ps=0, wall_s=1.0, cycles=0,
                 metrics=dict(sent=11, replies=11, mismatches=0, missing=0, unexpected=0,
                              flags_seen=0, late=0),
                 board_before=LINK, board_after=LINK)
        r.update(kw)
        return r
    doc = dict(size="quick", runs=[one("startup"), one("cyclic")], not_applicable=["clocks", "faults"],
               build_id=1)
    doc.update(over)
    return doc


class BnetTest(unittest.TestCase):
    def test_bnet_pass(self):
        from flowlib.board import evaluate_bnet
        status, metrics, errors = evaluate_bnet(board_results(), BOARD_MANIFEST, "quick", 1)
        self.assertEqual(status, "pass", errors)
        self.assertEqual(metrics["frames"], 22)

    def test_bnet_mismatch_is_fail(self):
        from flowlib.board import evaluate_bnet
        doc = board_results()
        doc["runs"][1]["metrics"]["mismatches"] = 2
        status, _, _ = evaluate_bnet(doc, BOARD_MANIFEST, "quick", 1)
        self.assertEqual(status, "fail")

    def test_bnet_flag_on_board_is_fail(self):
        from flowlib.board import evaluate_bnet
        doc = board_results()
        doc["runs"][0]["metrics"]["flags_seen"] = 2
        status, _, _ = evaluate_bnet(doc, BOARD_MANIFEST, "quick", 1)
        self.assertEqual(status, "fail")

    def test_bnet_link_lost_after_run_is_fail(self):
        from flowlib.board import evaluate_bnet
        doc = board_results()
        doc["runs"][1]["board_after"] = dict(LINK, state="configuring")
        status, _, errors = evaluate_bnet(doc, BOARD_MANIFEST, "quick", 1)
        self.assertEqual(status, "fail")
        self.assertIn("configuring", " ".join(e["message"] for e in errors))

    def test_bnet_missing_board_status_is_error(self):
        from flowlib.board import evaluate_bnet
        doc = board_results()
        del doc["runs"][0]["board_after"]
        status, _, _ = evaluate_bnet(doc, BOARD_MANIFEST, "quick", 1)
        self.assertEqual(status, "error")

    def test_bnet_wrong_not_applicable_is_error(self):
        from flowlib.board import evaluate_bnet
        status, _, _ = evaluate_bnet(board_results(not_applicable=["clocks"]), BOARD_MANIFEST, "quick", 1)
        self.assertEqual(status, "error")

    def test_bnet_other_image_is_error(self):
        from flowlib.board import evaluate_bnet
        status, _, errors = evaluate_bnet(board_results(), BOARD_MANIFEST, "quick", 77)
        self.assertEqual(status, "error")
        self.assertIn("77", " ".join(e["message"] for e in errors))

    def test_bnet_too_few_frames_is_error(self):
        from flowlib.board import evaluate_bnet
        doc = board_results()
        doc["runs"][0]["frames"] = 3
        status, _, _ = evaluate_bnet(doc, BOARD_MANIFEST, "quick", 1)
        self.assertEqual(status, "error")


SOEM_OK = {"checks": {"brd_count": True, "station_address": True, "fmmu_config": True,
                      "sm_config": True, "lrw_cycles": True},
           "pass": True, "cycle_us": {"min": 300.0, "median": 450.0, "max": 900.0}, "cycles": 10000}


class BsoemTest(unittest.TestCase):
    def test_bsoem_all_checks_pass(self):
        from flowlib.board import evaluate_bsoem
        status, metrics, errors = evaluate_bsoem(SOEM_OK, LINK, LINK, 1)
        self.assertEqual(status, "pass", errors)
        self.assertEqual(metrics["cycles"], 10000)
        self.assertEqual(metrics["cycle_us_median"], 450.0)

    def test_bsoem_one_check_false_is_fail(self):
        from flowlib.board import evaluate_bsoem
        res = dict(SOEM_OK, checks=dict(SOEM_OK["checks"], fmmu_config=False), **{"pass": False})
        status, _, errors = evaluate_bsoem(res, LINK, LINK, 1)
        self.assertEqual(status, "fail")
        self.assertIn("fmmu_config", errors[0]["message"])

    def test_bsoem_no_json_is_error(self):
        from flowlib.board import evaluate_bsoem
        status, _, _ = evaluate_bsoem(None, LINK, LINK, 1)
        self.assertEqual(status, "error")

    def test_bsoem_missing_check_is_error(self):
        from flowlib.board import evaluate_bsoem
        checks = dict(SOEM_OK["checks"])
        del checks["lrw_cycles"]
        status, _, _ = evaluate_bsoem(dict(SOEM_OK, checks=checks), LINK, LINK, 1)
        self.assertEqual(status, "error")

    def test_bsoem_board_absent_is_error_even_with_failed_checks(self):
        from flowlib.board import evaluate_bsoem
        res = dict(SOEM_OK, checks={k: False for k in SOEM_OK["checks"]}, **{"pass": False})
        status, _, _ = evaluate_bsoem(res, None, None, 1)
        self.assertEqual(status, "error")

    def test_bsoem_no_link_before_is_error(self):
        from flowlib.board import evaluate_bsoem
        status, _, errors = evaluate_bsoem(SOEM_OK, dict(LINK, state="configuring"), LINK, 1)
        self.assertEqual(status, "error")
        self.assertIn("configuring", errors[0]["message"])

    def test_bsoem_other_image_is_error(self):
        from flowlib.board import evaluate_bsoem
        status, _, _ = evaluate_bsoem(SOEM_OK, LINK, LINK, 77)
        self.assertEqual(status, "error")

    def test_bsoem_flag_after_the_run_is_fail(self):
        from flowlib.board import evaluate_bsoem
        after = dict(LINK, sticky=dict(LINK["sticky"], rx_overflow=1))
        status, _, errors = evaluate_bsoem(SOEM_OK, LINK, after, 1)
        self.assertEqual(status, "fail")
        self.assertIn("rx_overflow", errors[0]["message"])

    def test_bsoem_too_few_cycles_is_error(self):
        from flowlib.board import evaluate_bsoem
        status, _, _ = evaluate_bsoem(dict(SOEM_OK, cycles=10), LINK, LINK, 1)
        self.assertEqual(status, "error")


class BuildIdTest(unittest.TestCase):
    def _tree(self):
        import os
        import tempfile
        d = tempfile.mkdtemp()
        for name, text in (("a.v", "module a; endmodule\n"), ("b.v", "module b; endmodule\n")):
            with open(os.path.join(d, name), "w") as f:
                f.write(text)
        return d

    def test_same_sources_same_id(self):
        from flowlib.board import build_id
        d = self._tree()
        self.assertEqual(build_id([d], "8/5"), build_id([d], "8/5"))
        self.assertLess(build_id([d], "8/5"), 1 << 24)

    def test_a_changed_source_changes_the_id(self):
        import os
        from flowlib.board import build_id
        d = self._tree()
        before = build_id([d], "8/5")
        with open(os.path.join(d, "b.v"), "a") as f:
            f.write("// changed\n")
        self.assertNotEqual(build_id([d], "8/5"), before)

    def test_other_settings_change_the_id(self):
        from flowlib.board import build_id
        d = self._tree()
        self.assertNotEqual(build_id([d], "8/5"), build_id([d], "4/1"))


class StatusDecodeTest(unittest.TestCase):
    def test_decode_probe_value(self):
        import os
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "designs", "ecat_esc", "board"))
        from board_status import decode
        # BUILD_ID 0x01, state 2 (link_100fd), sticky 0b100 (tx_underrun)
        v = (0x01 << 8) | (2 << 3) | 0b100
        self.assertEqual(decode(f"ISSP {v:04X}\n"),
                         {"build_id": 1, "state": "link_100fd",
                          "sticky": {"rx_overflow": 0, "tx_overflow": 0, "tx_underrun": 1}})
        self.assertEqual(decode("ISSP 0100")["state"], "idle")
        self.assertEqual(decode("ISSP 0120")["state"], "mdio_error")
        self.assertIsNone(decode("no probe line here"))


if __name__ == "__main__":
    unittest.main()
