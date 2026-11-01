import random
import zlib

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


async def start(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    dut.init.value = 0
    dut.valid.value = 0
    dut.data.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


async def feed(dut, data):
    """Load the initial value, then clock the bytes in one per cycle."""
    dut.init.value = 1
    dut.valid.value = 0
    await RisingEdge(dut.clk)
    dut.init.value = 0
    for b in data:
        dut.valid.value = 1
        dut.data.value = b
        await RisingEdge(dut.clk)
    dut.valid.value = 0
    await RisingEdge(dut.clk)


def random_frame(rng, n):
    return bytes(rng.randrange(256) for _ in range(n))


@cocotb.test()
async def test_matches_zlib(dut):
    await start(dut)
    rng = random.Random(1)
    for n in (1, 4, 60, 61, 64, 1514):
        frame = random_frame(rng, n)
        await feed(dut, frame)
        assert int(dut.crc.value) == zlib.crc32(frame), f"crc mismatch for {n} bytes"


@cocotb.test()
async def test_residue_ok_with_fcs(dut):
    await start(dut)
    rng = random.Random(2)
    for n in (60, 100, 1514):
        frame = random_frame(rng, n)
        fcs = zlib.crc32(frame).to_bytes(4, "little")
        await feed(dut, frame + fcs)
        assert int(dut.residue_ok.value) == 1, f"residue not ok for {n}-byte frame + FCS"


@cocotb.test()
async def test_residue_bad_with_corrupt_fcs(dut):
    await start(dut)
    frame = random_frame(random.Random(3), 80)
    fcs = bytearray(zlib.crc32(frame).to_bytes(4, "little"))
    fcs[0] ^= 0x01
    await feed(dut, frame + bytes(fcs))
    assert int(dut.residue_ok.value) == 0, "a corrupted FCS must not give residue_ok"
