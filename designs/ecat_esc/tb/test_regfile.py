import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

import model_path  # noqa: F401
from ecat import SlaveModel

N_FMMU, N_SM = 3, 4          # regfile defaults, equal to SlaveModel defaults


async def start(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    dut.mem_req.value = 0
    dut.mem_we.value = 0
    dut.mem_addr.value = 0
    dut.mem_wdata.value = 0
    dut.dl_status.value = 0
    for _ in range(4):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


async def mem_write(dut, addr, val):
    dut.mem_req.value = 1
    dut.mem_we.value = 1
    dut.mem_addr.value = addr
    dut.mem_wdata.value = val
    await RisingEdge(dut.clk)
    dut.mem_req.value = 0
    dut.mem_we.value = 0


async def mem_read(dut, addr):
    """Present a read for one cycle; the data is valid two clocks later."""
    dut.mem_req.value = 1
    dut.mem_we.value = 0
    dut.mem_addr.value = addr
    await RisingEdge(dut.clk)
    dut.mem_req.value = 0
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    assert int(dut.mem_rvalid.value) == 1, f"no rvalid for read of {addr:#06x}"
    return int(dut.mem_rdata.value)


def model_write(model, addr, val):
    span = model._span(addr, 1)
    if span is not None:
        model._write(span, bytes([val]))


@cocotb.test()
async def test_reset_values_match_documentation(dut):
    await start(dut)
    ref = SlaveModel().regs
    for addr in range(0x1000):
        assert await mem_read(dut, addr) == ref[addr], f"reset value at {addr:#06x}"


@cocotb.test()
async def test_station_address_writable(dut):
    await start(dut)
    await mem_write(dut, 0x0010, 0x34)
    await mem_write(dut, 0x0011, 0x12)
    await RisingEdge(dut.clk)          # the write lands on the edge above; read one edge later
    assert int(dut.station_addr.value) == 0x1234
    assert await mem_read(dut, 0x0010) == 0x34
    assert await mem_read(dut, 0x0011) == 0x12


@cocotb.test()
async def test_read_latency_is_mem_lat(dut):
    await start(dut)
    samples = []

    async def watch():
        while True:
            await RisingEdge(dut.clk)
            samples.append((int(dut.mem_req.value), int(dut.mem_rvalid.value)))

    cocotb.start_soon(watch())
    dut.mem_req.value = 1
    dut.mem_addr.value = 0x0004
    await RisingEdge(dut.clk)
    dut.mem_req.value = 0
    for _ in range(5):
        await RisingEdge(dut.clk)
    req_at = samples.index((1, 0))
    valid_at = [i for i, (_, v) in enumerate(samples) if v]
    assert valid_at == [req_at + 2], f"rvalid at {valid_at}, request at {req_at}"
    assert int(dut.mem_rdata.value) == N_FMMU


@cocotb.test()
async def test_read_only_registers_ignore_writes(dut):
    await start(dut)
    ref = SlaveModel().regs
    for addr in (0x0000, 0x0004, 0x0006, 0x0007, 0x0012, 0x0110, 0x060D, 0x060F, 0x0805, 0x0807):
        await mem_write(dut, addr, 0xA5)
        assert await mem_read(dut, addr) == ref[addr], f"{addr:#06x} must stay read-only"
    await mem_write(dut, 0x0600, 0x77)           # writable neighbours still work
    assert await mem_read(dut, 0x0600) == 0x77


@cocotb.test()
async def test_write_past_register_space_ignored(dut):
    # Review Focus 5: accesses at or above 0x1000 belong to the RAM, not here.
    await start(dut)
    for addr in (0x1000, 0x1010, 0x2000, 0xFFFF):
        await mem_write(dut, addr, 0xFF)
    ref = SlaveModel().regs
    for addr in (0x0000, 0x0010, 0x0600, 0x0800, 0x0FFF):
        assert await mem_read(dut, addr) == ref[addr]


@cocotb.test()
async def test_fmmu_and_sm_config_outputs(dut):
    await start(dut)
    model = SlaveModel()
    rng = random.Random(5)
    for i in range(N_FMMU):
        for k in range(13):
            v = rng.randrange(256)
            await mem_write(dut, 0x0600 + 16 * i + k, v)
            model_write(model, 0x0600 + 16 * i + k, v)
    for i in range(N_SM):
        for k in range(8):
            v = rng.randrange(256)
            await mem_write(dut, 0x0800 + 8 * i + k, v)
            model_write(model, 0x0800 + 8 * i + k, v)
    await RisingEdge(dut.clk)
    fm, sm = int(dut.fmmu_cfg.value), int(dut.sm_cfg.value)
    for i in range(N_FMMU):
        for k in range(16):
            assert (fm >> (8 * (16 * i + k))) & 0xFF == model.regs[0x0600 + 16 * i + k]
    for i in range(N_SM):
        for k in range(8):
            assert (sm >> (8 * (8 * i + k))) & 0xFF == model.regs[0x0800 + 8 * i + k]


@cocotb.test()
async def test_random_access_matches_model(dut):
    await start(dut)
    model = SlaveModel()
    rng = random.Random(7)
    windows = [(0x0000, 0x0020), (0x0100, 0x0120), (0x0600, 0x0640), (0x0800, 0x0830), (0x0000, 0x1000)]
    for n in range(1500):
        lo, hi = rng.choice(windows)
        addr = rng.randrange(lo, hi)
        if rng.random() < 0.5:
            val = rng.randrange(256)
            await mem_write(dut, addr, val)
            model_write(model, addr, val)
        else:
            got = await mem_read(dut, addr)
            assert got == model.regs[addr], f"op {n}: read {addr:#06x} got {got:#04x}"
