# ESC datapath timing restructure

Status: implemented (default and min pass all nine corners at 10 ns, 2026-11-03). Branch `feat/ecat-spec`. Builds on
`2026-10-31-ecat-esc-frame-core-design.md` and `2026-11-01-ecat-esc-memory-design.md`.

## 1. Why

With the pipelined memory in place, `./flow dpnr LLCFG=default` still fails setup timing at
the 10 ns core clock: 12,987 violating endpoints, worst slack -15.2 ns (slow corner
`ss_100C_1v60`), -2.67 ns at `tt_025C_1v80`. Everything else passes (DRC, LVS, antenna,
hold). The receive and transmit clock domains have +25 to +29 ns of slack; every failing
path is in the core clock, and none is in the memory.

Worst paths, grouped by register (slow corner):

| Slack | From | To | What it is |
|---|---|---|---|
| -15.2 | `dg_len` | `u_fmmu.hr`, `hw` | per-FMMU window overlap and "fits" arithmetic: five 33-bit adds and compares in one cycle |
| -13.1 | `dg_addr` | `u_delay.data` | logical lookup `dg_addr + p_pos`, compare, `f_phys + d0`, mux, into the output byte |
| -12.4 | `dg_addr` | `mem_addr`, diag | the same lookup into the memory address |
| -12.3 | `lg_go` | `pf_addr` | the same lookup for the prefetch address |

The slow corner is about twice as slow as typical, so closing all nine corners needs the
deepest logic cones at about 5 ns typical; they are about 12.7 ns now. The cause is that
the engine and the FMMUs do 32-bit address arithmetic, comparison and a priority
selection as one combinational cone, once per byte, although a byte lasts at least 6
(line rate 8) core clocks.

## 2. Goals and non-goals

1. `./flow dpnr LLCFG=default` passes: zero setup and hold violations in all nine
   corners, plus the existing checks, with `CLOCK_PERIOD` still 10. No period, corner,
   metric or SDC change.
2. Behaviour is unchanged: all 76 current simulation tests pass, the reference model is
   not edited, and the output stream is byte-identical to today's for every test.
3. No new byte-spacing requirement: the core still handles streams with at least 6
   clocks between bytes (line rate 8), including the first read byte of a logical
   datagram.

Non-goals: new features, multi-port work, macro configs (`ram1k`, `ram8k` follow after
this closes), and any SDC multicycle exception (see section 6).

## 3. Design

Principle: no per-byte arithmetic wider than 16 bits, and no cone with more than one
adder or one compare plus a small mux. Work that is the same for the whole datagram is
done once, over several registered stages, in the two header bytes (12 or more clocks) that
follow the point where it becomes possible.

### 3.1 Datagram plan (physical and logical)

`dgram_exec` computes the per-datagram decision in registered stages:

- `P1` at `dg_start` (address known after header byte 5): `ado` and the addressed test.
- `P2` at `dg_len_done` (length known after byte 7): `a_end = ado + dg_len` into a
  register.
- `P3`: `reg_ok`, `ram_end` into registers; `P4`: `ram_ok`, `fits`, then `ok` is set
  at `dg_hdr_done` as today (writes still wait for `dg_hdr_done`).

### 3.2 FMMU plan (logical): `fmmu_plan`

Each FMMU runs a fixed-latency pipeline, one adder or compare per stage, started at
`dg_start` for everything that does not need the length and finished a few clocks after
`dg_len_done`:

| Stage | Computes | Registered |
|---|---|---|
| F1 (dg_start) | `ds = dg_addr`, `ws = f_start`, `we = ws + f_len`, `ds > ws` | `we`, `lo = max(ds, ws)` select |
| F2 (len_done) | `de = ds + dg_len`, `hi_sel = de < we` | `de`, `hi` |
| F3 | `ovl = act && f_len != 0 && lo < hi` | `ovl`, `lo`, `hi` |
| F4 | `seg_start = lo - ds` (11 bit), `seg_len = hi - lo`, `off = lo - ws` (16 bit) | `seg_start`, `seg_end = seg_start + seg_len`, `off` |
| F5 | `plo = f_phys + off`, `phi = plo + seg_len` | `seg_phys = plo`, `phi` |
| F6 | `fits` compare, then `hr`, `hw` | `hr`, `hw` |

The result per FMMU is a segment: `hr/hw`, `seg_start` and `seg_end` in datagram byte
offsets (11 bit) and `seg_phys` (16 bit). The first logical byte is needed no earlier
than two header bytes after `dg_len_done` plus the byte gap, so at most 6 clocks of
stages are on the critical start-up path; F1 runs two header bytes earlier.

### 3.3 Per-byte lookup (logical): `fmmu_walk`

For datagram byte offset `n` the walker answers, for reads and writes separately, whether
some FMMU serves the byte and at which physical address, in three registered stages:

| Stage | Computes |
|---|---|
| W1 | per FMMU `seg_start <= n < seg_end` (two 11-bit compares), qualified by `hr`/`hw` |
| W2 | lowest-numbered hit per direction, as an index; mux `seg_phys` and `seg_start` |
| W3 | `addr = seg_phys + (n - seg_start)` (16-bit adder) |

