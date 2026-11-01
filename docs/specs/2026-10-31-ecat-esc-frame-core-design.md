# EtherCAT slave controller (ESC) frame core: design

Status: draft for review. Date: 2026-10-31.

## 1. Purpose

Build an open, inspectable EtherCAT slave controller in Verilog and take it
through the open-source digital flow (LibreLane, sky130A) in `silicon-flow`.
This spec covers the first milestone: the frame core with logical addressing,
in simulation and through place-and-route. It is the digital counterpart of the
inverter milestone and the first step toward a LAN9253-class ESC.

Goals, in the user's words: learn the digital flow, work toward a tapeout, and
improve on closed ESCs by being (A) open and inspectable with built-in
diagnostics, (B) parameterised in resources, and (C) faster on the host side.
A and B are in this spec. C is a separate later spec.

## 2. Scope

In scope:

- One EtherCAT port (MII), frames returned out the same port.
- Datagram commands: auto-increment (APRD, APWR, APRW), configured-address
  (FPRD, FPWR, FPRW), broadcast (BRD, BWR, BRW) and logical (LRD, LWR, LRW).
- Register file with a subset of the real ESC register map, one or more FMMUs,
  SyncManager registers, a process-data RAM (sizes are parameters).
- A `diag` block: counters and a datagram trace buffer (goal A).
- Verilog parameters for FMMU count, SyncManager count and RAM size (goal B),
  with the flow reporting area and fmax per configuration.
- A single memory port behind which the register file and RAM sit, so a host
  interface (goal C) can be added later without touching the frame engine.

Out of scope for this milestone:

- Second and third ports, forwarding between ports, distributed clocks.
- Any host interface (SPI, parallel bus, digital I/O PDI).
- PHY (analog); an external PHY over MII is assumed.
- SII/EEPROM, mailbox protocols, EtherCAT state machine (AL) beyond registers.
- Fitting a specific shuttle area, I/O pad ring, and tapeout prechecks.

## 3. Language and tools

Verilog (SystemVerilog subset), cocotb tests in Python, Verilator for lint and
simulation (Icarus as an alternative), Yosys and OpenROAD through LibreLane
v3.1.0.dev3 (as installed in the pinned `iic-osic-tools:2026.10` image).

## 4. Architecture

```
 rx_clk domain         clk (core) domain                      tx_clk domain
 mii_rx --async FIFO--> delay_line --> dgram_exec --> crc32/merge --async FIFO--> mii_tx
                          frame_parse --^   |  \--> diag
                                            v
                                     memory port --> regfile / fmmu / pdram
```

Blocks (one job each):

- `mii_rx`: MII nibbles to bytes; strips preamble and SFD; flags RX_ER.
- `frame_parse`: walks the Ethernet header (EtherType 0x88A4), the 2-byte
  EtherCAT header, and each 10-byte datagram header (command, index, 4-byte
  address, length and flags, IRQ).
- `dgram_exec`: decides whether the datagram addresses this slave, performs the
  access, merges read data into the stream, applies the working-counter rules.
- `regfile`, `fmmu`, `pdram`: behind one memory port.
- `delay_line`: byte buffer holding the stream while `dgram_exec` decides;
  depth is a parameter.
- `crc32`: checks the received FCS and regenerates it for the transmitted frame.
- `diag`: taps the datapath read-only; never alters frames.
- `mii_tx`: regenerates preamble, sends bytes, appends FCS.
- `esc_top`: wiring and parameters.

## 5. Clocks

Three clocks: `rx_clk` and `tx_clk` from the PHY (25 MHz for 100 Mbit/s MII)
and the core `clk`, target 100 MHz. Two asynchronous FIFOs cross the domains:
RX (`rx_clk` to `clk`) and TX (`clk` to `tx_clk`). The TX FIFO also absorbs the
small frequency difference between the two PHY clocks; a few bytes of depth is
enough over a maximum frame. At 100 MHz a byte is 8 core cycles, so the
decision window between the end of the command/address bytes and the start of
the data field (4 byte-times) is about 32 cycles.

100 MHz is a target. The flow reports the real fmax per configuration; 50 MHz
is the fallback. Any change of target is a ruling recorded in the plan.

## 6. Datagram engine

Header facts (to be confirmed against the standard and the captures): the
datagram header is 10 bytes, the data field follows, then a 2-byte working
counter (WKC). The command and address are known after 6 bytes.

Address matching: auto-increment (position field zero selects this slave, and
every slave increments it), configured address (matches the station address
register), broadcast (every slave), logical (matches an FMMU window).

Data handling: reads replace the data field as it passes; writes take the
frame's data into the register file or RAM; read-write does both per the
standard. The working counter increments only on a successful access, by the
per-command rules in the standard. The exact increments are checked against the
captures. The FCS is regenerated after modification.

