# EtherCAT ESC: AL state machine and SII

Status: design approved in conversation 2026-11-05; awaiting review of this written spec.
Builds on `2026-11-04-ecat-esc-de2-115-design.md` (the board flow) and branches from
`feat/board-de2-115`.

## 1. Purpose

The ESC has no AL state registers and no SII interface, so a master's normal start-up cannot
run: `2026-11-04-ecat-esc-de2-115-design.md` could only use SOEM's low-level datagram calls.
This milestone adds both, so that:

1. SOEM's standard start-up runs against the ESC on the DE2-115: `ecx_config_init` finds the
   slave, `ecx_config_map` maps its process data from SII, and the slave reaches OP.
2. Real master traffic, recorded on the board, replays through the RTL in `dsim`.

TwinCAT is the target after this one. Nothing here should block it, but no TwinCAT run is part
of this work.

The ESC has no local CPU and no PDI, so the design takes the path real CPU-less ESCs take.

## 2. Decisions

| Topic | Decision |
|---|---|
| SII store | A ROM inside the ESC, behind a narrow interface an I2C store can later replace. No new pins. |
| AL state machine | Device emulation: AL status follows AL control. No transition checks, no error states. |
| Placement | A new block `rtl/al_sii.v` on the byte-wide memory port, beside `regfile` and `diag`. |
| ROM source | One Python source shared with the reference model; `rtl/sii_rom.v` is generated from it and a test holds the two equal. |
| Mailbox | None. SII declares mailbox size 0. |
| Process data | 8 bytes out at 0x1000 through SM2, 8 bytes in at 0x1080 through SM3. |
| Identity | Placeholder (vendor 0, product 1), in the Python source. |

## 3. What a master needs

Read from SOEM v2.0.1 (`ec_config.c`, `ec_main.c`), `ecx_config_init` needs:

- 0x0120 AL control written, 0x0130 AL status reading the requested state.
- 0x0500 to 0x050B, the SII interface, for identity, then the General, Strings, SM and FMMU
  categories. `ecx_config_map` then reads the PDO categories.
- 0x0110 DL status: without bit 9 SOEM sees no active port.
- 0x0008 ESC features may stay 0 (no distributed clocks).
- A mailbox size of 0 is valid: SOEM then takes the process-data layout from SII alone.

SOEM also writes registers the ESC does not implement (DL control, IRQ mask, RX error counters,
the DC block). These stay ignored, with the working counter as it is today.

## 4. RTL

### `rtl/al_sii.v`

On the memory port with the usual 3-clock read latency (`MEM_LAT`), selected in `esc_core`
beside `regfile` and `diag`. `MEM_LAT` and the 5-clock budget of the memory paths do not
change.

| Address | Register | Behaviour |
|---|---|---|
| 0x0120..0x0121 | AL control | Reads back the last write. |
| 0x0130..0x0131 | AL status | Bits 3:0 the state; resets to 1 (INIT). A write of AL control whose low 4 bits are 1, 2, 3, 4 or 8 sets them; any other value leaves them. Bit 4, the error flag, is always 0, so the usual `INIT|ACK` write of 0x11 reports no error. All other bits 0. |
| 0x0134..0x0135 | AL status code | Reads 0. |
| 0x0141 | ESC configuration | Reads 0x01: device emulation. |
| 0x0500 | SII access | Reads back a write of bit 0 (owner). The owner does not change behaviour: the ROM has one reader. |
| 0x0502..0x0503 | SII control and status | See below. |
| 0x0504..0x0507 | SII address | A word address. Reads back. |
| 0x0508..0x050B | SII data | The 4 bytes from the word address on. Read only. |

**Device emulation** is the mechanism real ESCs offer to devices with no local CPU, declared by
bit 0 of 0x0141 (the TI register list describes it as AL status updated from the value written
to AL control, mainly for simple ESCs without an MCU). Simple I/O terminals work this way.

**The SII data register follows the address.** A master writes command and address in one
datagram with the command bytes first, so the command byte arrives before its address. The
design does not wait for the end of the datagram: the data register tracks the address through
a few relaxed clocks, and the next datagram, microseconds later, reads the result.

- The read command (0x0100) needs no action. BUSY (bit 15) is always 0.
- R64 (bit 6) reads 0: reads are 4 bytes, which SOEM handles.
- A write command (0x0201) or reload (0x0300) sets NACK (bit 13), which a NOP write (0x0000)
  clears. This is how a read-only store tells a master no.
- A word address past the table reads 0xFFFF, as a blank EEPROM does.

### `rtl/sii_rom.v`

Generated. A combinational table of 16-bit words, addressed by word, with a registered output in
`al_sii.v`. The port is word address in, word out, so an I2C store can replace the block.

### `rtl/esc_core.v`

- The memory read mux gains `al_sii`, with no added stage.
- `dl_status` is 0x0211 instead of 0: bit 0 EEPROM loaded, bit 4 port 0 link, bit 9 port 0 open
  with communication. A constant is honest: on the board the ESC is held in reset until the
  link is up. Closed ports stay 0.

`regfile.v` does not change: it has never decoded these addresses, and reads 0 there.

