# ESC network simulation, with a live view

Status: implemented on branch `feat/ecat-netsim`; quick gate and soak pass (2026-11-04:
1,000,025 frames, 0 mismatches, about 7 min). Builds on
`2026-10-31-ecat-esc-frame-core-design.md`, `2026-11-01-ecat-esc-memory-design.md` and
`2026-11-01-ecat-esc-timing-design.md`.

## 1. Why

The ESC passes 92 cocotb tests and closes 100 MHz, but every test is short and targeted,
and every check compares the RTL with a reference model written from the same reading of
the spec. Before any tapeout decision the design needs long runs under the conditions of
a real line: PHY clocks off nominal and drifting, line-rate bursts, several slaves in a
chain, a master's start-up and cyclic traffic, and line faults. cocotb drives every MII
nibble from Python (about one large frame per second), so it cannot do this.

This milestone builds that simulation, a live view of it, and an audit of the model
against the LAN9253 datasheet. The next milestone runs the same master and scenarios on
a Terasic DE2-115 board (section 9).

## 2. Goals and success criteria

1. A Verilator test bench around the unchanged `esc_top` exchanges whole frames with a
   Python network over a pipe, with the core clock at its full ratio to the PHY clocks.
2. The Python side models a chain of K slaves ahead of the RTL slave, a master with
   start-up, cyclic and random traffic, and PHY-level faults. Every RTL reply is checked
   byte for byte against `SlaveModel`.
3. `./flow dnet` is a gate with the usual report and stamp (section 6), part of
   `./flow dall` after `dsim`. `./flow dnet NETSIM=soak` runs at least 1,000,000 frames.
4. A live GUI shows the network and the inside of the ESC as a block diagram with frames
   moving through it, driven by the RTL's own signals (section 5).
5. `./flow dselftest` proves `dnet` fails on seven injected faults (section 7.1).
6. Every model rule marked "verify" is checked against the LAN9253 datasheet, with each
   disagreement reported to the user, not silently changed (section 8).
7. No change to `rtl/`, to the existing gates, or to `spec.json`. The existing 92 cocotb
   tests and the unit tests still pass.

Non-goals: SII/EEPROM, the AL state machine, distributed clocks, more than one port, a
real master stack (SOEM) in the loop (section 10).

## 3. Structure

```
 Python (frame level)                                  C++ / Verilator (clock level)
 master (no I/O) -> network (K model slaves) -> link   <-pipe->  tb_netsim.cpp + esc_top
 checker, pcap writer, event log, GUI server
```

All new files are in `designs/ecat_esc/netsim/`:

| File | Job |
|---|---|
| `tb_netsim.cpp` | Verilator main loop, picosecond time, PHY RX driver, PHY TX monitor, probes |
| `probes.vlt` | Verilator configuration that makes the probed internal signals readable |
| `link.py` | Starts the simulator, speaks the pipe protocol (section 3.2) |
| `network.py` | The chain: K `SlaveModel`s, then the RTL slave |
| `master.py` | The master, with no I/O: produces frames, consumes replies |
| `scenarios.py` | The traffic (section 4) |
| `check.py` | Reply comparison, counter and flag checks, fault evidence |
| `gui/` | `server.py` (standard library only) and `index.html` (plain JavaScript and SVG) |
| `run_netsim.py` | Command line; writes the report, pcaps and event log |
| `manifest.txt` | Scenarios the gate requires, with minimum frame counts |

`master.py`, `network.py`, `scenarios.py` and `check.py` use only the Python standard
library and run on Python 3.12 (container) and 3.13 (Windows host), so the board
milestone can drive them from the host.

### 3.1 Test bench (`tb_netsim.cpp`)

- Three clocks: `rx_clk` and `tx_clk` at 25 MHz, each with its own ppm offset, a slow
  drift (ppm per simulated second, bounded to the configured range) and a random phase;
  `clk` at 100 MHz. The loop advances to the next edge of whichever clock is due.
