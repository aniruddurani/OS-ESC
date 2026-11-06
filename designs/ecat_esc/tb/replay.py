"""Capture replay: compare what the core returns with what a recorded slave returned.

A capture taken on a master's NIC holds each request followed by its response. For
a bus with only the slave under test, the response is that slave's output frame.
Because an ESC of a different make reports different identity registers, replay
returns the differences datagram by datagram and a test accepts only those listed
in an allowlist next to the capture (<name>.allow.json), which starts empty.
"""
import glob
import os
import struct

import model_path  # noqa: F401
from ecat import FrameError, parse_frame

# Replayed by test_replay_real_captures: losing one must fail, not quietly replay fewer.
REQUIRED_CAPTURES = ("board_cyclic_k3_s1.pcap", "board_soem_start.pcap")


def capture_files(folder):
    """Every .pcap in `folder`, sorted, which must include REQUIRED_CAPTURES."""
    files = sorted(glob.glob(os.path.join(folder, "*.pcap")))
    missing = sorted(set(REQUIRED_CAPTURES) - {os.path.basename(p) for p in files})
    assert not missing, f"captures missing: {missing}"
    return files


def pair_requests_and_responses(packets):
    """[(request, response)]: consecutive frames, the first from the master (even
    index), the second the answer. Odd trailing frames are ignored."""
    frames = [f for _, f in packets]
    return [(frames[i], frames[i + 1]) for i in range(0, len(frames) - 1, 2)]


def datagram_differences(index, expected, got):
    """Differences between the recorded response `expected` and our output `got`,
    each as (frame index, datagram index or None, field, expected, got)."""
    out = []
    try:
        e, g = parse_frame(expected, check_fcs=False), parse_frame(got, check_fcs=False)
    except FrameError:
        return [(index, None, "frame", expected.hex(), got.hex())] if expected != got else []
    if len(e) != len(g):
        return [(index, None, "datagram count", len(e), len(g))]
    for k, (a, b) in enumerate(zip(e, g)):
        for field in ("cmd", "idx", "address", "data", "wkc"):
            if getattr(a, field) != getattr(b, field):
                out.append((index, k, field, getattr(a, field), getattr(b, field)))
    return out


def allowed(path):
    """Allowed differences from <capture>.allow.json (a list of [frame, datagram, field])."""
    import json
    import os
    allow = os.path.splitext(path)[0] + ".allow.json"
    if not os.path.exists(allow):
        return set()
    with open(allow) as f:
        return {tuple(x) for x in json.load(f)}


def unexpected(differences, allow):
    return [d for d in differences if (d[0], d[1], d[2]) not in allow]


def frame_summary(frame):
    """Short text for failure messages."""
    return f"{len(frame)} bytes, ethertype {struct.unpack_from('>H', frame, 12)[0]:#06x}"
