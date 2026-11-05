"""phy_init against a clause 22 MDIO PHY model (mdio_phy.py).

run.py builds phy_init with CLK_DIV 4, RST_CYCLES 50, WAIT_CYCLES 50, POLL_CYCLES 200."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

from mdio_phy import MdioPhy

CLK_DIV, RST_CYCLES = 4, 50
IDLE, CONFIGURING, LINK_100FD, WRONG_LINK, MDIO_ERROR = range(5)
# One MDIO frame is 64 MDC periods of 2*CLK_DIV clocks; bring-up is reset, wait, five
# frames and a poll interval; this bound leaves room for several polls.
BRING_UP = 20 * 64 * 2 * CLK_DIV


async def start(dut):
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    dut.rst.value = 1
    dut.mdio_i.value = 1
    await ClockCycles(dut.clk, 5)
    dut.rst.value = 0


async def wait_state(dut, want, limit=BRING_UP):
    for _ in range(limit):
        await RisingEdge(dut.clk)
        if int(dut.state.value) == want:
            return
    raise AssertionError(f"state {int(dut.state.value)} after {limit} clocks, wanted {want}")


@cocotb.test()
async def test_brings_up_link_100fd(dut):
    phy = MdioPhy(dut)
    await start(dut)
    low = 0
    while int(dut.phy_rst_n.value) == 0:
        low += 1
        await RisingEdge(dut.clk)
    assert low >= RST_CYCLES, f"phy_rst_n low for {low} clocks"
    await wait_state(dut, LINK_100FD)
    assert int(dut.link_ok.value) == 1
    assert phy.writes[:3] == [(9, 0x0000), (4, 0x0101), (0, 0x9200)], phy.writes
    assert phy.reads[0] == 2, phy.reads


@cocotb.test()
async def test_resolved_1000_is_wrong_link(dut):
    MdioPhy(dut, speed=1000)
    await start(dut)
    await wait_state(dut, WRONG_LINK)
    assert int(dut.link_ok.value) == 0


@cocotb.test()
async def test_half_duplex_is_wrong_link(dut):
    MdioPhy(dut, full_duplex=False)
    await start(dut)
    await wait_state(dut, WRONG_LINK)
    assert int(dut.link_ok.value) == 0


@cocotb.test()
async def test_wrong_phy_id_is_mdio_error(dut):
    phy = MdioPhy(dut, phy_id1=0x1234)
    await start(dut)
    await wait_state(dut, MDIO_ERROR)
    await ClockCycles(dut.clk, 4 * 64 * 2 * CLK_DIV)
    assert int(dut.state.value) == MDIO_ERROR
    assert phy.writes == [], phy.writes


@cocotb.test()
async def test_no_turnaround_is_mdio_error(dut):
    phy = MdioPhy(dut, addr=3)
    await start(dut)
    await wait_state(dut, MDIO_ERROR)
    assert phy.writes == [] and phy.reads == []


@cocotb.test()
async def test_link_loss_returns_to_configuring(dut):
    phy = MdioPhy(dut)
    await start(dut)
    await wait_state(dut, LINK_100FD)
    phy.link = False
    await wait_state(dut, CONFIGURING)
    assert int(dut.link_ok.value) == 0
    phy.link = True
    await wait_state(dut, LINK_100FD)
    assert phy.writes.count((0, 0x9200)) == 1, "a lost link must not reset the PHY again"


@cocotb.test()
async def test_mdc_is_clk_over_2_clk_div(dut):
    MdioPhy(dut)
    await start(dut)
    await RisingEdge(dut.mdc)
    n = 0
    await RisingEdge(dut.clk)
    while True:
        n += 1
        prev = int(dut.mdc.value)
        await RisingEdge(dut.clk)
        if prev == 0 and int(dut.mdc.value) == 1:
            break
    assert n == 2 * CLK_DIV, f"MDC period {n} clocks"
