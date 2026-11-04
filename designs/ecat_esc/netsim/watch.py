"""Watch mode for the live view: one scenario through the chain and the bench, paced for a
person, publishing a record per frame to gui/server.py (and to events.jsonl for replay).

A "packet" record carries the frame as the RTL received it, its reply, what the model
expected when they differ, the working counter after every model slave, the latency, and
the RTL's own events while the frame was inside it. At speed 8 and below every frame is
sent alone and animated; above that frames go in batches and a few per second are
recorded, while every mismatch is still published. Not a gate."""
import copy
import json
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "gui"), os.path.join(HERE, "..", "model")]

from check import ALLOWANCE_PS, Checker, diag_reply  # noqa: E402
from ecat import FrameError, parse_frame  # noqa: E402
from link import Link  # noqa: E402
from master import Master  # noqa: E402
from network import Chain  # noqa: E402
from scenarios import clock_config  # noqa: E402
from server import GuiServer  # noqa: E402

NIBBLE_PS = 40_000
SLOW_SPEED = 8                 # at or below: one frame at a time, every frame animated
FRAME_SECONDS = 2.4            # wall time per frame at speed 1 (the page animates in it)
DIAG_EVERY = 25                # frames between diag reads in watch mode
RECORD_PER_S = 5               # frames recorded per wall second at high speed


def wkc_total(frame):
    try:
        return sum(d.wkc for d in parse_frame(frame, check_fcs=False))
    except (FrameError, struct.error, IndexError):
        return None


class Publisher:
    def __init__(self, server, log_path):
        self.server = server
        self.log = open(log_path, "w")
        self.t0 = time.time()

    def __call__(self, ev):
        ev["w"] = round(time.time() - self.t0, 3)
        self.server.publish(ev)
        self.log.write(json.dumps(ev) + "\n")
        self.log.flush()


def watch(binary, scenario, k, seed, frames, port, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    srv = GuiServer(port=port)
    srv.start()
    pub = Publisher(srv, os.path.join(out_dir, "events.jsonl"))
    print(f"live view on http://127.0.0.1:{port}  ({scenario}, K={k}, seed {seed})", flush=True)

    config = dict(clock_config(scenario, seed), event_level=1)
    link = Link(binary, config)
    chain = Chain(k)
    master = Master(scenario, seed, k=k, frames=frames)
    diag_tx = master.final_reads[0] if master.final_reads else None
    checker = Checker()
    pub(dict(type="config", scenario=scenario, k=k, seed=seed, frames=frames,
             rx_ppm=config["rx_ppm"], tx_ppm=config["tx_ppm"], drift=config["drift_ppm_per_s"]))

    pending = {}                 # bench index -> packet record still waiting for its reply
    sent_n = generated = 0
    last_record = 0.0
    status = {"t_ps": 0, "core_cycles": 0}
    started = time.time()
    done = False
    while True:
        c = srv.controls
        if c["paused"] and not srv.take_step():
            time.sleep(0.05)
            continue
        speed = c["speed"]
        slow = speed <= SLOW_SPEED and not done
        if done:
            time.sleep(0.2)
            continue
        n = 1 if slow else 32
        batch = []
        for _ in range(n):
            if diag_tx is not None and generated and generated % DIAG_EVERY == 0 and \
                    (not batch or batch[-1] is not diag_tx):
                batch.append(diag_tx)
            batch += master.next_batch(1)
            generated += 1
        batch = [t for t in batch if t is not None]
        if not batch:
            done = True
            pub(dict(type="done", **checker.metrics))
            continue
        want, wire = 0, 0
        for tx in batch:
            is_diag = diag_tx is not None and tx.frame == diag_tx.frame
            if is_diag:                          # the diag counters as they stand before it
                before, trace_len = copy.deepcopy(chain.dut.counters), len(chain.dut.trace)
            hops = []
            dut_input, expected = chain.forward(tx.frame, rx_err=tx.er_nibble >= 0, hops=hops)
            if dut_input is None:
                continue
            fill = None
            if is_diag and expected is not None:
                expected, fill = diag_reply(expected, before, trace_len)
            record = slow or time.time() - last_record > 1.0 / RECORD_PER_S
            if record:
                last_record = time.time()
            link.send(dut_input, ifg=tx.ifg, er_nibble=tx.er_nibble, record=record)
            checker.expect(sent_n, dut_input, expected, fill)
            pending[sent_n] = dict(type="packet", i=sent_n, record=record, fault=tx.fault, diag=is_diag,
                                   hops=[wkc_total(tx.frame)] + [wkc_total(h) for h in hops],
                                   hex_in=dut_input.hex(), expected=expected.hex() if expected else None,
                                   rtl=[])
            sent_n += 1
            want += expected is not None
            wire += (16 + 2 * len(dut_input) + tx.ifg) * NIBBLE_PS
        sent, replies, events, status = link.run(want, wire + ALLOWANCE_PS)
        for s in sent:
            checker.on_sent(s)
            if s["index"] in pending:
                pending[s["index"]]["t0"] = s["t_start_ps"]
        recorded = sorted((p for p in pending.values() if p["record"] and "t0" in p), key=lambda p: p["t0"])
        for e in events:                         # each event to the latest recorded frame started before it
            owner = None
            for p in recorded:
                if p["t0"] <= e["t_ps"]:
                    owner = p
            if owner is not None:
                owner["rtl"].append([e["t_ps"], e["block"], e["kind"], e["a"], e["b"]])
        failures_before = len(checker.failures)
        for r in replies:
            owner = checker.on_reply(r)
            p = pending.pop(owner, None) if owner is not None else None
            if p is None:
                continue
            bad = len(checker.failures) > failures_before
            failures_before = len(checker.failures)
            p.update(t1=r["t_end_ps"], latency_ns=(r["t_start_ps"] - p.get("t0", r["t_start_ps"])) / 1000,
                     hex_out=r["data"].hex(), match=not bad)
            if bad:
                p["expected"] = checker.failures[-1]["expected"]
            if p["record"] or bad:
                pub(p)
        checker.on_status(status)
        for i in [i for i, p in pending.items() if p.get("t0", 0) + 4 * ALLOWANCE_PS < status["t_ps"]]:
            pending.pop(i)                       # missing replies: counted by the checker
        wall = time.time() - started
        pub(dict(type="stats", sim_us=status["t_ps"] / 1e6, cycles=status["core_cycles"],
                 fps=checker.metrics["replies"] / wall if wall else 0, **checker.metrics))
        if slow:
            time.sleep(FRAME_SECONDS / speed)


def view(events_path, port):
    """Replays a watch run's events.jsonl at its recorded pace, scaled by the speed slider."""
    srv = GuiServer(port=port)
    srv.start()
    print(f"replay on http://127.0.0.1:{port}  ({events_path})", flush=True)
    with open(events_path) as f:
        evs = [json.loads(line) for line in f if line.strip()]
    last_w = 0.0
    for ev in evs:
        while srv.controls["paused"] and not srv.take_step():
            time.sleep(0.05)
        time.sleep(max(0.0, ev.get("w", last_w) - last_w) / srv.controls["speed"])
        last_w = ev.get("w", last_w)
        srv.publish(ev)
    while True:
        time.sleep(1)
