"""Reference model of the ESC frame core: the oracle for the RTL and its tests.

Spec-derived, NOT VERIFIED AGAINST REAL HARDWARE until the capture replay
(Task 11) runs on recordings from a real master. Sources used:
  * Beckhoff TwinCAT I/O documentation, "working counter": a successful read
    adds 1, a successful write adds 1, a read-write that does both adds 3
    (read +1, write +2).
  * Beckhoff ESC register map: FMMU registers from 0x0600 (16 bytes each),
    SyncManager registers from 0x0800 (8 bytes each), configured station
    address at 0x0010, process RAM from 0x1000.
Every rule marked "verify" below must be confirmed against ETG.1000 / the ESC
datasheet when those are available.
"""
import struct
import zlib
from dataclasses import dataclass, replace

ETHERTYPE = 0x88A4
CMD = {"NOP": 0, "APRD": 1, "APWR": 2, "APRW": 3, "FPRD": 4, "FPWR": 5, "FPRW": 6,
       "BRD": 7, "BWR": 8, "BRW": 9, "LRD": 10, "LWR": 11, "LRW": 12}
NAME = {v: k for k, v in CMD.items()}

REG_SPACE = 0x1000          # registers below, process RAM from here
FMMU_BASE, FMMU_SIZE = 0x0600, 16
SM_BASE, SM_SIZE = 0x0800, 8
MIN_FRAME, MAX_FRAME = 64, 1518     # on the wire, including the 4-byte FCS
DEFAULT_SRC = bytes([0x01, 0x01, 0x05, 0x01, 0x00, 0x00])


class FrameError(ValueError):
    """The bytes are not a well-formed EtherCAT frame."""


@dataclass
class Datagram:
    cmd: int
    idx: int
    address: int
    data: bytes
    irq: int = 0
    more: bool = False
    circ: bool = False
    wkc: int = 0


@dataclass
class Counters:
    """Mirrors the diag block's counters (Task 10)."""
    frames: int = 0
    rx_errors: int = 0
    crc_failures: int = 0
    runts: int = 0
    oversize: int = 0
    non_ethercat: int = 0
    malformed: int = 0
    datagrams: int = 0
    datagrams_addressed: int = 0
    wkc_increments: int = 0


def _fcs(body):
    return zlib.crc32(body).to_bytes(4, "little")


def build_frame(datagrams, dst=b"\xff" * 6, src=DEFAULT_SRC, with_fcs=True):
    area = b""
    for d in datagrams:
        word = len(d.data) | (0x4000 if d.circ else 0) | (0x8000 if d.more else 0)
        area += struct.pack("<BBIHH", d.cmd, d.idx, d.address, word, d.irq)
        area += d.data + struct.pack("<H", d.wkc)
    body = dst + src + struct.pack(">H", ETHERTYPE)
    body += struct.pack("<H", len(area) | (1 << 12)) + area
    body = body.ljust(MIN_FRAME - 4, b"\x00")
    return body + _fcs(body) if with_fcs else body


def _walk(raw):
    """Datagrams of a frame that has already passed the Ethernet checks."""
    word, = struct.unpack_from("<H", raw, 14)
    length, ptype = word & 0x7FF, word >> 12
    end = 16 + length
    if ptype != 1 or end > len(raw) - 4:
        raise FrameError("EtherCAT header disagrees with the frame")
    out, pos = [], 16
    while pos < end:
        if pos + 10 > end:
            raise FrameError("truncated datagram header")
        cmd, idx, addr, lw, irq = struct.unpack_from("<BBIHH", raw, pos)
        n = lw & 0x7FF
        if pos + 10 + n + 2 > end:
            raise FrameError("datagram runs past the EtherCAT area")
        data = bytes(raw[pos + 10:pos + 10 + n])
        wkc, = struct.unpack_from("<H", raw, pos + 10 + n)
        out.append(Datagram(cmd, idx, addr, data, irq, bool(lw & 0x8000), bool(lw & 0x4000), wkc))
        pos += 10 + n + 2
    return out


def parse_frame(raw, check_fcs=True):
    if len(raw) < MIN_FRAME or len(raw) > MAX_FRAME:
        raise FrameError("frame length out of range")
    if struct.unpack_from(">H", raw, 12)[0] != ETHERTYPE:
        raise FrameError("not an EtherCAT frame")
    if check_fcs and _fcs(raw[:-4]) != raw[-4:]:
        raise FrameError("bad FCS")
    return _walk(raw)