### Behaviour to note

AL control writes land as bytes pass, like every register here, so a frame that later fails its
FCS still changes the state. This follows the cut-through rule of the core spec. A real ESC may
wait for a good frame; that is unverified against real hardware.

## 5. SII contents

About 256 bytes. `designs/ecat_esc/model/sii.py` builds the image and is the single source: the
model reads it directly, and `scripts/gen_sii_rom.py` writes `rtl/sii_rom.v` from it.

| Words | Contents |
|---|---|
| 0..6 | Word 0 = 0x0100, agreeing with 0x0141; the rest 0. |
| 7 | CRC-8 (polynomial 0x07, initial 0xFF) over bytes 0..13, in the low byte. |
| 8..15 | Vendor 0, product 1, revision 1, serial 0, 32 bits each. |
| 0x18..0x1C | Mailbox addresses, sizes and protocols: all 0. |
| 0x3E | Size: 2 Kbit (value 1). |
| 0x3F | Version 1. |
| 0x40 on | Categories, then 0xFFFF. |

Categories: Strings (10) with the device name; General (30); FMMU (40): outputs, inputs, unused;
SyncM (41): SM0 and SM1 unused, SM2 outputs at 0x1000, 8 bytes, SM3 inputs at 0x1080, 8 bytes;
TxPDO (50) and RxPDO (51): one PDO of 8 bytes each, assigned to SM3 and SM2.

**Before tapeout or any TwinCAT work**, the identity must be set: a real vendor ID comes from
the EtherCAT Technology Group, and the ROM freezes whatever is in `sii.py` at tapeout.

**A limit of this design:** the chip has no local side, so nothing but the master changes the
process data. The board test writes a pattern to the input area itself. Outputs on LEDs and
inputs on switches would need a second port into the RAM, and are out of scope.

## 6. Reference model

`SlaveModel` implements the same registers, reading the SII image from `sii.py`, including the
DL status constant. The K model slaves of every netsim run therefore behave as the RTL does.

## 7. Verification

### Unit and cocotb

- `model/test_sii.py`: checksum correct; a category walk reaches every category and ends;
  `rtl/sii_rom.v` equals what the generator writes now.
- `tb/test_al_sii.py`, each case against the model:
  - AL: `INIT|ACK`, each defined state, an invalid value, broadcast and addressed writes,
    reset to INIT, a bad frame cut through.
  - SII: command and address in one datagram, command byte first; the identity words; a read
    across the end of the table; a write command gives NACK, a NOP clears it; 0x0500 reads back.
  - DL status reads 0x0211.

### Netsim

`startup` gains SOEM's sequence (AL writes, then SII reads); `random` adds the new registers to
its reads and writes. The `dnet` gate and soak stay as they are: zero mismatches. The FPGA's
`bnet` runs the same scenarios.

### `dselftest`

Two new lint-clean RTL mutants must fail `dnet` with mismatches, with `dsim` stamped: AL status
stuck at INIT, and SII data one word off.

### On the board

`board/soem_test/soem_config.c`, a second SOEM program, using the standard calls:

1. `ecx_config_init` finds 1 slave, with the identity and name in `sii.py`.
2. `ecx_config_map` reports 8 bytes out and 8 bytes in.
3. The slave reaches SAFE-OP, then OP.
4. A pattern written to 0x1080 appears in the process image's inputs.
5. 10,000 cycles of `ecx_send_processdata` / `ecx_receive_processdata`, each with working
   counter 3, with the outputs read back from 0x1000 afterwards.
6. The slave returns to INIT.

`bsoem` passes only when this and the low-level test both pass, with the board status rules it
has now.

### Captures

- SOEM's real traffic during `soem_config` is recorded from the NIC into
  `captures/board_soem_start.pcap`. The replay test runs it with no allowed differences: until
  now every capture is traffic this project generated.
- `board_cyclic_k3_s1.pcap` is recorded again, since DL status changes.

## 8. Done

- `dlint`, `dsim`, `dnet` and its soak, `dselftest` and `./flow test` pass.
- The default configuration passes `dpnr` at 100 MHz on all nine corners.
- `bbuild` passes at 80 MHz; `bnet`, its soak, `bsoem` and `bselftest` pass on the board.
- The real SOEM capture replays in `dsim`.
- docs/FLOW.md is updated.

If `dpnr` misses timing at 100 MHz, work stops and the failing paths go to the user, ranked.

## 9. Risks

- SII timing at the slow corner: the ROM read is relaxed over several clocks, but the read mux
  in `esc_core` sits on tight paths. Setup margin at 100 MHz is thin.
- The AL error flag and status code are constants. A master that expects a refusal gets none.
- SOEM's FMMU and SM set-up in `ecx_config_map` is untested against this RTL. FMMU bit
  granularity beyond byte alignment is not expected, since the PDOs are whole bytes.
- `sii.py`'s category layout is read back only by SOEM here; TwinCAT may be stricter.

## 10. Out of scope

Mailbox and CoE, an ESI file and a TwinCAT run, an I2C EEPROM, I/O pins or a PDI, distributed
clocks, SyncManager gating of process data, AL transition checks and error codes.
