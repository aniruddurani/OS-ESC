"""The link to the real board: frames go out and come back over a NIC through Npcap
(Windows), with the same calls as link.Link (the pipe to the Verilator bench), so
run_netsim.run_one drives either.

One frame is in flight at a time: send, then wait up to `timeout_s` for the board's reply.
The NIC appends the FCS on send and strips it on receive, so `send` drops the frame's FCS
and replies get theirs back, recomputed, and the checker compares the same bytes as in
simulation. Times are the host's perf_counter in ps; status has no FIFO flags (the board's
flags are read over JTAG, board_status.py).

The NIC may hand back the frames the host itself sent. Npcap's inbound-only filter
(pcap_setdirection) removes them when it works; when it does not, the first received frame
equal to the frame just sent is that copy and is dropped (it always arrives before the
board's reply, since one frame is in flight)."""
import ctypes
import json
import subprocess
import time
import zlib

from link import LinkError

ETHERTYPE = 0x88A4


def _with_fcs(frame):
    if len(frame) > 4 and zlib.crc32(frame[:-4]).to_bytes(4, "little") == frame[-4:]:
        return frame                                   # this NIC kept the FCS
    return frame + zlib.crc32(frame).to_bytes(4, "little")


def _now_ps():
    return time.perf_counter_ns() * 1000


class WireLink:
    def __init__(self, mac, pcap=None, timeout_s=0.1):
        self.pcap = pcap or NpcapBackend()
        self.timeout_s = timeout_s
        self.pcap.open(mac)
        self.direction_in = self.pcap.set_direction_in()
        self._queue, self._index = [], 0

    def send(self, frame, ifg=24, er_nibble=-1, record=False):
        if er_nibble >= 0:
            raise LinkError("a NIC cannot send rx_er")
        self._queue.append(bytes(frame[:-4]))

    def _await_reply(self, raw, t_sent):
        own_copy = not self.direction_in
        deadline = t_sent + int(self.timeout_s * 1e12)
        while True:
            left = (deadline - _now_ps()) / 1e12
            if left <= 0:
                return None
            f = self.pcap.recv(left)
            if f is None:
                return None
            if len(f) < 14 or int.from_bytes(f[12:14], "big") != ETHERTYPE:
                continue
            if own_copy and f == raw:
                own_copy = False
                continue
            return dict(t_start_ps=_now_ps(), t_end_ps=_now_ps(), data=_with_fcs(f))

    def run(self, replies, timeout_ps):
        """Send every queued frame, one at a time, each waiting for its reply.
        `replies` and `timeout_ps` are the bench's; the per-frame timeout applies here.
        Returns (sent, replies, events, status)."""
        sent, got = [], []
        for raw in self._queue:
            t = _now_ps()
            self.pcap.send(raw)
            sent.append(dict(index=self._index, t_start_ps=t, t_end_ps=t))
            self._index += 1
            r = self._await_reply(raw, t)
            if r is not None:
                got.append(r)
        self._queue = []
        return sent, got, [], self._status()

    def _status(self):
        return dict(t_ps=_now_ps(), core_cycles=0, seen=0, sticky=0)

    def dump(self):
        return [], self._status()

    def close(self):
        self.pcap.close()


# --- Npcap ----------------------------------------------------------------------------
class _Timeval(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_usec", ctypes.c_long)]


class _PktHdr(ctypes.Structure):
    _fields_ = [("ts", _Timeval), ("caplen", ctypes.c_uint32), ("len", ctypes.c_uint32)]


class _BpfProgram(ctypes.Structure):
    _fields_ = [("bf_len", ctypes.c_uint), ("bf_insns", ctypes.c_void_p)]


PCAP_D_IN = 1


def adapter_device(mac):
    """Npcap's device name (\\Device\\NPF_{GUID}) for the adapter with this MAC address."""
    want = mac.replace(":", "-").upper()
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-NetAdapter | Select-Object Name,MacAddress,InterfaceGuid | ConvertTo-Json"],
                         capture_output=True, text=True, timeout=30).stdout
    rows = json.loads(out or "[]")
    for r in rows if isinstance(rows, list) else [rows]:
        if (r.get("MacAddress") or "").upper() == want:
            return "\\Device\\NPF_" + r["InterfaceGuid"]
    raise LinkError(f"no network adapter with MAC {mac}")


