"""run_board against a fake board: a link that answers every frame with the reference
model's reply, and fake JTAG status functions. No NIC, Npcap or Quartus needed."""
import copy
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, ".."), os.path.join(HERE, "..", "..", "model"),
                os.path.join(HERE, "..", "..", "tb"), os.path.join(HERE, "..", "..", "board")]

import run_board  # noqa: E402
import run_netsim  # noqa: E402
from check import diag_reply  # noqa: E402
from ecat import SlaveModel  # noqa: E402

MANIFEST = os.path.join(HERE, "..", "..", "board", "de2_115", "manifest.txt")
LINK_UP = {"build_id": 1, "state": "link_100fd",
           "sticky": {"rx_overflow": 0, "tx_overflow": 0, "tx_underrun": 0}}


class PerfectBoard:
    """A link whose board is the reference model (fresh after every soft reset)."""
    def __init__(self, log):
        self.log, self.dut, self.queue, self.t = log, SlaveModel(ram_bytes=256), [], 0

    def send(self, frame, ifg=24, er_nibble=-1, record=False):
        self.queue.append(frame)

    def run(self, replies, timeout_ps):
        sent, got = [], []
        for f in self.queue:
            self.t += 1000
            sent.append(dict(index=len(self.log["sent"]), t_start_ps=self.t, t_end_ps=self.t))
            self.log["sent"].append(f)
            before, trace_len = copy.deepcopy(self.dut.counters), len(self.dut.trace)
            r = self.dut.process(f)
            if r is not None and int.from_bytes(f[20:22], "little") == 0x0E00:
                r = diag_reply(r, before, trace_len)[0]     # the RTL's diag block, as check.py predicts it
            self.t += 1000
            if r is not None:
                got.append(dict(t_start_ps=self.t, t_end_ps=self.t, data=r))
        self.queue = []
        return sent, got, [], dict(t_ps=self.t, core_cycles=0, seen=0, sticky=0)

    def dump(self):
        return [], dict(t_ps=self.t, core_cycles=0, seen=0, sticky=0)

    def close(self):
        pass


class FakeBoard:
    def __init__(self, before=LINK_UP, after=LINK_UP):
        self.calls, self.before, self.after = [], before, after
        self.log = {"sent": []}

    def soft_reset(self, stp=None):
        self.calls.append("reset")
        return self.before

    def read_status(self, stp=None):
        self.calls.append("read")
        return self.before if self.calls.count("read") % 2 else self.after

    def make_link(self):
        self.calls.append("link")
        return PerfectBoard(self.log)


def run(board, **kw):
    with tempfile.TemporaryDirectory() as out:
        return run_board.run_board(run_board.read_manifest(MANIFEST), "quick", out, mac="x",
                                   stp="stp", board=board, make_link=board.make_link,
                                   **dict(dict(frames=50, only=None), **kw))


class RunBoardTest(unittest.TestCase):
    def test_each_run_pulses_soft_reset_first(self):
        board = FakeBoard()
        res = run(board, only="cyclic_k3_s1")
        self.assertEqual(len(res["runs"]), 1)
        self.assertEqual(board.calls, ["reset", "read", "link", "read"])
        r = res["runs"][0]
        self.assertEqual(r["error"], "")
        self.assertEqual(r["metrics"]["mismatches"], 0)
        self.assertEqual(r["metrics"]["replies"], r["metrics"]["sent"])

    def test_link_down_before_run_is_error(self):
        board = FakeBoard(before=dict(LINK_UP, state="configuring"))
        res = run(board, only="cyclic_k3_s1")
        self.assertIn("configuring", res["runs"][0]["error"])
        self.assertNotIn("link", board.calls)

    def test_sticky_after_run_sets_flags_seen(self):
        board = FakeBoard(after=dict(LINK_UP, sticky=dict(LINK_UP["sticky"], tx_overflow=1)))
        res = run(board, only="cyclic_k3_s1")
        r = res["runs"][0]
        self.assertEqual(r["metrics"]["flags_seen"] & 2, 2)
        self.assertEqual(r["board_after"]["sticky"]["tx_overflow"], 1)

    def test_manifest_excludes_clocks_and_faults(self):
        runs = run_netsim.plan_runs(run_board.read_manifest(MANIFEST), "quick")
        self.assertEqual({r["scenario"] for r in runs}, {"startup", "cyclic", "random", "chain"})
        res = run(FakeBoard(), only="cyclic_k3_s1")
        self.assertEqual(res["not_applicable"], ["clocks", "faults"])

    def test_frames_override(self):
        board = FakeBoard()
        res = run(board, only="startup_k3_s1", frames=30)
        self.assertEqual(res["runs"][0]["frames"], 31)       # and the master's final diag read


class RunOneLinkTest(unittest.TestCase):
    def test_run_one_default_link_unchanged(self):
        made = []

        class Recorder:
            def __init__(self, binary, config):
                made.append((binary, config["core_mhz"]))
                raise run_netsim.LinkError("stop here")

        saved = run_netsim.Link
        run_netsim.Link = Recorder
        try:
            with tempfile.TemporaryDirectory() as out:
                res = run_netsim.run_one(dict(scenario="cyclic", k=0, seed=1, frames=10),
                                         "the/binary", out, False)
        finally:
            run_netsim.Link = saved
        self.assertEqual(made, [("the/binary", 100.0)])
        self.assertIn("stop here", res["error"])


if __name__ == "__main__":
    unittest.main()
