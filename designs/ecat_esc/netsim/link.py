"""The pipe to the Verilator bench (tb_netsim.cpp): starts it, configures it, and turns
SEND/RUN/DUMP into Python calls. It knows nothing about networks or checking."""
import os
import subprocess
import tempfile

import proto

DEFAULT_CONFIG = dict(seed=1, rx_ppm=0.0, tx_ppm=0.0, drift_ppm_per_s=0.0, rx_phase_ns=0.0,
                      tx_phase_ns=0.0, core_phase_ns=0.0, event_level=0)


class LinkError(Exception):
    pass


class Link:
    def __init__(self, binary, config):
        if not os.path.exists(binary):
            raise LinkError(f"bench binary missing: {binary}")
        self._err = tempfile.TemporaryFile()
        self.proc = subprocess.Popen([binary], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self._err, bufsize=0)
        self._write(proto.encode("CONFIG", **config))

    def _stderr_tail(self):
        self._err.seek(0)
        return self._err.read()[-2000:].decode(errors="replace").strip()

    def _fail(self, why):
        rc = self.proc.poll()
        tail = self._stderr_tail()
        return LinkError(f"{why} (bench exit {rc}){': ' + tail if tail else ''}")

    def _write(self, raw):
        try:
            self.proc.stdin.write(raw)
        except (BrokenPipeError, OSError) as e:
            raise self._fail(f"bench not accepting input: {e}") from None

    def send(self, frame, ifg=24, er_nibble=-1, record=False):
        self._write(proto.encode("SEND", ifg_nibbles=ifg, er_nibble=er_nibble,
                                 flags=1 if record else 0, data=frame))

    def _collect(self):
        sent, replies, events = [], [], []
        while True:
            try:
                kind, f = proto.read_message(self.proc.stdout)
            except proto.ProtocolError as e:
                raise self._fail(f"protocol error: {e}") from None
            if kind == "STATUS":
                return sent, replies, events, f
            if kind == "SENT":
                sent.append(f)
            elif kind == "REPLY":
                replies.append(f)
            elif kind == "EVENT":
                events.append(f)
            elif kind == "EOF":
                raise self._fail("bench ended before STATUS")
            else:
                raise self._fail(f"unexpected {kind} from the bench")

    def run(self, replies, timeout_ps):
        """Run until `replies` replies came back and nothing is left to send, or until
        `timeout_ps` of simulated time; replies=0 runs the whole timeout.
        Returns (sent, replies, events, status)."""
        self._write(proto.encode("RUN", replies=replies, timeout_ps=timeout_ps))
        return self._collect()

    def dump(self):
        """The bench's ring buffer of events; returns (events, status)."""
        self._write(proto.encode("DUMP"))
        _, _, events, status = self._collect()
        return events, status

    def close(self):
        if self.proc.poll() is None:
            try:
                self.proc.stdin.write(proto.encode("QUIT"))
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        for s in (self.proc.stdout, self.proc.stdin):
            try:
                s.close()
            except OSError:
                pass
        self._err.close()
