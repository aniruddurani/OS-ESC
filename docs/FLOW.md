# Flow guide

Open-source IC design flow on sky130A. Milestone 1 is a CMOS inverter taken from
schematic to post-layout simulation. Designs: `docs/specs/`.

## Running it

Everything runs inside the `sf-iic` container (image `hpretl/iic-osic-tools:2026.10`,
repo mounted at `/foss/designs/silicon-flow`). Use only the wrapper, from Git Bash
on Windows:

```bash
./flow up              # start the container (any ./flow <target> also starts it)
./flow all             # netlist, sim, layout, drc, lvs, pex; writes build/inv/reports/summary.json
./flow png             # build/inv/inv.png, a picture of the layout (not a gate)
./flow corners         # whole flow at 7 corners (tt ss ff sf fs, -40/27/125 C), build/inv/corners/
./flow selftest        # proves the DRC and LVS gates catch real faults
./flow test            # unit tests for scripts/flowlib
./flow <stage> -B      # rerun that stage and everything upstream of it
./flow shell           # interactive shell in the container
./flow down
```

The Docker CLI must be on PATH: `C:\Program Files\Docker\Docker\resources\bin`. Docker
Desktop must be running (the wrapper exits 2 with a message if it is not).

## Flow order and gates

`netlist` (Xschem) -> `sim` (ngspice) -> `layout` (Magic) -> `drc` (Magic + KLayout) ->
`lvs` (Magic extract + Netgen) -> `pex` (Magic extract with parasitics + ngspice).
Do not work on a later stage while an earlier one fails.

Each stage writes `build/<design>/reports/<stage>.json` (`stage, design, status, metrics,
errors, log`) and exits non-zero unless `status` is `pass`. `status` is `fail` when the
design misses the criteria and `error` when the tool crashed or its success evidence was
missing. A stage passes only on positive evidence; never treat "no error text" as a pass.

Stages are chained through pass stamps (`build/<design>/stamps/<stage>.pass`), written
only when the stage passes, so a failed stage's report never counts as a finished
prerequisite and later stages do not run on a failing design. `summary.json` marks a
pass report with no stamp as `stale`. To rerun one stage, delete its stamp.

## Fixing loop

Read `build/<design>/reports/*.json` first, raw logs in `build/<design>/logs/` only when
the report is not enough. Fix the sources, never `build/`: schematic
`designs/inv/xschem/`, testbench `designs/inv/tb/`, layout generator
`designs/inv/layout/inv.tcl`, limits `designs/inv/spec.json`.

## Never, without explicit sign-off

- Loosen `spec.json` or any pass criterion to make a gate pass.
- Waive, skip or disable a DRC rule, or change which KLayout rule groups run.
- Edit PDK files, or hand-edit GDS or any generated file.
- Change the pinned image tag (do it in its own commit, after `./flow all` and
  `./flow selftest` pass on the new tag).
- Treat tapeout steps (shuttle prechecks, density fill, antenna, submission) as done.
  They need a review and sign-off.

## Inverter and limits

nfet W=1 / pfet W=2, L=0.15 um, 1.8 V, 10 fF load; `./flow corners` runs tt/27, ss/125, ss/-40, ff/-40, ff/125, sf/27, fs/27 (all pass, same limits). Limits in
`designs/inv/spec.json`: VM 0.70-1.10 V, VOH >= 1.75 V, VOL <= 0.05 V, tpHL and tpLH
<= 100 ps. Last run: VM 0.872 V, tpHL 34 ps, tpLH 45 ps; post-layout tpHL +2.7%, tpLH +0.6%.

## Things that are not obvious (each cost a debugging round)

- **PDK variable.** The container's login shell exports `PDK=ihp-sg13g2`. The Makefile
  sets `PDK = sky130A` with plain `=` and exports `PDKPATH`, `SPICE_USERINIT_DIR`,
  `KLAYOUT_PATH`, `STD_CELL_LIBRARY` the way the image's `sak-pdk` does. Keep that.
- **Tool and PDK paths** are defined once, at the top of the `Makefile`
  (`MAGICRC`, `NETGEN_SETUP`, `XSCHEMRC`, `KLAYOUT_DRC`, `KLAYOUT_LYP`, `NGSPICE_LIB`);
  `make env` checks they exist. Tool versions: Magic 8.3.685, ngspice-47, Netgen 1.5.324,
  KLayout 0.30.12, Xschem 3.4.8RC.
- **Xschem** writes a top-level schematic's header as `**.subckt`; `top_is_subckt` drops the
  devices. The `netlist` rule uncomments `**.subckt`/`**.ends` and removes the bare `.end`.
