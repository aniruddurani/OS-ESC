"""Capture replay machinery: pcap reading and the response comparison, and the replay of the
recordings in captures/ (test_replay_real_captures)."""
import os
import random

import cocotb

import model_path  # noqa: F401

from ecat import CMD, Datagram, SlaveModel, build_frame
from pcap import read_pcap, write_pcap
from replay import allowed, capture_files, datagram_differences, pair_requests_and_responses, unexpected
from top_util import RAM, send_and_collect, start

CAPTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "captures")


def adp_ado(adp, ado):
    return (ado << 16) | adp


async def replay(dut, mon, packets):
    diffs = []
    for i, (request, response) in enumerate(pair_requests_and_responses(packets)):
        got = await send_and_collect(dut, mon, request)
        diffs += datagram_differences(i, response, got)
    return diffs


def synthetic_session(rng):
    """Requests plus the responses a perfect slave would give (the model plays the
    recorded slave), as (timestamp, frame) packets."""
    slave, packets, t = SlaveModel(ram_bytes=RAM), [], 0.0
    for k in range(10):
        name = rng.choice(["APRD", "APWR", "FPRD", "BRD", "BWR"])
        adp = 0 if not name.startswith("FP") else slave.station_address
        request = build_frame([Datagram(CMD[name], k, adp_ado(adp, rng.choice([0x0004, 0x0010, 0x1000])),
                                        bytes(rng.randrange(256) for _ in range(2)))])
        packets += [(t, request), (t + 0.0001, slave.process(request))]
        t += 0.001
    return packets


@cocotb.test()
async def test_pcap_reader_roundtrip(dut):
    path = os.path.join(os.environ.get("TMPDIR", "/tmp"), "roundtrip.pcap")
    packets = [(1.5, b"\x01" * 64), (2.000123, b"\x02" * 70)]
    write_pcap(path, packets)
    got = read_pcap(path)
    assert [f for _, f in got] == [f for _, f in packets]
    assert all(abs(a[0] - b[0]) < 1e-6 for a, b in zip(got, packets))
    with open(path, "wb") as f:
        f.write(b"\x0a\x0d\x0d\x0a" + bytes(40))                       # a pcapng header
    try:
        read_pcap(path)
        raise AssertionError("pcapng must be rejected")
    except ValueError:
        pass


@cocotb.test()
async def test_replay_harness_on_synthetic_capture(dut):
    mon, model, rng = await start(dut, 61)
    packets = synthetic_session(random.Random(5))
    diffs = await replay(dut, mon, packets)
    assert unexpected(diffs, set()) == [], f"core disagrees with the model-as-slave: {diffs[:3]}"


@cocotb.test()
async def test_replay_harness_reports_a_tampered_response(dut):
    mon, model, rng = await start(dut, 62)
    packets = synthetic_session(random.Random(6))
    ts, resp = packets[3]
    packets[3] = (ts, resp[:16 + 10] + bytes([resp[16 + 10] ^ 0x01]) + resp[16 + 11:])   # flip a data bit
    diffs = await replay(dut, mon, packets)
    assert [(d[0], d[1], d[2]) for d in diffs] == [(1, 0, "data")]


@cocotb.test()
async def test_replay_real_captures(dut):
    files = capture_files(CAPTURES)
    mon, model, rng = await start(dut, 63)
    for path in files:
        packets = read_pcap(path)
        assert packets, f"{os.path.basename(path)} holds no frames"
        diffs = await replay(dut, mon, packets)
        bad = unexpected(diffs, allowed(path))
        assert not bad, f"{os.path.basename(path)}: {bad[:5]}"
