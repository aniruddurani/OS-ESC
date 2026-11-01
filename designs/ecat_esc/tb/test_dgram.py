"""Datagram engine against the reference model: the core (esc_core) is fed the same
frames as SlaveModel and must produce the same output bytes and poison flag."""
import random
import zlib

import cocotb
from cocotb.clock import Clock

import model_path  # noqa: F401
from ecat import CMD, Datagram, SlaveModel, build_frame
from mii_util import StreamMonitor
from stream_util import StreamDriver

RAM = 256       # esc_core parameter set by run.py for this test


def adp_ado(adp, ado):
    return (ado << 16) | adp


def fcs_ok(frame):
    return zlib.crc32(frame[:-4]).to_bytes(4, "little") == frame[-4:]


async def start(dut, seed):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    rng = random.Random(seed)
    drv = StreamDriver(dut, dut.clk, rng, gap=(5, 9), prefix="in_")
    mon = StreamMonitor(dut, dut.clk, prefix="out_")
    await drv.idle(5)
    dut.rst.value = 0
    await drv.idle(3)
    # Reset does not clear the RAM (a real RAM would not either), and cocotb runs
    # every test in one simulation: zero it through the datapath, on both sides.
    model = SlaveModel(ram_bytes=RAM)
    clear = build_frame([Datagram(CMD["BWR"], 0, adp_ado(0, 0x1000), bytes(RAM))])
    await exchange(drv, mon, model, clear)
    return drv, mon, rng, model


async def exchange(drv, mon, model, frame, err=0):
    """Send `frame` through the DUT and the model; check the outputs agree."""
    expected = model.process(frame)
    before = len(mon.frames)
    await drv.send(frame, err=err)
    await drv.idle(200)
    assert len(mon.frames) == before + 1, "the core must emit exactly one frame per input frame"
    body, poison = mon.frames[-1]
    assert body == expected[:-4], "output bytes differ from the model"
    assert poison == (0 if fcs_ok(expected) else 1) or err, "poison flag differs from the model"
    return body, poison


def one(cmd, address, data, idx=1, wkc=0):
    return build_frame([Datagram(CMD[cmd], idx, address, data, wkc=wkc)])


@cocotb.test()
async def test_apwr_aprd(dut):
    drv, mon, rng, model = await start(dut, 1)
    await exchange(drv, mon, model, one("APWR", adp_ado(0, 0x0010), b"\x34\x12"))
    body, poison = await exchange(drv, mon, model, one("APRD", adp_ado(0, 0x0010), bytes(2)))
    assert poison == 0
    assert model.station_address == 0x1234


@cocotb.test()
async def test_auto_increment_changes_the_position_field(dut):
    drv, mon, rng, model = await start(dut, 2)
    for adp in (0, 1, 0xFFFF, 0x00FF):
        await exchange(drv, mon, model, one("APRD", adp_ado(adp, 0x0004), bytes(1)))


@cocotb.test()
async def test_fpwr_fprd_station_match(dut):
    drv, mon, rng, model = await start(dut, 3)
    await exchange(drv, mon, model, one("APWR", adp_ado(0, 0x0010), b"\x01\x10"))
    await exchange(drv, mon, model, one("FPRD", adp_ado(0x1001, 0x0010), bytes(2)))   # matches
    await exchange(drv, mon, model, one("FPRD", adp_ado(0x1002, 0x0010), bytes(2)))   # does not
    await exchange(drv, mon, model, one("FPWR", adp_ado(0x1001, 0x1000), b"\xde\xad\xbe\xef"))
    await exchange(drv, mon, model, one("FPRD", adp_ado(0x1001, 0x1000), bytes(4)))


@cocotb.test()
async def test_brd_bwr(dut):
    drv, mon, rng, model = await start(dut, 4)
    await exchange(drv, mon, model, one("BRD", adp_ado(0, 0x0004), b"\x10\x00"))     # OR with data
    await exchange(drv, mon, model, one("BWR", adp_ado(0, 0x1000), b"\x01\x02\x03"))
    await exchange(drv, mon, model, one("BRD", adp_ado(7, 0x1000), bytes(3)))


