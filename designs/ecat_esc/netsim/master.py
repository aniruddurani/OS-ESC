"""The EtherCAT master, with no I/O: it hands out frames and takes replies. The transport
(the Verilator bench here, a NIC on the board later) is supplied by the caller."""
import glob
import os
import random
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tb"))

from scenarios import DEFAULT_K, GENERATORS, Net, Tx  # noqa: E402,F401  (Tx is part of this API)

CAPTURES = os.path.join(HERE, "..", "captures")


def _with_fcs(frame):
    """Captured frames usually lack the FCS; add one unless the last 4 bytes already are it."""
    if len(frame) > 4 and zlib.crc32(frame[:-4]).to_bytes(4, "little") == frame[-4:]:
        return frame
    return frame + zlib.crc32(frame).to_bytes(4, "little")


def _replay(captures_dir):
    from pcap import read_pcap
    for path in sorted(glob.glob(os.path.join(captures_dir, "*.pcap"))):
        frames = [f for _, f in read_pcap(path)]
        for i in range(0, len(frames) - 1, 2):          # request, response pairs
            yield Tx(_with_fcs(frames[i]))


class Master:
    def __init__(self, scenario, seed, k=DEFAULT_K, frames=1000, ram_bytes=256, n_fmmu=3, n_sm=4,
                 captures_dir=CAPTURES):
        self.scenario, self.k = scenario, k
        self.injected = {}
        self.replies = {}
        self._left = frames
        if scenario == "replay":
            self._gen = _replay(captures_dir)
            self._final = []
        else:
            net = Net(random.Random(f"{scenario}:{seed}:{k}"), k, ram_bytes, n_fmmu, n_sm)
            self._gen = GENERATORS[scenario](net)
            self._final = [net.diag_read()]
        self.final_reads = list(self._final)

    def next_batch(self, max_n):
        """Up to `max_n` more frames; empty when the scenario is done. The last frame of
        every scenario except replay reads the DUT's diag counters."""
        out = []
        while len(out) < max_n:
            if self._left > 0:
                tx = next(self._gen, None)
                if tx is None:
                    self._left = 0
                    continue
                self._left -= 1
                if tx.fault:
                    self.injected[tx.fault] = self.injected.get(tx.fault, 0) + 1
                out.append(tx)
            elif self._final:
                out.append(self._final.pop(0))
            else:
                break
        return out

    def on_reply(self, index, reply):
        """The reply to frame `index` (None if it never came). Traffic does not depend on
        replies, so a seed reproduces it whatever the RTL does; the last reply is kept."""
        self.replies = {index: reply}
