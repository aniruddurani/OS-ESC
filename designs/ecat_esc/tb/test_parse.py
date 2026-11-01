import random
import struct
import zlib

import cocotb
from cocotb.clock import Clock

import model_path  # noqa: F401  (puts the reference model on sys.path)
from ecat import CMD, Datagram, build_frame
from stream_util import OutputLog, StreamDriver

K_OTHER, K_HDR, K_DATA, K_WKC = 0, 1, 2, 3
OUT = ("o_valid", "o_data", "o_first", "o_last", "o_err", "o_kind", "o_pos",
       "dg_start", "dg_len_done", "dg_hdr_done", "dg_cmd", "dg_idx", "dg_addr", "dg_len", "dg_more", "dg_circ",
       "ev_end", "ev_runt", "ev_oversize", "ev_non_ethercat", "ev_malformed", "ev_rx_err")


async def start(dut, seed=1):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    drv = StreamDriver(dut, dut.clk, random.Random(seed))
    await drv.idle(4)
    dut.rst.value = 0
    await drv.idle(2)
    return drv, OutputLog(dut, dut.clk, OUT)


def with_fcs(body):
    return body + zlib.crc32(body).to_bytes(4, "little")


def events(log):
    return [r for r in log.rows if r["ev_end"]]


def two_datagram_frame():
    return build_frame([
        Datagram(CMD["APWR"], 7, (0x0010 << 16) | 0xFFFF, b"\x34\x12", more=True),
        Datagram(CMD["FPRD"], 8, (0x0130 << 16) | 0x1001, bytes(4)),
    ])


@cocotb.test()
async def test_two_datagram_frame_fields(dut):
    drv, log = await start(dut)
    frame = two_datagram_frame()
    await drv.send(frame)
    await drv.idle(20)
    rows = log.rows
    out = [r for r in rows if r["o_valid"]]
    assert bytes(r["o_data"] for r in out) == frame, "pass-through must not alter bytes"
    assert out[0]["o_first"] == 1 and out[-1]["o_last"] == 1
    # Datagram 1 starts at frame byte 16: header 10, data 2, wkc 2; datagram 2: 10, 4, 2.
    kinds = [(r["o_kind"], r["o_pos"]) for r in out]
    assert all(k == K_OTHER for k, _ in kinds[:16])
    assert kinds[16:26] == [(K_HDR, i) for i in range(10)]
    assert kinds[26:28] == [(K_DATA, 0), (K_DATA, 1)]
    assert kinds[28:30] == [(K_WKC, 0), (K_WKC, 1)]
    assert kinds[30:40] == [(K_HDR, i) for i in range(10)]
    assert kinds[40:44] == [(K_DATA, i) for i in range(4)]
    assert kinds[44:46] == [(K_WKC, 0), (K_WKC, 1)]
    assert all(k == K_OTHER for k, _ in kinds[46:]), "pad and FCS are untagged"
    starts = [r for r in rows if r["dg_start"]]
    assert [(r["dg_cmd"], r["dg_idx"], r["dg_addr"]) for r in starts] == [
        (CMD["APWR"], 7, (0x0010 << 16) | 0xFFFF), (CMD["FPRD"], 8, (0x0130 << 16) | 0x1001)]
    early = [r for r in rows if r["dg_len_done"]]
    assert [r["dg_len"] for r in early] == [2, 4], "dg_len_done: the length is valid after header byte 7"
    assert [i for i, r in enumerate(rows) if r["dg_len_done"]] < [i for i, r in enumerate(rows) if r["dg_hdr_done"]]
    done = [r for r in rows if r["dg_hdr_done"]]
    assert [(r["dg_len"], r["dg_more"]) for r in done] == [(2, 1), (4, 0)]
    ev, = events(log)
    assert not (ev["ev_runt"] or ev["ev_oversize"] or ev["ev_non_ethercat"]
                or ev["ev_malformed"] or ev["ev_rx_err"])


@cocotb.test()
async def test_wrong_ethertype_flagged(dut):
    drv, log = await start(dut, 2)
    raw = bytearray(two_datagram_frame())
    raw[12:14] = b"\x08\x00"
    await drv.send(with_fcs(bytes(raw[:-4])))
    await drv.idle(20)
    ev, = events(log)
    assert ev["ev_non_ethercat"] == 1
    assert not any(r["dg_start"] for r in log.rows), "no datagram may be decoded"
    assert all(r["o_kind"] == K_OTHER for r in log.rows if r["o_valid"])


@cocotb.test()
async def test_length_exceeds_frame_flagged(dut):
    drv, log = await start(dut, 3)
    raw = bytearray(build_frame([Datagram(CMD["APWR"], 1, 0x00100000, b"\x34\x12")]))
    raw[16 + 6:16 + 8] = struct.pack("<H", 1400)       # datagram length far past the frame
    await drv.send(with_fcs(bytes(raw[:-4])))
    await drv.idle(20)
    ev, = events(log)
    assert ev["ev_malformed"] == 1


@cocotb.test()
async def test_ethercat_length_bigger_than_frame_flagged(dut):
    drv, log = await start(dut, 4)
    raw = bytearray(build_frame([Datagram(CMD["APWR"], 1, 0x00100000, b"\x34\x12")]))
    raw[14:16] = struct.pack("<H", (1 << 12) | 1200)
    await drv.send(with_fcs(bytes(raw[:-4])))
    await drv.idle(20)
    assert events(log)[0]["ev_malformed"] == 1


@cocotb.test()
async def test_truncated_frame_recovers_next_frame(dut):
    # Review Focus 2: a frame cut off mid-datagram must not hang the parser.
    drv, log = await start(dut, 5)
    frame = two_datagram_frame()
    await drv.send(frame[:34])                            # ends inside the second header
    await drv.idle(10)
    good = two_datagram_frame()
    n_before = len([r for r in log.rows if r["dg_start"]])
    await drv.send(good)
    await drv.idle(20)
    evs = events(log)
    assert evs[0]["ev_malformed"] == 1
    assert evs[1]["ev_malformed"] == 0
    starts = [r for r in log.rows if r["dg_start"]][n_before:]
    assert [r["dg_cmd"] for r in starts] == [CMD["APWR"], CMD["FPRD"]]


@cocotb.test()
async def test_runt_oversize_and_rx_err_flagged(dut):
    drv, log = await start(dut, 6)
    await drv.send(bytes(40))
    await drv.send(bytes(1600))
    await drv.send(two_datagram_frame(), err=1)
    await drv.idle(20)
    runt, big, bad = events(log)
    assert runt["ev_runt"] == 1 and runt["ev_oversize"] == 0
    assert big["ev_oversize"] == 1 and big["ev_runt"] == 0
    assert bad["ev_rx_err"] == 1
