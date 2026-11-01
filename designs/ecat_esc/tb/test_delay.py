import random

import cocotb
from cocotb.clock import Clock

from stream_util import OutputLog, StreamDriver

DEPTH = 4
OUT = ("o_valid", "o_data", "o_first", "o_last", "o_err", "o_tag")


async def start(dut, seed):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    dut.tag.value = 0
    drv = StreamDriver(dut, dut.clk, random.Random(seed))
    await drv.idle(4)
    dut.rst.value = 0
    await drv.idle(2)
    return drv, OutputLog(dut, dut.clk, OUT)


@cocotb.test()
async def test_delay_line_depth_exact(dut):
    drv, log = await start(dut, 1)
    frame = bytes(random.Random(9).randrange(256) for _ in range(30))
    # Count input bytes accepted per cycle so each output's delay can be measured.
    accepted = []

    async def count():
        from cocotb.triggers import RisingEdge
        n = 0
        while True:
            await RisingEdge(dut.clk)
            if int(dut.valid.value):
                n += 1
            accepted.append(n)

    cocotb.start_soon(count())
    await drv.send(frame, err=1)
    await drv.idle(10)
    out = [(i, r) for i, r in enumerate(log.rows) if r["o_valid"]]
    assert bytes(r["o_data"] for _, r in out) == frame[:-DEPTH], "last DEPTH bytes are dropped"
    for k, (cycle, _) in enumerate(out):
        # Output k appears one cycle after input k+DEPTH was accepted (registered).
        assert accepted[cycle - 1] == k + DEPTH + 1, f"byte {k} delayed by the wrong amount"
    assert out[0][1]["o_first"] == 1 and all(r["o_first"] == 0 for _, r in out[1:])
    assert out[-1][1]["o_last"] == 1 and all(r["o_last"] == 0 for _, r in out[:-1])
    assert out[-1][1]["o_err"] == 1, "err on the final input byte moves to the final output byte"


@cocotb.test()
async def test_short_frame_produces_nothing_and_recovers(dut):
    drv, log = await start(dut, 2)
    await drv.send(bytes(range(DEPTH)))                       # exactly DEPTH bytes
    good = bytes(range(10, 20))
    await drv.send(good)
    await drv.idle(10)
    got = bytes(r["o_data"] for r in log.rows if r["o_valid"])
    assert got == good[:-DEPTH]
