"""Traffic for the network simulation. Each scenario is a generator of Tx (frames with their
PHY-level settings) over a line of K model slaves at positions 0..K-1 and the RTL slave at
position K. Everything is drawn from the generator's own Random, so a seed reproduces the
traffic exactly, whatever the RTL answers."""
import os
import struct
import sys
import zlib
from dataclasses import dataclass, replace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "model"))

from ecat import CMD, ETHERTYPE, MAX_FRAME, Datagram, build_frame  # noqa: E402

SCENARIOS = ("startup", "cyclic", "random", "faults", "clocks", "chain", "replay")
FAULTS = ("rx_err", "crc", "runt", "oversize", "non_ethercat", "malformed", "truncated")
CHAIN_K = (0, 1, 7, 63)
DEFAULT_K = 3

NIBBLES_PER_US = 25
LBASE = 0x00010000                  # logical address of the first slave's process-data window
AREA = MAX_FRAME - 4 - 14 - 2       # datagram bytes that fit one frame (headers and WKC included)
ARMW, FRMW = 13, 14                 # commands the ESC does not implement
DIAG_BASE, DIAG_END = 0x0E00, 0x0F00
FMMU_BASE = 0x0600


@dataclass
class Tx:
    frame: bytes
    ifg: int = 24                   # idle nibbles after the frame (>= 24; 25,000 is 1 ms)
    er_nibble: int = -1             # rx_er on this data nibble, -1 for none
    fault: str = None               # one of FAULTS, or None for a good frame


def clock_config(scenario, seed):
    """CONFIG fields for one run. `clocks` covers the Ethernet limit of +-100 ppm, with the
    worst cases fixed on seeds 0 and 1 (RX fast and TX slow, then the reverse) and slow
    drift on the others; every other scenario gets static offsets within +-50 ppm."""
    import random
    rng = random.Random(f"clock:{scenario}:{seed}")
    phases = dict(rx_phase_ns=rng.uniform(0, 40), tx_phase_ns=rng.uniform(0, 40),
                  core_phase_ns=rng.uniform(0, 10))
    if scenario == "clocks":
        if seed % 4 == 0:
            rx, tx, drift = -100.0, 100.0, 0.0           # a shorter period is a faster clock
        elif seed % 4 == 1:
            rx, tx, drift = 100.0, -100.0, 0.0
        else:
            rx, tx, drift = rng.uniform(-100, 100), rng.uniform(-100, 100), rng.uniform(0, 20)
    else:
        rx, tx, drift = rng.uniform(-50, 50), rng.uniform(-50, 50), 0.0
    return dict(seed=seed, rx_ppm=rx, tx_ppm=tx, drift_ppm_per_s=drift, event_level=0, **phases)


