"""The behavioural macro model against a reference of the macro's port-0 behaviour.
The same traffic runs against the PDK's own verilog in test_sram_pdk.py, so the two
models are tied to one reference and so to each other.

A request set up before rising edge k is latched at k; a read's data is on dout0 after
the falling edge that follows, and sampled at edge k + 1."""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


async def check_random(dut, seed, cycles=3000):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    rng = random.Random(seed)
    ref = {}
    dut.csb0.value = 1
    dut.web0.value = 1
    dut.addr0.value = 0
    dut.din0.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    pending = None            # (addr) of a read latched at the previous edge
    reads = 0
    for _ in range(cycles):
        op = rng.choice(["idle", "read", "write", "write", "read"])
        addr = rng.randrange(48) if rng.random() < 0.8 else rng.randrange(1024)
        val = rng.randrange(256)
        dut.csb0.value = 0 if op != "idle" else 1
        dut.web0.value = 0 if op == "write" else 1
        dut.addr0.value = addr
        dut.din0.value = val
        await RisingEdge(dut.clk)                # latches this request; shows last cycle's read
        if pending is not None:
            got = int(dut.dout0.value)
            assert got == pending[1], f"read of {pending[0]} gave {got:#04x}, expected {pending[1]:#04x}"
            reads += 1
        # the request just latched runs on the falling edge after this edge
        if op == "write":
            ref[addr] = val
            pending = None
        elif op == "read":
            pending = (addr, ref.get(addr))
        else:
            pending = None
        if pending is not None and pending[1] is None:
            pending = None                       # never-written location: contents unknown
    assert reads > 200, f"only {reads} reads checked"


@cocotb.test()
async def test_sram_model_random(dut):
    await check_random(dut, 7)
