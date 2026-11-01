# ESC memory subsystem: pipelined, with SRAM macros

Status: draft for review. Branch `feat/ecat-spec`. Builds on
`2026-10-31-ecat-esc-frame-core-design.md`.

## 1. Why

The default configuration (3 FMMUs, 4 SyncManagers, 256 B process RAM) closes DRC, LVS,
antenna and hold, but fails setup timing at the 100 MHz core clock: about 84 MHz at the
typical corner and about 43 MHz at the slow corner (13,966 violating endpoints). The
cause is the flip-flop process RAM: the write-enable decode and the read mux sit
directly behind the engine's registered address. Synthesis area is also dominated by
flip-flop RAM (about 0.39 mm² per KB against 0.14 to 0.20 mm² per KB for the PDK macros).

Every later comparison with industry ESCs (resources, latency, frequency, RAM size)
rests on this block, so it is rebuilt first.

## 2. Goals and success criteria

1. `dpnr` passes for the default configuration at CLOCK_PERIOD 10 ns: zero setup and hold
   violations in all nine corners, plus the existing DRC, LVS and antenna checks. The
   clock period is not loosened to get there.
2. Process RAM sizes from 64 B to 16 KB build from one parameter, `RAM_BYTES`.
3. The memory port seen by the engine, FMMUs, regfile and diag block keeps its signals
   (`mem_req, mem_we, mem_addr, mem_wdata, mem_rdata, mem_rvalid`). Only the latency
   changes, from 2 to 3 clocks.
4. All 63 existing cocotb tests and the 155 unit tests still pass, with the model
   unchanged (the model is cycle-free).
5. `dselftest` gains a check that a deliberately slow memory path fails `dpnr`.

Non-goals: dual-port host access (axis E, but the second macro port is left unused and
untouched), ECC, power gating, any change to register semantics.

## 3. Design

### 3.1 Interface and latency

`MEM_LAT` becomes 3 for every memory target (regfile, process RAM, diag window). All
three already answer with `mem_rvalid`; the core ORs them, so a single latency keeps the
read mux in `esc_core` unchanged. The engine consumes `mem_rvalid` and does not count
cycles, so the prefetch margin is the only thing that moves.

Byte spacing: a stream byte is 8 core clocks at line rate. The engine issues a read
prefetch one byte ahead and needs the data `MEM_LAT` clocks later plus its own two
stages. At latency 3 the requirement is a gap of at least 6 clocks (was 5); 8 is
available. A new simulation assertion in `dgram_exec` (checked by the testbench, not
synthesised) fails the test if a read result arrives after the byte that needs it, so
the assumption is checked rather than assumed. The text in `dgram_exec.v` and
`docs/FLOW.md` is updated to the new figure.

### 3.2 `pdram` as a wrapper

`pdram` keeps its name and port list and chooses an implementation at elaboration time:

- `RAM_BYTES < 1024`: flip-flop array, pipelined. Stage 1 registers
  `req, we, addr, wdata` and the in-range test. Stage 2 performs the write and, in
  parallel, the read mux on the registered address. Stage 3 registers `mem_rdata`.
- `RAM_BYTES >= 1024`: `pdram_macro`, a bank of `sky130_sram_1kbyte_1rw1r_8x1024_8`
  macros (byte-wide, 1 KB, 455.3 x 446.5 µm), one per 1 KB. The upper address bits pick
  the bank at stage 1; a bank-select register steers the read data at stage 3. Only
  port 0 (read/write) is used. `RAM_BYTES` must be a multiple of 1024 and at most 16384;
  other values are a build error (an elaboration `$error` in simulation, a
  `SYNTH_PARAMETERS` check in the gate).

The array is sized per bank, not per `RAM_BYTES`, so the mux is at most 16-way.

### 3.3 Macros in simulation and in the flow

- Simulation uses a behavioural model with the macro's module name and ports, kept in
  `designs/ecat_esc/tb/models/sky130_sram_1kbyte_1rw1r_8x1024_8.v` (not in `rtl/`). It
  mirrors the real macro's port-0 behaviour: inputs registered on `clk0` rising, read
  data driven after the falling edge, previous contents kept on a write. It is the
  testbench's job to prove it matches the PDK model: a test reads and writes the real
  PDK verilog and the behavioural model with the same stimulus and compares outputs.
- `dlint` lints `rtl/` with the macro declared as an empty stub from `tb/models/`
  (module declaration only, no body), so lint still covers the wrapper's wiring.
- LibreLane configs list the macro under `MACROS` (GDS, LEF, Liberty, Verilog netlist
  taken from `libs.ref/sky130_sram_macros`; no PDK file is edited), with a placement
  location and an exclusion halo. The flip-flop sizes (`min`, `default`) need no macro.
- The Liberty timing is analytical (data out 0.36 to 0.51 ns, minimum period 1.88 ns). I
  treat it as the best available number, not as signoff evidence, and say so in the
  config table.

### 3.4 Config set

`min.yaml` (64 B) and `default.yaml` (256 B) stay on flip-flops. `default.yaml` is not
renamed so existing stamps keep meaning. New configs: `ram1k.yaml` (1 KB, one macro) and
`ram8k.yaml` (8 KB, eight macros). `max.yaml` moves to 8 KB with 8 FMMUs and 8
SyncManagers; its previous 1 KB flip-flop figure stays in the area table for comparison.

## 4. Verification

New or changed tests, each written to fail first:

| Test | Proves |
|---|---|
| `test_pdram_latency` | Read data valid exactly 3 clocks after a read, for flip-flop and macro builds, any address |
| `test_pdram_bank_boundary` | Last byte of bank n and first of bank n+1 are distinct and persist |
| `test_pdram_rw_hazard` | A write followed by a read of the same address on the next clock returns the new data |
| `test_pdram_out_of_range` | Requests above the RAM, and below 0x1000, are ignored (no valid, no write) |
| `test_sram_model_vs_pdk` | The behavioural macro model matches the PDK verilog under random traffic |
| `test_engine_prefetch_margin` | At line rate and at 100 ppm and 200 ppm fast clocks, no read result arrives late (the new assertion stays quiet) |
| existing 63 | Unchanged, run at the new latency |

Mutation checks (lint-clean mutations, reports deleted first): drop the bank-select
register; reduce latency to 2 in the wrapper only; swap the write enable polarity. Each
must be caught by a named test above.

Gate: `./flow dpnr` for `default` and `ram8k`, `./flow dconfigs` for the table, then
`./flow dselftest` with the new slow-memory check (a one-config variant with a 1 ns clock
already exists; this adds a variant with the registered address stage removed, which
must fail on setup timing).

## 5. Risks

- Macro placement may bring DRC or timing issues of its own that only the full flow
  shows. If `ram8k` does not close, the macro count or placement is changed; the 100 MHz
  target is not.
- The macro Liberty is analytical, so timing at the macro pins is optimistic until
  checked against the real characterisation. Reported, not hidden.
- Latency 3 uses most of the byte-spacing margin. A faster line rate or a second port
  needs a different structure (a short prefetch FIFO), which is axis A/E work.
- The bad-frame policy and ESC identity values are untouched and still unverified
  against hardware.

## 6. Out of scope

Multi-port forwarding (axis B), forwarding-latency work (A), distributed clocks (C),
host interface (E), and anything Tiny Tapeout related.
