"""Probe events from the RTL's internal signals (probes.vlt, tb_netsim.cpp).
Needs the built bench: run with `make dnettest`."""
import os
import struct
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "model"))

from ecat import CMD, Datagram, build_frame  # noqa: E402
from link import DEFAULT_CONFIG, Link  # noqa: E402
from proto import BLOCK, KIND  # noqa: E402

BIN = os.environ.get("NETSIM_BIN", "build/ecat_esc/netsim/obj/Vesc_top")


def fpwr(ado, data):
    return build_frame([Datagram(CMD["FPWR"], 1, ado << 16, data)])     # station address 0


def of(events, block, kind):
    return [e for e in events if e["block"] == BLOCK[block] and e["kind"] == KIND[kind]]


class EventTest(unittest.TestCase):
    def link(self, level):
        self.l = Link(BIN, dict(DEFAULT_CONFIG, event_level=level))
        return self.l

    def tearDown(self):
        self.l.close()

    def run_one(self, frame, level=2, record=False):
        l = self.link(level)
        l.send(frame, record=record)
        return l.run(1, 10**9)[2]

    def test_dg_hdr_event(self):
        ev = of(self.run_one(fpwr(0x1004, b"\x01\x02\x03\x04")), "PARSER", "DG_HDR")
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]["a"] & 0xFF, ev[0]["a"] >> 8, ev[0]["b"]), (5, 4, 0x10040000))

    def test_mem_write_events(self):
        events = self.run_one(fpwr(0x1004, b"\x01\x02\x03\x04"))
        hdr = of(events, "PARSER", "DG_HDR")[0]
        wr = of(events, "MEM", "MEM_WR")
        self.assertEqual([e["a"] for e in wr], [0x1004, 0x1005, 0x1006, 0x1007])
        self.assertTrue(all(e["t_ps"] > hdr["t_ps"] for e in wr))

    def test_fmmu_hit_event(self):
        l = self.link(2)
        cfg = struct.pack("<IHBBHBBB3x", 0x10000, 8, 0, 7, 0x1000, 0, 3, 1)
        l.send(fpwr(0x0600, cfg))
        l.send(build_frame([Datagram(CMD["LWR"], 2, 0x10000, b"\xaa\xbb\xcc\xdd")]))
        events = l.run(2, 10**9)[2]
        hits = of(events, "FMMU", "FMMU_HIT")
        self.assertTrue(any(e["a"] & 1 or e["b"] & 1 for e in hits), hits)

    def test_event_order(self):
        events = self.run_one(fpwr(0x1004, b"\x01\x02\x03\x04"))
        t = lambda b, k: of(events, b, k)[0]["t_ps"]                           # noqa: E731
        self.assertLess(t("MII_RX", "FRAME_START"), t("PARSER", "DG_HDR"))
        self.assertLess(t("PARSER", "DG_HDR"), t("MEM", "MEM_WR"))
        self.assertLess(t("MEM", "MEM_WR"), t("MII_TX", "FRAME_END"))
        self.assertLess(t("RX_FIFO", "FRAME_START"), t("TX_FIFO", "FRAME_START"))

    def test_level_none_emits_nothing(self):
        self.assertEqual(self.run_one(fpwr(0x1004, b"\x01"), level=0), [])

    def test_recorded_frame_emits_at_level_one(self):
        self.assertTrue(of(self.run_one(fpwr(0x1004, b"\x01"), level=1, record=True), "PARSER", "DG_HDR"))
        self.l.close()
        self.assertEqual(self.run_one(fpwr(0x1004, b"\x01"), level=1, record=False), [])

    def test_dump_flushes_ring(self):
        l = self.link(1)
        for i in range(70):
            l.send(fpwr(0x1000 + i, bytes([i])))
        self.assertEqual(l.run(70, 10**10)[2], [])
        events, status = l.dump()
        self.assertEqual(len(of(events, "MII_TX", "FRAME_START")), 64)
        self.assertEqual(len(of(events, "PARSER", "DG_HDR")), 64)


if __name__ == "__main__":
    unittest.main()