class SlaveModel:
    def __init__(self, station_address=0, n_fmmu=3, ram_bytes=1024, n_sm=4,
                 commit_on_bad_fcs=False):
        self.n_fmmu, self.n_sm = n_fmmu, n_sm
        self.commit_on_bad_fcs = commit_on_bad_fcs
        self.regs = bytearray(REG_SPACE)
        self.ram = bytearray(ram_bytes)
        self.counters = Counters()
        # Identification mirrors an ET1100-class ESC (verify): type, revision.
        self.regs[0x0000], self.regs[0x0001] = 0x11, 0x02
        self.regs[0x0004], self.regs[0x0005] = n_fmmu, n_sm
        self.regs[0x0006] = ram_bytes // 1024
        self.regs[0x0007] = 0x03
        self.station_address = station_address

    @property
    def station_address(self):
        return struct.unpack_from("<H", self.regs, 0x0010)[0]

    @station_address.setter
    def station_address(self, value):
        struct.pack_into("<H", self.regs, 0x0010, value)

    # --- memory ---------------------------------------------------------
    def _span(self, addr, n):
        """(buffer, offset) if the whole range lies in registers or in RAM; an
        access that crosses the boundary or the end of RAM fails entirely."""
        if addr + n <= REG_SPACE:
            return self.regs, addr
        if addr >= REG_SPACE and addr - REG_SPACE + n <= len(self.ram):
            return self.ram, addr - REG_SPACE
        return None

    def _writable(self, buf, offset):
        if buf is self.ram:
            return True
        if FMMU_BASE <= offset < FMMU_BASE + FMMU_SIZE * self.n_fmmu:
            return (offset - FMMU_BASE) % FMMU_SIZE <= 12        # bytes 13..15 reserved
        if SM_BASE <= offset < SM_BASE + SM_SIZE * self.n_sm:
            return (offset - SM_BASE) % SM_SIZE not in (5, 7)    # status, PDI activate
        return 0x0010 <= offset < 0x0012

    def _write(self, span, data):
        buf, off = span
        for i, b in enumerate(data):
            if self._writable(buf, off + i):
                buf[off + i] = b

    @staticmethod
    def _read(span, n):
        buf, off = span
        return bytes(buf[off:off + n])

    # --- datagram execution ------------------------------------------------
    def _execute(self, dg):
        c = self.counters
        c.datagrams += 1
        name = NAME.get(dg.cmd, "NOP")
        out = replace(dg)
        if name == "NOP":
            return out
        op = name[-2:]
        reads, writes = op in ("RD", "RW"), op in ("WR", "RW")
        read_hit = write_hit = False
        data = bytearray(dg.data)
        n = len(dg.data)

        if name[0] != "L":
            adp, ado = dg.address & 0xFFFF, dg.address >> 16
            if name.startswith("AP"):
                addressed = adp == 0
            elif name.startswith("FP"):
                addressed = adp == self.station_address
            else:                               # broadcast
                addressed = True
            # Position field is incremented by every slave on AP and B (verify).
            if name[0] == "A" or name[0] == "B":
                out.address = (dg.address & 0xFFFF0000) | ((adp + 1) & 0xFFFF)
            if addressed:
                c.datagrams_addressed += 1
                span = self._span(ado, n)
                if span is not None:
                    old = self._read(span, n)
                    if reads:
                        # Broadcast read ORs each slave's data into the frame (verify).
                        data = bytearray(o | d for o, d in zip(old, dg.data)) \
                            if name[0] == "B" else bytearray(old)
                        read_hit = True
                    if writes:
                        self._write(span, dg.data)
                        write_hit = True
        else:
            hit_any = False
            for i in range(self.n_fmmu):
                base = FMMU_BASE + FMMU_SIZE * i
                start, length, _sb, _eb, phys, _pb, ftype, act = \
                    struct.unpack_from("<IHBBHBBB", self.regs, base)
                lo, hi = max(dg.address, start), min(dg.address + n, start + length)
                if not (act & 1) or length == 0 or lo >= hi:
                    continue
                span = self._span(phys + (lo - start), hi - lo)
                if span is None:
                    continue
                a, b = lo - dg.address, hi - dg.address
                if reads and ftype & 1:
                    data[a:b] = self._read(span, hi - lo)
                    read_hit = hit_any = True
                if writes and ftype & 2:
                    self._write(span, dg.data[a:b])
                    write_hit = hit_any = True
            if hit_any:
                c.datagrams_addressed += 1

        inc = (1 if read_hit else 0) + ((2 if reads and writes else 1) if write_hit else 0)
        c.wkc_increments += inc
        out.data = bytes(data)
        out.wkc = (dg.wkc + inc) & 0xFFFF
        return out

    # --- frame ----------------------------------------------------------
    def process(self, raw):
        """Response frame (with regenerated FCS), or None if the frame is dropped."""
        c = self.counters
        c.frames += 1
        if len(raw) < MIN_FRAME:
            c.runts += 1
            return None
        if len(raw) > MAX_FRAME:
            c.oversize += 1
            return None
        if struct.unpack_from(">H", raw, 12)[0] != ETHERTYPE:
            c.non_ethercat += 1
            return None
        fcs_ok = _fcs(raw[:-4]) == raw[-4:]
        if not fcs_ok:
            c.crc_failures += 1
            if not self.commit_on_bad_fcs:
                return None
        try:
            dgs = _walk(raw)
        except FrameError:
            c.malformed += 1
            return None
        out = [self._execute(d) for d in dgs]
        rebuilt = bytearray(raw[:-4])
        pos = 16
        for o in out:
            word = len(o.data) | (0x4000 if o.circ else 0) | (0x8000 if o.more else 0)
            struct.pack_into("<BBIHH", rebuilt, pos, o.cmd, o.idx, o.address, word, o.irq)
            pos += 10
            rebuilt[pos:pos + len(o.data)] = o.data
            pos += len(o.data)
            struct.pack_into("<H", rebuilt, pos, o.wkc)
            pos += 2
        fcs = _fcs(bytes(rebuilt))
        if not fcs_ok:                    # a corrupted frame stays corrupted downstream
            fcs = bytes(b ^ 0xFF for b in fcs)
        return bytes(rebuilt) + fcs
