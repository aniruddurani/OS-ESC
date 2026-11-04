"""Runs the network simulation: every manifest scenario through the chain and the Verilator
bench, several bench processes at once, and writes the raw evidence the dnet gate reads.

Usage: python3 run_netsim.py --binary B --manifest M --size quick|soak --out DIR [--jobs N]

DIR/results.json   one entry per run (scenario, K, seed, clocks, frames, checker metrics,
                   injected faults, diag counters, first failures, times, error)
DIR/<run>.pcap     both directions of every frame (quick size only)
DIR/fail_<run>.*   on a failure: the failures, the settings to rerun it, and a pcap of the
                   last frames before it
"""
import argparse
import collections
import copy
import json
import math
import multiprocessing
import os
import struct
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "model"), os.path.join(HERE, "..", "tb")]

from check import ALLOWANCE_PS, Checker, diag_reply, fault_evidence  # noqa: E402
from ecat import FrameError, parse_frame  # noqa: E402
from link import Link, LinkError  # noqa: E402
from master import Master  # noqa: E402
from network import Chain  # noqa: E402
from pcap import write_pcap  # noqa: E402
from scenarios import CHAIN_K, DEFAULT_K, clock_config  # noqa: E402

NIBBLE_PS = 40_000
BATCH = 64
PART_FRAMES = 50_000            # longest single run; longer scenarios are split by seed
DIAG_NAMES = ("frames", "rx_errors", "crc_failures", "runts", "oversize", "non_ethercat",
              "malformed", "datagrams", "datagrams_addressed", "wkc_increments", "latency",
              "trace_count")
RING = 32                        # frames kept for a failure pcap


def read_manifest(path):
    rows = []
    for line in open(path):
        line = line.split("#", 1)[0].strip()
        if line:
            name, quick, soak = line.split()
            rows.append((name, int(quick), int(soak)))
    return rows


def plan_runs(manifest, size, base_seed=1):
    """One dict per bench run. Frames are scaled up evenly so the total reaches its minimum;
    chain runs once per K, clocks once per clock case, long scenarios split by seed."""
    col = 1 if size == "quick" else 2
    total = next((r[col] for r in manifest if r[0] == "total"), 0)
    mins = [(r[0], r[col]) for r in manifest if r[0] != "total"]
    scale = max(1.0, total / max(1, sum(n for _, n in mins)))
    runs = []
    for name, need in mins:
        frames = math.ceil(need * scale)
        if name == "chain":
            parts = [(k, base_seed) for k in CHAIN_K]
        elif name == "clocks":
            parts = [(DEFAULT_K, 4 * base_seed + i) for i in range(4)]   # seed % 4: the clock cases
        else:
            parts = [(DEFAULT_K, base_seed + i) for i in range(math.ceil(frames / PART_FRAMES))]
        each = math.ceil(frames / len(parts))
        runs += [dict(scenario=name, k=k, seed=seed, frames=each) for k, seed in parts]
    return runs


def run_name(r):
    return f"{r['scenario']}_k{r['k']}_s{r['seed']}"


def parse_diag(reply):
    try:
        d = parse_frame(reply, check_fcs=False)[0]
    except (FrameError, IndexError, struct.error):
        return {}
    if len(d.data) < 24:
        return {}
    return dict(zip(DIAG_NAMES, struct.unpack_from("<12H", d.data)))


