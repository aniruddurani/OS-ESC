"""esc_core built with N_FMMU = 0: it must elaborate, report zero FMMUs, ignore the
FMMU register window and leave logical datagrams untouched."""
import cocotb

from core_util import adp_ado, exchange, one, start


@cocotb.test()
async def test_n_fmmu_zero_elaborates(dut):
    drv, mon, rng, model = await start(dut, 31, n_fmmu=0)
    body, poison = await exchange(drv, mon, model, one("APRD", adp_ado(0, 0x0004), bytes(1)))
    assert poison == 0 and model.regs[0x0004] == 0
    await exchange(drv, mon, model, one("APWR", adp_ado(0, 0x0600), bytes(range(1, 14))))
    await exchange(drv, mon, model, one("APRD", adp_ado(0, 0x0600), bytes(13)))
    for name in ("LRD", "LWR", "LRW"):
        await exchange(drv, mon, model, one(name, 0x0001_0000, bytes(6)))
