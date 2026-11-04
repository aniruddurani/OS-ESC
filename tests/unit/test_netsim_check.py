"""netsim chain of model slaves (network.py) and the reply checker (check.py)."""
import copy
import os
import struct
import sys
import unittest
import zlib

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "designs", "ecat_esc")
sys.path.insert(0, os.path.join(ROOT, "netsim"))
sys.path.insert(0, os.path.join(ROOT, "model"))

from ecat import CMD, Datagram, SlaveModel, build_frame, parse_frame  # noqa: E402
from check import Checker, diag_reply, fault_evidence, saturate  # noqa: E402
from network import Chain  # noqa: E402

US = 1_000_000          # 1 us in ps


def frame(idx=0):
    return build_frame([Datagram(CMD["BRD"], idx, 0, bytes(2))])


def run(checker, frames, replies):
    """frames: list of (expected bytes or None); frame i on the wire from 10*i us for 6 us.
    replies: list of (frame index it answers, bytes), each starting 1 us after its frame."""
    for i, exp in enumerate(frames):
        checker.expect(i, b"in", exp)
        checker.on_sent({"index": i, "t_start_ps": 10 * i * US, "t_end_ps": (10 * i + 6) * US})
    for i, data in replies:
        checker.on_reply({"t_start_ps": (10 * i + 1) * US, "t_end_ps": (10 * i + 7) * US, "data": data})
    checker.close(10**12)
    return checker.metrics


class ChainTest(unittest.TestCase):
    def test_chain_wkc_accumulates(self):
        _, expected = Chain(7).forward(frame())
        self.assertEqual(parse_frame(expected)[0].wkc, 8)

    def test_chain_auto_increment_position(self):
        f = build_frame([Datagram(CMD["APRD"], 0, (-7) & 0xFFFF, bytes(2))])
        dut_input, expected = Chain(7).forward(f)
        self.assertEqual(parse_frame(dut_input)[0].address & 0xFFFF, 0)        # seven increments
        self.assertEqual(parse_frame(expected)[0].wkc, 1)                       # only the DUT answered

    def test_dropped_by_a_model_slave(self):
        self.assertEqual(Chain(2).forward(b"\x01\x02\x03"), (None, None))


class CheckerTest(unittest.TestCase):
    def test_match(self):
        m = run(Checker(), [b"a", b"b"], [(0, b"a"), (1, b"b")])
        self.assertEqual((m["sent"], m["replies"], m["mismatches"], m["missing"], m["unexpected"]),
                         (2, 2, 0, 0, 0))

    def test_mismatch_recorded(self):
        c = Checker()
        m = run(c, [b"\x01\x02"], [(0, b"\x01\x03")])
        self.assertEqual(m["mismatches"], 1)
        self.assertEqual((c.failures[0]["index"], c.failures[0]["kind"],
                          c.failures[0]["expected"], c.failures[0]["got"]),
                         (0, "mismatch", "0102", "0103"))

    def test_missing_reply_does_not_shift_later_replies(self):
        exp = [bytes([i]) for i in range(5)]
        m = run(Checker(), exp, [(i, exp[i]) for i in (0, 1, 3, 4)])
        self.assertEqual((m["missing"], m["mismatches"], m["unexpected"]), (1, 0, 0))

    def test_dropped_frame_expects_no_reply(self):
        self.assertEqual(run(Checker(), [None], [])["missing"], 0)
        self.assertEqual(run(Checker(), [None], [(0, b"x")])["unexpected"], 1)

    def test_reply_never_arrives_is_missing_at_close(self):
        self.assertEqual(run(Checker(), [b"a"], [])["missing"], 1)

    def test_flags_and_late_counted(self):
        c = Checker()
        c.on_status({"seen": 0b1000, "sticky": 0b1000})
        c.on_status({"seen": 0b0001, "sticky": 0b1001})
        self.assertEqual((c.metrics["late"], c.metrics["flags_seen"]), (1, 0b1001))

    def test_fault_evidence(self):
        errors = fault_evidence({"crc": 3}, {"crc_failures": 2})
        self.assertEqual(len(errors), 1)
        self.assertIn("crc", errors[0])
        self.assertEqual(fault_evidence({"crc": 3}, {"crc_failures": 3}), [])

    def test_fault_evidence_requires_every_class(self):
        errors = fault_evidence({"crc": 1}, {"crc_failures": 1}, require_all=True)
        self.assertTrue(any("runt" in e for e in errors))

    def test_fill_takes_bytes_from_reply(self):
        body = bytes(range(60))
        exp = body + zlib.crc32(body).to_bytes(4, "little")
        got_body = body[:30] + b"\xAA\xBB" + body[32:]
        got = got_body + zlib.crc32(got_body).to_bytes(4, "little")
        c = Checker()
        c.expect(0, b"in", exp, fill=(30, 32))
        c.on_sent({"index": 0, "t_start_ps": 0, "t_end_ps": 6 * US})
        self.assertEqual(c.on_reply({"t_start_ps": US, "t_end_ps": 7 * US, "data": got}), 0)
        self.assertEqual(c.metrics["mismatches"], 0)
        bad_body = got_body[:5] + b"\x00" + got_body[6:]
        c.expect(1, b"in", exp, fill=(30, 32))
        c.on_sent({"index": 1, "t_start_ps": 10 * US, "t_end_ps": 16 * US})
        c.on_reply({"t_start_ps": 11 * US, "t_end_ps": 17 * US,
                    "data": bad_body + zlib.crc32(bad_body).to_bytes(4, "little")})
        self.assertEqual(c.metrics["mismatches"], 1)

    def test_diag_reply_fills_counters(self):
        model = SlaveModel(ram_bytes=256)
        model.process(frame())
        model.counters.crc_failures = 70000                      # saturates
        f = build_frame([Datagram(CMD["APRD"], 0, 0x0E000000, bytes(24))])
        before, trace_len = copy.deepcopy(model.counters), len(model.trace)
        expected = model.process(f)
        exp, fill = diag_reply(expected, before, trace_len)
        d = parse_frame(exp)[0]
        vals = struct.unpack("<12H", d.data)
        self.assertEqual((vals[0], vals[2], vals[7], vals[11]), (1, 65535, 1, trace_len))
        self.assertEqual(fill, (26 + 20, 26 + 22))               # slot 10, the latency

    def test_saturated_counter_expected_value(self):
        self.assertEqual((saturate(70000), saturate(5), saturate(9, cnt_w=3)), (65535, 5, 7))


if __name__ == "__main__":
    unittest.main()