- **ngspice batch** aborts a transient when `.dc` and `.tran` cards share a deck, so the
  testbench runs its analyses from a `.control` block.
- **KLayout DRC deck** (`sky130A_mr.drc`) runs no front/back-end rules unless
  `feol`/`beol` are set, and reports a clean database otherwise. The `drc` rule passes
  `-rd feol=true -rd beol=true -rd offgrid=true` and absolute `input`/`report` paths, and
  the CLI errors if the KLayout log does not show them on.
- **Magic gencell** in `-dnull` mode drops a stray instance into the edited cell, so
  devices are generated in a `scratch` cell and placed with `getcell ... child 0 0 parent x y`.
  Stale `.mag` files in `build/` make it open a dialog, so the `layout` rule deletes them.
  `findlabel` does not work on unsaved cells; read the label from the saved `.mag`.
- Shapes painted in the top cell must each meet minimum metal area on their own.
- `make selftest` injects faults through `::post_wire_hook` (see `tests/fixtures/`).
- Line endings are LF (`.gitattributes`). Python text-mode writes produce CRLF on Windows.

## Digital flow: the EtherCAT slave controller (`designs/ecat_esc`)

Verilog RTL in `rtl/`, cocotb tests in `tb/`, a Python reference model in `model/`,
LibreLane configs in `librelane/`. Spec: `docs/specs/` (the 2026-10-31 ecat-esc
file). Same rules as the analog flow: each stage
writes `build/ecat_esc/reports/<stage>.json` and a pass stamp only on pass; a later
stage never runs on a failing earlier one.

```bash
./flow dlint                     # Verilator, all warnings, every module as its own top: zero allowed
./flow dsim                      # cocotb: every test in tb/manifest.txt must run and pass
./flow dsynth [LLCFG=min|default|max]   # Yosys via LibreLane: no latches, no unmapped cells
./flow dpnr   [LLCFG=...]        # place, route, DRC (Magic+KLayout), LVS, antenna, 9-corner timing
./flow dconfigs                  # area / frequency table for min, default, max (a report, not a gate)
./flow dnet [NETSIM=soak]        # network simulation: every RTL reply equal to the model's (below)
./flow dnet GUI=1 [SCENARIO=cyclic] [GUI_K=3]   # live block diagram on http://127.0.0.1:8765, not a gate
./flow dnet-view [RUN=<dir>]     # replay a watch run's events.jsonl in the live view
./flow dnettest                  # tests of the netsim bench and pipe link (not a gate)
./flow dselftest                 # proves the gates catch real faults (lint, wrong WKC, impossible clock, nine for dnet)
./flow test                      # unit tests: scripts/flowlib and the reference model
```

Network simulation (`designs/ecat_esc/netsim`, spec `2026-11-03-ecat-esc-netsim-design.md`):
a Verilator bench (`tb_netsim.cpp`) runs the unchanged `esc_top` with the core clock at
100 MHz (settable) and 25 MHz PHY clocks with ppm offset and drift (positive ppm is a slower clock), and
exchanges whole frames over a pipe with Python: a master, a chain of K model slaves ahead of
the RTL, and a checker. The `dnet` gate (`dnet.json`, about 100 s, 20k frames) and the soak
(`NETSIM=soak`, `dnet_soak.json`, about 7 min, 1M frames in 25 runs) pass on zero mismatches,
missing or unexpected replies, FIFO flags and late replies, with every injected fault counted
in the diag counters. A failure writes `build/ecat_esc/netsim/<size>/fail_<run>.{json,pcap}`;
rerun one run with `run_netsim.py --only <scenario>_k<K>_s<seed>` and `--event-level 1`.

- **GUI port** 8765 is published by `compose.yaml` (`127.0.0.1:8765:8765`); a container
  created before that needs `./flow down` and `./flow up` once.
- **Probes** (`netsim/probes.vlt`) name internal RTL signals. Renaming one makes the bench
  exit with an error at start-up (status `error`), never a quiet run without events.
- **`mii_tx` gap**: 24 nibbles idle, but a frame already waiting starts after 16
  (`GAP_MIN`). Without that, a transmitter 100 ppm slower than its receiver backs up the TX
  FIFO on back-to-back maximum frames (`test_back_to_back_at_ppm_limits`).
- **`mii_rx` last byte**: it leaves one clock after `rx_dv` falls, so it keeps the two-clock
  spacing of the other bytes. Released as `rx_dv` fell it came half a byte early, 4 core clocks
  at 100 MHz against the 6 the core assumes; a frame cut inside a datagram ends on a data byte,
  and the soak's `faults_k3_s2` raised `late`.
