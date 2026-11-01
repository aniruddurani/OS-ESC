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
