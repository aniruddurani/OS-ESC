"""Shared setup for the esc_top tests: three clocks, reset, send frames on the MII,
collect what comes out of the transmit pins."""
import random

import cocotb
from cocotb.triggers import RisingEdge

import model_path  # noqa: F401
from clocks import start_clocks
from ecat import CMD, Datagram, SlaveModel, build_frame
from mii_top_util import TxMonitor, drive_frame

RAM = 256       # esc_top parameter set by run.py for these tests


async def start(dut, seed=1, rx_ppm=0.0, tx_ppm=0.0, phases=None):
    rng = random.Random(seed)
    if phases is None:
        phases = (rng.uniform(0, 40), rng.uniform(0, 40), rng.uniform(0, 10))
    dut.rst.value = 1
    dut.rx_dv.value = 0
    dut.rx_er.value = 0
    dut.rxd.value = 0
    start_clocks(dut, rx_ppm, tx_ppm, phases)
    for _ in range(10):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    for _ in range(10):
        await RisingEdge(dut.clk)
    mon = TxMonitor(dut)
    model = SlaveModel(ram_bytes=RAM)
    # Reset does not clear the RAM; zero it through the datapath, on both sides.
    clear = build_frame([Datagram(CMD["BWR"], 0, (0x1000 << 16), bytes(RAM))])
    await send_and_collect(dut, mon, clear)
    model.process(clear)
    return mon, model, rng


async def wait_frames(dut, mon, count, max_cycles=20000):
    for _ in range(max_cycles):
        if len(mon.frames) >= count:
            break
        await RisingEdge(dut.clk)
    for _ in range(40):                       # let the transmitter finish its gap
        await RisingEdge(dut.clk)
    assert len(mon.frames) >= count, f"only {len(mon.frames)} of {count} frames came out"


async def send_and_collect(dut, mon, frame, er_at=None):
    before = len(mon.frames)
    await drive_frame(dut, frame, er_at=er_at)
    await wait_frames(dut, mon, before + 1)
    return mon.frames[-1]


def flags(dut):
    return (int(dut.rx_overflow.value), int(dut.tx_overflow.value), int(dut.tx_underrun.value))
