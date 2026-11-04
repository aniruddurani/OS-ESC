"""Minimal pcap (libpcap, not pcapng) reader and writer for the capture replay."""
import struct

MAGIC_US, MAGIC_NS = 0xA1B2C3D4, 0xA1B23C4D


def read_pcap(path):
    """[(timestamp seconds as float, frame bytes)] from a classic pcap file.
    Wireshark can save this format ("Wireshark/tcpdump/... - pcap"); pcapng is
    not supported and raises ValueError."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 24:
        raise ValueError("not a pcap file: too short")
    magic_le = struct.unpack_from("<I", data, 0)[0]
    if magic_le in (MAGIC_US, MAGIC_NS):
        endian, ns = "<", magic_le == MAGIC_NS
    elif struct.unpack_from(">I", data, 0)[0] in (MAGIC_US, MAGIC_NS):
        endian, ns = ">", struct.unpack_from(">I", data, 0)[0] == MAGIC_NS
    else:
        raise ValueError("not a classic pcap file (pcapng is not supported)")
    out, pos = [], 24
    while pos + 16 <= len(data):
        sec, frac, incl, _orig = struct.unpack_from(endian + "IIII", data, pos)
        pos += 16
        if pos + incl > len(data):
            raise ValueError("truncated pcap record")
        out.append((sec + frac / (1e9 if ns else 1e6), bytes(data[pos:pos + incl])))
        pos += incl
    return out


def write_pcap(path, packets):
    """Write [(timestamp, frame)] as a little-endian microsecond pcap (Ethernet)."""
    with open(path, "wb") as f:
        f.write(struct.pack("<IHHiIII", MAGIC_US, 2, 4, 0, 0, 65535, 1))
        for ts, frame in packets:
            sec = int(ts)
            f.write(struct.pack("<IIII", sec, int(round((ts - sec) * 1e6)), len(frame), len(frame)))
            f.write(frame)
