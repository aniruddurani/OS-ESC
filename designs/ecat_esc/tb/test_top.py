import cocotb

import model_path  # noqa: F401

from clocks import start_clock  # noqa: F401  (clock helpers are exercised through top_util)
from ecat import CMD, Datagram, build_frame
from mii_top_util import drive_frame
from top_util import flags, send_and_collect, start, wait_frames


def adp_ado(adp, ado):
    return (ado << 16) | adp


def mixed_frame(rng, station):
    names = ["NOP", "APRD", "APWR", "APRW", "FPRD", "FPWR", "BRD", "BWR", "LRD"]
    dgs = []
    for k in range(rng.randint(1, 3)):
        name = rng.choice(names)
        ado = rng.choice([rng.randrange(0, 0x40), rng.randrange(0x1000, 0x1000 + 128)])
        adp = station if name.startswith("FP") else rng.choice([0, 0, 0xFFFF])
        dgs.append(Datagram(CMD[name], k + 1, adp_ado(adp, ado),
                            bytes(rng.randrange(256) for _ in range(rng.randint(1, 16))), more=True))
    dgs[-1].more = False
    return build_frame(dgs)


@cocotb.test()
async def test_top_matches_model_on_generated_sessions(dut):
    mon, model, rng = await start(dut, 51)
    for i in range(25):
        frame = mixed_frame(rng, model.station_address)
        er_at = None
        if i % 8 == 7:
            frame = frame[:-1] + bytes([frame[-1] ^ 0xFF])               # bad FCS
        rx_err = i % 11 == 10
        expected = model.process(frame, rx_err=rx_err)
        got = await send_and_collect(dut, mon, frame, er_at=40 if rx_err else None)
        assert got == expected, f"frame {i} differs from the model"
    assert flags(dut) == (0, 0, 0)
    assert mon.bad == 0


async def back_to_back(dut, rx_ppm, tx_ppm, seed):
    """Frames sent with the minimum inter-frame gap while the two PHY clocks differ."""
    mon, model, rng = await start(dut, seed, rx_ppm=rx_ppm, tx_ppm=tx_ppm)
    base = len(mon.frames)
    expected = []
    for i in range(14):
        frame = mixed_frame(rng, model.station_address)
        expected.append(model.process(frame))
        await drive_frame(dut, frame, ifg_nibbles=24)                  # 12 bytes: the minimum
    await wait_frames(dut, mon, base + len(expected))
    got = mon.frames[base:]
    bad = [i for i, (g, e) in enumerate(zip(got, expected)) if g != e]
    assert len(got) == len(expected) and not bad, (
        f"frames differ or are out of order: got {len(got)} of {len(expected)}, first bad "
        f"{bad[:3]}; flags {flags(dut)}, monitor-bad {mon.bad}; "
        + (f"first diff at byte {[k for k in range(min(len(got[bad[0]]), len(expected[bad[0]]))) if got[bad[0]][k] != expected[bad[0]][k]][:4]}, "
           f"lengths {len(got[bad[0]])}/{len(expected[bad[0]])}" if bad else ""))
    assert flags(dut) == (0, 0, 0), f"fifo overflow/underrun flags {flags(dut)}"
    assert mon.bad == 0


@cocotb.test()
async def test_back_to_back_ppm_offsets(dut):
    # Review Focus 3: back-to-back frames, rx 200 ppm fast and tx 200 ppm slow.
    await back_to_back(dut, +200, -200, 52)


@cocotb.test()
async def test_back_to_back_opposite_ppm_offsets(dut):
    await back_to_back(dut, -200, +200, 53)
