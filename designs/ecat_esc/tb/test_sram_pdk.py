"""The PDK's own macro verilog (not the behavioural model) against the same reference as
test_sram_model.py. run.py builds this module with the PDK file in place of tb/models."""
import cocotb

from test_sram_model import check_random


@cocotb.test()
async def test_sram_pdk_random(dut):
    await check_random(dut, 7)
