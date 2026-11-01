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
}


def run_one(module, top, rtl_dir, build):
    sources = sorted(glob.glob(os.path.join(rtl_dir, "*.v")))
    runner = get_runner("verilator")
    runner.build(sources=sources, hdl_toplevel=top,
                 build_dir=os.path.join(build, top),
                 build_args=["--timing", "-Wno-fatal", "-Wno-lint", "-Wno-style"],
                 always=True)
    return runner.test(hdl_toplevel=top, test_module=module, test_dir=TB_DIR,
                       build_dir=os.path.join(build, top),
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
