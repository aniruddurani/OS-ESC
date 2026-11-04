"""The Verilator bench (tb_netsim.cpp) through the link, against SlaveModel.
Needs the built bench: run with `make dnettest` (sets NETSIM_BIN)."""
import os
import select
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "model"))

from ecat import CMD, Datagram, SlaveModel, build_frame  # noqa: E402
from link import DEFAULT_CONFIG, Link, LinkError  # noqa: E402

BIN = os.environ.get("NETSIM_BIN", "build/ecat_esc/netsim/obj/Vesc_top")
NIBBLE_PS = 40_000
RAM = 256


def wire_ps(nbytes):
    """Time on the MII for a frame of `nbytes` (FCS included) with preamble and SFD."""
    return (16 + 2 * nbytes) * NIBBLE_PS


def aprd_frame(idx=0):
    return build_frame([Datagram(CMD["APRD"], idx, 0x0000, bytes(2))])


def max_frame(i):
    """1518 bytes on the wire: a BRD of the type register and a 1472-byte NOP."""
    return build_frame([Datagram(CMD["BRD"], i & 0xFF, 0x0000, bytes(2)),
                        Datagram(CMD["NOP"], 0, 0, bytes(1472))])


class BenchTest(unittest.TestCase):
    def setUp(self):
        self.link = Link(BIN, dict(DEFAULT_CONFIG))

    def tearDown(self):
        self.link.close()

    def test_one_frame_matches_model(self):
        frame = aprd_frame()
        self.link.send(frame)
        _, replies, _, _ = self.link.run(1, 10**9)
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["data"].hex(), SlaveModel(ram_bytes=RAM).process(frame).hex())

    def test_sent_reported(self):
        frames = [aprd_frame(i) for i in range(3)]
        for f in frames:
            self.link.send(f)
        sent, _, _, _ = self.link.run(3, 10**9)
        self.assertEqual([s["index"] for s in sent], [0, 1, 2])
        for s, f in zip(sent, frames):
            self.assertAlmostEqual(s["t_end_ps"] - s["t_start_ps"], wire_ps(len(f)), delta=NIBBLE_PS)

    def test_reply_timing(self):
        self.link.send(aprd_frame())
        sent, replies, _, _ = self.link.run(1, 10**9)
        r = replies[0]
        self.assertGreater(r["t_start_ps"], sent[0]["t_start_ps"])
        self.assertAlmostEqual(r["t_end_ps"] - r["t_start_ps"], wire_ps(len(r["data"])), delta=NIBBLE_PS)

    def test_back_to_back(self):
        model = SlaveModel(ram_bytes=RAM)
        frames = [max_frame(i) for i in range(50)]
        self.assertEqual({len(f) for f in frames}, {1518})
        for f in frames:
            self.link.send(f, ifg=24)
        _, replies, _, status = self.link.run(50, 10**10)
        self.assertEqual(len(replies), 50)
        for i, (f, r) in enumerate(zip(frames, replies)):
            self.assertEqual(r["data"], model.process(f), f"frame {i}")
        self.assertEqual(status["sticky"], 0)        # flags clear at nominal clocks

    def test_back_to_back_at_ppm_limits(self):
        # LAN9253 datasheet 11.16.17 note 6: an ESC forwards 1518-byte frames with +-100 ppm
        # clocks. RX 100 ppm fast and TX 100 ppm slow (and the reverse), minimum gap.
        for rx_ppm, tx_ppm in ((-100.0, 100.0), (100.0, -100.0)):
            with self.subTest(rx_ppm=rx_ppm, tx_ppm=tx_ppm):
                link = Link(BIN, dict(DEFAULT_CONFIG, rx_ppm=rx_ppm, tx_ppm=tx_ppm))
                try:
                    model = SlaveModel(ram_bytes=RAM)
                    frames = [max_frame(i) for i in range(80)]
                    for f in frames:
                        link.send(f, ifg=24)
                    _, replies, _, status = link.run(80, 10**10)
                    self.assertEqual((status["seen"], status["sticky"]), (0, 0))
                    self.assertEqual([r["data"] for r in replies], [model.process(f) for f in frames])
                finally:
                    link.close()

    def test_phy_clock_offsets_below_50_ppm(self):
        # A half period of 20,000 ps rounded to whole picoseconds moves in 50 ppm steps; the
        # clock must keep the offset it was given (here 20 ppm) to well under that.
        f = max_frame(0)

        def durations(ppm):
            link = Link(BIN, dict(DEFAULT_CONFIG, rx_ppm=ppm, tx_ppm=ppm))
            try:
                link.send(f, ifg=24)
                sent, replies, _, _ = link.run(1, 10**9)
                return (sent[0]["t_end_ps"] - sent[0]["t_start_ps"],
                        replies[0]["t_end_ps"] - replies[0]["t_start_ps"])
            finally:
                link.close()

        base, off = durations(0.0), durations(20.0)
        want = wire_ps(len(f)) * 20e-6          # about 2.4 ns longer over the frame
        for b, o, name in zip(base, off, ("rx", "tx")):
            self.assertAlmostEqual(o - b, want, delta=want * 0.1, msg=name)

    def test_run_ends_with_one_status(self):
        sent, replies, events, status = self.link.run(0, 1_000_000)
        self.assertEqual((sent, replies, events), ([], [], []))
        self.assertGreaterEqual(status["t_ps"], 1_000_000)
        ready, _, _ = select.select([self.link.proc.stdout], [], [], 0.3)
        self.assertEqual(ready, [], "the bench wrote after its STATUS")
        self.link.send(aprd_frame())                # and the stream is still in step
        self.assertEqual(len(self.link.run(1, 10**9)[1]), 1)

    def test_flags_clear_at_nominal(self):
        for i in range(10):
            self.link.send(max_frame(i))
        _, replies, _, status = self.link.run(10, 10**10)
        self.assertEqual((len(replies), status["seen"], status["sticky"]), (10, 0, 0))

    def test_rx_er_poisons_fcs(self):
        frame = aprd_frame()
        self.link.send(frame, er_nibble=40)
        _, replies, _, _ = self.link.run(1, 10**9)
        expected = SlaveModel(ram_bytes=RAM).process(frame, rx_err=True)
        self.assertNotEqual(expected, SlaveModel(ram_bytes=RAM).process(frame))
        self.assertEqual(replies[0]["data"].hex(), expected.hex())

    def test_dead_process_raises(self):
        self.link.proc.kill()
        self.link.proc.wait()
        with self.assertRaises(LinkError):
            self.link.send(aprd_frame())
            self.link.run(1, 10**6)


if __name__ == "__main__":
    unittest.main()
