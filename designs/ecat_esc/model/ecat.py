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


def _walk_partial(raw):
    """([(position, Datagram)], malformed): the datagrams that fit, in order, and
    whether the structure is broken. Mirrors rtl/frame_parse.v, which sees the FCS
    and padding bytes as frame bytes too: the EtherCAT length must be at least one
    datagram (12 bytes), each datagram must fit what is left of that length, and
    the frame must not end inside a header or datagram."""
    word, = struct.unpack_from("<H", raw, 14)
    length, ptype = word & 0x7FF, word >> 12
    if ptype != 1 or length < 12:
        return [], True
    out, pos, remain = [], 16, length
    while remain:
        if remain < 12 or pos + 10 > len(raw):
            return out, True
        cmd, idx, addr, lw, irq = struct.unpack_from("<BBIHH", raw, pos)
        n = lw & 0x7FF
        if remain - 10 < n + 2 or pos + 10 + n + 2 > len(raw):
            return out, True
        data = bytes(raw[pos + 10:pos + 10 + n])
        wkc, = struct.unpack_from("<H", raw, pos + 10 + n)
        out.append((pos, Datagram(cmd, idx, addr, data, irq, bool(lw & 0x8000),
                                  bool(lw & 0x4000), wkc)))
        pos += 12 + n
        remain -= 12 + n
    return out, False


def _walk(raw):
    """Datagrams of a well-formed frame; FrameError if the structure is broken."""
    items, malformed = _walk_partial(raw)
    if malformed:
        raise FrameError("EtherCAT header or datagrams disagree with the frame")
    return [d for _, d in items]


def parse_frame(raw, check_fcs=True):
    if len(raw) < MIN_FRAME or len(raw) > MAX_FRAME:
        raise FrameError("frame length out of range")
    if struct.unpack_from(">H", raw, 12)[0] != ETHERTYPE:
        raise FrameError("not an EtherCAT frame")
    if check_fcs and _fcs(raw[:-4]) != raw[-4:]:
        raise FrameError("bad FCS")
    return _walk(raw)


class SlaveModel:
    def __init__(self, station_address=0, n_fmmu=3, ram_bytes=1024, n_sm=4):
        self.n_fmmu, self.n_sm = n_fmmu, n_sm
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
            took_r, took_w = [False] * n, [False] * n   # lowest FMMU owns each byte
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
                    chunk = self._read(span, hi - lo)
                    for k in range(a, b):
                        if not took_r[k]:
                            data[k], took_r[k] = chunk[k - a], True
                    read_hit = hit_any = True
                if writes and ftype & 2:
                    for k in range(a, b):
                        if not took_w[k]:
                            self._write((span[0], span[1] + k - a), dg.data[k:k + 1])
                            took_w[k] = True
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
        """The frame as this ESC sends it on, FCS included, or None when the input
        is too short (under 5 bytes) to produce any output.

        Cut-through behaviour: datagrams execute as the bytes pass, so a frame that
        turns out bad (wrong FCS, runt, oversize, not EtherCAT, malformed) cannot
        be held back. It is forwarded with a deliberately wrong FCS, so the next
        device and the master discard it, and the cause is counted. Datagrams that
        completed before a malformation have already run."""
        c = self.counters
        c.frames += 1
        if len(raw) < 5:
            c.runts += 1
            return None
        poison = False
        if len(raw) < MIN_FRAME:
            c.runts += 1
            poison = True
        if len(raw) > MAX_FRAME:
            c.oversize += 1
            poison = True
        if _fcs(raw[:-4]) != raw[-4:]:
            c.crc_failures += 1
            poison = True
        body = bytearray(raw[:-4])
        if len(raw) >= 14:
            if struct.unpack_from(">H", raw, 12)[0] != ETHERTYPE:
                c.non_ethercat += 1
                poison = True
            else:
                items, malformed = _walk_partial(raw) if len(raw) >= 16 else ([], True)
                if malformed:
                    c.malformed += 1
                    poison = True
                for pos, dg in items:
                    o = self._execute(dg)
                    word = len(o.data) | (0x4000 if o.circ else 0) | (0x8000 if o.more else 0)
                    struct.pack_into("<BBIHH", body, pos, o.cmd, o.idx, o.address, word, o.irq)
                    body[pos + 10:pos + 10 + len(o.data)] = o.data
                    struct.pack_into("<H", body, pos + 10 + len(o.data), o.wkc)
        fcs = _fcs(bytes(body))
        if poison:
            fcs = bytes(b ^ 0xFF for b in fcs)
        return bytes(body) + fcs