- PHY RX driver: adds preamble and SFD, drives nibbles low first, holds `rx_dv`, asserts
  `rx_er` at a given nibble if asked, keeps an inter-frame gap of at least 24 nibbles
  (96 bit times). Frames queue, so back-to-back traffic is real.
- PHY TX monitor: collects nibbles while `tx_en`, strips preamble and SFD, returns the
  frame with FCS and its simulated start and end times.
- Flags: `rx_overflow`, `tx_overflow` (sticky), `tx_underrun` (pulse, latched by the
  bench) and the access engine's `late` (`u_exec.u_acc.late`, a byte passed before its
  read data was held; probed, latched by the bench) are reported in every STATUS.
- Each scenario run is its own simulator process from reset, so runs are independent and
  `run_netsim.py` runs them in parallel across the container's cores.
- Same RTL file list and parameters as `dsim`; default configuration (3 FMMUs, 4
  SyncManagers, 256-byte RAM, diagnostics on), others selectable.

### 3.2 Pipe protocol

Messages on the simulator's stdin and stdout: a 4-byte little-endian length, a 1-byte
type, a payload. Any length or type the reader does not expect is a protocol error,
which ends the run with status `error`.

| Type | Direction | Payload |
|---|---|---|
| CONFIG | to sim | seed, rx and tx ppm, drift, phases, event level |
| SEND | to sim | inter-frame gap in nibbles (>= 24), `rx_er` nibble or none, frame bytes with FCS |
| RUN | to sim | reply count to wait for, timeout in simulated ps |
| REPLY | from sim | start and end time in ps, frame bytes |
| STATUS | from sim | time in ps, core cycles, flags seen since last STATUS, sticky flags |
| EVENT | from sim | time in ps, block, kind, arguments (section 5.2) |
| QUIT | to sim | none |

Truncated frames, bad FCS, runts and oversize frames are built in Python and sent as
ordinary SENDs.

### 3.3 Network and checking

- A frame from the master passes through model slaves 1..K in order, each a
  `SlaveModel` with its own state, as on the forward path of a real line. The result is
  what the RTL receives.
- The RTL is always last (it has one port). Its reply must equal
  `SlaveModel.process()` of the RTL's own model instance for that input, byte for byte.
- The return path changes nothing.
- A frame with no reply within 2 x (longest frame time) + forwarding latency in
  simulated time is a missing reply. A reply that matches no sent frame is unexpected.
  Both are counted, logged with seed and frame index, and the run continues.

## 4. Scenarios

Every scenario is seeded and reproducible; a failure reports seed and frame index.

1. `startup`: BRD to count slaves (WKC = slave count), auto-increment APWR station
   addresses, FPRD check, FPWR of FMMUs (0x0600) and SyncManagers (0x0800), BWR clear of
   process RAM.
2. `cyclic`: one LRW per cycle over every slave's FMMU window, occasional FPRD status
   reads, cycle times 1 ms, 250 us and back-to-back. Process data changes every cycle.
3. `random`: all 13 supported commands, random addresses and lengths, several datagrams
   per frame up to the maximum frame, random FMMU layouts including overlapping and
   aliased windows, windows ending at the RAM end and straddling 0x1000. Unsupported
   commands (ARMW, FRMW) pass as the model predicts (as NOP).
4. `faults`: `rx_er` at a random nibble, truncated frames, bad FCS, runts, oversize,
   non-EtherCAT frames (IPv6, LLDP), minimum gaps. The next good frame after each fault
   must be correct.
5. `clocks`: RX and TX ppm combinations up to +-100 with drift; the worst case is RX
   fast, TX slow, maximum-size frames at minimum gap.
6. `chain`: K = 0, 1, 7 and 63 model slaves.
7. `replay`: real pcaps from `designs/ecat_esc/captures/` once they exist; skipped and
   absent from the manifest until then.

A failure writes a pcap of the failing frame and the frames before it, plus the CONFIG
and seed to rerun it, so it can be turned into a cocotb test.

## 5. Live GUI

