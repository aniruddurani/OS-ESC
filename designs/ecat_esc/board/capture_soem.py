"""Records a master's real traffic from the NIC cabled to the board, for the replay test.

    python capture_soem.py --mac MAC --out PCAP -- <command that talks to the board>

Npcap's inbound filter is unavailable on this NIC, so the capture sees each frame the PC sends
and then the board's reply. A frame the PC sends may be shorter than 60 bytes: the NIC pads it
on the wire, and the capture stores what the ESC received. The capture is refused unless its
frames pair up, request then reply, as the replay reads them."""
import argparse
import os
import struct
import subprocess
import sys
import threading
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "netsim"), os.path.join(HERE, "..", "model"),
                os.path.join(HERE, "..", "tb")]

from ecat import FrameError, parse_frame  # noqa: E402
from pcap import write_pcap  # noqa: E402
from replay import pair_requests_and_responses  # noqa: E402
from wire_link import NpcapBackend  # noqa: E402

MIN_BODY = 60


def normalize(frame):
    """The frame as it is on the wire: padded to 60 bytes, with its FCS."""
    body = frame.ljust(MIN_BODY, b"\x00")
    return body + zlib.crc32(body).to_bytes(4, "little")


def pairs_ok(packets):
    """True if the frames alternate request and reply, each pair holding one datagram index."""
    if not packets or len(packets) % 2:
        return False
    try:
        return all(parse_frame(a, check_fcs=False)[0].idx == parse_frame(b, check_fcs=False)[0].idx
                   for a, b in pair_requests_and_responses(packets))
    except (FrameError, IndexError, struct.error):
        return False


def capture(mac, command):
    """[(time, frame)] seen while `command` runs, and its exit code."""
    pcap = NpcapBackend()
    pcap.open(mac)
    packets, stop = [], threading.Event()

    def listen():
        while not stop.is_set():
            f = pcap.recv(0.05)
            if f is not None:
                packets.append((time.time(), normalize(f)))

    thread = threading.Thread(target=listen)
    thread.start()
    try:
        code = subprocess.run(command).returncode
    finally:
        time.sleep(0.2)
        stop.set()
        thread.join()
        pcap.close()
    return packets, code


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--mac", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("command", nargs=argparse.REMAINDER)
    a = p.parse_args(argv)
    command = a.command[1:] if a.command[:1] == ["--"] else a.command
    packets, code = capture(a.mac, command)
    if code != 0:
        print(f"the command exited {code}: nothing recorded")
        return 1
    if not pairs_ok(packets):
        print(f"{len(packets)} frames do not pair up as request and reply: nothing recorded")
        return 1
    write_pcap(a.out, packets)
    print(f"{len(packets) // 2} request and reply pairs in {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
