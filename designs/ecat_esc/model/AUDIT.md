# Reference model audit against the LAN9253 datasheet

Source: Microchip LAN9253 datasheet DS00003421B (2023), section numbers below. The file is
in `build/ref/` (git-ignored, copyrighted); nothing here is quoted from it. Audited
2026-11-03 against `designs/ecat_esc/model/ecat.py`.

The datasheet describes registers and the host interface. It does not describe datagram
semantics (working counter, addressing, FMMU byte ownership), so those rows are "not
covered" and still need ETG.1000.4 or Beckhoff's ESC datasheet (section I).

Verdicts: **agrees**, **disagrees** (goes to the user; neither model nor RTL is changed until
they decide), **not covered**.

| # | Rule | Model | Datasheet | Verdict | Note |
|---|---|---|---|---|---|
| 1 | Type and revision registers (0x0000, 0x0001) read 0x11, 0x02 | `ecat.py:133-134` | 11.16.1, 11.16.2 | disagrees (a choice) | Each vendor reports its own type (Microchip C0h, revision 03h). Ours copies an ET1100-class value; what this ESC should report is the user's call. |
| 2 | FMMUs and SyncManagers supported (0x0004, 0x0005) give the channel counts | `ecat.py:135` | 11.16.4, 11.16.5 | agrees | |
| 3 | RAM size register (0x0006) | `ecat.py:136` | 11.16.6 | disagrees | The register is in KB. The model (and so the RTL it checks) writes `RAM_BYTES // 1024`, which is 0 for the default 256-byte configuration: a master would read "no process RAM". |
| 4 | Port descriptor (0x0007) is 0x03: port 0 MII, the rest not implemented | `ecat.py:137` | 11.16.7 | agrees | Two bits per port, 11b = MII, 00b = not implemented. |
| 5 | Configured station address (0x0010-0x0011) is writable | `ecat.py:165` | 11.16.9 | agrees | The station alias (0x0012, 11.16.10) and its enable (DL control bit 24, 11.16.17) are not implemented; out of scope with SII. |
| 6 | FMMU registers: 16 bytes from 0x0600; logical start (4), length (2), start/stop bit, physical start (2), physical bit, type (bit 0 read, bit 1 write), activate (bit 0); bytes 13-15 reserved, read-only | `ecat.py:162`, `ecat.py:224-225` | 11.16.60.1-9 | agrees | The bit-granular start/stop/physical bits are stored but not used: mapping is byte-granular. A master that maps single bits gets whole bytes. |
| 7 | SyncManager status (+5) and PDI control (+7) are read-only from EtherCAT | `ecat.py:164` | 11.16.61.4, 11.16.61.6 | agrees | SyncManager behaviour (mailbox, buffered mode) is not modelled; out of scope. |
| 8 | Every other register reads 0 and ignores writes | `ecat.py:165` | 11.16 (register table) | not covered (out of scope) | The LAN9253 has many writable registers (DL control 0x0100, AL control 0x0120, ...). A master writing AL control here sees no effect, as the frame-core spec intends. |
| 9 | The source MAC address passes unchanged | `ecat.py:65` (frames) / `process` | 11.16.17, bit 0 note | disagrees | The datasheet says the ESC sets bit 1 of the first source-address byte (locally administered) on every frame. Masters use that bit to tell a returned frame from their own outgoing copy. Neither model nor RTL does it. |
| 10 | Non-EtherCAT frames are forwarded with an inverted FCS and counted | `ecat.py:285-288` | 11.16.17, bit 0 | agrees (probably) | The default forwarding rule destroys non-EtherCAT frames; the datasheet does not define "destroyed" further. The other rule (forward them unprocessed) is not implemented. |
| 11 | Cut-through forwarding with no deliberate delay | RTL (`esc_top`, `mii_tx`), not the model | 11.16.17, bits 18:16 and note 6 | disagrees (RTL) | A real ESC holds forwarding until its RX FIFO is half full, sized for +-100 ppm on 1518/1522-byte frames. The network simulation sets `tx_overflow` and `tx_underrun` at RX -100 / TX +100 ppm with back-to-back long frames (netsim Task 3 finding). |
| 12 | Frames over 1518 bytes are oversize | `ecat.py:26` | 11.16.17, note 6 | disagrees (probably) | The datasheet sizes the FIFO for 1518/1522-byte frames, which suggests VLAN-tagged frames (1522) are accepted. The model counts them as oversize, and a VLAN tag makes the EtherType 0x8100, so the frame is also treated as non-EtherCAT. |
| 13 | Error counters | diag block at 0x0E00 (`ecat.py:47-58`) | 11.16.37, 11.16.41 (0x0300-0x0313) | disagrees | Standard masters read RX error, forwarded RX error and lost-link counters at 0x0300-0x0313. Ours reads 0 there; our counters are at 0x0E00 in our own layout. |
| 14 | Working counter: read +1, write +1, read-write +3 | `ecat.py:249` | none | not covered | |
| 15 | AP and B datagrams increment the position field at every slave | `ecat.py:202` | none | not covered | |
| 16 | Broadcast read ORs each slave's data into the frame | `ecat.py:212` | none | not covered | |
| 17 | The lowest-numbered FMMU owns a byte when windows overlap | `ecat.py:221` | none | not covered | |
| 18 | An access crossing the register/RAM boundary or the RAM end fails entirely | `ecat.py:149-156` | none | not covered | |
| 19 | Bad frames are forwarded cut-through with an inverted FCS; completed datagrams have run | `ecat.py:257-294` | 7.2.4, 11.1 (overview only) | not covered | |
| 20 | Runt (< 64 bytes) and minimum frame | `ecat.py:26` | none | not covered | |
