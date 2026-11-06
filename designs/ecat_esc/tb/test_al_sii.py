"""AL registers and the SII interface: esc_core against the reference model. `exchange`
compares every reply byte with the model's, so a read is its own check."""
import cocotb

import model_path  # noqa: F401
import sii
from core_util import RAM, adp_ado, exchange, one, start
from ecat import SlaveModel


async def write(drv, mon, model, ado, data, cmd="APWR"):
    await exchange(drv, mon, model, one(cmd, adp_ado(0, ado), bytes(data)))


async def read(drv, mon, model, ado, n):
    """The data of a read, from the reply checked against the model."""
    body, _ = await exchange(drv, mon, model, one("APRD", adp_ado(0, ado), bytes(n)))
    return body[26:26 + n]


async def sii_address(drv, mon, model, word):
    """Command bytes first, then the address, in one datagram, as SOEM sends them."""
    await write(drv, mon, model, 0x0502, [0x00, 0x01] + list(word.to_bytes(4, "little")))


@cocotb.test()
async def test_al_states_match_model(dut):
    drv, mon, rng, model = await start(dut, 71)
    assert await read(drv, mon, model, 0x0130, 2) == b"\x01\x00"
    for cmd in ("APWR", "BWR"):
        for value in (0x02, 0x04, 0x08, 0x03, 0x11, 0x05, 0x00, 0x0F):
            await write(drv, mon, model, 0x0120, [value, 0x00], cmd)
            await read(drv, mon, model, 0x0120, 2)
            await read(drv, mon, model, 0x0130, 2)
    await write(drv, mon, model, 0x0010, [0x34, 0x12])
    await exchange(drv, mon, model, one("FPWR", adp_ado(0x1234, 0x0120), b"\x08\x00"))
    assert await read(drv, mon, model, 0x0130, 2) == b"\x08\x00"
    await write(drv, mon, model, 0x0130, [0x02, 0xFF])          # read only
    assert await read(drv, mon, model, 0x0130, 2) == b"\x08\x00"


@cocotb.test()
async def test_al_bad_frame_still_lands(dut):
    drv, mon, rng, model = await start(dut, 72)
    raw = bytearray(one("APWR", adp_ado(0, 0x0120), b"\x04\x00"))
    raw[-1] ^= 0xFF
    _, poison = await exchange(drv, mon, model, bytes(raw))
    assert poison == 1
    assert await read(drv, mon, model, 0x0130, 2) == b"\x04\x00"


@cocotb.test()
async def test_dl_status_and_fixed_registers(dut):
    drv, mon, rng, model = await start(dut, 73)
    assert await read(drv, mon, model, 0x0110, 2) == b"\x11\x02"
    assert await read(drv, mon, model, 0x0134, 2) == b"\x00\x00"
    assert await read(drv, mon, model, 0x0141, 1) == b"\x01"
    await read(drv, mon, model, 0x0120, 0x30)                   # the whole block
    await read(drv, mon, model, 0x0500, 16)
    await write(drv, mon, model, 0x0141, [0x00])
    assert await read(drv, mon, model, 0x0141, 1) == b"\x01"


@cocotb.test()
async def test_sii_read_command_then_address_in_one_datagram(dut):
    drv, mon, rng, model = await start(dut, 74)
    for word in (8, 10, 12, 14, 0x40, 0x41):
        await sii_address(drv, mon, model, word)
        assert await read(drv, mon, model, 0x0508, 4) == sii.read32(word)
        assert await read(drv, mon, model, 0x0503, 1) == b"\x00"       # no BUSY, no error
    assert await read(drv, mon, model, 0x0504, 4) == (0x41).to_bytes(4, "little")


@cocotb.test()
async def test_sii_whole_image(dut):
    drv, mon, rng, model = await start(dut, 75)
    image = b""
    for word in range(0, sii.WORDS, 2):
        await sii_address(drv, mon, model, word)
        image += await read(drv, mon, model, 0x0508, 4)
    assert image == sii.image()


@cocotb.test()
async def test_read_straddles_end_of_table(dut):
    drv, mon, rng, model = await start(dut, 76)
    await sii_address(drv, mon, model, sii.WORDS - 1)
    assert (await read(drv, mon, model, 0x0508, 4))[2:] == b"\xff\xff"
    await sii_address(drv, mon, model, sii.WORDS)
    assert await read(drv, mon, model, 0x0508, 4) == b"\xff" * 4


@cocotb.test()
async def test_high_address_reads_blank(dut):
    drv, mon, rng, model = await start(dut, 77)
    for word in (0x00010008, 0x01000008, 0x80000000, 0x00000180):
        await sii_address(drv, mon, model, word)
        assert await read(drv, mon, model, 0x0508, 4) == b"\xff" * 4


@cocotb.test()
async def test_write_command_naks_nop_clears(dut):
    drv, mon, rng, model = await start(dut, 78)
    for command in ([0x01, 0x02], [0x00, 0x03]):
        await write(drv, mon, model, 0x0502, command)
        assert await read(drv, mon, model, 0x0502, 2) == b"\x00\x20"
        await write(drv, mon, model, 0x0502, [0x00, 0x01])     # a read leaves it
        assert await read(drv, mon, model, 0x0503, 1) == b"\x20"
        await write(drv, mon, model, 0x0502, [0x00, 0x00])
        assert await read(drv, mon, model, 0x0502, 2) == b"\x00\x00"


@cocotb.test()
async def test_owner_bit(dut):
    drv, mon, rng, model = await start(dut, 79)
    await write(drv, mon, model, 0x0500, [0xFF])
    assert await read(drv, mon, model, 0x0500, 2) == b"\x01\x00"
    await write(drv, mon, model, 0x0500, [0x02])
    assert await read(drv, mon, model, 0x0500, 2) == b"\x00\x00"


@cocotb.test()
async def test_reset_restores_init_and_word_zero(dut):
    drv, mon, rng, model = await start(dut, 80)
    await write(drv, mon, model, 0x0120, [0x08, 0x00])
    await write(drv, mon, model, 0x0502, [0x01, 0x02])
    await write(drv, mon, model, 0x0504, [0x08, 0x00, 0x00, 0x00])
    await write(drv, mon, model, 0x0500, [0x01])
    dut.rst.value = 1
    await drv.idle(5)
    dut.rst.value = 0
    await drv.idle(20)
    model = SlaveModel(ram_bytes=RAM)
    assert await read(drv, mon, model, 0x0130, 2) == b"\x01\x00"
    assert await read(drv, mon, model, 0x0120, 2) == b"\x00\x00"
    assert await read(drv, mon, model, 0x0503, 1) == b"\x00"
    assert await read(drv, mon, model, 0x0500, 1) == b"\x00"
    assert await read(drv, mon, model, 0x0504, 4) == bytes(4)
    assert await read(drv, mon, model, 0x0508, 4) == sii.read32(0)
