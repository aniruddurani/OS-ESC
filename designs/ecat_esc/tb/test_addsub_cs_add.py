"""addsub_cs as a 17-bit adder (the fmmu_plan physical start)."""
import cocotb

from addsub_check import check_stream

W = 17                       # must equal the W set for this module in run.py
SUB = 0                      # ... and SUB


@cocotb.test()
async def test_addsub_cs_add(dut):
    await check_stream(dut, W, SUB)