- **Core clock** (`NETSIM_ARGS="--core-mhz 76"`, default 100): the gate runs at 100 MHz,
  8 clocks per byte, where a memory one clock slower still passes; at 76 MHz it fails, and
  `dselftest`'s `net_ramlat` checks that. Exactly 75 MHz is too slow: a byte is 6 clocks
  only on average, an RX clock faster than nominal leaves 5, and broadcast reads mismatch
  (`random_k3_s1`, 2026-11-04). The board needs a core clock above 75 MHz with ppm margin.
- **The model executes like the RTL**: a frame cut inside a datagram applies the bytes that
  passed (not counted, no WKC); a logical datagram runs byte by byte in stream order, so two
  logical bytes FMMU-mapped onto one physical byte see each other's writes.
- The `dsim` stamp does not depend on `model/*.py` (the `dnet` stamp does): after a model
  change delete `stamps/dsim.pass` to rerun the cocotb tests against it. It does depend on
  `captures/`.
- `dselftest`'s dnet checks: seven lint-clean RTL mutants with `dsim` stamped (LRW WKC, full
  gap only, four-entry FIFOs, a CRC counter that never counts, RAM one clock slower at
  76 MHz, AL status stuck at INIT, SII data one word off) and two harness faults
  (`NETSIM_ARGS="--sabotage checker|drop-reply"`).

FPGA prototype on a Terasic DE2-115 (`designs/ecat_esc/board/`, spec
`2026-11-04-ecat-esc-de2-115-design.md`): the unchanged `esc_top` (default configuration)
on ENET0's Marvell 88E1111 in MII mode, cabled to the PC's Ethernet port
(IPv4/IPv6 unbound). These stages run on the Windows host (Quartus Lite 25.1, Npcap,
MinGW `mingw32-make`), not in the container; `./flow`
hands them to `board.mk`. Same report and stamp rules; `bbuild` needs the container's
`dsim` stamp.

```bash
./flow bbuild          # Quartus compile; all timing checks on all three models, 80 MHz core, no unconstrained paths
./flow bprog           # load esc_board.sof over JTAG (SW19 on RUN); PHY must reach link_100fd
./flow bstatus         # board status over JTAG (not a gate)
./flow bnet [BNET=soak] # the netsim scenarios a NIC can produce, against the board
./flow bsoem           # two SOEM programs: low-level calls (BRD, addressing, FMMU, SM, 10,000 cyclic
                       # LRW) and the standard start-up to OP with 10,000 process-data cycles
./flow bcapture        # record SOEM's traffic into captures/board_soem_start.pcap (not a gate)
./flow bwatch [SCENARIO=cyclic] [GUI_K=3]   # live view on the board, http://127.0.0.1:8766, not a gate
./flow bselftest       # mutant, wrong image, harness faults, back-to-back runs, impossible clock
./flow bselftest STEP=nolink   # with the board's cable unplugged: bnet must be error
```

- **JP1 on pins 2-3** puts ENET0 in MII mode (1-2, the factory default, is RGMII). The PHY
  then links at 100 Mbit on its own; `phy_init` still writes regs 9/4/0 and polls reg 17.
- **Board status** is one In-System Sources and Probes instance ("ESC", works in Lite):
  probe `{BUILD_ID[23:0], 2'b0, PHY state, sticky FIFO flags}`, source 0 the ESC's soft reset.
  `status.tcl` finds the device by JTAG ID (Quartus names it `10CL120(Y|Z)/EP3C120/..`) and
  must query the instance before starting its own session. Each `quartus_stp` call takes
  about 5 s.
- **Build identity**: `bbuild` hashes `rtl/`, the board blocks, the SDC and the PLL settings
  into `build_id.txt` and compiles that into the image. `bprog`, `bnet` and `bsoem` are
  `error` unless the board reports the same ID, so a stamp cannot vouch for another image (a
  stage reruns only on stamps; the board can be reloaded by hand or left holding a mutant).
- **`bwatch` has no RTL events**: the FPGA has no probes, so the live view's inner blocks stay
  dark, the FIFO flags are not read live and its latency is the PC's round trip. Port 8766,
  because the container publishes 8765.
- **Soft reset before every board run**: the board keeps RAM, registers and diag counters
  between runs, unlike a fresh bench, so `run_board.py` resets the ESC (not the PHY) first.
