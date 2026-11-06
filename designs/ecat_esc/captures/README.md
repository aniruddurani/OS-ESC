# Captures

Put Wireshark captures from a real EtherCAT master here (classic **pcap** format,
not pcapng: in Wireshark use File > Save As > "Wireshark/tcpdump/... - pcap").

Record a short session against **one** slave: the bus scan, address assignment
and a few cyclic exchanges. Each request must be followed by its response (the
capture taken on the master's NIC has both).

The replay (`tb/test_replay.py::test_replay_real_captures`) runs every `*.pcap`
here through `esc_top` and compares each response datagram by datagram. A real
slave of a different make reports different identity registers, so differences
that are expected (type, revision, build, counts, RAM size, DL status ...) go in
`<capture>.allow.json` as a list of `[frame, datagram, field]`; anything else is a
failure. The test fails if a capture named in `REQUIRED_CAPTURES` (`tb/replay.py`) is
missing, and `dsim`'s stamp depends on this folder, so `./flow dsim` replays a new recording.

## `board_cyclic_k3_s1.pcap`

Recorded on the real board (the ESC on a DE2-115), 201 request/response pairs,
from `run_board.py --only cyclic_k3_s1 --frames 200`, recorded again 2026-11-05 with the AL and SII registers in the RTL: the netsim's cyclic scenario with
three model slaves ahead of the board, so it shows our own RTL on real PHYs, not a
slave of another make. The master's traffic is synthetic. The session starts right after
the ESC's soft reset.

Its allowed difference is the data of frame 200, the final read of the diag counters. The
replay's `start()` sends one extra frame first (the RAM clear), so frames, datagrams,
addressed datagrams, working-counter increments and trace count each read one higher than
on the board, and the latency counter counts core clocks: 80 MHz on the board, 100 MHz in
cocotb. The other 200 pairs must match byte for byte.

## `board_soem_start.pcap`

SOEM v2.0.1's real traffic against the ESC on the board, from `./flow bcapture`: 371
request/response pairs of `soem_config`, from `ecx_config_init` through OP and 200 process-data
cycles back to INIT. It is the first capture here of a master this project did not write, though
the slave is still our own RTL. It has no allowed differences. `capture_soem.py` pads short
requests as the NIC does, so the file holds what the ESC received.
