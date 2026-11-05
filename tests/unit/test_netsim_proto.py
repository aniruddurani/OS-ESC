"""The netsim pipe protocol (designs/ecat_esc/netsim/proto.py)."""
import io
import os
import random
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "designs", "ecat_esc", "netsim"))

import proto  # noqa: E402

SAMPLES = {
    "CONFIG": dict(seed=7, rx_ppm=-12.5, tx_ppm=99.0, drift_ppm_per_s=3.0, rx_phase_ns=1.5,
                   tx_phase_ns=20.0, core_phase_ns=4.0, core_mhz=75.0, event_level=2),
    "SEND": dict(ifg_nibbles=24, er_nibble=-1, flags=1, data=b"\x01\x02\x03"),
    "RUN": dict(replies=5, timeout_ps=10**12),
    "REPLY": dict(t_start_ps=123, t_end_ps=456789, data=bytes(range(64))),
    "STATUS": dict(t_ps=2**40, core_cycles=2**33, seen=0b1010, sticky=0b0101),
    "EVENT": dict(t_ps=99, block=proto.BLOCK["PARSER"], kind=proto.KIND["DG_HDR"],
                  a=5 | (4 << 8), b=0x10040000),
    "QUIT": dict(),
    "DUMP": dict(),
    "SENT": dict(index=3, t_start_ps=1000, t_end_ps=2000),
}


def decode(raw):
    return proto.read_message(io.BytesIO(raw))


class ProtoTest(unittest.TestCase):
    def test_roundtrip_every_type(self):
        self.assertEqual(set(SAMPLES), set(proto.TYPES))
        for kind, fields in SAMPLES.items():
            self.assertEqual(decode(proto.encode(kind, **fields)), (kind, fields), kind)

    def test_send_bytes_preserved(self):
        data = bytes(random.Random(1).randrange(256) for _ in range(1518))
        for er in (-1, 37):
            kind, f = decode(proto.encode("SEND", ifg_nibbles=30, er_nibble=er, flags=0, data=data))
            self.assertEqual((kind, f["data"], f["er_nibble"], f["ifg_nibbles"]), ("SEND", data, er, 30))

    def test_unknown_type_raises(self):
        with self.assertRaises(proto.ProtocolError):
            decode(struct.pack("<IB", 1, 99))

    def test_length_too_short_raises(self):
        with self.assertRaises(proto.ProtocolError):
            decode(struct.pack("<IB", 4, proto.TYPES["STATUS"]) + b"\x00\x00\x00")

    def test_truncated_stream_raises(self):
        with self.assertRaises(proto.ProtocolError):
            decode(struct.pack("<I", 20) + b"\x05" + b"\x00" * 4)

    def test_clean_eof(self):
        self.assertEqual(decode(b""), ("EOF", {}))


if __name__ == "__main__":
    unittest.main()
