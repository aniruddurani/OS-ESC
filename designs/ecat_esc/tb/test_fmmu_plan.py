"""fmmu_plan: the pipelined per-FMMU segment calculation against fmmu_ref."""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

import fmmu_ref

N_FMMU = 16                  # must equal the N_FMMU set for this module in run.py
RAM = 256                    # ... and RAM_BYTES
LATENCY = 7                  # plan_done is visible this many edges after the plan_len edge
EARLY = 6                    # the window and address are held this many edges before plan_len


async def start(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    dut.plan_len.value = 0
    dut.cfg.value = 0
    dut.dg_addr.value = 0
    dut.dg_len.value = 0
    dut.is_rd.value = 0
    dut.is_wr.value = 0
    for _ in range(4):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


def bits(value, width, count, i):
    return (int(value) >> (width * i)) & ((1 << width) - 1)


async def run_plan(dut, fmmus, ds, dl, is_rd, is_wr):
    """Present one datagram; return the plan_done cycle (edges after the plan_len edge) and
    the FMMU outputs at that point."""
    dut.cfg.value = fmmu_ref.pack_cfg(fmmus)
    dut.dg_addr.value = ds
    dut.dg_len.value = 0                    # the length is not known yet
    dut.is_rd.value = int(is_rd)
    dut.is_wr.value = int(is_wr)
    for _ in range(EARLY):                  # window and address are known before the length
        await RisingEdge(dut.clk)
    dut.dg_len.value = dl
    dut.plan_len.value = 1
    await RisingEdge(dut.clk)               # the edge that samples plan_len
    dut.plan_len.value = 0
    done_at = None
    got = None
    for k in range(1, LATENCY + 4):
        await RisingEdge(dut.clk)
        if int(dut.plan_done.value) and done_at is None:
            done_at = k
            got = (int(dut.hr.value), int(dut.hw.value), int(dut.seg_start.value),
                   int(dut.seg_end.value), int(dut.seg_base.value))
    return done_at, got


def check(got, expect, n, label):
    hr, hw, s0, s1, ph = got
    for i, (ehr, ehw, es0, es1, eph) in enumerate(expect):
        assert bool((hr >> i) & 1) == ehr, f"{label}: hr[{i}]"
        assert bool((hw >> i) & 1) == ehw, f"{label}: hw[{i}]"
        if ehr or ehw:
            assert bits(s0, 11, n, i) == es0, f"{label}: seg_start[{i}]"
            assert bits(s1, 11, n, i) == es1, f"{label}: seg_end[{i}]"
            assert bits(ph, 16, n, i) == eph, f"{label}: seg_base[{i}]"


@cocotb.test()
async def test_fmmu_plan_matches_reference(dut):
    await start(dut)
    rng = random.Random(5)
    hits = 0
    for it in range(1500):
        ds = fmmu_ref.random_datagram_addr(rng)
        dl = rng.choice([0, 1, 2, rng.randint(1, 64), rng.randint(1, 1486)])
        count = rng.choice([N_FMMU, N_FMMU, 3])
        fmmus = fmmu_ref.random_fmmus(rng, count, ds, RAM) + \
            [dict(start=0, len=0, phys=0, rd=0, wr=0, act=0)] * (N_FMMU - count)
        is_rd, is_wr = rng.choice([(1, 0), (0, 1), (1, 1)])
        expect = fmmu_ref.plan(fmmus, ds, dl, is_rd, is_wr, RAM)
        done_at, got = await run_plan(dut, fmmus, ds, dl, is_rd, is_wr)
        assert done_at == LATENCY, f"iteration {it}: plan_done after {done_at} edges"
        check(got, expect, N_FMMU, f"iteration {it}")
        hits += sum(1 for e in expect if e[0] or e[1])
    assert hits > 500, f"only {hits} contributing FMMUs in the random set"


@cocotb.test()
async def test_fmmu_plan_edges(dut):
    await start(dut)
    window = dict(start=1000, len=100, phys=0x1000, rd=1, wr=1, act=1)
    off = dict(window, act=0)
    zero = dict(window, len=0)
    cases = [
        (window, 1000, 100, "datagram equals the window"),
        (window, 900, 150, "datagram starts below, ends inside"),
        (window, 1050, 200, "datagram starts inside, ends beyond"),
        (window, 800, 400, "datagram contains the window"),
        (window, 1099, 1, "last byte of the window"),
        (window, 1100, 5, "just past the window"),
        (window, 995, 5, "just below the window"),
        (off, 1000, 100, "inactive"),
        (zero, 1000, 100, "zero length"),
        (dict(window, phys=0x1000 + RAM - 50), 1000, 50, "chunk ends at the RAM end"),
        (dict(window, phys=0x1000 + RAM - 49), 1000, 50, "chunk one past the RAM end"),
        (dict(window, phys=0x0FF0), 1000, 16, "chunk ends at the register space end"),
        (dict(window, phys=0x0FF0), 1000, 17, "chunk straddles registers and RAM"),
    ]
    blank = dict(start=0, len=0, phys=0, rd=0, wr=0, act=0)
    for f, ds, dl, label in cases:
        fmmus = [f] + [blank] * (N_FMMU - 1)
        expect = fmmu_ref.plan(fmmus, ds, dl, 1, 1, RAM)
        done_at, got = await run_plan(dut, fmmus, ds, dl, 1, 1)
        assert done_at == LATENCY
        check(got, expect, N_FMMU, label)


@cocotb.test()
async def test_fmmu_plan_holds_until_next_plan(dut):
    await start(dut)
    window = dict(start=500, len=40, phys=0x1010, rd=1, wr=1, act=1)
    blank = dict(start=0, len=0, phys=0, rd=0, wr=0, act=0)
    fmmus = [window] + [blank] * (N_FMMU - 1)
    _, first = await run_plan(dut, fmmus, 500, 40, 1, 1)
    expect = fmmu_ref.plan(fmmus, 500, 40, 1, 1, RAM)
    check(first, expect, N_FMMU, "first")
    dut.dg_addr.value = 123456          # the next datagram's address arrives
    dut.dg_len.value = 7
    for _ in range(20):
        await RisingEdge(dut.clk)
        got = (int(dut.hr.value), int(dut.hw.value), int(dut.seg_start.value),
               int(dut.seg_end.value), int(dut.seg_base.value))
        check(got, expect, N_FMMU, "hold")
