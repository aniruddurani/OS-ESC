"""run_board against a fake board: a link that answers every frame with the reference
model's reply, and fake JTAG status functions. No NIC, Npcap or Quartus needed."""
import copy
import json
import os
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, ".."), os.path.join(HERE, "..", "..", "model"),
                os.path.join(HERE, "..", "..", "tb"), os.path.join(HERE, "..", "..", "board")]

import board_status  # noqa: E402
import run_board  # noqa: E402
import run_netsim  # noqa: E402
from check import diag_reply  # noqa: E402
from ecat import SlaveModel  # noqa: E402
from pcap import read_pcap  # noqa: E402

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
            self.t += 1_000_000
            sent.append(dict(index=len(self.log["sent"]), t_start_ps=self.t, t_end_ps=self.t))
            self.log["sent"].append(f)
            before, trace_len = copy.deepcopy(self.dut.counters), len(self.dut.trace)
            r = self.dut.process(f)
            if r is not None and int.from_bytes(f[20:22], "little") == 0x0E00:
                r = diag_reply(r, before, trace_len)[0]     # the RTL's diag block, as check.py predicts it
            self.t += 1_000_000
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

    def test_wrong_image_is_error(self):
        board = FakeBoard()
        res = run(board, only="cyclic_k3_s1", build_id=77)
        self.assertIn("77", res["runs"][0]["error"])
        self.assertNotIn("link", board.calls)
        self.assertEqual(res["build_id"], 77)

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


class RunPcapTest(unittest.TestCase):
    def test_run_pcap_alternates_request_and_reply_in_time_order(self):
        board = FakeBoard()
        with tempfile.TemporaryDirectory() as out:
            run_board.run_board(run_board.read_manifest(MANIFEST), "quick", out, mac="x", stp="stp",
                                board=board, make_link=board.make_link, frames=100,
                                only="cyclic_k3_s1")
            packets = read_pcap(os.path.join(out, "cyclic_k3_s1.pcap"))
        times = [t for t, _ in packets]
        self.assertEqual(times, sorted(times))
        self.assertGreater(times[0], 0)
        self.assertEqual([f for _, f in packets][0::2], board.log["sent"])


class WatchBoardTest(unittest.TestCase):
    def _events(self, out, want_done=True):
        path = os.path.join(out, "events.jsonl")
        for _ in range(200):
            time.sleep(0.05)
            if os.path.exists(path):
                with open(path) as f:
                    evs = [json.loads(line) for line in f if line.strip()]
                if not want_done or any(e["type"] == "done" for e in evs):
                    return evs
        self.fail("the watch run never finished")

    def test_watch_board_resets_first_and_labels_the_run(self):
        board = FakeBoard()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as out:     # the run keeps its log open
            threading.Thread(target=run_board.watch_board, daemon=True,
                             args=(out, "x", "stp", "cyclic", 3, 1, 60, 0),
                             kwargs=dict(board=board, make_link=board.make_link, speed=1024.0)).start()
            evs = self._events(out)
        self.assertEqual(board.calls[:3], ["reset", "read", "link"])
        cfg = next(e for e in evs if e["type"] == "config")
        self.assertTrue(cfg["board"])
        self.assertEqual((cfg["rx_ppm"], cfg["tx_ppm"], cfg["drift"]), (0.0, 0.0, 0.0))
        done = next(e for e in evs if e["type"] == "done")
        self.assertEqual(done["mismatches"], 0)
        self.assertGreater(done["replies"], 0)
        packets = [e for e in evs if e["type"] == "packet"]
        self.assertTrue(packets and all(p["match"] for p in packets))

    def test_watch_board_refuses_a_board_without_link(self):
        board = FakeBoard(before=dict(LINK_UP, state="configuring"))
        with tempfile.TemporaryDirectory() as out:
            with self.assertRaises(board_status.BoardError):
                run_board.watch_board(out, "x", "stp", "cyclic", 3, 1, 60, 0, board=board,
                                      make_link=board.make_link)
        self.assertNotIn("link", board.calls)


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
