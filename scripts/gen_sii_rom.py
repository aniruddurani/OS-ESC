#!/usr/bin/env python3
"""Writes designs/ecat_esc/rtl/sii_rom.v from model/sii.py. --check exits 1 if the file differs."""
import argparse
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "designs", "ecat_esc", "model"))

import sii  # noqa: E402

ROM_V = os.path.join(ROOT, "designs", "ecat_esc", "rtl", "sii_rom.v")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true")
    a = p.parse_args(argv)
    text = sii.render_rom()
    if a.check:
        with open(ROM_V, newline="") as f:
            same = f.read() == text
        print("sii_rom.v is up to date" if same else "sii_rom.v differs from the generator")
        return 0 if same else 1
    with open(ROM_V, "w", newline="\n") as f:
        f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