@cocotb.test()
async def test_rw_commands(dut):
    drv, mon, rng, model = await start(dut, 5)
    await exchange(drv, mon, model, one("APWR", adp_ado(0, 0x1000), b"\x11\x22\x33\x44"))
    await exchange(drv, mon, model, one("APRW", adp_ado(0, 0x1000), b"\xaa\xbb\xcc\xdd"))
    await exchange(drv, mon, model, one("BRW", adp_ado(0, 0x1000), b"\x01\x02\x03\x04"))
    await exchange(drv, mon, model, one("APWR", adp_ado(0, 0x0010), b"\x07\x00"))
    await exchange(drv, mon, model, one("FPRW", adp_ado(7, 0x1000), b"\x55\x66\x77\x88"))


@cocotb.test()
async def test_wkc_per_command(dut):
    drv, mon, rng, model = await start(dut, 6)
    await exchange(drv, mon, model, one("APWR", adp_ado(0, 0x0010), b"\x01\x00"))
    for name in ("APRD", "APWR", "APRW", "FPRD", "FPWR", "FPRW", "BRD", "BWR", "BRW", "NOP"):
        adp = 1 if name.startswith("FP") else 0
        body, _ = await exchange(drv, mon, model, one(name, adp_ado(adp, 0x1000), bytes(4), wkc=0xFFFE))


@cocotb.test()
async def test_failed_access_does_not_count(dut):
    drv, mon, rng, model = await start(dut, 7)
    await exchange(drv, mon, model, one("BRD", adp_ado(0, 0x1000 + RAM - 2), bytes(4)))   # runs past RAM
    await exchange(drv, mon, model, one("BWR", adp_ado(0, 0x0FFE), bytes(4)))             # crosses boundary
    await exchange(drv, mon, model, one("APRD", adp_ado(0, 0x2000), bytes(2)))


@cocotb.test()
async def test_two_datagrams_in_one_frame(dut):
    drv, mon, rng, model = await start(dut, 8)
    frame = build_frame([
        Datagram(CMD["APWR"], 1, adp_ado(0, 0x0010), b"\x01\x00", more=True),
        Datagram(CMD["FPRD"], 2, adp_ado(1, 0x0010), bytes(2), more=True),
        Datagram(CMD["BRD"], 3, adp_ado(0, 0x0004), bytes(1))])
    await exchange(drv, mon, model, frame)


@cocotb.test()
async def test_bad_fcs_writes_land_and_frame_is_poisoned(dut):
    # Review Focus 1: counted, never silently accepted; cut-through policy (Task 8).
    drv, mon, rng, model = await start(dut, 9)
    raw = bytearray(one("APWR", adp_ado(0, 0x0010), b"\x34\x12"))
    raw[-1] ^= 0xFF
    body, poison = await exchange(drv, mon, model, bytes(raw))
    assert poison == 1
    body, poison = await exchange(drv, mon, model, one("APRD", adp_ado(0, 0x0010), bytes(2)))
    assert poison == 0 and model.station_address == 0x1234


@cocotb.test()
async def test_random_frames_match_model(dut):
    drv, mon, rng, model = await start(dut, 10)
    names = ["NOP", "APRD", "APWR", "APRW", "FPRD", "FPWR", "FPRW", "BRD", "BWR", "BRW"]
    for _ in range(60):
        dgs = []
        for k in range(rng.randint(1, 3)):
            name = rng.choice(names)
            n = rng.randint(1, 12)
            area = rng.choice([(0x0000, 0x0100), (0x0100, 0x0130), (0x1000 - 8, 0x1000 + RAM + 8)])
            ado = rng.randrange(*area)
            if 0x0600 <= ado < 0x0700:
                ado = 0x0004            # keep the FMMUs off: logical addressing is Task 9
            adp = rng.choice([0, 0, 0xFFFF, model.station_address, rng.randrange(65536)])
            if name.startswith("FP") and rng.random() < 0.7:
                adp = model.station_address
            dgs.append(Datagram(CMD[name], k + 1, adp_ado(adp, ado),
                                bytes(rng.randrange(256) for _ in range(n)),
                                more=(k < 0), wkc=rng.randrange(0, 70000) & 0xFFFF))
        for i, d in enumerate(dgs[:-1]):
            d.more = True
        await exchange(drv, mon, model, build_frame(dgs))
