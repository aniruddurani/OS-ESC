"""Shared check for the addsub_cs tests: a random and edge-case stream against Python integers."""
import random

from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

LATENCY = 2          # y shows the result of the inputs applied before edge i after edge i + 1


async def check_stream(dut, width, sub, count=600, seed=7):
    """Apply one operand pair per clock and compare each result with Python's, two clocks later."""
    import cocotb
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    mask = (1 << width) - 1
    rng = random.Random(seed)
    edge = [0, mask, 1, mask - 1, 1 << (width - 1), (1 << (width // 2)) - 1, 1 << (width // 2)]
    pairs = [(a, b) for a in edge for b in edge]          # carry and borrow runs across chunks
    pairs += [(rng.getrandbits(width), rng.getrandbits(width)) for _ in range(count)]
    pairs += [(rng.getrandbits(width), 0) for _ in range(20)]
    expect = []
    checked = 0
    for i, (a, b) in enumerate(pairs + [(0, 0)] * LATENCY):
        dut.a.value = a
        dut.b.value = b
        expect.append(((a - b) if sub else (a + b)) & mask)
        await RisingEdge(dut.clk)
        if i >= LATENCY:
            got = int(dut.y.value)
            assert got == expect[i - LATENCY], (
                f"pair {i - LATENCY}: {pairs[i - LATENCY]} -> {got:#x}, "
                f"want {expect[i - LATENCY]:#x}")
            checked += 1
    assert checked == len(pairs)
