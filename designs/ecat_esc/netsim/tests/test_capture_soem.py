"""capture_soem: what it stores from the NIC, and when it refuses a capture."""
import os
import sys
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, ".."), os.path.join(HERE, "..", "..", "model"),
                os.path.join(HERE, "..", "..", "tb"), os.path.join(HERE, "..", "..", "board")]

from capture_soem import normalize, pairs_ok  # noqa: E402
from ecat import CMD, Datagram, build_frame  # noqa: E402


def body(idx, data=b"\x00\x00"):
    return build_frame([Datagram(CMD["BRD"], idx, 0, data)], with_fcs=False)


class CaptureTest(unittest.TestCase):
    def test_capture_pads_short_requests_and_adds_fcs(self):
        short = body(1)[:30]
        out = normalize(short)
        self.assertEqual(len(out), 64)
        self.assertEqual(out[:30], short)
        self.assertEqual(out[30:60], bytes(30))
        self.assertEqual(out[60:], zlib.crc32(out[:60]).to_bytes(4, "little"))

    def test_long_frames_keep_their_length(self):
        long = body(1, bytes(100))
        out = normalize(long)
        self.assertEqual(len(out), len(long) + 4)
        self.assertEqual(out[:-4], long)

    def test_pairs_ok_on_alternating_frames(self):
        packets = [(i, normalize(body(i // 2))) for i in range(6)]
        self.assertTrue(pairs_ok(packets))

    def test_pairs_not_ok_on_a_missing_reply(self):
        packets = [(0, normalize(body(1))), (1, normalize(body(2))), (2, normalize(body(2))),
                   (3, normalize(body(3)))]
        self.assertFalse(pairs_ok(packets))

    def test_pairs_not_ok_on_an_odd_count_or_nothing(self):
        self.assertFalse(pairs_ok([(0, normalize(body(1)))]))
        self.assertFalse(pairs_ok([]))


if __name__ == "__main__":
    unittest.main()
