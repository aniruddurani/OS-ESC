"""Logical access through the read-ahead queue (dg_access) with the pipelined FMMUs.

esc_core with 16 FMMUs and 256 bytes of RAM. Byte spacing is set per test; 6 clocks is
the tightest the core promises to handle.
"""
import random

import cocotb

from cocotb.triggers import RisingEdge

from core_util import adp_ado, cfg_fmmu, exchange, one, start

BASE = 0x20000                # logical address used by the tests
RAMB = 0x1000                 # first RAM address
N = 16


async def watch_late(dut):
    """Fail the test the moment a data byte passes before its read data is held."""
    while True:
        await RisingEdge(dut.clk)
        assert int(dut.u_exec.u_acc.late.value) == 0, "a byte passed before its read data was held"


async def setup_map(dut, seed, period):
    drv, mon, rng, model = await start(dut, seed, n_fmmu=N)
    cocotb.start_soon(watch_late(dut))
    drv.gap = (period, period)
    await exchange(drv, mon, model, one("APWR", adp_ado(0, RAMB), bytes(range(0x30, 0x70))))
    return drv, mon, rng, model


async def many_fmmus(drv, mon, model):
    """16 FMMUs: 0 read+write for BASE..+32, 3 and 7 overlap it (lower number wins), the rest
    cover other places or are inactive."""
    await cfg_fmmu(drv, mon, model, 0, BASE, 32, RAMB, rd=True, wr=True)
    await cfg_fmmu(drv, mon, model, 3, BASE + 16, 32, RAMB + 0x40, rd=True, wr=True)
    await cfg_fmmu(drv, mon, model, 7, BASE + 8, 8, RAMB + 0x80, rd=True)
    for i, (off, rd, wr) in {1: (0x100, 1, 0), 2: (0x200, 0, 1), 5: (0x300, 1, 1), 9: (0x400, 1, 1)}.items():
        await cfg_fmmu(drv, mon, model, i, BASE + off, 16, RAMB + 0x20, rd=bool(rd), wr=bool(wr))
    await cfg_fmmu(drv, mon, model, 15, BASE + 40, 8, RAMB + 0x60, rd=True, wr=True, active=0)


async def margin(dut, seed, period):
    drv, mon, rng, model = await setup_map(dut, seed, period)
    await many_fmmus(drv, mon, model)
    await exchange(drv, mon, model, one("LRD", BASE, bytes(32)))
    await exchange(drv, mon, model, one("LRD", BASE - 3, bytes(48)))      # starts below the first window
    await exchange(drv, mon, model, one("LWR", BASE, bytes(range(1, 41))))
    await exchange(drv, mon, model, one("LRW", BASE, bytes(range(50, 100))))
    await exchange(drv, mon, model, one("LRW", BASE + 20, bytes(range(7, 31))))
    await exchange(drv, mon, model, one("LRD", BASE + 31, bytes(1)))      # last byte of a window
    await exchange(drv, mon, model, one("LRD", BASE + 0x100, bytes(16)))  # read-only FMMU 1
    await exchange(drv, mon, model, one("LWR", BASE + 0x200, bytes(16)))  # write-only FMMU 2
    await exchange(drv, mon, model, one("APRD", adp_ado(0, RAMB), bytes(0x90)))


@cocotb.test()
async def test_first_byte_margin_logical_6(dut):
    await margin(dut, 51, 6)


@cocotb.test()
async def test_first_byte_margin_logical_7(dut):
    await margin(dut, 52, 7)


@cocotb.test()
async def test_first_byte_margin_logical_8(dut):
    await margin(dut, 53, 8)


@cocotb.test()
async def test_write_read_same_byte(dut):
    """A read of offset k from the byte that offset k-1 wrote in the same datagram sees the
    new data: bytes are processed in order, whatever the byte spacing."""
    for period in (6, 8):
        drv, mon, rng, model = await setup_map(dut, 60 + period, period)
        await cfg_fmmu(drv, mon, model, 0, BASE, 8, RAMB + 0x1F, rd=True)           # reads 0x101F..
        await cfg_fmmu(drv, mon, model, 1, BASE, 8, RAMB + 0x20, wr=True)           # writes 0x1020..
        await exchange(drv, mon, model, one("APWR", adp_ado(0, RAMB + 0x1F), bytes([0xEE])))
        n = 8
        data = bytes(0x10 + k for k in range(n))
        await drv.send(one("LRW", BASE, data))
        await drv.idle(200)
        body = mon.frames[-1][0]
        got = body[26:26 + n]
        expect = bytes([0xEE]) + data[:n - 1]
        assert got == expect, f"period {period}: got {got.hex()}, expected {expect.hex()}"


@cocotb.test()
async def test_queue_full_and_flush(dut):
    drv, mon, rng, model = await setup_map(dut, 71, 6)
    await many_fmmus(drv, mon, model)
    await exchange(drv, mon, model, one("LRW", BASE - 700, bytes(rng.randrange(256) for _ in range(1486))))
    # a frame that ends bad, then at once a new datagram: nothing stale may be used
    await exchange(drv, mon, model, one("LRW", BASE, bytes(range(1, 41))), err=1)
    await exchange(drv, mon, model, one("LRD", BASE, bytes(32)))
    await exchange(drv, mon, model, one("LRD", BASE + 16, bytes(40)))


@cocotb.test()
async def test_zero_and_one_byte_datagrams(dut):
    drv, mon, rng, model = await setup_map(dut, 72, 6)
    await many_fmmus(drv, mon, model)
    await exchange(drv, mon, model, one("LRD", BASE, b""))
    await exchange(drv, mon, model, one("LRW", BASE, b""))
    await exchange(drv, mon, model, one("LRD", BASE, bytes(1)))
    await exchange(drv, mon, model, one("LWR", BASE + 31, b"\x5a"))
    await exchange(drv, mon, model, one("LRD", BASE + 31, bytes(1)))
    await exchange(drv, mon, model, one("LRD", BASE + 1000, bytes(1)))    # hits nothing


@cocotb.test()
async def test_random_logical_16_fmmus(dut):
    drv, mon, rng, model = await setup_map(dut, 73, 6)
    r = random.Random(11)
    for i in range(16):
        # one 16-byte physical slot per FMMU: windows may overlap logically but never alias
        # physically (the model orders aliased accesses per FMMU, the core per byte)
        await cfg_fmmu(drv, mon, model, i, BASE + r.randrange(0, 200), r.randrange(1, 17),
                       RAMB + 16 * i, rd=r.random() < 0.7, wr=r.random() < 0.5,
                       active=1 if r.random() < 0.85 else 0)
    for _ in range(40):
        cmd = r.choice(["LRD", "LWR", "LRW"])
        n = r.randrange(0, 90)
        await exchange(drv, mon, model, one(cmd, BASE + r.randrange(-20, 220), bytes(r.randrange(256) for _ in range(n))))