The write path has one commit strobe so the policy for writes in a frame whose
FCS proves bad is a local change (see open items).

## 7. Registers (initial subset)

Type/revision/build (0x0000..), FMMU and SyncManager counts, RAM size and port
descriptor, configured station address (0x0010), DL control/status, FMMU
configuration (0x0600..), SyncManager configuration (0x0800..). Addresses and
reset values are verified against the ESC documentation when the plan is
written; none are taken from memory alone.

## 8. Diagnostics (goal A)

Counters (saturating, readable through the memory port): frames received,
RX errors, CRC failures, runt/oversize frames, non-EtherCAT frames,
datagrams processed, WKC increments, datagrams addressed to this slave, and
measured forwarding latency in core cycles. A trace buffer records, per
datagram, the command, address, length, WKC result and a core-cycle timestamp.
`diag` is read-only with respect to the datapath; a test asserts that the
output stream is bit-identical with `diag` removed.

## 9. Parameters (goal B)

`N_FMMU`, `N_SYNCMANAGER`, `RAM_BYTES`, `DELAY_DEPTH`, `TRACE_DEPTH`. Ranges are
fixed in the plan after the first area measurement. Large RAM sizes use SRAM
macros available in the PDK; small ones may use flops. Min, default and max
sets are each run through the flow and tabulated (area, fmax).

## 10. Verification

- Block-level cocotb tests per block.
- Reference model in `model/`: Python implementation of the datagram semantics,
  used as the oracle for generated and malformed frames.
- Capture replay: pcaps recorded in Wireshark from the user's TwinCAT master
  (bus scan, address assignment, cyclic exchanges), replayed through the core
  and compared byte for byte with what a real slave returned. Until the
  captures exist, vectors are derived from the standard and the report marks
  them "not verified against real hardware".
- Clock-crossing tests: the three clocks at different phases and with a ppm
  offset; many frames back to back.
- A test asserts the stream is identical with `diag` removed.

## 11. Flow integration

Layout: `designs/ecat_esc/{rtl,tb,model,captures,librelane}`. Stages, each
writing `build/<design>/reports/<stage>.json` and a pass stamp on pass only,
exactly as in the analog flow, run through `./flow`:

1. `lint`: Verilator, zero warnings.
2. `sim`: cocotb; passes only if every expected test ran (manifest) and none
   failed.
3. `synth`: Yosys via LibreLane; fails on unintended latches or combinational
   loops; reports cells and area.
4. `pnr` and `signoff`: timing at TT, SS and FF corners (setup and hold slack
   non-negative), DRC (Magic and KLayout), LVS (Netgen), antenna. Evidence is
   read from LibreLane's metrics; exact metric names are confirmed during
   implementation.
5. Configuration table (report, not a gate).

Self-test: a wrong WKC increment must fail the capture replay; an impossible
clock period must fail timing; a lint-dirty file must fail lint.

Inherited rules from `docs/FLOW.md`: never loosen a limit, waive a rule, or edit PDK
or generated files without approval; tapeout steps are never marked done.

## 12. Acceptance criteria

1. `./flow` runs all stages on the default configuration and exits 0 only if
   every stage passes.
2. Capture replay matches the real slave's responses byte for byte for the
   recorded sessions, for the command set in section 2.
3. Clock-crossing tests pass with phase and ppm offsets.
4. Timing is met at TT, SS and FF at the target clock, or the flow records the
   achieved fmax and the target change is approved.
5. DRC, LVS and antenna are clean.
6. Min, default and max configurations all complete; the table is produced.
7. The self-test passes.

## 13. Risks and open items

- **Licensing and patents.** The EtherCAT protocol is public but the
  technology is Beckhoff's and managed by the ETG (vendor ID, conformance,
  patents). Beckhoff's own FPGA IP core is commercially licensed with a
  per-device royalty. Whether a from-scratch ESC-compatible chip needs a
  licence is unresolved and must be checked before any fabrication. A private
  learning design in simulation and layout is a different matter from a product.
- **FCS-bad policy.** What a real slave does with writes in a frame whose FCS
  proves bad is unknown; decided from captures and the standard.
- **Non-EtherCAT frames.** Assumed dropped and counted; a real ESC may forward
  them; confirm from captures.
- **Captures.** Needed from the user; until then results are labelled
  unverified.
- **Timing at 100 MHz** on sky130 is a target, not a promise.
- **LAN9253 datasheet** (Microchip returned 403): the user may supply the PDF
  and errata so documented limitations replace the hypotheses used here.
- **Area.** Large RAM and many FMMUs may not fit small shuttle areas; the
  configuration table is the evidence for that decision.
