"""Process RAM built from macros (RAM_BYTES = 2048: two banks), same contract as the
flip-flop build: three-clock latency, new data after a write, banks distinct,
out-of-range requests ignored."""
import cocotb

from test_pdram import BASE, MEM_LAT, rd, run_seq, start, valid_at, wr

SIZE = 2048                  # must equal the RAM_BYTES set for this module in run.py


@cocotb.test()
async def test_macro_latency(dut):
    await start(dut)
    await run_seq(dut, [wr(BASE + 0x10, 0xA5)])
    s = await run_seq(dut, [rd(BASE + 0x10)])
    assert valid_at(s) == [MEM_LAT] and s[MEM_LAT][1] == 0xA5
    addrs = [BASE + 0x3FC + i for i in range(8)]          # crosses the bank boundary
    await run_seq(dut, [wr(a, 0x40 + i) for i, a in enumerate(addrs)])
    s = await run_seq(dut, [rd(a) for a in addrs])
    assert valid_at(s) == [MEM_LAT + i for i in range(8)], f"rvalid at {valid_at(s)}"
    assert [s[MEM_LAT + i][1] for i in range(8)] == [0x40 + i for i in range(8)]


@cocotb.test()
async def test_macro_rw_hazard(dut):
    await start(dut)
    for a in (BASE + 0x44, BASE + 0x444):                  # one address in each bank
        await run_seq(dut, [wr(a, 0x11)])
        s = await run_seq(dut, [wr(a, 0x22), rd(a)])
        assert s[1 + MEM_LAT] == (1, 0x22), f"read after write at {a:#06x}: {s[1 + MEM_LAT]}"
        s = await run_seq(dut, [rd(a), wr(a, 0x33)])
        assert s[MEM_LAT] == (1, 0x22), f"read before write at {a:#06x}: {s[MEM_LAT]}"
        s = await run_seq(dut, [rd(a)])
        assert s[MEM_LAT] == (1, 0x33)


@cocotb.test()
async def test_macro_bank_boundary(dut):
    await start(dut)
    last0, first1, last1 = BASE + 0x3FF, BASE + 0x400, BASE + SIZE - 1
    await run_seq(dut, [wr(last0, 0xA0), wr(first1, 0xB1), wr(last1, 0xC2), wr(BASE, 0xD3)])
    s = await run_seq(dut, [rd(last0), rd(first1), rd(last1), rd(BASE)])
    assert [s[MEM_LAT + i][1] for i in range(4)] == [0xA0, 0xB1, 0xC2, 0xD3]


@cocotb.test()
async def test_macro_out_of_range(dut):
    await start(dut)
    marks = [BASE + i for i in range(0, SIZE, 97)]
    await run_seq(dut, [wr(a, (a >> 1) & 0xFF) for a in marks])
    bad = [BASE - 1, 0x0000, 0x0FFF, BASE + SIZE, BASE + SIZE + 0x100, 0xFFFF]
    s = await run_seq(dut, [rd(a) for a in bad])
    assert valid_at(s) == [], f"rvalid for out-of-range read at {valid_at(s)}"
    await run_seq(dut, [wr(a, 0xEE) for a in bad])
    s = await run_seq(dut, [rd(a) for a in marks])
    assert [s[MEM_LAT + i][1] for i in range(len(marks))] == [(a >> 1) & 0xFF for a in marks]