- **Quartus Lite cannot parse `$error`** in a generate branch (even as SystemVerilog), so
  `esc_board.tcl` gives Quartus a copy of `rtl/` (`build/.../rtl_fpga/`) without the
  `RAM_BYTES` check in `pdram.v`, failing if that block is not found exactly once.
- **Npcap's inbound-only filter is unavailable** on the I219-V, so the NIC hands back the
  frames the PC sent; `wire_link.py` drops the first received copy equal to the frame in
  flight (one frame in flight). Round trip about 0.5 ms, about 1,750 frames/s.
- **SOEM v2.0.1**, not v1.4.0: v1.4.0's Windows layer builds only with MSVC. v2 is GPLv3 or
  commercial; the test tool is local and not distributed. The Npcap SDK is in
  `C:\npcap-sdk`.
- **SOEM calls wait 20 ms, not SOEM's 2 ms**: Windows passed 2 ms about once in 5,000 cycles,
  and on the first BRD under CPU load. A lost frame still fails.
- Results (2026-11-06, build 14373202): `bbuild` setup +3.031 ns / hold +0.099 ns at 80 MHz,
  8,995 LEs (7.9%); `bnet` 20,007 frames; soak 1,000,023 frames, no mismatch; `bsoem` both
  programs, 10,000 cycles each, about 0.49 ms; `bselftest` 22 checks and `STEP=nolink`.

AL and SII (`rtl/al_sii.v`, spec `2026-11-05-ecat-esc-al-sii-design.md`): SOEM's standard
start-up reaches OP on the board, with no mailbox, no PDI and no local side.

- **Device emulation** (0x0141 bit 0 = 1): AL status follows AL control. A write whose low 4
  bits are 1, 2, 3, 4 or 8 sets the state and any other value leaves it. No transition checks,
  no error flag, status code 0, so a master that expects a refusal gets none. AL control lands
  as bytes pass, so a frame that fails its FCS still changes the state.
- **The SII store is a ROM**: `model/sii.py` is the source, `scripts/gen_sii_rom.py` writes
  `rtl/sii_rom.v`, and `model/test_sii.py` holds the two equal. Never edit the `.v`. The ROM
  freezes whatever is in `sii.py` at tapeout, and the identity there is a placeholder (vendor
  0, product 1): a real vendor ID must come from the EtherCAT Technology Group before tapeout
  or TwinCAT.
- **SII data follows the address**, because SOEM sends the command bytes before the address in
  one datagram. BUSY and R64 read 0, so reads are 4 bytes. A write or reload command sets NACK
  and a NOP clears it. Past the table reads 0xFFFF.
- **DL status is the constant 0x0211** in `esc_core`: on the board the ESC is held in reset
  until the link is up.
- **Dependent registers**: the model's physical path reads everything, then writes; the RTL
  sees its own write. One read-write datagram spanning 0x0120 and 0x0132, or 0x0502 and 0x050C,
  would differ and means nothing to a master, so `random_datagram` moves such a datagram to RAM.
- **Dependent byte rules in two places**: `al_sii.v` and `_al_sii_write` in `model/ecat.py`
  must change together; `dnet` catches a difference.

