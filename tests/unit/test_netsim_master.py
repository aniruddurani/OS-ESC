"""netsim master and scenarios (master.py, scenarios.py)."""
import os
import struct
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "designs", "ecat_esc")
sys.path.insert(0, os.path.join(ROOT, "netsim"))
sys.path.insert(0, os.path.join(ROOT, "model"))

from ecat import CMD, MAX_FRAME, MIN_FRAME, NAME, Datagram, build_frame, parse_frame  # noqa: E402
from master import Master  # noqa: E402
from network import Chain  # noqa: E402
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

    def test_faults_cut_physical_writes_and_relayout_fmmus(self):
        # A truncated frame must also cut physical writes (the model's _execute_cut), and the
        # good traffic must re-program the DUT's FMMUs, not only run the fixed LRW window.
        txs = drain(Master("faults", seed=3, k=3, frames=4000))
        cut = {t.frame[16] for t in txs if t.fault == "truncated" and len(t.frame) > 16}
        self.assertTrue({CMD["APWR"], CMD["FPWR"], CMD["BWR"]} & cut, sorted(cut))
        good = commands([t for t in txs[40:] if t.fault is None])
        self.assertIn(CMD["FPWR"], {c for c, a in good if a >> 16 == 0x0600})

    def test_faults_and_clocks_send_minimum_gaps(self):
        # 16 nibbles is the shortest gap a receiver must take (a repeater may shrink 96 bit
        # times to 64); the RTL's own transmitter sends it when a frame is waiting.
        for s in ("faults", "clocks"):
            gaps = [t.ifg for t in drain(Master(s, seed=2, k=3, frames=1000))]
            self.assertIn(16, set(gaps), s)
            self.assertGreaterEqual(min(gaps), 16, s)

    def test_startup_has_the_soem_sequence(self):
        k = 2
        kinds = [(NAME.get(d.cmd), d.address & 0xFFFF, d.address >> 16, d.data)
                 for t in drain(Master("startup", seed=1, k=k, frames=60)) for d in parse_frame(t.frame)]
        self.assertIn(("BWR", 0, 0x0120, b"\x11\x00"), [(n, 0, a, x) for n, _, a, x in kinds])
        for p in range(k + 1):
            at = kinds.index(("FPWR", 0x1001 + p, 0x0502, b"\x00\x01\x08\x00\x00\x00"))
            self.assertEqual(kinds[at + 1][:3], ("FPRD", 0x1001 + p, 0x0508))
            self.assertIn(("FPWR", 0x1001 + p, 0x0120, b"\x02\x00"), kinds)
            self.assertIn(("FPRD", 0x1001 + p, 0x0130, bytes(2)), [(n, s, a, bytes(len(x))) for n, s, a, x in kinds])

    def test_random_reaches_al_and_sii(self):
        ados = [d.address >> 16 for t in drain(Master("random", seed=3, k=3, frames=4000))
                for d in parse_frame(t.frame)
                if d.cmd not in (CMD["NOP"], CMD["LRD"], CMD["LWR"], CMD["LRW"])]
        self.assertTrue(any(0x0120 <= a < 0x0150 for a in ados))
        self.assertTrue(any(0x0500 <= a < 0x0510 for a in ados))

    def test_random_has_no_rw_over_dependent_registers(self):
        # The RTL sees its own write inside a read-write datagram; the model reads all, then
        # writes. A datagram spanning a register and what depends on it means nothing to a
        # master, so traffic must not hold one.
        rw = (CMD["APRW"], CMD["FPRW"], CMD["BRW"])
        for t in drain(Master("random", seed=9, k=3, frames=20000)):
            for d in parse_frame(t.frame):
                if d.cmd in rw:
                    lo, hi = d.address >> 16, (d.address >> 16) + len(d.data)
                    self.assertFalse(lo < 0x0132 and hi > 0x0120, hex(lo))
                    self.assertFalse(lo < 0x050C and hi > 0x0502, hex(lo))

    def test_chain_brd_of_al_status_ors(self):
        chain = Chain(1, ram_bytes=256)
        chain.forward(build_frame([Datagram(CMD["APWR"], 1, 0x0120 << 16, b"\x02\x00"),
                                   Datagram(CMD["APWR"], 2, (0x0120 << 16) | 0xFFFF, b"\x04\x00")]))
        _, reply = chain.forward(build_frame([Datagram(CMD["BRD"], 3, 0x0130 << 16, bytes(2))]))
        self.assertEqual(parse_frame(reply)[0].data, b"\x06\x00")

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
                    self.assertGreaterEqual(t.ifg, 16)

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
