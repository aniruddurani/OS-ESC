"""Process-data RAM: three-clock read latency, hazards, range checks.

Runs on the `pdram` wrapper (flip-flop build for RAM_BYTES = 256, macro build for
larger sizes; the parameter is set per test module in run.py). Samples are taken
right after each rising edge, so they show the values from just before it: a
request presented before edge k with its data valid in sample k + MEM_LAT.
"""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

MEM_LAT = 3
BASE = 0x1000
SIZE = 256                  # must equal the RAM_BYTES set for this module in run.py


async def start(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    dut.mem_req.value = 0
    dut.mem_we.value = 0
    dut.mem_addr.value = 0
    dut.mem_wdata.value = 0
    for _ in range(4):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


def rd(addr):
    return (0, addr, 0)


def wr(addr, val):
    return (1, addr, val)


async def run_seq(dut, seq, tail=6):
    """Present one step per clock (None = idle); return [(rvalid, rdata)] per edge."""
    out = []
    for step in list(seq) + [None] * tail:
        if step is None:
            dut.mem_req.value = 0
            dut.mem_we.value = 0
        else:
            we, addr, val = step
            dut.mem_req.value = 1
            dut.mem_we.value = we
            dut.mem_addr.value = addr
            dut.mem_wdata.value = val
        await RisingEdge(dut.clk)
        out.append((int(dut.mem_rvalid.value), int(dut.mem_rdata.value)))
    return out


def valid_at(samples):
    return [i for i, (v, _) in enumerate(samples) if v]


@cocotb.test()
async def test_pdram_latency(dut):
    await start(dut)
    await run_seq(dut, [wr(BASE + 0x10, 0xA5)])
    s = await run_seq(dut, [rd(BASE + 0x10)])
    assert valid_at(s) == [MEM_LAT], f"rvalid at {valid_at(s)}"
    assert s[MEM_LAT][1] == 0xA5

    # one request per clock: one result per request, in order, none lost
    addrs = [BASE + 0x20 + i for i in range(8)]
    await run_seq(dut, [wr(a, 0x30 + i) for i, a in enumerate(addrs)])
    s = await run_seq(dut, [rd(a) for a in addrs])
    assert valid_at(s) == [MEM_LAT + i for i in range(8)], f"rvalid at {valid_at(s)}"
    assert [s[MEM_LAT + i][1] for i in range(8)] == [0x30 + i for i in range(8)]


@cocotb.test()
async def test_pdram_rw_hazard(dut):
    await start(dut)
    a = BASE + 0x44
    await run_seq(dut, [wr(a, 0x11)])
    # write then read on the next clock: the read sees the new data
    s = await run_seq(dut, [wr(a, 0x22), rd(a)])
    assert s[1 + MEM_LAT] == (1, 0x22), f"read after write: {s[1 + MEM_LAT]}"
    # read then write on the next clock: the read still sees the old data
    s = await run_seq(dut, [rd(a), wr(a, 0x33)])
    assert s[MEM_LAT] == (1, 0x22), f"read before write: {s[MEM_LAT]}"
    s = await run_seq(dut, [rd(a)])
    assert s[MEM_LAT] == (1, 0x33)


@cocotb.test()
async def test_pdram_out_of_range(dut):
    await start(dut)
    size = SIZE
    await run_seq(dut, [wr(BASE + i, i ^ 0x5A) for i in range(0, size, 17)])
    bad = [BASE - 1, 0x0000, 0x0FFF, BASE + size, BASE + size + 0x100, 0xFFFF]
    s = await run_seq(dut, [rd(a) for a in bad])
    assert valid_at(s) == [], f"rvalid for out-of-range read at {valid_at(s)}"
    await run_seq(dut, [wr(a, 0xEE) for a in bad])
    s = await run_seq(dut, [rd(BASE + i) for i in range(0, size, 17)])
    assert [d for _, d in s[MEM_LAT:MEM_LAT + len(range(0, size, 17))]] == \
        [i ^ 0x5A for i in range(0, size, 17)], "an out-of-range write changed the RAM"
