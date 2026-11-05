"""board_flags: the ESC's FIFO flags kept sticky and brought into the 50 MHz clock."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, Timer


async def start(dut):
    cocotb.start_soon(Clock(dut.clk50, 20, unit="ns").start())
    cocotb.start_soon(Clock(dut.tx_clk, 40, unit="ns").start())
    for s in (dut.rx_overflow, dut.tx_overflow, dut.tx_underrun):
        s.value = 0
    dut.rst.value = 1
    await ClockCycles(dut.clk50, 4)
    dut.rst.value = 0
    await ClockCycles(dut.clk50, 4)


async def tx_pulse(dut):
    """tx_underrun high for exactly one tx_clk period, as mii_tx drives it."""
    await RisingEdge(dut.tx_clk)
    await Timer(1, unit="ns")
    dut.tx_underrun.value = 1
    await RisingEdge(dut.tx_clk)
    await Timer(1, unit="ns")
    dut.tx_underrun.value = 0


@cocotb.test()
async def test_one_tx_clk_underrun_pulse_is_kept(dut):
    await start(dut)
    await tx_pulse(dut)
    for n in range(3):
        await RisingEdge(dut.clk50)
        if int(dut.sticky.value) & 4:
            break
    else:
        raise AssertionError("sticky[2] not set within 3 clk50 cycles of the pulse")
    await ClockCycles(dut.clk50, 50)
    assert int(dut.sticky.value) == 0b100, f"sticky {int(dut.sticky.value):03b}"


@cocotb.test()
async def test_flags_clear_on_rst(dut):
    await start(dut)
    dut.rx_overflow.value = 1
    dut.tx_overflow.value = 1
    await tx_pulse(dut)
    await ClockCycles(dut.clk50, 6)
    assert int(dut.sticky.value) == 0b111
    dut.rx_overflow.value = 0             # esc_top clears its own sticky flags in reset
    dut.tx_overflow.value = 0
    dut.rst.value = 1
    await ClockCycles(dut.clk50, 2)
    assert int(dut.sticky.value) == 0
    dut.rst.value = 0
    await ClockCycles(dut.clk50, 10)
    assert int(dut.sticky.value) == 0


@cocotb.test()
async def test_each_flag_maps_to_its_bit(dut):
    await start(dut)
    for sig, bit in ((dut.rx_overflow, 0b001), (dut.tx_overflow, 0b010)):
        dut.rst.value = 1
        await ClockCycles(dut.clk50, 2)
        dut.rst.value = 0
        sig.value = 1
        await ClockCycles(dut.clk50, 6)
        assert int(dut.sticky.value) == bit, f"{sig._name}: sticky {int(dut.sticky.value):03b}"
        sig.value = 0
    dut.rst.value = 1
    await ClockCycles(dut.clk50, 2)
    dut.rst.value = 0
    await tx_pulse(dut)
    await ClockCycles(dut.clk50, 6)
    assert int(dut.sticky.value) == 0b100
