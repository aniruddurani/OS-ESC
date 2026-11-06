"""The DE2-115 board's status and soft reset over JTAG (status.tcl through quartus_stp).

    python board_status.py read  [--stp EXE]               one status read, as JSON
    python board_status.py reset [--stp EXE]               pulse the soft reset, then read
    python board_status.py wait  [--stp EXE] --seconds S --out F
        read until the PHY is link_100fd or S seconds pass; write the last read (or null)

The probe value is {BUILD_ID[23:0], 2'b0, PHY state[2:0], sticky[2:0]}."""
import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATUS_TCL = os.path.join(HERE, "de2_115", "status.tcl")
STP = r"C:\altera_lite\25.1std\quartus\bin64\quartus_stp.exe"
STATES = ("idle", "configuring", "link_100fd", "wrong_link", "mdio_error")


class BoardError(Exception):
    pass


def decode(text):
    """The status dict from status.tcl's output, or None if it holds no probe value."""
    m = re.search(r"^ISSP ([0-9A-Fa-f]+)\s*$", text, re.M)
    if not m:
        return None
    v = int(m.group(1), 16)
    state = (v >> 3) & 7
    return {"build_id": v >> 8,
            "state": STATES[state] if state < len(STATES) else f"unknown_{state}",
            "sticky": {"rx_overflow": v & 1, "tx_overflow": (v >> 1) & 1, "tx_underrun": (v >> 2) & 1}}


def _stp(stp_exe, action):
    try:
        p = subprocess.run([stp_exe, "-t", STATUS_TCL, action], capture_output=True, text=True,
                           timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise BoardError(f"quartus_stp {action}: {e}") from None
    status = decode(p.stdout)
    if p.returncode != 0 or status is None:
        tail = (p.stdout + p.stderr).strip().splitlines()[-5:]
        raise BoardError(f"quartus_stp {action} exit {p.returncode}: {' | '.join(tail)}")
    return status


def read_status(stp_exe=STP):
    return _stp(stp_exe, "read")


def soft_reset(stp_exe=STP):
    """Pulse the ESC's soft reset (not the PHY's); returns the status read after it."""
    return _stp(stp_exe, "reset")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["read", "reset", "wait"])
    p.add_argument("--stp", default=STP)
    p.add_argument("--seconds", type=float, default=5.0)
    p.add_argument("--out")
    a = p.parse_args(argv)
    if a.action == "wait":
        last, deadline = None, time.monotonic() + a.seconds
        while True:
            try:
                last = read_status(a.stp)
            except BoardError as e:
                print(e, file=sys.stderr)
            if (last and last["state"] == "link_100fd") or time.monotonic() >= deadline:
                break
            time.sleep(0.5)
        with open(a.out, "w") as f:
            json.dump(last, f)
        print(json.dumps(last))
        return 0
    try:
        s = read_status(a.stp) if a.action == "read" else soft_reset(a.stp)
    except BoardError as e:
        print(e, file=sys.stderr)
        return 1
    print(json.dumps(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