Initiation interval is one offset per clock. A request carries a tag (the offset and a
valid bit) and the result is delivered three clocks later with the same tag.

Physical commands do not use the walker: `ok`, `is_rd`, `is_wr` give the hit and the
address is a 16-bit running counter (`ado`, then +1 per byte) instead of `ado + p_pos`.

### 3.4 Access pipeline and read-ahead

Memory access is decoupled from the stream with a small queue (depth 4 offsets):

- The walker runs ahead of the stream: it starts offset `n` as soon as the queue has a
  free entry, beginning with offset 0 when the plan is ready.
- Each queue entry holds `{rd_hit, wr_hit, wr_addr, rd_data, rd_ready}`. A read request
  goes to the memory port when its walker result arrives; its data lands in the entry
  after the memory latency (`MEM_LAT = 3`) plus one clock.
- A stream byte `p` consumes entry `p`: it takes `rd_data` into the output (OR for BRD,
  as today) and, on a write hit, issues the write from `wr_addr` and `p_data`.
- Memory port arbitration: a write has priority; a read waits one clock. The queue is deep
  enough that this does not delay a result past the byte that needs it at 6-clock spacing.
- The queue is cleared at each datagram's start. `ev_wkc_inc` and the working-counter
  rules are unchanged (`rd_seen`, `wr_seen`).

Budget for the first byte, at the minimum spacing: header byte 5 to data byte 0 is
at least 5 byte times of 6 clocks. F1 runs after byte 5, F2 to F6 after byte 7, W1 to
W3 and the read take 3 + 1 + 3 + 1 = 8 clocks, the stages after length take about
6 clocks, so about 14 clocks are needed after byte 7 against 18 available (byte 7 to
data byte 0 is three byte times). The margin is 4 clocks and is part of the tests
(section 4).

### 3.5 Other paths

Nothing else is changed up front. The first full run after this change shows the next
worst paths (parser, diag, CRC, regfile read mux); those are fixed in the same plan
under the same rule (register the intermediate, or split the cone), in the order the fast
timing loop (section 4) ranks them.

## 4. Verification

- **Equivalence:** all 76 current tests pass unchanged (they compare the output stream
  with the model, including random logical traffic and LRW with overlapping windows).
- **New tests** (each written to fail first, on the old RTL or by a stub):
  - `test_fmmu_plan_latency`: for random FMMU configs and datagrams, `hr/hw/seg_*`
    equal a Python computation of the same segments, valid within 7 clocks of `dg_len_done`.
  - `test_fmmu_walk_matches_model`: for random segments and offsets, `hit` and `addr`
    equal the model, delivered 3 clocks after the request, one request per clock.
  - `test_first_byte_margin_logical`: first byte of an LRD, LWR and LRW at spacing 6, 7 and
    8, with 16 FMMUs configured and the lowest-numbered one overlapping a higher one.
  - `test_write_read_same_clock`: an LRW whose read and write hit the same physical byte
    on consecutive offsets, at spacing 6 (arbitration).
  - `test_queue_full_and_flush`: a long datagram (1486 bytes), then an aborted frame (bad
    FCS) followed by a new datagram; no stale entry is used.
- **Mutation checks** (lint-clean): drop the lowest-number priority; use `<` for the
  segment end; skip arbitration; start the plan one stage late. Each must be caught.
- **Timing loop:** after synthesis, `librelane ... --to OpenROAD.STAPrePNR` (about 40 s,
  unbuffered, pessimistic) ranks the paths; the gate is the full `dpnr` (about 70 min).
  Each iteration records the worst slack per register group in the ledger, so progress
  or a plateau is visible.
- **Gate:** `./flow dpnr LLCFG=default` passes, then `min`; `./flow dselftest` unchanged.

## 5. Risks

- The tail of failing paths is not known (12,987 endpoints). Fixing the three groups above
  may reveal others; the plan reserves iterations for that and stops after three failed
  rounds to report the architectural cause instead of loosening anything.
- The queue and arbiter add area and a new class of ordering bugs; section 4 targets them.
- The first-byte margin is 4 clocks at minimum spacing. If it proves too thin, the F stages
  are merged where timing allows, not the gap requirement.
- Closing 100 MHz at the slow corner in sky130 may not be reachable with this structure; the
  fast loop shows that early, and the options then are a further split or a decision
  from the user about the target. Neither is taken without asking.

## 6. Alternatives considered

- **Multicycle SDC exceptions** on `dg_len`/`dg_addr` into the FMMU registers: fast to
  write, but it hides the real paths, does nothing for the per-byte lookup, and changes
  what the gate means. Rejected; it would need explicit approval.
- **Lower core clock target**: loosens the criterion the user set. Not taken; offered as an
  option only if section 5's last risk occurs.
- **Single-ALU micro-sequenced engine**: smaller, but a rewrite with a new verification
  burden and less headroom for higher line rates. Not chosen.
