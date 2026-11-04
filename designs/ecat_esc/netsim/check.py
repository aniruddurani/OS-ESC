"""Checks the RTL's replies against the model, frame by frame.

A reply belongs to the most recent frame whose transmission had started when the reply
started: the ESC forwards cut-through, so a reply starts within a few microseconds of its
frame, before the next frame (at least 6.7 us later) begins. A missing reply therefore
leaves the replies after it on their own frames."""
MAX_FRAME_PS = (1518 + 8) * 2 * 40_000      # longest frame on the wire, preamble included
LATENCY_PS = 2_000_000                      # bound on first byte in to first byte out
ALLOWANCE_PS = 2 * MAX_FRAME_PS + LATENCY_PS

# STATUS flag bits (proto.FLAG)
LATE_BIT = 8

# injected fault class -> diag counter that must have counted it
FAULT_COUNTER = {"rx_err": "rx_errors", "crc": "crc_failures", "runt": "runts",
                 "oversize": "oversize", "non_ethercat": "non_ethercat", "malformed": "malformed"}


def saturate(n, cnt_w=16):
    return min(n, (1 << cnt_w) - 1)


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

    def expect(self, index, dut_input, expected):
        self._frames[index] = dict(expected=expected, dut_input=dut_input, t_start=None,
                                   answered=False)
        self.metrics["sent"] += 1

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
            return
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
            return
        f["answered"] = True
        self.metrics["latency_max_ps"] = max(self.metrics["latency_max_ps"], t - f["t_start"])
        if got != f["expected"]:
            self._fail(owner, "mismatch", f["expected"], got)
        self._retire(t)

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
