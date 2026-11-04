"""Clock-crossing tests for async_fifo: random phases, +-200 ppm, line-rate pacing."""
import random

import cocotb
from cocotb.triggers import RisingEdge

from clocks import start_clock

DEPTH = 16
FIRST, LAST = 1 << 8, 1 << 9


def make_frames(n_frames, length, seed):
    rng = random.Random(seed)
    frames = []
    for _ in range(n_frames):
        words = [rng.randrange(256) for _ in range(length)]
        words[0] |= FIRST
        words[-1] |= LAST
        frames.append(words)
    return frames


async def reset(dut):
    dut.wrst.value = 1
    dut.rrst.value = 1
    dut.we.value = 0
    dut.re.value = 0
    dut.wdata.value = 0
    for _ in range(6):
        await RisingEdge(dut.wclk)
    for _ in range(6):
        await RisingEdge(dut.rclk)
    dut.wrst.value = 0
    dut.rrst.value = 0
    for _ in range(4):
        await RisingEdge(dut.wclk)
        await RisingEdge(dut.rclk)


async def run_frames(dut, frames, w_period, w_slot, r_period, r_slot, w_ppm=0.0, r_ppm=0.0,
                     w_phase=0.0, r_phase=0.0, start_delay_slots=3, ifg_slots=12,
                     greedy_reader=False):
    """Write `frames` at one word per `w_slot` write-clock edges (with `ifg_slots`
    idle slots between frames) while a reader paces itself like the MII
    transmitter: it starts `start_delay_slots` slots after a frame's first word
    appears, then needs a word every `r_slot` read-clock edges. Returns stats."""
    start_clock(dut.wclk, w_period, w_ppm, w_phase)
    start_clock(dut.rclk, r_period, r_ppm, r_phase)
    await reset(dut)
    stats = {"overflow": 0, "underflow": 0, "read": [], "done": False}

    async def writer():
        for frame in frames:
            for word in frame:
                if int(dut.full.value) == 1:
                    stats["overflow"] += 1      # a word is presented while full
                dut.we.value = 1
                dut.wdata.value = word
                await RisingEdge(dut.wclk)
                dut.we.value = 0
                for _ in range(w_slot - 1):
                    await RisingEdge(dut.wclk)
            for _ in range(ifg_slots * w_slot):
                await RisingEdge(dut.wclk)
        stats["done"] = True

    async def reader():
        total = sum(len(f) for f in frames)
        while len(stats["read"]) < total:
            # wait for the start of a frame, then the start-up delay
            if not greedy_reader:
                while int(dut.empty.value) == 1:
                    await RisingEdge(dut.rclk)
                for _ in range(start_delay_slots * r_slot):
                    await RisingEdge(dut.rclk)
            in_frame = True
            while in_frame and len(stats["read"]) < total:
                if int(dut.empty.value) == 1:
                    if greedy_reader:
                        await RisingEdge(dut.rclk)
                        continue
                    stats["underflow"] += 1
                    in_frame = False
                    break
                word = int(dut.rdata.value)
                stats["read"].append(word)
                dut.re.value = 1
                await RisingEdge(dut.rclk)
                dut.re.value = 0
                if word & LAST and not greedy_reader:
                    in_frame = False
                # The pop lands on the edge above, so the next head is visible
                # one edge later; never sample sooner than two edges per word.
                for _ in range(max(r_slot, 2) - 1):
                    await RisingEdge(dut.rclk)

    cocotb.start_soon(writer())
    await reader()
    return stats


def flat(frames):
    return [w for f in frames for w in f]


@cocotb.test()
async def test_fifo_order_preserved_random_phase(dut):
    rng = random.Random(11)
    frames = make_frames(3, 300, 1)
    # Receive direction: 25 MHz writer, 100 MHz reader. The same instance is
    # reused across iterations, so each run starts from a reset.
    for i in range(4):
        s = await run_frames(dut, frames, 40.0, 2, 10.0, 1, w_phase=rng.uniform(0, 40),
                             r_phase=rng.uniform(0, 10), greedy_reader=True)
        assert s["read"] == flat(frames), f"rx direction, phase run {i}"
        assert s["overflow"] == 0


@cocotb.test()
async def test_no_overflow_at_plus_200ppm(dut):
    # Transmit direction, writer 200 ppm fast and reader 200 ppm slow, maximum
    # frames back to back with the minimum inter-frame gap.
    frames = make_frames(20, 1518, 2)
    s = await run_frames(dut, frames, 10.0, 8, 40.0, 2, w_ppm=+200, r_ppm=-200, w_phase=3.7,
                         r_phase=17.0)
    assert s["overflow"] == 0, "FIFO overflowed"
    assert s["underflow"] == 0
    assert s["read"] == flat(frames)


@cocotb.test()
async def test_no_underflow_at_minus_200ppm(dut):
    frames = make_frames(20, 1518, 3)
    s = await run_frames(dut, frames, 10.0, 8, 40.0, 2, w_ppm=-200, r_ppm=+200, w_phase=8.1,
                         r_phase=2.3)
    assert s["underflow"] == 0, "reader ran dry mid-frame"
    assert s["overflow"] == 0
    assert s["read"] == flat(frames)


@cocotb.test()
async def test_full_flag_blocks_write(dut):
    start_clock(dut.wclk, 10.0, 0.0, 1.0)
    start_clock(dut.rclk, 40.0, 0.0, 5.0)
    await reset(dut)
    for k in range(DEPTH + 6):
        dut.we.value = 1
        dut.wdata.value = k
        await RisingEdge(dut.wclk)
    dut.we.value = 0
    for _ in range(6):
        await RisingEdge(dut.wclk)
    assert int(dut.full.value) == 1, "full must be set after DEPTH unread writes"
    got = []
    while True:
        for _ in range(3):
            await RisingEdge(dut.rclk)
        if int(dut.empty.value) == 1:
            break
        got.append(int(dut.rdata.value))
        dut.re.value = 1
        await RisingEdge(dut.rclk)
        dut.re.value = 0
    assert got == list(range(DEPTH)), f"writes past full must be dropped, got {got}"
