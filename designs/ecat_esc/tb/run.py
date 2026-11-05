"""Run every cocotb test module and write one combined results.xml.

Usage: python3 run.py --rtl <rtl dir> --build <build dir> --results <results.xml>

Each entry of TESTS is a test module and the toplevel it exercises. A module
that fails to build or run contributes no testcases, so the dsim stage reports
its manifest tests as missing instead of passing silently.
"""
import argparse
import glob
import os
import sys
import xml.etree.ElementTree as ET

from cocotb_tools.runner import get_runner

TB_DIR = os.path.dirname(os.path.abspath(__file__))

# test module -> toplevel module
TESTS = {
    "test_crc32": "crc32",
    "test_mii_rx": "mii_rx",
    "test_mii_loop": "mii_loop",
    "test_cdc": "async_fifo",
    "test_parse": "frame_parse",
    "test_delay": "delay_line",
    "test_regfile": "regfile",
    "test_pdram": "pdram",
    "test_pdram_macro": "pdram",
    "test_sram_model": "sram_tb",
    "test_sram_pdk": "sram_tb",
    "test_dgram": "esc_core",
    "test_logical": "esc_core",
    "test_prefetch": "esc_core",
    "test_addsub_cs": "addsub_cs",
    "test_addsub_cs_add": "addsub_cs",
    "test_fmmu_plan": "fmmu_plan",
    "test_fmmu_walk": "fmmu_walk",
    "test_access": "esc_core",
    "test_fmmu0": "esc_core",
    "test_diag": "esc_core",
    "test_diag_sat": "esc_core",
    "test_diag_pair": "core_pair",
    "test_top": "esc_top",
    "test_replay": "esc_top",
    "test_phy_init": "phy_init",
    "test_board_flags": "board_flags",
}


# Verilog parameters per test module (the test files state the values they assume).
PARAMS = {
    "test_pdram": {"RAM_BYTES": 256},
    "test_pdram_macro": {"RAM_BYTES": 2048},
    "test_dgram": {"RAM_BYTES": 256},
    "test_logical": {"RAM_BYTES": 256},
    "test_prefetch": {"RAM_BYTES": 256},
    "test_addsub_cs": {"W": 35, "SUB": 1},
    "test_addsub_cs_add": {"W": 17, "SUB": 0},
    "test_fmmu_plan": {"RAM_BYTES": 256, "N_FMMU": 16},
    "test_fmmu_walk": {"N_FMMU": 16},
    "test_access": {"RAM_BYTES": 256, "N_FMMU": 16},
    "test_fmmu0": {"RAM_BYTES": 256, "N_FMMU": 0},
    "test_diag": {"RAM_BYTES": 256},
    "test_diag_sat": {"RAM_BYTES": 256, "CNT_W": 3},
    "test_top": {"RAM_BYTES": 256},
    "test_replay": {"RAM_BYTES": 256},
    "test_phy_init": {"CLK_DIV": 4, "RST_CYCLES": 50, "WAIT_CYCLES": 50, "POLL_CYCLES": 200},
}


PDK_MACRO_VARIANT = {"test_sram_pdk"}

# Board-level blocks (designs/ecat_esc/board/de2_115/rtl) these tests need; esc_board.v
# itself is left out because it instantiates Altera blocks.
BOARD_TESTS = {"test_phy_init", "test_board_flags"}
BOARD_RTL = os.path.join(TB_DIR, "..", "board", "de2_115", "rtl")


def run_one(module, top, rtl_dir, build):
    # RTL plus testbench-only wrappers (tb/*.v); lint covers rtl/ only.
    sources = sorted(glob.glob(os.path.join(rtl_dir, "*.v")) + glob.glob(os.path.join(TB_DIR, "*.v")))
    if module in BOARD_TESTS:
        sources += sorted(f for f in glob.glob(os.path.join(BOARD_RTL, "*.v"))
                          if os.path.basename(f) != "esc_board.v")
    if module in PDK_MACRO_VARIANT:      # the PDK's own macro model instead of the behavioural one
        sources += [os.path.join(os.environ["PDK_ROOT"], "sky130A/libs.ref/sky130_sram_macros/verilog",
                                 "sky130_sram_1kbyte_1rw1r_8x1024_8.v")]
    else:
        sources += sorted(glob.glob(os.path.join(TB_DIR, "models", "*.v")))
    bdir = os.path.join(build, top + ("_pdk" if module in PDK_MACRO_VARIANT else ""))
    runner = get_runner("verilator")
    runner.build(sources=sources, hdl_toplevel=top,
                 build_dir=bdir,
                 build_args=["--timing", "-Wno-fatal", "-Wno-lint", "-Wno-style"],
                 parameters=PARAMS.get(module, {}),
                 always=True)
    return runner.test(hdl_toplevel=top, test_module=module, test_dir=TB_DIR,
                       build_dir=bdir,
                       results_xml=os.path.join(build, f"{module}.xml"))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rtl", required=True)
    p.add_argument("--build", required=True)
    p.add_argument("--results", required=True)
    a = p.parse_args()
    os.makedirs(a.build, exist_ok=True)

    combined = ET.Element("testsuites", name="results")
    suite = ET.SubElement(combined, "testsuite", name="all", package="all")
    status = 0
    for module, top in TESTS.items():
        try:
            xml_path = run_one(module, top, a.rtl, a.build)
            for case in ET.parse(xml_path).getroot().iter("testcase"):
                suite.append(case)
        except Exception as e:  # build or run failure: leave the tests missing
            print(f"{module}: {e}", file=sys.stderr)
            status = 1
    suite.set("tests", str(len(suite)))
    ET.ElementTree(combined).write(a.results, encoding="utf-8", xml_declaration=True)
    return status


if __name__ == "__main__":
    sys.exit(main())