def run_one(spec, binary, out_dir, write_pcaps):
    start = time.time()
    config = clock_config(spec["scenario"], spec["seed"])
    res = dict(spec, config=config, metrics={}, injected={}, diag={}, failures=[], sim_ps=0,
               wall_s=0.0, cycles=0, error="")
    link = None
    packets, ring = [], collections.deque(maxlen=2 * RING)
    try:
        link = Link(binary, config)
        chain = Chain(spec["k"])
        master = Master(spec["scenario"], spec["seed"], k=spec["k"], frames=spec["frames"])
        checker = Checker()
        final_index, generated, sent_n = None, 0, 0
        status0 = status = None

        def absorb(sent, replies, status_):
            for s in sent:
                checker.on_sent(s)
            for r in replies:
                owner = checker.on_reply(r)
                if owner is not None and owner == final_index:
                    res["diag"] = parse_diag(r["data"])
                ts = r["t_start_ps"] / 1e12
                ring.append((ts, r["data"]))
                if write_pcaps:
                    packets.append((ts, r["data"]))
            checker.on_status(status_)

        while True:
            batch = master.next_batch(BATCH)
            if not batch:
                break
            want, wire = 0, 0
            for tx in batch:
                generated += 1
                is_final = master.final_reads and tx is master.final_reads[0]
                if is_final:                    # the diag counters as they stand before it
                    before, trace_len = copy.deepcopy(chain.dut.counters), len(chain.dut.trace)
                dut_input, expected = chain.forward(tx.frame, rx_err=tx.er_nibble >= 0)
                if dut_input is None:
                    continue
                fill = None
                if is_final and expected is not None:
                    expected, fill = diag_reply(expected, before, trace_len)
                link.send(dut_input, ifg=tx.ifg, er_nibble=tx.er_nibble)
                checker.expect(sent_n, dut_input, expected, fill)
                if is_final:
                    final_index = sent_n
                ts = (status["t_ps"] if status else 0) / 1e12
                ring.append((ts, dut_input))
                if write_pcaps:
                    packets.append((ts, dut_input))
                sent_n += 1
                want += expected is not None
                wire += (16 + 2 * len(dut_input) + tx.ifg) * NIBBLE_PS
            s, r, _, status = link.run(want, wire + ALLOWANCE_PS)
            status0 = status0 or status
            absorb(s, r, status)
            if checker.failures and not res["failures"]:
                res["failures"] = list(checker.failures)        # first failure: keep the frames before it
                res["ring"] = list(ring)
        s, r, _, status = link.run(0, ALLOWANCE_PS)           # let the last replies come back
        absorb(s, r, status)
        checker.close(status["t_ps"])
        res.update(frames=generated, metrics=checker.metrics, injected=master.injected,
                   failures=checker.failures[:50], sim_ps=status["t_ps"], cycles=status["core_cycles"])
    except (LinkError, OSError) as e:
        res["error"] = str(e)
    except Exception:                                          # a bug here must not look like a pass
        res["error"] = "runner: " + traceback.format_exc(limit=3).strip().replace("\n", " | ")
    finally:
        if link:
            link.close()
    res["wall_s"] = time.time() - start
    name = run_name(spec)
    if write_pcaps and packets:
        write_pcap(os.path.join(out_dir, name + ".pcap"), packets)
    ring_frames = res.pop("ring", list(ring))
    if res["failures"] or res["error"]:
        write_pcap(os.path.join(out_dir, f"fail_{name}.pcap"), ring_frames)
        with open(os.path.join(out_dir, f"fail_{name}.json"), "w") as f:
            json.dump(dict(scenario=spec["scenario"], k=spec["k"], seed=spec["seed"],
                           frames=spec["frames"], config=config, error=res["error"],
                           failures=res["failures"],
                           rerun=f"python3 designs/ecat_esc/netsim/run_netsim.py --only {name} ..."),
                      f, indent=1)
    evidence = fault_evidence(res["injected"], res["diag"], require_all=spec["scenario"] == "faults")
    print(f"{name}: frames {res['frames']} {res['metrics']} evidence {evidence or 'ok'} "
          f"error {res['error'] or '-'} wall {res['wall_s']:.1f}s", flush=True)
    return res


def _worker(args):
    return run_one(*args)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--binary", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--size", choices=["quick", "soak"], default="quick")
    p.add_argument("--out", required=True)
    p.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--only", help="run only the named run (scenario_kK_sSEED)")
    a = p.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    runs = plan_runs(read_manifest(a.manifest), a.size, a.seed)
    if a.only:
        runs = [r for r in runs if run_name(r) == a.only]
    start = time.time()
    jobs = [(r, a.binary, a.out, a.size == "quick") for r in runs]
    with multiprocessing.Pool(min(a.jobs, len(jobs) or 1)) as pool:
        results = pool.map(_worker, jobs, chunksize=1)
    with open(os.path.join(a.out, "results.json"), "w") as f:
        json.dump(dict(size=a.size, wall_s=time.time() - start, runs=results), f, indent=1)
    print(f"{len(results)} runs in {time.time() - start:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
