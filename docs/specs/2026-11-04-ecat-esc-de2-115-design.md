# EtherCAT ESC on the DE2-115: FPGA prototype before tapeout

Status: implemented; on the real board `bnet`, the soak and `bsoem` pass (section 11).
Supersedes the sketch in section 9 of `2026-11-03-ecat-esc-netsim-design.md`.

## 1. Purpose

Before the ESC goes to silicon, run the unchanged RTL (`designs/ecat_esc/rtl/`) on a
Terasic DE2-115 (Cyclone IV E EP4CE115F29C7) against real Ethernet. The board must show:

1. The netsim scenarios that a NIC can produce, sent from a Windows PC over a cable,
   get replies equal to the reference model's (the same checker as the `dnet` gate).
2. An open-source master, SOEM, using its low-level datagram calls, finds the slave,
   addresses it, configures an FMMU and SyncManagers, and exchanges cyclic process data.
3. The tapeout configuration meets timing in the FPGA at the core clock the netsim
   requires (above 75 MHz; 80 MHz chosen).
4. Real captures exist for `test_replay_real_captures`.

It does not show sky130 timing, the ASIC memory macro or power; those stay with LibreLane.

## 2. Decisions

| Topic | Decision |
|---|---|
| SOEM depth | Low-level calls only (BRD, APWR, FPRD, FPWR, LRW). No AL state machine or SII EEPROM in this milestone: they change the ASIC and get their own milestone. |
| Host path | The netsim's master, model chain and checker run in Python on Windows over Npcap (approach A). Not SOEM-only, not a USB NIC passed into the container. |
| NIC | The onboard Intel I219-V ("Ethernet"), dedicated to the board, direct cable to the DE2-115's ETHERNET 0 jack. The user unbinds IPv4/IPv6 from it. |
| ESC configuration | `librelane/default.yaml`: N_FMMU=3, N_SM=4, RAM_BYTES=256, DIAG_EN=1, FIFO_DEPTH=16 (CNT_W, TRACE_DEPTH at their defaults there). |
| Core clock | 80 MHz from a PLL (50 MHz x 8/5). |
| Captures | Pcaps of our own synthetic traffic may be committed to `designs/ecat_esc/captures/`. |
| Programming | JTAG `.sof` only (SW19 = RUN). No flash (`.pof`). |

## 3. Board top (`designs/ecat_esc/board/de2_115/`)

`esc_board.v` instantiates the unchanged `esc_top` with the parameters above.

- **Clocks.** A Cyclone IV PLL (`altpll`) makes the 80 MHz core clock from `CLOCK_50`
  (PIN_Y2). `rx_clk` and `tx_clk` are ENET0_RX_CLK and ENET0_TX_CLK, the 88E1111's 25 MHz
  MII clocks.
- **Reset.** `esc_top.rst` is held while any of these is true: power-on (a counter on
  `CLOCK_50`), KEY[0] pressed, PLL not locked, `phy_init` not done, the ISSP soft-reset
  source set. `esc_top` releases reset synchronously in each of its three domains.
  - The soft reset resets `esc_top` and the sticky flags only, not the PHY, so the link
    stays up.
  - The board keeps RAM, registers and diag counters between runs, unlike a fresh
    simulator bench, so `run_board.py` pulses it before every run.
- **`phy_init.v` (new).** Runs on `CLOCK_50` with a 2.5 MHz MDC. Steps:
  1. Pulse ENET0_RST_N low for at least 10 ms, then wait.
  2. Over MDIO (clause 22): write register 9 to stop advertising 1000BASE-T, and register 4
     to advertise only 100BASE-TX full duplex (selector 802.3).
  3. Restart auto-negotiation through register 0.
  4. Poll the 88E1111 PHY-specific status (register 17) until link is up, resolved,
     100 Mbit and full duplex.

  It exports a 3-bit state: `idle`, `configuring`, `link_100fd`, `wrong_link` (resolved but
  not 100/full), `mdio_error` (no turnaround from the PHY). The PHY address comes from the
  Terasic manual and is a parameter.
- **Status.** `rx_overflow`, `tx_overflow` and `tx_underrun` are latched in a sticky
  register until reset, synchronised to `CLOCK_50`. They show on red LEDs; link-up, the
  PHY state and a heartbeat show on green LEDs. One In-System Sources and Probes
  instance exposes the sticky flags, the PHY state and a build ID to JTAG as probes, and
  the soft reset as a source.
- **Pins.** CLOCK_50, KEY[0], LEDs, and ENET0 MII (`TX_CLK, RX_CLK, RX_DV, RX_ER,
  RXD[3:0], TX_EN, TXD[3:0], MDC, MDIO, RST_N`). Pin numbers and I/O standards come from
  the Terasic DE2-115 manual. All unused pins are inputs with a weak pull-up.
- **Board jumpers.** ENET0 must be in MII mode (JP1/JP2). The required setting comes from
  the manual and is confirmed on the board before `bprog` is first run.

