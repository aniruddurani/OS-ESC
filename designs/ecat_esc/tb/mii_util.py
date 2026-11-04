"""Helpers shared by the MII tests: nibble driving and a stream monitor."""
import cocotb
from cocotb.triggers import RisingEdge

# Seven preamble bytes 0x55 then the SFD 0xD5, low nibble first on MII.
PREAMBLE = [0x5] * 15 + [0xD]


def nibbles(data):
    for b in data:
        yield b & 0xF
        yield b >> 4


class StreamMonitor:
    """Collects frames from a `valid/data/first/last/err` byte stream.

    `prefix` selects signals such as `rx_valid`; frames holds (bytes, err)."""

    def __init__(self, dut, clk, prefix=""):
        self.sig = {n: getattr(dut, prefix + n) for n in ("valid", "data", "first", "last", "err")}
        self.clk = clk
        self.frames = []
        self.bad_framing = 0
        self._cur = None
        cocotb.start_soon(self._run())

    async def _run(self):
        s = self.sig
        while True:
            await RisingEdge(self.clk)
            if int(s["valid"].value) != 1:
                continue
            if int(s["first"].value) == 1:
                if self._cur is not None:
                    self.bad_framing += 1       # a frame started before the last one ended
                self._cur = bytearray()
            elif self._cur is None:
                self.bad_framing += 1           # byte outside any frame
                continue
            self._cur.append(int(s["data"].value))
            if int(s["last"].value) == 1:
                self.frames.append((bytes(self._cur), int(s["err"].value)))
                self._cur = None


async def idle(dut_clk, n):
    for _ in range(n):
        await RisingEdge(dut_clk)