Metric names the gates read are in `scripts/flowlib/digital.py` (`SYNTH_ZERO`,
`PNR_ZERO`, `PNR_NONNEG`, read off a real LibreLane v3.1.0.dev3 run). A skipped cocotb
test never counts as a pass: `test_replay_real_captures` is in the manifest (116 tests in
all) and replays both captures in `designs/ecat_esc/captures/`, recorded on the DE2-115:
`board_soem_start.pcap`, SOEM's real traffic with no allowed differences, and
`board_cyclic_k3_s1.pcap`, with one (see the folder's README). Rerecord them with `bcapture`
and `run_board.py` when a register a master reads changes. Both are our own RTL on real PHYs;
no capture from a slave of another make exists yet.

Things that are not obvious:

- **LibreLane needs the PDK variables** the Makefile exports (`PDK`, `PDK_ROOT`,
  `STD_CELL_LIBRARY`), because the container defaults to `ihp-sg13g2`. `dir::` paths in a
  config resolve against the design directory, so `runs/` lands in
  `designs/ecat_esc/librelane/runs/` (git-ignored).
- **Mutation-checking a stage**: delete the stage's report first, and change the RTL so
  it still lints clean. A mutation that fails lint leaves the previous `dsim.json` in
  place and looks like a pass.
- **cocotb reads pre-edge values** right after `await RisingEdge`: a register written on
  that edge shows its new value one edge later.
- **Bad frames are cut-through**: writes land as bytes pass; a bad frame leaves with an
  inverted FCS and is counted (spec section 6). Unverified against real hardware.
- The design assumes at least 6 core clocks between stream bytes (a line-rate byte is 8). At
  6-clock spacing the first read of a datagram is ready 2 clocks before its byte, but the
  physical one-byte-ahead prefetch and a logical read of a byte written by the previous offset
  have no spare clock: request issue plus memory latency must stay within 5 clocks. A RAM one
  clock slower fails test_prefetch and test_access at spacing 6 (checked 2026-11-03), so do not
  add a register stage on the memory port without restructuring those paths.
- **Timing at 100 MHz is set by the slow corner (ss/100C/1.60V), after routing.** At 10 ns
  the default configuration passes all nine corners with thin margin (setup +0.011 ns at
  `max_ss` on 2026-11-03, +0.62 ns on 2026-11-04 after the `mii_tx` gap change, +0.46 ns on
  2026-11-06 with `al_sii`; the margin moves by that much between runs, so read it as thin). Three things made that so, all in `designs/ecat_esc/librelane/*.yaml`:
  `PNR_CORNERS: [nom_ss_100C_1v60]` (by default the repair steps see only `nom_tt`, and the
  slow corner then misses by about 5 ns), a 4 ns `PL_RESIZER_SETUP_SLACK_MARGIN` and
  `RUN_POST_GRT_RESIZER_TIMING` with a 3 ns margin (routing roughly doubles net capacitance
  against the placement estimate, about 4 ns at the slow corner). Several corners in
  `PNR_CORNERS` made the repair give up (ss -41 ns). None of this touches the gate, the
  period or the SDC.
- **Fast timing loop**: `scripts/fast_timing.sh <tag> [corner[,corner]] [last step]`, run in the
  container from the repo root, takes about five minutes to post-CTS repair. It goes through a
  throwaway config because `-c PNR_CORNERS=[...]` reaches LibreLane as one string. It does not
  see the roughly 4 ns that routing adds, so a clean fast run is not a pass. Rank failures
  with `python3 scripts/timing_groups.py <tag> [corner|x|single] [count]`. Wait for the
  `exit=` line in the log (a `pgrep -f` inside `bash -lc` matches itself).
- **Wide carry chains on the start-up path of a datagram are split** (wide compares and some
  16-bit adds elsewhere close in one clock). Wide adds there use
  `rtl/addsub_cs.v` (two-clock carry-select add/subtract, tested in `tb/addsub_check.py`).
  `fmmu_plan` is a six-stage pipeline (`plan_done` 7 edges after `plan_len`) and needs `cfg`
  and `dg_addr` stable six clocks before `plan_len`; the parser holds the address at least 12.
  `dg_access` has a four-entry read-ahead queue; `fmmu_walk` is three stages; the staged
  fit check and the address counters (`wctr`, `rctr`) are in `dgram_exec`.
- `pdram_flop` is 16-byte groups with a two-level read, latency `MEM_LAT = 3`. The regfile and
  diag registers decode in stages (writes land one clock later), `esc_top` registers the RX
  FIFO output (one more clock of RX latency), and `esc_core` keeps seven reset copies
  (`keep`; whether Yosys leaves them unmerged is unchecked).
- **Antennas**: `dpnr` repairs after detailed routing, and each round routes again and can move
  a net. The default three rounds left one net on 2026-11-06 (102, 11, 1 violations), so
  `default.yaml` sets `DRT_ANTENNA_REPAIR_ITERS: 6`. That is effort, not a limit: the gate still
  wants zero. The report is `runs/<tag>/*-openroad-checkantennas-1/reports/`.
- The post-route report of the failing endpoint is in
  `runs/<tag>/*-openroad-stapostpnr/<corner>/max.rpt`; `./flow dpnr` runs take about an hour.
- `./flow dconfigs` (2026-11-03): min passes at 100 MHz (setup +1.34 ns, est. 115 MHz), default
  passes (+0.011 ns). Max (`RAM_BYTES=1024`) selects the SRAM macro and stops at floorplan:
  the configs give LibreLane no macro LEF/GDS yet (the ram1k/ram8k macro configs of the
  memory plan are not done).

## Not yet done

Resistive parasitics, the digital flow (LibreLane, the image's
renamed OpenLane) and tapeout prechecks. On the DE2-115 board: a TwinCAT run
(needs a real vendor ID and an ESI file), an I2C SII EEPROM, the second Ethernet port, flash
programming. Open shuttles seen on 2026-10-31: Tiny Tapeout GF (closes 7 Dec 2026) and SKY
(closes 30 Nov 2026); verify before committing to a PDK.
