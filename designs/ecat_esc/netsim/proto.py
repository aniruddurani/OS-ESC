"""Messages between the Python network and the Verilator bench (tb_netsim.cpp).

Twin of proto.h: change both together. On the pipe a message is a 4-byte little-endian
length (of the type byte and payload), a 1-byte type, and the payload, all little-endian.
Types that carry a frame end with its bytes, named `data` here.
"""
import struct

# name -> (type value, fixed payload layout, field names, ends with frame bytes)
_LAYOUT = {
    "CONFIG": (1, "<QddddddB", ("seed", "rx_ppm", "tx_ppm", "drift_ppm_per_s", "rx_phase_ns",
                                "tx_phase_ns", "core_phase_ns", "event_level"), False),
    "SEND":   (2, "<HiB", ("ifg_nibbles", "er_nibble", "flags"), True),
    "RUN":    (3, "<IQ", ("replies", "timeout_ps"), False),
    "REPLY":  (4, "<QQ", ("t_start_ps", "t_end_ps"), True),
    "STATUS": (5, "<QQBB", ("t_ps", "core_cycles", "seen", "sticky"), False),
    "EVENT":  (6, "<QBBII", ("t_ps", "block", "kind", "a", "b"), False),
    "QUIT":   (7, "<", (), False),
    "DUMP":   (8, "<", (), False),
    "SENT":   (9, "<IQQ", ("index", "t_start_ps", "t_end_ps"), False),
}
TYPES = {name: v[0] for name, v in _LAYOUT.items()}
_BY_VALUE = {v[0]: name for name, v in _LAYOUT.items()}

# STATUS seen/sticky bits
FLAG = {"rx_overflow": 1, "tx_overflow": 2, "tx_underrun": 4, "late": 8}
# EVENT block and kind
BLOCK = {"MII_RX": 1, "RX_FIFO": 2, "PARSER": 3, "EXEC": 4, "FMMU": 5, "MEM": 6,
         "TX_FIFO": 7, "MII_TX": 8}
KIND = {"FRAME_START": 1, "FRAME_END": 2, "DG_HDR": 3, "FMMU_HIT": 4, "MEM_RD": 5,
        "MEM_WR": 6, "WKC": 7, "FLAG": 8, "FAULT": 9}
# FAULT event a bits
FAULT = {"runt": 1, "oversize": 2, "non_ethercat": 4, "malformed": 8, "rx_err": 16}


class ProtocolError(Exception):
    pass


def encode(msg, /, **fields):
    """`msg` is positional-only: EVENT has a field named `kind`."""
    value, fmt, names, has_data = _LAYOUT[msg]
    body = struct.pack(fmt, *(fields[n] for n in names))
    if has_data:
        body += bytes(fields["data"])
    return struct.pack("<IB", len(body) + 1, value) + body


def _read_exact(stream, n):
    buf = b""
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


def read_message(stream):
    """The next message as (kind, fields); ("EOF", {}) at a clean end of stream."""
    head = _read_exact(stream, 4)
    if not head:
        return "EOF", {}
    if len(head) < 4:
        raise ProtocolError("stream ended inside a message length")
    (length,) = struct.unpack("<I", head)
    if length < 1:
        raise ProtocolError("message length 0")
    raw = _read_exact(stream, length)
    if len(raw) < length:
        raise ProtocolError(f"stream ended inside a message: {len(raw)} of {length} bytes")
    kind = _BY_VALUE.get(raw[0])
    if kind is None:
        raise ProtocolError(f"unknown message type {raw[0]}")
    _, fmt, names, has_data = _LAYOUT[kind]
    size = struct.calcsize(fmt)
    payload = raw[1:]
    if len(payload) < size or (not has_data and len(payload) != size):
        raise ProtocolError(f"{kind}: payload of {len(payload)} bytes, expected "
                            f"{'at least ' if has_data else ''}{size}")
    fields = dict(zip(names, struct.unpack_from(fmt, payload)))
    if has_data:
        fields["data"] = payload[size:]
    return kind, fields
