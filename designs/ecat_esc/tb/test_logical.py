import random
import struct  # noqa: F401

import cocotb

from core_util import RAM, adp_ado, cfg_fmmu, exchange, one, start
from ecat import CMD, Datagram, build_frame

BASE = 0x0001_0000          # logical address base used by these tests


@cocotb.test()
async def test_lrd_through_fmmu(dut):
    drv, mon, rng, model = await start(dut, 21)
    await exchange(drv, mon, model, one("APWR", adp_ado(0, 0x1000), b"\x11\x22\x33\x44"))
    await cfg_fmmu(drv, mon, model, 0, BASE, 4, 0x1000, rd=True)
    await exchange(drv, mon, model, one("LRD", BASE, bytes(4)))
    await exchange(drv, mon, model, one("LRD", BASE - 2, bytes(8)))        # window inside a longer datagram


@cocotb.test()
async def test_lwr_through_fmmu(dut):
    drv, mon, rng, model = await start(dut, 22)
    await cfg_fmmu(drv, mon, model, 0, BASE, 4, 0x1010, wr=True)
    await exchange(drv, mon, model, one("LWR", BASE, b"\xaa\xbb\xcc\xdd"))
    await exchange(drv, mon, model, one("APRD", adp_ado(0, 0x1010), bytes(4)))


@cocotb.test()
async def test_lrw_both_directions_wkc(dut):
    drv, mon, rng, model = await start(dut, 23)
    await exchange(drv, mon, model, one("APWR", adp_ado(0, 0x1020), b"\xaa\xbb"))
    await cfg_fmmu(drv, mon, model, 0, BASE, 4, 0x1000, wr=True)             # outputs
    await cfg_fmmu(drv, mon, model, 1, BASE + 0x10, 2, 0x1020, rd=True)      # inputs
    body, poison = await exchange(drv, mon, model, one("LRW", BASE, bytes([1, 2, 3, 4]) + bytes(14)))
    assert poison == 0
    await exchange(drv, mon, model, one("APRD", adp_ado(0, 0x1000), bytes(4)))


@cocotb.test()
async def test_two_fmmus_disjoint_windows(dut):
    drv, mon, rng, model = await start(dut, 24)
    await cfg_fmmu(drv, mon, model, 0, BASE, 3, 0x1000, wr=True, rd=True)
    await cfg_fmmu(drv, mon, model, 1, BASE + 8, 3, 0x1100 - 0x100 + 0x20, wr=True, rd=True)
    await exchange(drv, mon, model, one("LRW", BASE, bytes(range(1, 13))))
    await exchange(drv, mon, model, one("LRD", BASE, bytes(12)))


@cocotb.test()
async def test_overlapping_windows_first_one_wins(dut):
    drv, mon, rng, model = await start(dut, 25)
    await exchange(drv, mon, model, one("APWR", adp_ado(0, 0x1000), b"\x01\x02\x03\x04"))
    await exchange(drv, mon, model, one("APWR", adp_ado(0, 0x1010), b"\x09\x08\x07\x06"))
    await cfg_fmmu(drv, mon, model, 0, BASE, 4, 0x1000, rd=True)
    await cfg_fmmu(drv, mon, model, 1, BASE + 2, 4, 0x1010, rd=True)
    await exchange(drv, mon, model, one("LRD", BASE, bytes(6)))


@cocotb.test()
async def test_window_past_ram_end_rejected(dut):
    # Review Focus 5: a mapped chunk that does not fit the RAM is skipped whole.
    drv, mon, rng, model = await start(dut, 26)
    await cfg_fmmu(drv, mon, model, 0, BASE, 8, 0x1000 + RAM - 4, wr=True, rd=True)
    await exchange(drv, mon, model, one("LWR", BASE, bytes([7] * 8)))
    await exchange(drv, mon, model, one("LRD", BASE, bytes(8)))
    await exchange(drv, mon, model, one("APRD", adp_ado(0, 0x1000 + RAM - 4), bytes(4)))


@cocotb.test()
async def test_inactive_and_wrong_direction_ignored(dut):
    drv, mon, rng, model = await start(dut, 27)
    await cfg_fmmu(drv, mon, model, 0, BASE, 4, 0x1000, rd=True, active=0)
    await cfg_fmmu(drv, mon, model, 1, BASE + 8, 4, 0x1000, rd=True)
    await exchange(drv, mon, model, one("LRD", BASE, bytes(4)))                # inactive window
    await exchange(drv, mon, model, one("LWR", BASE + 8, bytes([5] * 4)))      # read-only window


@cocotb.test()
async def test_random_logical_matches_model(dut):
    drv, mon, rng, model = await start(dut, 28)
    for i in range(3):
        await cfg_fmmu(drv, mon, model, i, BASE + rng.randrange(0, 40), rng.randint(1, 12),
                       0x1000 + rng.randrange(0, RAM - 8), rd=rng.random() < 0.7,
                       wr=rng.random() < 0.7, active=1 if rng.random() < 0.85 else 0)
    for _ in range(40):
        name = rng.choice(["LRD", "LWR", "LRW", "APRD", "BRD"])
        if name.startswith("L"):
            address, n = BASE + rng.randrange(0, 50), rng.randint(1, 24)
        else:
            address, n = adp_ado(0, 0x1000 + rng.randrange(0, RAM - 8)), rng.randint(1, 8)
        data = bytes(rng.randrange(256) for _ in range(n))
        await exchange(drv, mon, model, build_frame([Datagram(CMD[name], 1, address, data)]))
