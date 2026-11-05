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
failure. While this folder has no `.pcap`, that test is skipped and the design
is **not verified against real hardware**.

## `board_cyclic_k3_s1.pcap`

Recorded on the real board (the ESC on a DE2-115, 2026-11-04), 201 request/response pairs,
from `run_board.py --only cyclic_k3_s1 --frames 200`: the netsim's cyclic scenario with
three model slaves ahead of the board, so it shows our own RTL on real PHYs, not a
slave of another make. The master's traffic is synthetic. The session starts right after
the ESC's soft reset.

Its allowed difference is the data of frame 200, the final read of the diag counters. The
replay's `start()` sends one extra frame first (the RAM clear), so frames, datagrams,
addressed datagrams, working-counter increments and trace count each read one higher than
on the board, and the latency counter counts core clocks: 80 MHz on the board, 100 MHz in
cocotb. The other 200 pairs must match byte for byte.