### Quartus build

`esc_board.tcl` creates and compiles the project, the way `board/blink/blink.tcl` does.

`esc_board.sdc`:
- `CLOCK_50` at 20 ns; PLL clocks through `derive_pll_clocks`; `rx_clk` and `tx_clk` at 40 ns.
- The core, RX and TX clocks are asynchronous groups, as in the RTL (two async FIFOs and
  per-domain reset synchronisers). `CLOCK_50` (`phy_init`, status) is asynchronous to all three.
- MII input and output delays from IEEE 802.3 clause 22:
  - RX: RXD, RX_DV and RX_ER valid from 10 ns before to 10 ns after the rising RX_CLK.
  - TX: TXD and TX_EN change 0 to 25 ns after the rising TX_CLK.
- KEY and LEDs are false paths.

## 4. PC side (Windows host)

- **`netsim/wire_link.py`.** Same interface as `link.Link`: `send(frame, ifg, er_nibble)`,
  `run(replies, timeout)` returning sent records, replies and status, and `close()`.
  - Npcap's `wpcap.dll` through ctypes.
  - Opens the I219-V by its MAC address, in promiscuous mode, with a BPF filter
    `ether proto 0x88a4`.
  - `send` strips the frame's FCS (the NIC appends its own). `er_nibble >= 0` raises
    `LinkError`: a NIC cannot send `rx_er`.
  - Received frames have the FCS stripped by the NIC; the link recomputes it, so the
    checker compares the same bytes as in simulation.
  - Frames the PC sent itself (seen on capture loopback) are dropped by direction.
    The master's frames carry a fixed source MAC.
  - Timestamps are Npcap's (microseconds, reported in ps).
- **`netsim/run_board.py`.** Runs the scenarios against the board with the same `Master`,
  `Chain` and `Checker` as `run_netsim.py`. It writes `results.json` and a pcap per run in
  the same layout.
  - **Scenarios.** Those a NIC can produce run. `clocks` and the fault frames are recorded
    as `not_applicable`: real crystals replace simulated ppm and drift, and a NIC cannot
    send `rx_er`, a bad FCS or runts.
  - **Pacing.** A window of at most 8 frames in flight.
  - **Timeouts.** A reply missing after 100 ms is `missing`. The simulator's late-reply
    check does not apply.
- **Board status.** `board/de2_115/status.tcl` (`quartus_stp`) reads the ISSP values as
  JSON and can pulse the soft reset. For each run, `run_board.py`:
  1. pulses the soft reset;
  2. reads the status, which must show the flags clear and the link at `link_100fd`;
  3. runs the scenario;
  4. reads the status again.
- **`board/soem_test/`.** A C program on a pinned SOEM release, built with MinGW and
  CMake against the Npcap SDK. Using low-level calls only:
  1. BRD 0x0000 returns working counter 1.
  2. APWR sets station address 0x1001; FPRD reads it back.
  3. FPWR configures FMMU0 (logical 0x00010000, 8 bytes, read and write, onto 0x1000) and
     SM2/SM3.
  4. 10,000 cyclic LRWs each return working counter 3 and the data written by the previous
     cycle.

  This ESC's SyncManagers are registers only, with no buffer gating, which is why step 4
  expects exactly that data.

  It prints JSON: `checks`, `pass`, cycle-time statistics.

## 5. Gates

Board stages run on the Windows host. Each writes `build/ecat_esc/reports/<stage>.json`
(`stage, design, status, metrics, errors, log`) and a stamp in `build/ecat_esc/stamps/`
only on pass. A stage does not run unless the stage before it has a pass stamp. A pass
needs positive evidence from the tool's own reports, never only the absence of error text.
`fail` means the design misses the criteria; `error` means the tool crashed, the board or
cable is missing, or the evidence is absent.

| Stage | Prerequisite | Pass only when |
|---|---|---|
| `dlint` (extended) | none | `esc_board.v` and `phy_init.v` lint with zero warnings; Altera blocks as stubs |
| `dsim` (extended) | `dlint` | `phy_init` cocotb tests pass: normal bring-up, wrong speed resolved (`wrong_link`), PHY not answering (`mdio_error`), register values written as in section 3 |
| `bbuild` | `dsim` | All of the following, read from the Quartus reports: 0 errors; no inferred latches; setup and hold slack >= 0 for every clock on the Slow 1200mV 85C, Slow 1200mV 0C and Fast 1200mV 0C models; zero unconstrained paths; the core PLL output reported at 80.0 MHz; `esc_top` parameters equal to `default.yaml` |
| `bprog` | `bbuild` | `quartus_pgm` succeeds on JTAG ID 0x020F70DD; within 5 s the PHY state reads `link_100fd` and all sticky flags read 0 |
| `bnet [BNET=soak]` | `bprog` | Zero mismatches, missing and unexpected replies; diag counters equal the model's; sticky flags still 0 and PHY still `link_100fd` after the run. Quick: at least 20,000 frames. Soak: at least 1,000,000 |
| `bsoem` | `bprog` | Every check in the SOEM test's JSON passes |
| `bselftest` | none | See section 6 |