class NpcapBackend:
    """ctypes over Npcap's wpcap.dll: open by MAC, promiscuous, immediate mode,
    filtered to EtherCAT frames."""
    DLL = r"C:\Windows\System32\Npcap\wpcap.dll"

    def __init__(self):
        self.p = None

    def open(self, mac):
        try:
            w = self.w = ctypes.CDLL(self.DLL)
        except OSError as e:
            raise LinkError(f"Npcap not available: {e}") from None
        w.pcap_create.restype = ctypes.c_void_p
        w.pcap_create.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        for name in ("pcap_set_snaplen", "pcap_set_promisc", "pcap_set_timeout",
                     "pcap_set_immediate_mode", "pcap_setdirection"):
            getattr(w, name).argtypes = [ctypes.c_void_p, ctypes.c_int]
        for name in ("pcap_activate", "pcap_close"):
            getattr(w, name).argtypes = [ctypes.c_void_p]
        w.pcap_geterr.restype = ctypes.c_char_p
        w.pcap_geterr.argtypes = [ctypes.c_void_p]
        w.pcap_compile.argtypes = [ctypes.c_void_p, ctypes.POINTER(_BpfProgram), ctypes.c_char_p,
                                   ctypes.c_int, ctypes.c_uint32]
        w.pcap_setfilter.argtypes = [ctypes.c_void_p, ctypes.POINTER(_BpfProgram)]
        w.pcap_freecode.argtypes = [ctypes.POINTER(_BpfProgram)]
        w.pcap_sendpacket.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        w.pcap_next_ex.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(_PktHdr)),
                                   ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte))]

        err = ctypes.create_string_buffer(256)
        dev = adapter_device(mac)
        self.p = w.pcap_create(dev.encode(), err)
        if not self.p:
            raise LinkError(f"pcap_create {dev}: {err.value.decode(errors='replace')}")
        w.pcap_set_snaplen(self.p, 2048)
        w.pcap_set_promisc(self.p, 1)
        w.pcap_set_timeout(self.p, 1)
        w.pcap_set_immediate_mode(self.p, 1)
        if w.pcap_activate(self.p) < 0:
            raise LinkError(f"pcap_activate {dev}: {self._err()}")
        prog = _BpfProgram()
        if w.pcap_compile(self.p, ctypes.byref(prog), b"ether proto 0x88a4", 1, 0) < 0 or \
                w.pcap_setfilter(self.p, ctypes.byref(prog)) < 0:
            raise LinkError(f"pcap filter: {self._err()}")
        w.pcap_freecode(ctypes.byref(prog))

    def _err(self):
        return self.w.pcap_geterr(self.p).decode(errors="replace")

    def set_direction_in(self):
        return self.w.pcap_setdirection(self.p, PCAP_D_IN) == 0

    def send(self, raw):
        if self.w.pcap_sendpacket(self.p, raw, len(raw)) != 0:
            raise LinkError(f"pcap_sendpacket: {self._err()}")

    def recv(self, timeout_s):
        hdr = ctypes.POINTER(_PktHdr)()
        data = ctypes.POINTER(ctypes.c_ubyte)()
        deadline = time.perf_counter() + timeout_s
        while True:
            rc = self.w.pcap_next_ex(self.p, ctypes.byref(hdr), ctypes.byref(data))
            if rc == 1:
                return ctypes.string_at(data, hdr.contents.caplen)
            if rc < 0:
                raise LinkError(f"pcap_next_ex: {self._err()}")
            if time.perf_counter() >= deadline:
                return None

    def close(self):
        if self.p:
            self.w.pcap_close(self.p)
            self.p = None
