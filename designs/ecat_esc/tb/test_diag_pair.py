"""diag never changes the stream: the same frames through esc_core with and without
diag give identical output, cycle for cycle."""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

from core_util import adp_ado
from ecat import CMD, Datagram, build_frame
from stream_util import StreamDriver


@cocotb.test()
async def test_stream_identical_with_diag_removed(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    rng = random.Random(46)
    drv = StreamDriver(dut, dut.clk, rng, gap=(6, 9), prefix="in_")
    mismatches, cycles = [], [0]

    async def compare():
        names = ("valid", "data", "first", "last", "err")
        while True:
            await RisingEdge(dut.clk)
            a = tuple(int(getattr(dut, "a_" + n).value) for n in names)
            b = tuple(int(getattr(dut, "b_" + n).value) for n in names)
            cycles[0] += 1
            if a != b:
                mismatches.append((cycles[0], a, b))

    cocotb.start_soon(compare())
    await drv.idle(5)
    dut.rst.value = 0
    await drv.idle(3)
    names = ["NOP", "APRD", "APWR", "FPRD", "BRD", "BWR", "LRD", "LRW"]
    for _ in range(25):
        dgs = [Datagram(CMD[rng.choice(names)], k + 1,
                        adp_ado(rng.choice([0, 0, 0xFFFF]), rng.randrange(0, 0x100)),
                        bytes(rng.randrange(256) for _ in range(rng.randint(1, 8))),
                        more=k < 1) for k in range(rng.randint(1, 2))]
        dgs[-1].more = False
        await drv.send(build_frame(dgs))
        await drv.idle(150)
    await drv.send(bytes(40))                       # a runt, a bad frame too
    await drv.idle(150)
    assert not mismatches, f"streams differ: {mismatches[:3]}"
    assert cycles[0] > 1000
