"""A clause 22 MDIO PHY for the phy_init tests: decodes frames from the controller's
mdio_o/mdio_oe on rising MDC, answers reads at its address, and records writes.

The line is the PHY's drive when it drives, else the controller's output when it
drives, else 1 (the board's pull-up).

reg 17 (88E1111 PHY-specific status) is computed: bit 11 resolved, bit 10 link,
bits 15:14 speed (00 10, 01 100, 10 1000), bit 13 full duplex. The link resolves only
after a write to reg 0 that restarts auto-negotiation (bit 9) or resets the PHY (bit 15).
"""
import cocotb
from cocotb.triggers import RisingEdge

SPEED_CODE = {10: 0b00, 100: 0b01, 1000: 0b10}


class MdioPhy:
    def __init__(self, dut, addr=16, phy_id1=0x0141, speed=100, full_duplex=True, link=True):
        self.dut = dut
        self.addr = addr
        self.regs = {2: phy_id1}
        self.speed = speed
        self.full_duplex = full_duplex
        self.link = link
        self.restarted = False
        self.writes = []                  # [(reg, value)] in order
        self.reads = []                   # [reg] in order
        self._drive = None                # bit the PHY drives on the line, or None
        dut.mdio_i.value = 1
        cocotb.start_soon(self._run())

    def reg17(self):
        if not self.restarted:
            return 0
        v = (SPEED_CODE[self.speed] << 14) | (int(self.full_duplex) << 13) | (1 << 11)
        return v | (int(self.link) << 10)

    def _line(self):
        if self._drive is not None:
            return self._drive
        if int(self.dut.mdio_oe.value):
            return int(self.dut.mdio_o.value)
        return 1

    async def _bit(self):
        await RisingEdge(self.dut.mdc)
        return self._line()

    async def _bits(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | await self._bit()
        return v

    async def _run(self):
        ones = 0
        while True:
            b = await self._bit()
            if b:
                ones += 1
                continue
            if ones < 32:                 # a 0 without a full preamble: not a frame
                ones = 0
                continue
            ones = 0
            if await self._bit() != 1:    # ST = 01 (the 0 was just seen)
                continue
            op = await self._bits(2)
            addr = await self._bits(5)
            reg = await self._bits(5)
            if op == 0b01:                # write: TA 10, then 16 data bits
                await self._bits(2)
                value = await self._bits(16)
                if addr == self.addr:
                    self._write(reg, value)
            elif op == 0b10:              # read: TA Z0, then 16 data bits from the PHY
                await self._bit()         # first TA bit: nobody drives
                if addr != self.addr:
                    continue
                self.reads.append(reg)
                value = self.reg17() if reg == 17 else self.regs.get(reg, 0)
                self._set(0)              # second TA bit
                await self._bit()
                for i in range(15, -1, -1):
                    self._set((value >> i) & 1)
                    await self._bit()
                self._set(None)

    def _set(self, bit):
        """The controller samples only while it does not drive, so mdio_i is the PHY's
        bit, or the pull-up."""
        self._drive = bit
        self.dut.mdio_i.value = 1 if bit is None else bit

    def _write(self, reg, value):
        self.writes.append((reg, value))
        self.regs[reg] = value
        if reg == 0 and value & 0x8200:
            self.restarted = True
