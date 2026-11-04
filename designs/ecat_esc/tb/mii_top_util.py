"""MII-level helpers for esc_top tests: drive frames onto the receive pins and decode
what the transmit pins send."""
import cocotb
from cocotb.triggers import RisingEdge

PREAMBLE = [0x5] * 15 + [0xD]       # 7 bytes 0x55 then SFD 0xD5, low nibble first


async def drive_frame(dut, frame, ifg_nibbles=24, er_at=None):
    """Send `frame` (FCS included) on the MII receive pins, then idle for the gap."""
    nibs = list(PREAMBLE)
    for b in frame:
        nibs += [b & 0xF, b >> 4]
    for i, n in enumerate(nibs):
        dut.rx_dv.value = 1
        dut.rxd.value = n
        dut.rx_er.value = 1 if i == er_at else 0
        await RisingEdge(dut.rx_clk)
    dut.rx_dv.value = 0
    dut.rx_er.value = 0
    dut.rxd.value = 0
    for _ in range(ifg_nibbles):
        await RisingEdge(dut.rx_clk)


class TxMonitor:
    """Decodes frames from tx_en/txd on tx_clk into byte strings (FCS included)."""

    def __init__(self, dut):
        self.dut = dut
        self.frames = []
        self.bad = 0
        cocotb.start_soon(self._run())

    async def _run(self):
        nibs = []
        while True:
            await RisingEdge(self.dut.tx_clk)
            if int(self.dut.tx_en.value) == 1:
                nibs.append(int(self.dut.txd.value))
            elif nibs:
                self._finish(nibs)
                nibs = []

    def _finish(self, nibs):
        # preamble nibbles 0x5 then the SFD nibble 0xD, then data low nibble first
        try:
            sfd = nibs.index(0xD)
        except ValueError:
            self.bad += 1
            return
        if sfd < 2 or any(n != 0x5 for n in nibs[:sfd]) or (len(nibs) - sfd - 1) % 2:
            self.bad += 1
            return
        body = nibs[sfd + 1:]
        self.frames.append(bytes(body[i] | (body[i + 1] << 4) for i in range(0, len(body), 2)))
