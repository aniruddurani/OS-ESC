import random
import zlib

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

from mii_util import StreamMonitor, idle


async def start(dut):
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    dut.rst.value = 1
    dut.tx_valid.value = 0
    dut.tx_data.value = 0
    dut.tx_first.value = 0
    dut.tx_last.value = 0
    await idle(dut.clk, 3)
    dut.rst.value = 0
    await idle(dut.clk, 2)
    return StreamMonitor(dut, dut.clk, prefix="rx_")


async def send(dut, frame):
    """Present bytes with valid/first/last and advance only when ready is high."""
    for i, b in enumerate(frame):
        dut.tx_valid.value = 1
        dut.tx_data.value = b
        dut.tx_first.value = 1 if i == 0 else 0
        dut.tx_last.value = 1 if i == len(frame) - 1 else 0
        while True:
            await RisingEdge(dut.clk)
            if int(dut.tx_ready.value) == 1:
                break
    dut.tx_valid.value = 0
    dut.tx_first.value = 0
    dut.tx_last.value = 0


def frame_bytes(n, seed):
    return bytes(random.Random(seed).randrange(256) for _ in range(n))


def with_fcs(body):
    return body + zlib.crc32(body).to_bytes(4, "little")


@cocotb.test()
async def test_tx_appends_valid_fcs(dut):
    mon = await start(dut)
    body = frame_bytes(60, 1)
    await send(dut, body)
    await idle(dut.clk, 40)
    assert mon.frames == [(with_fcs(body), 0)], "tx must append the CRC-32, low byte first"
    assert int(dut.underrun.value) == 0


@cocotb.test()
async def test_rx_tx_roundtrip_random_frames(dut):
    mon = await start(dut)
    rng = random.Random(7)
    sent = []
    for k in range(8):
        body = frame_bytes(rng.randrange(60, 400), 100 + k)
        sent.append(with_fcs(body))
        await send(dut, body)
    await idle(dut.clk, 60)
    assert [f for f, _ in mon.frames] == sent
    assert all(e == 0 for _, e in mon.frames)
    assert mon.bad_framing == 0


@cocotb.test()
async def test_tx_inserts_interframe_gap(dut):
    await start(dut)
    low_runs, run, seen_frame = [], 0, False

    async def watch():
        nonlocal run, seen_frame
        while True:
            await RisingEdge(dut.clk)
            if int(dut.tx_en.value) == 1:
                if seen_frame and run:
                    low_runs.append(run)
                run, seen_frame = 0, True
            else:
                run += 1

    cocotb.start_soon(watch())
    for k in range(3):
        await send(dut, frame_bytes(60, 50 + k))
    await idle(dut.clk, 60)
    assert len(low_runs) == 2
    assert all(r >= 24 for r in low_runs), f"gaps (nibble times) {low_runs}, need >= 24 (12 bytes)"