class Net:
    """The line as the master sees it, and frame building on top of it."""

    def __init__(self, rng, k, ram_bytes=256, n_fmmu=3, n_sm=4):
        self.rng, self.k, self.ram, self.n_fmmu, self.n_sm = rng, k, ram_bytes, n_fmmu, n_sm
        self.idx = 0
        self.window = 8

    @staticmethod
    def station(p):
        return 0x1001 + p

    def dg(self, cmd, adp, ado, data, wkc=0):
        self.idx = (self.idx + 1) & 0xFF
        return Datagram(cmd, self.idx, (ado << 16) | (adp & 0xFFFF), bytes(data), wkc=wkc)

    def ldg(self, cmd, logical, data):
        self.idx = (self.idx + 1) & 0xFF
        return Datagram(cmd, self.idx, logical & 0xFFFFFFFF, bytes(data))

    @staticmethod
    def frame(dgs):
        dgs = [replace(d, more=i < len(dgs) - 1) for i, d in enumerate(dgs)]
        return build_frame(dgs)

    @staticmethod
    def size(dgs):
        return sum(12 + len(d.data) for d in dgs)

    def frames(self, dgs, ifg=24):
        """Datagrams packed greedily into as few frames as fit."""
        out, cur = [], []
        for d in dgs:
            if cur and self.size(cur + [d]) > AREA:
                out.append(Tx(self.frame(cur), ifg))
                cur = []
            cur.append(d)
        if cur:
            out.append(Tx(self.frame(cur), ifg))
        return out

    def rand(self, n):
        return bytes(self.rng.randrange(256) for _ in range(n))

    @staticmethod
    def fmmu_entry(logical, length, phys, ftype, act=1):
        return struct.pack("<IHBBHBBB3x", logical, length, 0, 7, phys, 0, ftype, act)

    def fmmu_layout(self, p, randomize):
        """FMMU registers of slave p. FMMU 0 maps its process-data window read-write;
        randomized layouts add overlapping, aliased and boundary windows."""
        w = self.window
        cfg = self.fmmu_entry(LBASE + p * w, w, 0x1000, 3)
        for _ in range(1, self.n_fmmu):
            if not randomize:
                cfg += bytes(16)
                continue
            r, ram = self.rng, self.ram
            length = r.randint(1, 32)
            phys = r.choice([0x1000 + r.randrange(ram), 0x1000 + ram - length,
                             0x1000 + ram - length + 1, 0x0FF0, 0x0D00, 0x1000])
            logical = r.choice([LBASE + p * w + r.randint(-4, w), LBASE + r.randrange(64),
                                r.randrange(1 << 32)])
            cfg += self.fmmu_entry(logical & 0xFFFFFFFF, length, phys & 0xFFFF, r.randint(0, 3),
                                   r.choice([0, 1, 1]))
        return cfg

    def sm_layout(self):
        cfg = b""
        for i in range(self.n_sm):
            cfg += struct.pack("<HHBBBB", 0x1000 + 16 * i, 16, self.rng.choice([0x26, 0x64, 0x20]),
                               0, 1, 0)
        return cfg

    def startup(self, randomize=False):
        """BRD count, auto-increment station addresses, check, FMMU and SM set-up, RAM clear."""
        k, r = self.k, self.rng
        if randomize:
            self.window = r.randint(1, max(1, min(32, 1400 // (k + 1))))
        else:
            self.window = max(1, min(8, 1400 // (k + 1)))
        out = self.frames([self.dg(CMD["BRD"], 0, 0x0000, bytes(2))])
        out += self.frames([self.dg(CMD["APWR"], -p, 0x0010, struct.pack("<H", self.station(p)))
                            for p in range(k + 1)])
        out += self.frames([self.dg(CMD["FPRD"], self.station(k), 0x0010, bytes(2))])
        out += self.frames([self.dg(CMD["FPWR"], self.station(p), 0x0600, self.fmmu_layout(p, randomize))
                            for p in range(k + 1)])
        out += self.frames([self.dg(CMD["FPWR"], self.station(p), 0x0800, self.sm_layout())
                            for p in range(k + 1)])
        out += self.frames([self.dg(CMD["BWR"], 0, 0x1000, bytes(self.ram))])
        return out

    def lrw(self, extra=()):
        n = (self.k + 1) * self.window
        return [self.ldg(CMD["LRW"], LBASE, self.rand(n))] + list(extra)

    def diag_read(self):
        """The DUT's 12 diag counters, addressed by position so it works whatever the
        traffic did to station addresses."""
        return Tx(self.frame([self.dg(CMD["APRD"], -self.k, 0x0E00, bytes(24))]))

    def random_datagram(self, room):
        r, k, ram = self.rng, self.k, self.ram
        cmd = r.choice(list(CMD.values()) + [ARMW, FRMW])
        n = r.randint(1, max(1, min(room - 12, 128)))
        wkc = r.choice([0, 0, 0, r.randrange(1 << 16)])
        if cmd in (CMD["LRD"], CMD["LWR"], CMD["LRW"]):
            span = (k + 1) * self.window
            return self.ldg(cmd, LBASE + r.randint(-32, span + 32), self.rand(n))
        ado = r.choice([r.randrange(0x1000), 0x1000 + r.randrange(ram), 0x1000 - r.randint(1, 16),
                        0x1000 + ram - n, 0x1000 + ram - n + 1, 0x0D00 + r.randrange(0x100),
                        0x0600 + r.randrange(16 * self.n_fmmu), 0x0800 + r.randrange(8 * self.n_sm),
                        0x0000, 0x0010])
        # The model has no diag block (0x0E00-0x0EFF; the final read checks it), and random
        # bytes written to the FMMUs could map one onto it: such accesses move to process RAM.
        fmmu_end = FMMU_BASE + 16 * self.n_fmmu
        writes = cmd not in (CMD["APRD"], CMD["FPRD"], CMD["BRD"], ARMW, FRMW)
        if (ado < DIAG_END and ado + n > DIAG_BASE) or (writes and ado < fmmu_end and ado + n > FMMU_BASE):
            ado = 0x1000 + r.randrange(ram)
        if cmd in (CMD["APRD"], CMD["APWR"], CMD["APRW"]):
            adp = -r.randint(-1, k + 1)
        elif cmd in (CMD["FPRD"], CMD["FPWR"], CMD["FPRW"], FRMW):
            adp = r.choice([self.station(r.randint(0, k)), r.randrange(1 << 16)])
        else:
            adp = r.randrange(1 << 16)
        return self.dg(cmd, adp, ado & 0xFFFF, self.rand(n), wkc=wkc)


def _startup(net):
    yield from net.startup(randomize=False)
    while True:
        yield from net.startup(randomize=True)
        for p in range(net.k + 1):
            yield Tx(net.frame([net.dg(CMD["FPRD"], net.station(p), 0x0600, bytes(16 * net.n_fmmu))]))


def _cyclic(net):
    """One LRW per cycle over every slave's window, with a status read every tenth cycle;
    in each block of 100 cycles, 5 at 1 ms, 15 at 250 us, the rest back to back."""
    yield from net.startup()
    c = 0
    while True:
        extra = [net.dg(CMD["FPRD"], net.station(net.k), 0x0010, bytes(2))] if c % 10 == 0 else []
        slot = c % 100
        ifg = 1000 * NIBBLES_PER_US if slot < 5 else (250 * NIBBLES_PER_US if slot < 20 else 24)
        yield Tx(net.frame(net.lrw(extra)), ifg)
        c += 1


def _random(net):
    yield from net.startup()
    r = net.rng
    while True:
        if r.random() < 0.05:              # re-program the DUT's FMMUs with a random layout
            yield Tx(net.frame([net.dg(CMD["FPWR"], -net.k, 0x0600, net.fmmu_layout(net.k, True))]))
            continue
        dgs = []
        for _ in range(r.randint(1, 6)):
            room = AREA - net.size(dgs)
            if room < 13:
                break
            dgs.append(net.random_datagram(room))
        yield Tx(net.frame(dgs))


def _fault(net, cls, good):
    """A frame carrying fault `cls`, made from the good frame `good` (FCS included)."""
    r = net.rng
    fcs = lambda body: body + zlib.crc32(body).to_bytes(4, "little")   # noqa: E731
    if cls == "rx_err":
        return Tx(good, er_nibble=r.randrange(2 * len(good)), fault=cls)
    if cls == "crc":
        return Tx(good[:-1] + bytes([good[-1] ^ 0xFF]), fault=cls)
    if cls == "runt":
        return Tx(fcs(good[:r.randint(16, 59)]), fault=cls)
    if cls == "oversize":
        # a NOP of n bytes makes a 32 + n byte frame: over 1518 from n = 1487
        big = net.frame([net.dg(CMD["NOP"], 0, 0, net.rand(r.randint(1487, 1560)))])
        return Tx(big, fault=cls)
    if cls == "non_ethercat":
        etype = r.choice([0x86DD, 0x88CC, 0x0800])
        return Tx(fcs(good[:12] + struct.pack(">H", etype) + net.rand(r.randint(46, 200))), fault=cls)
    if cls == "malformed":
        body = bytearray(good[:-4])
        word = struct.unpack_from("<H", body, 14)[0]
        struct.pack_into("<H", body, 14, (word & 0xF800) | ((word & 0x7FF) + r.randint(20, 200)) & 0x7FF)
        return Tx(fcs(bytes(body)), fault=cls)
    return Tx(good[:r.randint(5, len(good) - 1)], fault=cls)      # truncated


def _faults(net):
    """Every fourth frame carries a fault, the classes taking turns in a shuffled order;
    the frames between are good process-data frames."""
    yield from net.startup()
    r = net.rng
    order = []
    i = 0
    while True:
        good = net.frame(net.lrw())
        if i % 4 == 3:
            if not order:
                order = list(FAULTS)
                r.shuffle(order)
            yield _fault(net, order.pop(), good)
        else:
            yield Tx(good)
        i += 1


def _clocks(net):
    """Maximum-size frames back to back (an LRW padded with a NOP to 1518 bytes), with a
    short process-data frame every eighth."""
    yield from net.startup()
    c = 0
    while True:
        dgs = net.lrw()
        if c % 8:
            dgs.append(net.dg(CMD["NOP"], 0, 0, bytes(AREA - net.size(dgs) - 12)))
        yield Tx(net.frame(dgs))
        c += 1


GENERATORS = {"startup": _startup, "cyclic": _cyclic, "random": _random, "faults": _faults,
              "clocks": _clocks, "chain": _cyclic}