`./flow` gains these targets. They run on the host (Git Bash), not in the container:
`bbuild`, `bprog`, `bnet`, `bsoem`, `bselftest`, `bstatus` (status read, not a gate).

If `bbuild` misses timing at 80 MHz, work stops and the failing paths go to the user,
ranked. The RTL, the clock and the gate are not changed without the user's approval.

## 6. Self-test (`bselftest`)

Each fault must be caught by the gate built for it:

1. **RTL mutant.** The LRW working-counter mutant of `dselftest`'s `net_wkc`, built and
   programmed, fails `bnet` with mismatches > 0. The board is reprogrammed with the clean
   build afterwards.
2. **Harness faults.** `bnet` with `--sabotage checker` fails with 1 mismatch;
   with `--sabotage drop-reply` it fails with 1 missing reply.
3. **Timing.** A build with the PLL at an impossible core clock fails `bbuild` on setup
   slack.
4. **No link.** `bnet` with the NIC unplugged (or the board unprogrammed) ends as
   `error`, never `pass`. This check asks the user to unplug the cable; it is the only
   interactive step.

## 7. Testing before hardware

- `phy_init` cocotb test with a clause 22 MDIO PHY model in `tb/`.
- `wire_link` unit tests against a fake pcap layer: FCS handling, filtering, direction,
  timeout to `missing`, and `er_nibble` rejected.
- `run_board` unit tests: scenario selection and the `not_applicable` list.
- SOEM test: builds in the plan's first SOEM task; run only on the board.

## 8. Done

- `bbuild`, `bprog`, `bnet`, `bnet BNET=soak`, `bsoem` and `bselftest` pass on the real board.
- At least one capture of a passing `bnet` run is committed to `designs/ecat_esc/captures/`,
  and `test_replay_real_captures` runs and passes against it (it joins `tb/manifest.txt`).
- docs/FLOW.md documents the board targets and anything that cost a debugging round.

## 9. Risks

- Timing at 80 MHz on the -7 device is unknown until Quartus runs (section 5).
- ISSP is expected in Quartus Lite. If it is not available, the status goes out over a
  JTAG UART instead; the LEDs stay either way.
- Npcap from Python via ctypes: throughput and timestamp quality are unmeasured. Slowness
  lengthens runs but does not change pass or fail.
- SOEM with MinGW and the Npcap SDK is untried here; the fallback is MSYS2.
- The jumper setting, the PHY address and the 88E1111's strap defaults on this board come
  from the manual and are confirmed on hardware.
- Windows link chatter is filtered by EtherType. With IP still bound, the ESC counts it as
  non-EtherCAT, so the diag comparison would fail. Unbinding IP is part of setup.

## 10. Out of scope

AL state machine, SII EEPROM, `ec_config_init`, TwinCAT, the second Ethernet port and
multi-port forwarding, distributed clocks, flash programming, sky130 timing.

## 11. As built

Results on the board (2026-11-05): `bbuild` at 80 MHz, setup +3.805 ns, hold +0.133 ns, 8,607
LEs (7.5%). `bnet` 20,007 frames and the soak 1,000,023 frames, no mismatch, missing or
unexpected reply and no FIFO flag. `bsoem` all five checks over 10,000 LRW cycles, median
493 us.

Where the build differs from the text above:

- **`phy_init`'s MDIO** is `mdio_i`, `mdio_o`, `mdio_oe`; `esc_board` drives the pad.
- **`bbuild`** reads Quartus's own `sta.rpt`, with no script of ours in between, and judges
  all its checks, not only setup and hold: recovery, removal and minimum pulse width too.
- **Build identity**: the probe carries a 24-bit hash of the sources (not a constant 8-bit ID),
  and `bprog`, `bnet` and `bsoem` are `error` unless the board reports it.
- **`bsoem`** reads the board's status around the test, so a missing board is `error`, and a
  FIFO flag or lost link after it is `fail`.
- **`bwatch`** (added on request) shows the netsim live view on the board, without RTL events.
- **The RTL Quartus compiles** is a build-time copy of `rtl/` without `pdram.v`'s `$error`
  check, which Quartus Lite cannot parse. The check fires only for an invalid `RAM_BYTES`.
- **In flight**: one frame at a time, not up to 8, because Npcap's inbound-only filter is
  unavailable on the I219-V and the NIC's copy of a sent frame has to be told from the reply.
- **SOEM** is v2.0.1; v1.4.0's Windows layer builds only with MSVC.
- **The no-link check** is its own step, `./flow bselftest STEP=nolink`, with the cable
  unplugged.
- **The first replay capture** has one allowed difference, the final diag read (see
  `designs/ecat_esc/captures/README.md`).
