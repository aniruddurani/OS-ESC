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


def sta_rpt(slack=None, models=MODELS, core_mhz="80.0", unconstrained=0):
    """slack: {(model, kind, clock): value} overriding the default 1.0."""
    slack = slack or {}
    clocks = ["CLOCK_50", CORE, "ENET0_RX_CLK", "ENET0_TX_CLK"]
    parts = [table("Clocks", ["Clock Name", "Type", "Period", "Frequency"],
                   [["CLOCK_50", "Base", "20.000", "50.0 MHz"],
                    [CORE, "Generated", "12.500", f"{core_mhz} MHz"],
                    ["ENET0_RX_CLK", "Base", "40.000", "25.0 MHz"],
                    ["ENET0_TX_CLK", "Base", "40.000", "25.0 MHz"]])]
    for m in models:
        for kind in ("Setup", "Hold"):
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


class BprogTest(unittest.TestCase):
    def test_bprog_pass_needs_jtag_id_and_link(self):
        from flowlib.board import evaluate_bprog
        status, metrics, errors = evaluate_bprog(PGM_OK, LINK)
        self.assertEqual(status, "pass", errors)
        self.assertEqual(metrics["state"], "link_100fd")

    def test_bprog_wrong_link_is_fail(self):
        from flowlib.board import evaluate_bprog
        status, _, errors = evaluate_bprog(PGM_OK, dict(LINK, state="wrong_link"))
        self.assertEqual(status, "fail")
        self.assertIn("wrong_link", errors[0]["message"])

    def test_bprog_sticky_flag_is_fail(self):
        from flowlib.board import evaluate_bprog
        status, _, _ = evaluate_bprog(PGM_OK, dict(LINK, sticky=dict(LINK["sticky"], tx_underrun=1)))
        self.assertEqual(status, "fail")

    def test_bprog_no_status_is_error(self):
        from flowlib.board import evaluate_bprog
        status, _, _ = evaluate_bprog(PGM_OK, None)
        self.assertEqual(status, "error")

    def test_bprog_other_jtag_id_is_error(self):
        from flowlib.board import evaluate_bprog
        status, _, _ = evaluate_bprog(PGM_OK.replace("0x020F70DD", "0x020F30DD"), LINK)
        self.assertEqual(status, "error")

    def test_bprog_configuration_not_succeeded_is_error(self):
        from flowlib.board import evaluate_bprog
        status, _, _ = evaluate_bprog(PGM_OK.replace("Configuration succeeded", "Configuration failed"), LINK)
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
    doc = dict(size="quick", runs=[one("startup"), one("cyclic")], not_applicable=["clocks", "faults"])
    doc.update(over)
    return doc


class BnetTest(unittest.TestCase):
    def test_bnet_pass(self):
        from flowlib.board import evaluate_bnet
        status, metrics, errors = evaluate_bnet(board_results(), BOARD_MANIFEST, "quick")
        self.assertEqual(status, "pass", errors)
        self.assertEqual(metrics["frames"], 22)

    def test_bnet_mismatch_is_fail(self):
        from flowlib.board import evaluate_bnet
        doc = board_results()
        doc["runs"][1]["metrics"]["mismatches"] = 2
        status, _, _ = evaluate_bnet(doc, BOARD_MANIFEST, "quick")
        self.assertEqual(status, "fail")

    def test_bnet_flag_on_board_is_fail(self):
        from flowlib.board import evaluate_bnet
        doc = board_results()
        doc["runs"][0]["metrics"]["flags_seen"] = 2
        status, _, _ = evaluate_bnet(doc, BOARD_MANIFEST, "quick")
        self.assertEqual(status, "fail")

    def test_bnet_link_lost_after_run_is_fail(self):
        from flowlib.board import evaluate_bnet
        doc = board_results()
        doc["runs"][1]["board_after"] = dict(LINK, state="configuring")
        status, _, errors = evaluate_bnet(doc, BOARD_MANIFEST, "quick")
        self.assertEqual(status, "fail")
        self.assertIn("configuring", " ".join(e["message"] for e in errors))

    def test_bnet_missing_board_status_is_error(self):
        from flowlib.board import evaluate_bnet
        doc = board_results()
        del doc["runs"][0]["board_after"]
        status, _, _ = evaluate_bnet(doc, BOARD_MANIFEST, "quick")
        self.assertEqual(status, "error")

    def test_bnet_wrong_not_applicable_is_error(self):
        from flowlib.board import evaluate_bnet
        status, _, _ = evaluate_bnet(board_results(not_applicable=["clocks"]), BOARD_MANIFEST, "quick")
        self.assertEqual(status, "error")

    def test_bnet_too_few_frames_is_error(self):
        from flowlib.board import evaluate_bnet
        doc = board_results()
        doc["runs"][0]["frames"] = 3
        status, _, _ = evaluate_bnet(doc, BOARD_MANIFEST, "quick")
        self.assertEqual(status, "error")


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
