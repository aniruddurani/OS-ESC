"""Shared helpers for the esc_core tests: reset, send a frame through the DUT and
the reference model, and compare the results."""
import random
import zlib

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

import model_path  # noqa: F401
from ecat import CMD, Datagram, SlaveModel, build_frame
from mii_util import StreamMonitor
from stream_util import StreamDriver

RAM = 256       # esc_core parameter set by run.py for this test


def adp_ado(adp, ado):
    return (ado << 16) | adp


def fcs_ok(frame):
    return zlib.crc32(frame[:-4]).to_bytes(4, "little") == frame[-4:]


async def watch_late(dut):
    """Fail the test the moment a logical data byte passes before its read data is held."""
    late = dut.u_exec.u_acc.late
    while True:
        await RisingEdge(dut.clk)
        assert int(late.value) == 0, "a byte passed before its read data was held"


async def start(dut, seed, n_fmmu=3):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    rng = random.Random(seed)
    drv = StreamDriver(dut, dut.clk, rng, gap=(6, 9), prefix="in_")
    mon = StreamMonitor(dut, dut.clk, prefix="out_")
    await drv.idle(5)
    dut.rst.value = 0
    await drv.idle(3)
    cocotb.start_soon(watch_late(dut))      # every test fails if a byte passes before its read data
    # Reset does not clear the RAM (a real RAM would not either), and cocotb runs
    # every test in one simulation: zero it through the datapath, on both sides.
    model = SlaveModel(ram_bytes=RAM, n_fmmu=n_fmmu)
    clear = build_frame([Datagram(CMD["BWR"], 0, adp_ado(0, 0x1000), bytes(RAM))])
    await exchange(drv, mon, model, clear)
    return drv, mon, rng, model


async def exchange(drv, mon, model, frame, err=0):
    """Send `frame` through the DUT and the model; check the outputs agree."""
    expected = model.process(frame, rx_err=bool(err))
    before = len(mon.frames)
    await drv.send(frame, err=err)
    await drv.idle(200)
    assert len(mon.frames) == before + 1, "the core must emit exactly one frame per input frame"
    body, poison = mon.frames[-1]
    assert body == expected[:-4], "output bytes differ from the model"
    assert poison == (0 if fcs_ok(expected) else 1), "poison flag differs from the model"
    return body, poison


def one(cmd, address, data, idx=1, wkc=0):
    return build_frame([Datagram(CMD[cmd], idx, address, data, wkc=wkc)])



async def cfg_fmmu(drv, mon, model, i, logical, length, physical, rd=False, wr=False, active=1):
    """Configure FMMU `i` through the datapath (an APWR of its 13 writable bytes)."""
    import struct
    cfg = struct.pack("<IHBBHBBB", logical, length, 0, 7, physical, 0,
                      (1 if rd else 0) | (2 if wr else 0), active)
    await exchange(drv, mon, model, one("APWR", adp_ado(0, 0x0600 + 16 * i), cfg))
