"""netsim master and scenarios (master.py, scenarios.py)."""
import os
import struct
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "designs", "ecat_esc")
sys.path.insert(0, os.path.join(ROOT, "netsim"))
sys.path.insert(0, os.path.join(ROOT, "model"))

from ecat import CMD, MAX_FRAME, MIN_FRAME, NAME, parse_frame  # noqa: E402
from master import Master  # noqa: E402
from scenarios import FAULTS, SCENARIOS, clock_config  # noqa: E402


def drain(m, batch=97):
    out = []
    while True:
        b = m.next_batch(batch)
        if not b:
            return out
        out += b


def commands(txs):
    return [(d.cmd, d.address) for t in txs for d in parse_frame(t.frame)]


class MasterTest(unittest.TestCase):
    def test_same_seed_same_frames(self):
        for s in ("startup", "cyclic", "random", "faults", "clocks", "chain"):
            a = drain(Master(s, seed=11, k=3, frames=2000))
            b = drain(Master(s, seed=11, k=3, frames=2000))
            self.assertEqual([(t.frame, t.ifg, t.er_nibble) for t in a],
                             [(t.frame, t.ifg, t.er_nibble) for t in b], s)
            c = drain(Master(s, seed=12, k=3, frames=2000))
            self.assertNotEqual([t.frame for t in a], [t.frame for t in c], s)

    def test_frame_count(self):
        txs = drain(Master("cyclic", seed=1, k=3, frames=500))
        self.assertEqual(len(txs), 500 + 1)                # plus the final diag read

    def test_startup_sequence(self):
        k = 3
        cmds = commands(drain(Master("startup", seed=1, k=k, frames=100))[:20])
        names = [NAME[c] for c, _ in cmds]
        self.assertEqual(names[0], "BRD")
        self.assertEqual(names[1:2 + k], ["APWR"] * (k + 1))
        self.assertEqual([a >> 16 for _, a in cmds[1:2 + k]], [0x0010] * (k + 1))
        rest = cmds[2 + k:]
        self.assertEqual(NAME[rest[0][0]], "FPRD")
        kinds = [(NAME[c], a >> 16) for c, a in rest[1:]]
        i6 = kinds.index(("FPWR", 0x0600))
        i8 = kinds.index(("FPWR", 0x0800))
        ib = kinds.index(("BWR", 0x1000))
        self.assertLess(i6, i8)
        self.assertLess(i8, ib)

    def test_faults_cover_every_class(self):
        m = Master("faults", seed=3, k=1, frames=1000)
        drain(m)
        for cls in FAULTS:
            self.assertGreaterEqual(m.injected.get(cls, 0), 1, cls)

    def test_fault_frames_have_their_fault(self):
        for t in drain(Master("faults", seed=9, k=1, frames=2000)):
            if t.fault == "oversize":
                self.assertGreater(len(t.frame), MAX_FRAME)
            elif t.fault == "runt":
                self.assertLess(len(t.frame), MIN_FRAME)

    def test_fault_followed_by_good_frame(self):
        txs = drain(Master("faults", seed=4, k=1, frames=1000))
        for a, b in zip(txs, txs[1:]):
            if a.fault:
                self.assertIsNone(b.fault)

    def test_random_uses_unsupported_commands(self):
        cmds = {c for c, _ in commands(drain(Master("random", seed=5, k=3, frames=2000)))}
        self.assertTrue({13, 14} <= cmds)
        self.assertTrue({CMD[n] for n in CMD} <= cmds)

    def test_random_avoids_diag_window(self):
        # The model has no diag block; the final read checks it against the model's counters.
        txs = drain(Master("random", seed=8, k=3, frames=3000))[:-1]
        for t in txs:
            for d in parse_frame(t.frame):
                if d.cmd not in (CMD["NOP"], CMD["LRD"], CMD["LWR"], CMD["LRW"]):    # physical
                    ado = d.address >> 16
                    self.assertFalse(ado < 0x0F00 and ado + len(d.data) > 0x0E00, hex(ado))
                if (NAME.get(d.cmd) in ("FPWR", "APWR", "BWR", "FPRW", "APRW", "BRW")
                        and d.address >> 16 == 0x0600):
                    for i in range(0, len(d.data) - 15, 16):
                        length, phys = struct.unpack_from("<H", d.data, i + 4)[0], \
                            struct.unpack_from("<H", d.data, i + 8)[0]
                        self.assertFalse(phys < 0x0F00 and phys + length > 0x0E00, hex(phys))

    def test_frames_within_limits(self):
        for s in ("startup", "cyclic", "random", "faults", "clocks", "chain"):
            for t in drain(Master(s, seed=6, k=3, frames=1000)):
                if t.fault is None:
                    self.assertTrue(MIN_FRAME <= len(t.frame) <= MAX_FRAME, s)
                    parse_frame(t.frame)
                    self.assertGreaterEqual(t.ifg, 24)

    def test_clock_config_bounds(self):
        for seed in range(100):
            c = clock_config("clocks", seed)
            self.assertLessEqual(max(abs(c["rx_ppm"]), abs(c["tx_ppm"])), 100)
        worst = [clock_config("clocks", s) for s in range(4)]
        self.assertIn((-100.0, 100.0), [(c["rx_ppm"], c["tx_ppm"]) for c in worst])   # RX fast, TX slow
        self.assertEqual(clock_config("cyclic", 1)["drift_ppm_per_s"], 0.0)

    def test_replay_without_captures_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(Master("replay", seed=1, k=0, frames=100, captures_dir=d).next_batch(10), [])

    def test_every_scenario_ends_with_diag_read(self):
        for s in ("startup", "cyclic", "random", "faults", "clocks", "chain"):
            k = 3
            last = parse_frame(drain(Master(s, seed=7, k=k, frames=300))[-1].frame)
            self.assertEqual(len(last), 1, s)
            d = last[0]
            self.assertEqual((NAME[d.cmd], d.address >> 16, d.address & 0xFFFF, len(d.data)),
                             ("APRD", 0x0E00, (-k) & 0xFFFF, 24), s)

    def test_scenarios_listed(self):
        self.assertEqual(SCENARIOS, ("startup", "cyclic", "random", "faults", "clocks", "chain", "replay"))


if __name__ == "__main__":
    unittest.main()
