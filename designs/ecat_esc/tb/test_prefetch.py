"""Read prefetch margin: every read byte, the first one in particular, must be
ready when it passes at the tightest byte spacing the core promises to handle.

A line-rate byte is 8 core clocks; the clocks in the core and the PHY differ by
up to 400 ppm, so the core is tested down to 6 clocks per byte.
"""
import cocotb

from core_util import adp_ado, cfg_fmmu, exchange, one, start

BASE = 0x10000


async def run_reads(dut, seed, period):
    drv, mon, rng, model = await start(dut, seed)
    drv.gap = (period, period)
    await exchange(drv, mon, model, one("APWR", adp_ado(0, 0x1000), bytes(range(0x21, 0x31))))
    await cfg_fmmu(drv, mon, model, 0, BASE, 16, 0x1000, rd=True, wr=True)
    await exchange(drv, mon, model, one("APRD", adp_ado(0, 0x1000), bytes(16)))   # physical, byte 0 first
    await exchange(drv, mon, model, one("FPRD", 0x00000000, bytes(16)))
    await exchange(drv, mon, model, one("LRD", BASE, bytes(16)))                  # logical, byte 0 first
    await exchange(drv, mon, model, one("LRD", BASE - 3, bytes(24)))              # window starts mid-datagram
    await exchange(drv, mon, model, one("LRW", BASE, bytes(16)))
    await exchange(drv, mon, model, one("BRD", adp_ado(0, 0x1000), bytes(16)))


@cocotb.test()
async def test_first_byte_prefetch_margin_6(dut):
    await run_reads(dut, 31, 6)


@cocotb.test()
async def test_first_byte_prefetch_margin_7(dut):
    await run_reads(dut, 32, 7)


@cocotb.test()
async def test_first_byte_prefetch_margin_8(dut):
    await run_reads(dut, 33, 8)


async def run_physical(dut, seed, period):
    drv, mon, rng, model = await start(dut, seed)
    drv.gap = (period, period)
    top = 0x1000 + 256                                                              # RAM_BYTES = 256
    await exchange(drv, mon, model, one("APWR", adp_ado(0, top - 16), bytes(range(0x61, 0x71))))
    await exchange(drv, mon, model, one("APRD", adp_ado(0, top - 16), bytes(16)))   # last byte of RAM
    await exchange(drv, mon, model, one("APRD", adp_ado(0, top - 1), bytes(1)))     # one byte
    await exchange(drv, mon, model, one("APRD", adp_ado(0, top - 8), bytes(16)))    # runs past the RAM: no access
    await exchange(drv, mon, model, one("APRD", adp_ado(0, 0x1000), bytes(64)))
    await exchange(drv, mon, model, one("APRW", adp_ado(0, 0x1000), bytes(range(64))))
    await exchange(drv, mon, model, one("BRD", adp_ado(0, 0x1000), bytes(32)))
    await exchange(drv, mon, model, one("FPRD", 0x00000000, bytes(24)))


@cocotb.test()
async def test_physical_first_byte_margin_6(dut):
    await run_physical(dut, 41, 6)


@cocotb.test()
async def test_physical_first_byte_margin_7(dut):
    await run_physical(dut, 42, 7)
