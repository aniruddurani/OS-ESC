"""wire_link.WireLink against a fake capture layer: what it sends, what it counts as the
board's reply, and what it ignores. No NIC or Npcap needed."""
import os
import sys
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, ".."), os.path.join(HERE, "..", "..", "model")]

from ecat import CMD, Datagram, build_frame  # noqa: E402
from link import LinkError  # noqa: E402
from wire_link import WireLink  # noqa: E402


def frame(adp=0, data=b"\x00\x00"):
    return build_frame([Datagram(CMD["BRD"], 1, adp, data)])          # with FCS


def reply_of(f, wkc=1):
    """What the board sends back for `f`, as the NIC delivers it (no FCS)."""
    body = bytearray(f[:-4])
    body[-2:] = wkc.to_bytes(2, "little")                              # padding ends the frame
    return bytes(body)


class FakePcap:
    """Scripted backend: `script(sent)` returns the frames to deliver after each send
    (None for a timeout). Records sends."""
    def __init__(self, script, direction=True):
        self.script, self.direction = script, direction
        self.sent, self.queue, self.events = [], [], []

    def open(self, mac):
        self.mac = mac

    def set_direction_in(self):
        return self.direction

    def send(self, raw):
        self.events.append(("send", raw))
        self.sent.append(raw)
        self.queue = list(self.script(raw))

    def recv(self, timeout_s):
        if not self.queue:
            self.events.append(("timeout", None))
            return None
        f = self.queue.pop(0)
        self.events.append(("recv", f))
        return f

    def close(self):
        pass


def link_with(script, direction=True):
    pcap = FakePcap(script, direction)
    return WireLink("00:11:22:33:44:55", pcap=pcap, timeout_s=0.01), pcap


class WireLinkTest(unittest.TestCase):
    def test_fcs_stripped_on_send_and_restored_on_reply(self):
        f = frame()
        link, pcap = link_with(lambda raw: [reply_of(raw + zlib.crc32(raw).to_bytes(4, "little"))])
        link.send(f)
        sent, replies, _, _ = link.run(1, 0)
        self.assertEqual(pcap.sent, [f[:-4]])
        r = replies[0]["data"]
        self.assertEqual(r[:-4], reply_of(f))
        self.assertEqual(r[-4:], zlib.crc32(r[:-4]).to_bytes(4, "little"))

    def test_one_frame_in_flight(self):
        link, pcap = link_with(lambda raw: [reply_of(raw + bytes(4))])
        for adp in range(3):
            link.send(frame(adp))
        link.run(3, 0)
        kinds = [k for k, _ in pcap.events]
        self.assertEqual(kinds, ["send", "recv"] * 3)

    def test_own_copy_is_not_a_reply(self):
        f = frame()
        link, _ = link_with(lambda raw: [raw, reply_of(f)], direction=False)
        link.send(f)
        _, replies, _, _ = link.run(1, 0)
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["data"][:-4], reply_of(f))

    def test_own_copy_kept_when_direction_filter_works(self):
        f = frame()
        link, _ = link_with(lambda raw: [raw], direction=True)   # the board changed nothing
        link.send(f)
        _, replies, _, _ = link.run(1, 0)
        self.assertEqual([r["data"] for r in replies], [f])

    def test_timeout_gives_no_reply(self):
        link, _ = link_with(lambda raw: [])
        link.send(frame())
        sent, replies, _, status = link.run(1, 0)
        self.assertEqual(replies, [])
        self.assertEqual([s["index"] for s in sent], [0])
        self.assertEqual(status["seen"], 0)

    def test_non_ethercat_frames_are_ignored(self):
        f = frame()
        ipv6 = b"\x33\x33\x00\x00\x00\x02" + b"\x02" * 6 + b"\x86\xdd" + bytes(50)
        link, _ = link_with(lambda raw: [ipv6, reply_of(f)])
        link.send(f)
        _, replies, _, _ = link.run(1, 0)
        self.assertEqual([r["data"][:-4] for r in replies], [reply_of(f)])

    def test_rx_er_rejected(self):
        link, _ = link_with(lambda raw: [])
        with self.assertRaises(LinkError):
            link.send(frame(), er_nibble=3)

    def test_sent_and_reply_times_ordered(self):
        link, _ = link_with(lambda raw: [reply_of(raw + bytes(4))])
        for adp in range(4):
            link.send(frame(adp))
        sent, replies, _, status = link.run(4, 0)
        times = []
        for s, r in zip(sent, replies):
            times += [s["t_start_ps"], r["t_start_ps"]]
        self.assertEqual(times, sorted(times))
        self.assertEqual([s["index"] for s in sent], [0, 1, 2, 3])
        self.assertGreaterEqual(status["t_ps"], times[-1])


if __name__ == "__main__":
    unittest.main()
