"""Counters saturate instead of wrapping (esc_core built with CNT_W = 3, so the limit is 7)."""
import cocotb

from core_util import adp_ado, exchange, one, start


@cocotb.test()
async def test_counters_saturate(dut):
    drv, mon, rng, model = await start(dut, 45)
    for _ in range(12):
        await exchange(drv, mon, model, one("APRD", adp_ado(0, 0x0004), bytes(1)))
    d = dut.g_diag.u_diag
    assert int(d.c_frames.value) == 7, "frames must stick at the maximum"
    assert int(d.c_datagrams.value) == 7
    assert int(d.c_wkc_increments.value) == 7
