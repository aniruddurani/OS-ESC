"""Runs the netsim scenarios against the real board (the ESC on a DE2-115) over a NIC:
the same master, model chain and checker as run_netsim.py, with wire_link in place of the
Verilator pipe. Runs go one after another (one NIC). Before each run the ESC is
soft-reset over JTAG (the board keeps RAM, registers and diag counters between runs) and
its status read; after the run the status is read again and its FIFO flags join the run's
flags.

Usage (Windows host):
  python run_board.py --mac MAC --manifest M --size quick|soak --out DIR [--stp QUARTUS_STP]
                      [--only RUN] [--frames N] [--sabotage checker|drop-reply] [--seed S]
  python run_board.py --watch --mac MAC --out DIR [--scenario S] [--k K] [--frames N] [--port P]
      the live view of netsim/gui on the real board, paced for a person (not a gate)

DIR/results.json   run_netsim's layout, plus board_before / board_after per run and
                   not_applicable: the scenarios a NIC cannot run
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "model"), os.path.join(HERE, "..", "tb"),
                os.path.join(HERE, "..", "board")]

import board_status  # noqa: E402
from run_netsim import plan_runs, read_manifest, run_name, run_one  # noqa: E402
from scenarios import clock_config  # noqa: E402

NOT_APPLICABLE = ["clocks", "faults"]
FLAG_BITS = {"rx_overflow": 1, "tx_overflow": 2, "tx_underrun": 4}


def _ready(status, build_id=None):
    """Why the board is not ready for a run, or '' if it is. `build_id`: the build it must hold."""
    if build_id is not None and status["build_id"] != build_id:
        return f"it holds build {status['build_id']}, not {build_id}"
    if status["state"] != "link_100fd":
        return f"PHY state {status['state']}"
    flags = [k for k, v in status["sticky"].items() if v]
    return f"FIFO flags set after reset: {flags}" if flags else ""


def run_board(manifest, size, out, mac, stp, only=None, frames=None, sabotage=None, seed=1,
              board=board_status, make_link=None, build_id=None):
    if make_link is None:
        from wire_link import WireLink
        make_link = lambda: WireLink(mac)       # noqa: E731
    runs = plan_runs(manifest, size, seed)
    if only:
        runs = [r for r in runs if run_name(r) == only]
    if frames:
        for r in runs:
            r["frames"] = frames
    if sabotage and runs:
        runs[0]["sabotage"] = sabotage
    start, results = time.time(), []
    for spec in runs:
        res = None
        try:
            board.soft_reset(stp)
            before = board.read_status(stp)
            why = _ready(before, build_id)
            if why:
                res = dict(spec, config=clock_config(spec["scenario"], spec["seed"]), metrics={},
                           injected={}, diag={}, failures=[], error=f"board not ready: {why}")
            else:
                res = run_one(spec, None, out, size == "quick", make_link=lambda c: make_link())
                after = board.read_status(stp)
                res["board_after"] = after
                res["metrics"]["flags_seen"] = res["metrics"].get("flags_seen", 0) | sum(
                    bit for name, bit in FLAG_BITS.items() if after["sticky"].get(name))
            res["board_before"] = before
        except board_status.BoardError as e:
            res = res or dict(spec, metrics={}, injected={}, diag={}, failures=[])
            res["error"] = f"board status: {e}"
        results.append(res)
    doc = dict(size=size, wall_s=time.time() - start, runs=results, not_applicable=NOT_APPLICABLE,
               build_id=build_id)
    with open(os.path.join(out, "results.json"), "w") as f:
        json.dump(doc, f, indent=1)
    return doc


def watch_board(out, mac, stp, scenario, k, seed, frames, port, board=board_status, make_link=None,
                speed=1.0):
    """The live view against the board, after the same soft reset and check as a run."""
    if make_link is None:
        from wire_link import WireLink
        make_link = lambda: WireLink(mac)       # noqa: E731
    board.soft_reset(stp)
    why = _ready(board.read_status(stp))
    if why:
        raise board_status.BoardError(f"board not ready: {why}")
    import watch
    return watch.watch(None, scenario, k, seed, frames, port, out, make_link=lambda c: make_link(),
                       host="127.0.0.1", speed=speed)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--mac", required=True, help="the NIC cabled to the board's ETHERNET 0")
    p.add_argument("--manifest")
    p.add_argument("--size", choices=["quick", "soak"], default="quick")
    p.add_argument("--out", required=True)
    p.add_argument("--stp", default=board_status.STP)
    p.add_argument("--only", help="run only the named run (scenario_kK_sSEED)")
    p.add_argument("--frames", type=int, help="frames per run, in place of the manifest's")
    p.add_argument("--sabotage", choices=["checker", "drop-reply"],
                   help="self-test only: flip one expected byte, or drop one reply, in the first run")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--build-id", type=int, help="the build the board must hold")
    p.add_argument("--watch", action="store_true", help="the live view on the board, not a gate")
    p.add_argument("--scenario", default="cyclic")
    p.add_argument("--k", type=int, default=3)
    p.add_argument("--port", type=int, default=8766)
    a = p.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    if a.watch:
        return watch_board(a.out, a.mac, a.stp, a.scenario, a.k, a.seed, a.frames or 100_000, a.port)
    if not a.manifest:
        p.error("--manifest is required without --watch")
    doc = run_board(read_manifest(a.manifest), a.size, a.out, a.mac, a.stp, a.only, a.frames,
                    a.sabotage, a.seed, build_id=a.build_id)
    print(f"{len(doc['runs'])} runs in {doc['wall_s']:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
