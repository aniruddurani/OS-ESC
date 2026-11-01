"""Drive and watch the core's byte-stream bus (valid/data/first/last/err)."""
import cocotb
from cocotb.triggers import RisingEdge


class StreamDriver:
    """Sends frames as bytes with random idle gaps, like a line-rate source."""

    def __init__(self, dut, clk, rng, gap=(3, 9), prefix=""):
        self.sig = {n: getattr(dut, prefix + n) for n in ("valid", "data", "first", "last", "err")}
        self.clk, self.rng, self.gap = clk, rng, gap
        self.bytes_sent = 0
        for s in self.sig.values():
            s.value = 0

    async def send(self, frame, err=0):
        s = self.sig
        for i, b in enumerate(frame):
            s["valid"].value = 1
            s["data"].value = b
            s["first"].value = 1 if i == 0 else 0
            s["last"].value = 1 if i == len(frame) - 1 else 0
            s["err"].value = err if i == len(frame) - 1 else 0
            await RisingEdge(self.clk)
            self.bytes_sent += 1
            s["valid"].value = 0
            s["first"].value = 0
            s["last"].value = 0
            s["err"].value = 0
            for _ in range(self.rng.randint(*self.gap) - 1):
                await RisingEdge(self.clk)

    async def idle(self, n):
        for _ in range(n):
            await RisingEdge(self.clk)


class OutputLog:
    """Samples chosen output signals on every clock edge into a list of dicts."""

    def __init__(self, dut, clk, names):
        self.sigs = {n: getattr(dut, n) for n in names}
        self.clk = clk
        self.rows = []
        cocotb.start_soon(self._run())

    async def _run(self):
        while True:
            await RisingEdge(self.clk)
            self.rows.append({n: int(s.value) for n, s in self.sigs.items()})
