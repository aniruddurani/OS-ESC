"""addsub_cs as a 35-bit subtractor (the fmmu_plan datagram-end compare)."""
import cocotb

from addsub_check import check_stream

W = 35                       # must equal the W set for this module in run.py
SUB = 1                      # ... and SUB


@cocotb.test()
async def test_addsub_cs_sub(dut):
    await check_stream(dut, W, SUB)
