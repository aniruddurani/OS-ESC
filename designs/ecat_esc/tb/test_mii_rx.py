import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

from mii_util import PREAMBLE, StreamMonitor, idle, nibbles


async def start(dut):
    cocotb.start_soon(Clock(dut.rx_clk, 40, unit="ns").start())
    dut.rst.value = 1
    dut.rx_dv.value = 0
    dut.rx_er.value = 0
    dut.rxd.value = 0
    await idle(dut.rx_clk, 3)
    dut.rst.value = 0
    await idle(dut.rx_clk, 2)
    return StreamMonitor(dut, dut.rx_clk)


async def drive(dut, nibs, er_at=None, gap=12):
    for i, n in enumerate(nibs):
        dut.rx_dv.value = 1
        dut.rxd.value = n
        dut.rx_er.value = 1 if i == er_at else 0
        await RisingEdge(dut.rx_clk)
    dut.rx_dv.value = 0
    dut.rx_er.value = 0
    dut.rxd.value = 0
    await idle(dut.rx_clk, gap)


def payload(n, seed=1):
    return bytes(random.Random(seed).randrange(256) for _ in range(n))


@cocotb.test()
async def test_rx_strips_preamble(dut):
    mon = await start(dut)
    body = payload(64)
    await drive(dut, PREAMBLE + list(nibbles(body)))
    assert mon.frames == [(body, 0)], f"got {mon.frames}"
    assert mon.bad_framing == 0


@cocotb.test()
async def test_rx_flags_rx_er(dut):
    mon = await start(dut)
    body = payload(64, 2)
    await drive(dut, PREAMBLE + list(nibbles(body)), er_at=len(PREAMBLE) + 40)
    assert len(mon.frames) == 1
    assert mon.frames[0][0] == body
    assert mon.frames[0][1] == 1, "RX_ER during the frame must set err on the last byte"


@cocotb.test()
async def test_rx_short_preamble_ignored(dut):
    mon = await start(dut)
    body = payload(64, 3)
    await drive(dut, [0xD] + list(nibbles(body)))                # SFD with no preamble
    await drive(dut, [0x5, 0xD] + list(nibbles(body)))           # one preamble nibble
    await drive(dut, [0x5, 0x5, 0x3, 0xD] + list(nibbles(body)))  # corrupted preamble
    assert mon.frames == [], "a frame without a valid preamble must produce no bytes"
    await drive(dut, PREAMBLE + list(nibbles(body)))              # and the next good one works
    assert mon.frames == [(body, 0)]


@cocotb.test()
async def test_rx_odd_nibble_count_flags_err(dut):
    mon = await start(dut)
    body = payload(33, 4)
    await drive(dut, PREAMBLE + list(nibbles(body)) + [0x7])
    assert len(mon.frames) == 1 and mon.frames[0][1] == 1


@cocotb.test()
async def test_rx_back_to_back_frames(dut):
    mon = await start(dut)
    frames = [payload(60 + i, 10 + i) for i in range(5)]
    for f in frames:
        await drive(dut, PREAMBLE + list(nibbles(f)), gap=24)
    assert [f for f, _ in mon.frames] == frames
    assert all(e == 0 for _, e in mon.frames)
