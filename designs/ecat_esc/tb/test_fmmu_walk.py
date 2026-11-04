"""fmmu_walk: the three-stage per-offset lookup against fmmu_ref.walk."""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

import fmmu_ref

N_FMMU = 16                  # must equal the N_FMMU set for this module in run.py
LATENCY = 3


def pack(segs):
    hr = sum((1 << i) for i, s in enumerate(segs) if s[0])
    hw = sum((1 << i) for i, s in enumerate(segs) if s[1])
    s0 = sum(s[2] << (11 * i) for i, s in enumerate(segs))
    s1 = sum(s[3] << (11 * i) for i, s in enumerate(segs))
    ph = sum(s[4] << (16 * i) for i, s in enumerate(segs))
    return hr, hw, s0, s1, ph


async def start(dut, segs):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    dut.req_valid.value = 0
    dut.req_off.value = 0
    hr, hw, s0, s1, ph = pack(segs)
    dut.hr.value, dut.hw.value = hr, hw
    dut.seg_start.value, dut.seg_end.value, dut.seg_base.value = s0, s1, ph
    for _ in range(4):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


async def run_requests(dut, offsets, gaps=None):
    """Present one request per entry of `offsets` (None = idle clock); return the list of
    (clock index, res_off, hits and addresses) for every valid result."""
    results = []
    for i, n in enumerate(list(offsets) + [None] * (LATENCY + 2)):
        if n is None:
            dut.req_valid.value = 0
        else:
            dut.req_valid.value = 1
            dut.req_off.value = n
        await RisingEdge(dut.clk)
        if int(dut.res_valid.value):
            results.append((i, int(dut.res_off.value),
                            (bool(int(dut.rd_hit.value)), int(dut.rd_addr.value),
                             bool(int(dut.wr_hit.value)), int(dut.wr_addr.value))))
    return results


def random_segs(rng):
    ds = rng.randint(0, 5000)
    fmmus = fmmu_ref.random_fmmus(rng, N_FMMU, ds, 256)
    # shift windows so that many land in the datagram
    return fmmu_ref.plan(fmmus, ds, rng.randint(1, 1486), 1, 1, 256)


@cocotb.test()
async def test_fmmu_walk_matches_reference(dut):
    rng = random.Random(9)
    segs = random_segs(rng)
    await start(dut, segs)
    checked = hits = 0
    for round_no in range(60):
        segs = random_segs(rng)
        hr, hw, s0, s1, ph = pack(segs)
        dut.hr.value, dut.hw.value = hr, hw
        dut.seg_start.value, dut.seg_end.value, dut.seg_base.value = s0, s1, ph
        await RisingEdge(dut.clk)
        edges = [s[2] for s in segs] + [s[3] for s in segs] + [s[3] - 1 for s in segs]
        offsets = [rng.choice(edges + [rng.randint(0, 2047)]) & 0x7FF for _ in range(60)]
        results = await run_requests(dut, offsets)
        assert [r[1] for r in results] == offsets, "results out of order or missing"
        for (_, n, got), _ in zip(results, offsets):
            expect = fmmu_ref.walk(segs, n)
            assert got == expect, f"round {round_no} offset {n}: got {got}, expected {expect}"
            checked += 1
            hits += int(expect[0]) + int(expect[2])
    assert hits > 300, f"only {hits} hits in {checked} lookups"


@cocotb.test()
async def test_fmmu_walk_throughput(dut):
    segs = [(True, True, 0, 100, 0x1000)] + [(False, False, 0, 0, 0)] * (N_FMMU - 1)
    await start(dut, segs)
    results = await run_requests(dut, list(range(500)))
    assert [r[1] for r in results] == list(range(500)), "results out of order or lost"
    # a request presented before edge n is answered in the sample LATENCY edges later
    assert [r[0] for r in results] == [n + LATENCY for n in range(500)]
    for (_, n, got) in results:
        assert got == fmmu_ref.walk(segs, n)


@cocotb.test()
async def test_fmmu_walk_lowest_number_wins(dut):
    # FMMUs 2, 5 and 9 all cover offsets 10..29. Reads: FMMU 5 and 9 only (so 5 wins);
    # writes: all three (so 2 wins). seg_base is the physical address of offset 0.
    segs = [(False, False, 0, 0, 0)] * N_FMMU
    segs = list(segs)
    segs[2] = (False, True, 10, 30, 0x1100)
    segs[5] = (True, True, 10, 30, 0x1200)
    segs[9] = (True, True, 10, 30, 0x1300)
    await start(dut, segs)
    results = await run_requests(dut, [9, 10, 20, 29, 30])
    got = {n: r for _, n, r in results}
    assert got[9] == (False, 0, False, 0)
    assert got[10] == (True, 0x1200 + 10, True, 0x1100 + 10)
    assert got[20] == (True, 0x1200 + 20, True, 0x1100 + 20)
    assert got[29] == (True, 0x1200 + 29, True, 0x1100 + 29)
    assert got[30] == (False, 0, False, 0)
