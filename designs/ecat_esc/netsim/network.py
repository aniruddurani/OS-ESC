"""The simulated EtherCAT line: K model slaves, then the RTL slave (one port, so always last).

A frame is processed once on its way out, by every slave in order, as on a real line; the
return path changes nothing. The RTL's own model instance (`dut`) predicts its reply."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "model"))

from ecat import SlaveModel  # noqa: E402


class Chain:
    def __init__(self, k, ram_bytes=256, n_fmmu=3, n_sm=4):
        self.slaves = [SlaveModel(n_fmmu=n_fmmu, ram_bytes=ram_bytes, n_sm=n_sm) for _ in range(k)]
        self.dut = SlaveModel(n_fmmu=n_fmmu, ram_bytes=ram_bytes, n_sm=n_sm)

    def forward(self, frame, rx_err=False):
        """(what the RTL receives, what it must send back); (None, None) if a model slave
        drops the frame. `rx_err` is a fault on the RTL's own receive pins."""
        for s in self.slaves:
            frame = s.process(frame)
            if frame is None:
                return None, None
        return frame, self.dut.process(frame, rx_err=rx_err)