### 5.1 What it shows

- Network row: master, model slaves, the RTL ESC, and the return path. Each frame is a
  packet moving along it; its WKC is shown rising at each slave.
- ESC row: MII RX, RX FIFO, frame parser, datagram execution, FMMU, memory port (RAM,
  registers, diag), TX FIFO, MII TX. The packet moves through these from probe events
  (section 5.2): frame start and end in the parser, each datagram's command and address,
  FMMU hits, memory reads and writes (also shown on a heat map of the RAM), WKC update.
- Match state: green on match, red on mismatch; optional pause on mismatch.
- Click a packet: decoded datagrams, expected and actual side by side.
- Panels: frames per second, diag counters against the master's counts, flags,
  forwarding latency, PHY clock ppm.

### 5.2 Probes and events

`probes.vlt` marks selected internal signals readable (Verilator `public_flat_rd`), so
`rtl/` is unchanged. The bench samples them each core clock and emits EVENT messages on
changes. Probe signal names are fixed in the plan. Event level from CONFIG: none (gate),
sampled (only frames the GUI animates, plus a ring buffer of the last 64 frames' events,
written out when a mismatch occurs), or all (watch mode).

### 5.3 Pacing and controls

- Watch mode: the master sends slowly; each hop animates in about 0.5 s; true simulated
  times are shown. Pause, single step, speed slider.
- Full speed: the simulation runs flat out; panels update live; a few frames per second
  are animated; every mismatch is shown.

### 5.4 Serving

`gui/server.py` (standard library) serves `index.html`, streams events to it as
server-sent events, and takes pause, step and speed as POSTs. It listens on port 8765
inside the container. `compose.yaml` gains `ports: ["127.0.0.1:8765:8765"]` (this PC
only); the container is recreated once (`./flow down`, `./flow up`). The image tag does
not change. No external scripts or fonts.

Every run writes its event log (`events.jsonl`), and `./flow dnet-view RUN=<dir>`
replays it. The GUI never gates anything.

## 6. Flow integration and the gate

| Command | |
|---|---|
| `./flow dnet` | gate, quick size; needs the `dlint` and `dsim` stamps; in `dall` after `dsim` |
| `./flow dnet NETSIM=soak` | gate, at least 1,000,000 frames; not in `dall` |
| `./flow dnet GUI=1` | watch mode with the GUI on `http://127.0.0.1:8765`; not a gate |
| `./flow dnet-view RUN=<dir>` | replay a run in the GUI |

Report `build/ecat_esc/reports/dnet.json` (`dnet_soak.json` for the soak), stamp only on
pass. Metrics: frames sent, replies, mismatches, missing, unexpected, flags, diag counter
mismatches, frames per scenario, simulated time, wall time, core cycles per second.

`pass` requires all of:

1. Every scenario in `manifest.txt` ran at least its minimum frames: 1,000 each in the
   quick run, at least 20,000 in total; at least 1,000,000 in total for the soak.
2. Mismatches, missing replies and unexpected replies are zero.
3. No flag (overflow, underrun, `late`) set in any run whose clocks are within +-100 ppm.
4. Fault evidence: for each fault class the ESC's diag counter (read over EtherCAT at
   0x0E00 at the end of each run) equals the number injected (rx errors, CRC failures,
   runts, oversize, non-EtherCAT, malformed), and at least one of each was injected in
   `faults`, so a fault scenario that injects nothing fails.
5. The diag frame and datagram counters equal the master's own counts. The counters
   saturate at 2^CNT_W - 1 (65,535 by default), so the expected value saturates too; a
   run that exceeds that is checked on its fault counters, which stay far below it.

`fail` when any is missed. `error` when the simulator crashes, the protocol desyncs, the
final STATUS is missing, or the run ends short of its frame counts.

If the quick run takes more than 30 minutes, the frame counts are the user's decision;
they are not lowered to save time.

## 7. Verification of the harness

### 7.1 Self-test (`./flow dselftest` grows)

