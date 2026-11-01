"""Clock helpers for the clock-crossing tests: any period, a ppm offset, a phase."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import Timer

MII_PERIOD_NS = 40.0     # 25 MHz rx_clk / tx_clk
CORE_PERIOD_NS = 10.0    # 100 MHz core clock


async def _delayed(sig, period_ps, phase_ps):
    if phase_ps:
        await Timer(phase_ps, unit="ps")
    await Clock(sig, period_ps, unit="ps").start()


def start_clock(sig, period_ns, ppm=0.0, phase_ns=0.0):
    """Start `sig` toggling with the given period, scaled by (1 + ppm*1e-6) and
    delayed by `phase_ns`. The period is rounded to an even number of ps."""
    period_ps = 2 * round(period_ns * 1000 * (1 + ppm * 1e-6) / 2)
    cocotb.start_soon(_delayed(sig, period_ps, round(phase_ns * 1000)))


def start_clocks(dut, rx_ppm=0.0, tx_ppm=0.0, phases=(0.0, 0.0, 0.0)):
    """rx_clk and tx_clk at 25 MHz, clk at 100 MHz; phases in ns as (rx, tx, core)."""
    start_clock(dut.rx_clk, MII_PERIOD_NS, rx_ppm, phases[0])
    start_clock(dut.tx_clk, MII_PERIOD_NS, tx_ppm, phases[1])
    start_clock(dut.clk, CORE_PERIOD_NS, 0.0, phases[2])
