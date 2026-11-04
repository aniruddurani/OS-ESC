"""Checks the RTL's replies against the model, frame by frame.

A reply belongs to the most recent frame whose transmission had started when the reply
started: the ESC forwards cut-through, so a reply starts within a few microseconds of its
frame, before the next frame (at least 6.7 us later) begins. A missing reply therefore
leaves the replies after it on their own frames."""
import struct
import zlib

MAX_FRAME_PS = (1518 + 8) * 2 * 40_000      # longest frame on the wire, preamble included
LATENCY_PS = 2_000_000                      # bound on first byte in to first byte out
ALLOWANCE_PS = 2 * MAX_FRAME_PS + LATENCY_PS

# STATUS flag bits (proto.FLAG)
LATE_BIT = 8

# injected fault class -> diag counter that must have counted it
FAULT_COUNTER = {"rx_err": "rx_errors", "crc": "crc_failures", "runt": "runts",
                 "oversize": "oversize", "non_ethercat": "non_ethercat", "malformed": "malformed"}


DIAG_COUNTERS = ("frames", "rx_errors", "crc_failures", "runts", "oversize", "non_ethercat",
                 "malformed", "datagrams", "datagrams_addressed", "wkc_increments")
DIAG_DATA = 26                  # first datagram's data in a frame (Ethernet 14 + EtherCAT 2 + header 10)


def saturate(n, cnt_w=16):
    return min(n, (1 << cnt_w) - 1)


def _fcs(body):
    return zlib.crc32(body).to_bytes(4, "little")


def diag_reply(expected, counters, trace_len, cnt_w=16):
    """The model has no diag block, so the reply it predicts for a read of 0x0E00..0x0E17
    holds zeros. Put in the counters as they were before that frame (`counters`, a model
    Counters) and the trace count; the latency slot (10) is the RTL's own measurement and is
    taken from the reply. Returns (expected frame, fill range for Checker.expect)."""
    vals = [saturate(getattr(counters, n), cnt_w) for n in DIAG_COUNTERS] + [0, trace_len & 0xFFFF]
    poisoned = expected[-4:] != _fcs(expected[:-4])
    body = expected[:DIAG_DATA] + struct.pack("<12H", *vals) + expected[DIAG_DATA + 24:-4]
    fcs = _fcs(body)
    if poisoned:
        fcs = bytes(b ^ 0xFF for b in fcs)
    return body + fcs, (DIAG_DATA + 20, DIAG_DATA + 22)


def fault_evidence(injected, diag, require_all=False, cnt_w=16):
    """An error per fault class the ESC counted fewer times than it was injected (one frame
    can carry several faults, so a counter may be higher), and, with `require_all`, per class
    never injected."""
    errors = []
    for cls, counter in FAULT_COUNTER.items():
        n = injected.get(cls, 0)
        got = diag.get(counter, 0)
        if got < saturate(n, cnt_w):
            errors.append(f"{cls}: injected {n}, diag {counter} = {got}")
        if require_all and n == 0:
            errors.append(f"{cls}: never injected")
    return errors


class Checker:
    def __init__(self):
        self._frames = {}        # index -> {"expected", "dut_input", "t_start", "answered"}
        self._order = []         # indices with a known start time, in start order
        self.failures = []
        self.metrics = dict(sent=0, replies=0, mismatches=0, missing=0, unexpected=0,
                            flags_seen=0, late=0, latency_max_ps=0)

    def expect(self, index, dut_input, expected, fill=None):
        """`fill` = (start, end): bytes the reply supplies (the diag latency slot); the
        FCS is recomputed over the result before comparing."""
        self._frames[index] = dict(expected=expected, dut_input=dut_input, t_start=None,
                                   answered=False, fill=fill)
        self.metrics["sent"] += 1

    @staticmethod
    def _filled(f, got):
        exp, fill = f["expected"], f["fill"]
        if fill is None or len(got) != len(exp):
            return exp
        s, e = fill
        body = exp[:s] + got[s:e] + exp[e:-4]
        fcs = _fcs(body)
        if exp[-4:] != _fcs(exp[:-4]):
            fcs = bytes(b ^ 0xFF for b in fcs)
        return body + fcs

    def on_sent(self, sent):
        f = self._frames.get(sent["index"])
        if f is None:
            return
        f["t_start"] = sent["t_start_ps"]
        self._order.append(sent["index"])

    def _fail(self, index, kind, expected=None, got=None):
        self.metrics[{"mismatch": "mismatches"}.get(kind, kind)] += 1
        self.failures.append(dict(index=index, kind=kind,
                                  expected=expected.hex() if expected is not None else None,
                                  got=got.hex() if got is not None else None))

    def _retire(self, before_ps):
        """Frames that started before `before_ps` - ALLOWANCE_PS and never got a reply."""
        while self._order:
            i = self._order[0]
            f = self._frames[i]
            if f["t_start"] + ALLOWANCE_PS > before_ps:
                break
            self._order.pop(0)
            if not f["answered"] and f["expected"] is not None:
                self._fail(i, "missing", f["expected"])
            del self._frames[i]

    def on_reply(self, reply):
        """Checks one reply; returns the index of the frame it answers, or None."""
        self.metrics["replies"] += 1
        t, got = reply["t_start_ps"], reply["data"]
        owner = None
        for i in self._order:                     # most recent frame started at or before t
            if self._frames[i]["t_start"] <= t:
                owner = i
            else:
                break
        if owner is None:
            self._fail(None, "unexpected", None, got)
            return None
        # frames before the owner that are still waiting will not be answered now
        for i in list(self._order):
            if i == owner:
                break
            f = self._frames[i]
            if not f["answered"] and f["expected"] is not None:
                self._fail(i, "missing", f["expected"])
            f["answered"] = True
        f = self._frames[owner]
        if f["answered"] or f["expected"] is None:
            self._fail(owner, "unexpected", f["expected"], got)
            return None
        f["answered"] = True
        self.metrics["latency_max_ps"] = max(self.metrics["latency_max_ps"], t - f["t_start"])
        exp = self._filled(f, got)
        if got != exp:
            self._fail(owner, "mismatch", exp, got)
        self._retire(t)
        return owner

    def on_status(self, status):
        self.metrics["flags_seen"] |= status["seen"]
        if status["seen"] & LATE_BIT:
            self.metrics["late"] = 1

    def close(self, t_now_ps):
        """End of run: every frame still waiting is missing."""
        self._retire(t_now_ps + ALLOWANCE_PS + 1)
        for i, f in list(self._frames.items()):
            if not f["answered"] and f["expected"] is not None:
                self._fail(i, "missing", f["expected"])
        self._frames.clear()
        self._order.clear()