Each mutant is a copy under `build/`; `rtl/` is not touched. Each must make `dnet` fail;
`dselftest` fails if any passes.

| Mutant | Caught by |
|---|---|
| WKC off by one on LRW only | mismatches in `cyclic` |
| RAM one clock slower (the final-review mutant) | mismatches or `late` at line rate, with the core at 76 MHz (`--core-mhz`); at 100 MHz a byte is 8 clocks and the extra clock is harmless |
| Transmitter always waits the full 12-byte gap | overflow or underrun at +-100 ppm |
| TX FIFO too shallow | underrun in `clocks` |
| Bad FCS not counted by `diag` | fault evidence (criterion 4) |
| Checker: one expected byte flipped | mismatch, proves the comparison compares |
| Harness drops one reply | missing reply |

### 7.2 Unit tests (`./flow test`)

Pipe protocol encode and decode, including bad lengths and truncated messages; chain
processing; master behaviour against a fake transport; scenario reproducibility from a
seed; GUI server smoke test (serves the page, streams an event, accepts the controls).

The GUI's visuals are checked by eye.

## 8. Model audit

`designs/ecat_esc/model/AUDIT.md`: one row per model rule marked "verify" and per
behaviour the scenarios rely on (WKC rules, register map, read-only registers,
auto-increment, broadcast read OR, FMMU mapping, bad-frame handling). Each row names the
model location, the LAN9253 datasheet section (DS00003421B in `build/ref/`, git-ignored),
and the verdict: agrees, disagrees, or not covered by the datasheet. Rows cite section
numbers and paraphrase; the datasheet is not quoted or copied into the repo. Every
"disagrees" goes to the user before the model or RTL changes.

## 9. Next milestone (not in this spec)

The same master, scenarios and GUI on a Terasic DE2-115 (Cyclone IV E EP4CE115F29C7,
114k LEs): Quartus Prime Lite, a Windows Npcap transport, and the unchanged RTL on port 0's
Marvell 88E1111 PHY, jumpered to MII mode (the board manual's JP1/JP2; check on the board)
so the PHY's 25 MHz nibble clocks drive `esc_top` directly, with no RGMII adapter. The
second PHY is spare until multi-port forwarding. A Yosys estimate puts the default
configuration at about 9.0k LUT4 and 7.2k flip-flops, under 10% of the device.

Board-level work, all outside the core: pin assignments, a PLL from the 50 MHz oscillator
to the core clock, reset, the PHY reset pin, and a small MDIO block that restricts the
88E1111's advertisement to 100 Mbit full duplex (by default it advertises 1000 Mbit,
which MII cannot carry; forcing the host NIC to 100 full is the fallback). The core must
run faster than 75 MHz. At exactly 75 MHz a byte is 6 clocks only on average: with the RX
clock faster than nominal some bytes arrive 5 clocks apart, and the netsim (`--core-mhz 75`,
2026-11-04) shows broadcast-read mismatches. The quick gate passes at 76 MHz; with the
PHYs' +-100 ppm and the board oscillator's own tolerance, a PLL output such as 80 MHz
(50 MHz x 8/5) leaves margin. Whether the -7 device closes that is unknown until Quartus
runs.

This replaces the earlier Cyclone 10 LP Evaluation Kit (EK-10CL025U256) target, which
needed an RGMII-to-MII adapter and would have been about 40% full.

## 10. Risks

- The model and RTL share one reading of the spec; the audit (section 8) is the only
  independent check until real captures exist.
- Simulation speed is unmeasured; the soak may take many hours.
- Internal probe names follow the RTL; renaming an RTL signal breaks `probes.vlt`. The
  bench looks every probe up at start-up and exits with an error if one is missing, so a
  rename shows as `error`, never as a quiet run without events.
- The GUI port needs the container recreated once.

## 11. Out of scope

SOEM or TwinCAT in the loop (a TAP bridge can be added on the pipe later), SII/EEPROM,
AL state machine, distributed clocks, multi-port forwarding, the board milestone.
