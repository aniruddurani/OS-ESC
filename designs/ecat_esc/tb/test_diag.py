import struct
import zlib

import cocotb

from core_util import adp_ado, exchange, one, start
from ecat import CMD, Datagram, build_frame

NAMES = ["frames", "rx_errors", "crc_failures", "runts", "oversize", "non_ethercat",
         "malformed", "datagrams", "datagrams_addressed", "wkc_increments"]
TRACE_BASE, ENTRY = 0x0E40, 16


def diag(dut):
    return dut.g_diag.u_diag


def peek(dut):
    return {n: int(getattr(diag(dut), "c_" + n).value) for n in NAMES}


def counts(model):
    return {n: getattr(model.counters, n) for n in NAMES}


def with_fcs(body):
    return body + zlib.crc32(body).to_bytes(4, "little")


@cocotb.test()
async def test_counters_match_model_counters(dut):
    drv, mon, rng, model = await start(dut, 41)
    good = one("APRD", adp_ado(0, 0x0004), bytes(1))
    await exchange(drv, mon, model, good)
    await exchange(drv, mon, model, one("BRD", adp_ado(0, 0x0004), bytes(1)))
    bad = bytearray(good)
    bad[-1] ^= 0xFF
    await exchange(drv, mon, model, bytes(bad))                              # bad FCS
    foreign = bytearray(good)
    foreign[12:14] = b"\x08\x00"
    await exchange(drv, mon, model, with_fcs(bytes(foreign[:-4])))           # not EtherCAT
    broken = bytearray(good)
    broken[16 + 6:16 + 8] = struct.pack("<H", 1400)
    await exchange(drv, mon, model, with_fcs(bytes(broken[:-4])))            # malformed
    await exchange(drv, mon, model, bytes(40))                               # runt
    await exchange(drv, mon, model, bytes(1600))                             # oversize
    await exchange(drv, mon, model, bytes(1600))                             # (twice, so counts differ)
    await exchange(drv, mon, model, bytes(40))                               # runt again
    await exchange(drv, mon, model, good, err=1)                             # receiver error
    await exchange(drv, mon, model, good)                                    # and the next frame is fine
    assert peek(dut) == counts(model)


@cocotb.test()
async def test_trace_entry_per_datagram(dut):
    drv, mon, rng, model = await start(dut, 42)
    await exchange(drv, mon, model, one("APRD", adp_ado(0, 0x0004), bytes(1)))
    await exchange(drv, mon, model, one("FPRD", adp_ado(0x1234, 0x0010), bytes(2)))
    await exchange(drv, mon, model, build_frame([
        Datagram(CMD["BWR"], 3, adp_ado(0, 0x1000), b"\x01\x02\x03", more=True),
        Datagram(CMD["NOP"], 4, 0, b"\x00")]))
    n = len(model.trace)
    assert n == 5                                           # the RAM clear from start() is first
    await drv.send(one("APRD", adp_ado(0, TRACE_BASE), bytes(ENTRY * n)))
    await drv.idle(200)
    data = mon.frames[-1][0][16 + 10:16 + 10 + ENTRY * n]
    stamps = []
    for e, (cmd, addr, length, inc, addressed) in enumerate(model.trace):
        raw = data[e * ENTRY:(e + 1) * ENTRY]
        assert raw[0] == cmd and struct.unpack_from("<I", raw, 1)[0] == addr, f"entry {e}"
        assert struct.unpack_from("<H", raw, 5)[0] == length
        assert raw[7] == (int(addressed) << 2) | inc
        stamps.append(struct.unpack_from("<H", raw, 8)[0])
        assert raw[10:] == bytes(6)
    assert stamps == sorted(stamps) and len(set(stamps)) == n, "timestamps must increase"


@cocotb.test()
async def test_counters_visible_through_register_reads(dut):
    drv, mon, rng, model = await start(dut, 43)
    await exchange(drv, mon, model, one("APRD", adp_ado(0, 0x0004), bytes(1)))
    before = peek(dut)
    trace_count = int(diag(dut).trace_count.value)
    await drv.send(one("APRD", adp_ado(0, 0x0E00), bytes(24)))
    await drv.idle(200)
    data = mon.frames[-1][0][16 + 10:16 + 10 + 24]
    got = struct.unpack("<12H", data)
    assert list(got[:10]) == [before[n] for n in NAMES]
    # The latency slot is read after this frame's own first byte has left the core,
    # so it already holds this frame's measurement.
    assert got[10] == int(diag(dut).latency.value), "slot 10 is the forwarding latency"
    assert got[11] == trace_count == len(model.trace), "slot 11 is the trace entry count"


@cocotb.test()
async def test_latency_is_measured(dut):
    drv, mon, rng, model = await start(dut, 44)
    drv.gap = (8, 8)                                        # a steady line-rate source
    lats = []
    for _ in range(3):
        await exchange(drv, mon, model, one("APRD", adp_ado(0, 0x0004), bytes(1)))
        lats.append(int(diag(dut).latency.value))
    assert lats[0] == lats[1] == lats[2], f"latency changed between frames: {lats}"
    assert 20 <= lats[0] <= 120, f"latency {lats[0]} core clocks is implausible"
