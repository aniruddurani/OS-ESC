"""Tests for the reference model. Vectors are derived from the standard and the
Beckhoff documentation, not from hardware: NOT VERIFIED AGAINST REAL HARDWARE
until Task 11 replays recorded captures."""
import struct
import unittest
import zlib

import ecat
from ecat import CMD, Datagram, SlaveModel, build_frame, parse_frame


def adp_ado(adp, ado):
    """Physical address field: position/station in the low half, offset in the high half."""
    return (ado << 16) | adp


def run(slave, datagrams):
    raw = slave.process(build_frame(datagrams))
    return None if raw is None else parse_frame(raw)


def set_fmmu(slave, i, logical, length, physical, rd=False, wr=False):
    cfg = struct.pack("<IHBBHBBB3x", logical, length, 0, 7, physical, 0,
                      (1 if rd else 0) | (2 if wr else 0), 1)
    slave.regs[0x0600 + 16 * i:0x0600 + 16 * i + 16] = cfg


class AddressingTest(unittest.TestCase):
    def test_apwr_then_aprd_roundtrip(self):
        s = SlaveModel()
        out = run(s, [Datagram(CMD["APWR"], 1, adp_ado(0, 0x0010), b"\x34\x12")])
        self.assertEqual(out[0].wkc, 1)
        self.assertEqual(s.station_address, 0x1234)
        out = run(s, [Datagram(CMD["APRD"], 2, adp_ado(0, 0x0010), b"\x00\x00")])
        self.assertEqual((out[0].data, out[0].wkc), (b"\x34\x12", 1))

    def test_auto_increment_position_counts_up(self):
        first, second = SlaveModel(), SlaveModel()
        dg = Datagram(CMD["APRD"], 3, adp_ado(0xFFFF, 0x0004), b"\x00")
        mid = parse_frame(first.process(build_frame([dg])))
        self.assertEqual(mid[0].address & 0xFFFF, 0)   # first slave incremented it
        self.assertEqual(mid[0].wkc, 0)                # and was not addressed
        self.assertEqual(first.counters.datagrams_addressed, 0)
        end = parse_frame(second.process(build_frame(mid)))
        self.assertEqual(end[0].address & 0xFFFF, 1)
        self.assertEqual((end[0].data, end[0].wkc), (bytes([3]), 1))  # FMMU count, default 3

    def test_fprd_matches_station_address_only(self):
        s = SlaveModel()
        run(s, [Datagram(CMD["APWR"], 1, adp_ado(0, 0x0010), b"\x00\x10")])
        hit = run(s, [Datagram(CMD["FPRD"], 2, adp_ado(0x1000, 0x0010), b"\x00\x00")])
        miss = run(s, [Datagram(CMD["FPRD"], 3, adp_ado(0x2000, 0x0010), b"\x00\x00")])
        self.assertEqual((hit[0].data, hit[0].wkc), (b"\x00\x10", 1))
        self.assertEqual((miss[0].data, miss[0].wkc), (b"\x00\x00", 0))
        self.assertEqual(miss[0].address & 0xFFFF, 0x2000)  # configured address is not touched

    def test_brd_always_matches(self):
        s = SlaveModel()
        out = run(s, [Datagram(CMD["BRD"], 4, adp_ado(0, 0x0004), b"\x00")])
        self.assertEqual(out[0].wkc, 1)
        # Broadcast reads OR each slave's data into the frame.
        out = run(s, [Datagram(CMD["BRD"], 5, adp_ado(0, 0x0004), b"\x10")])
        self.assertEqual(out[0].data, bytes([3 | 0x10]))

    def test_bwr_writes_and_counts(self):
        s = SlaveModel()
        out = run(s, [Datagram(CMD["BWR"], 6, adp_ado(0, 0x0010), b"\xcd\xab")])
        self.assertEqual(out[0].wkc, 1)
        self.assertEqual(s.station_address, 0xABCD)


class WorkingCounterTest(unittest.TestCase):
    def test_wkc_increments_per_command(self):
        # Beckhoff TwinCAT documentation (working counter): successful read +1,
        # successful write +1, read-write with both +3. Verify against ETG.1000.
        s = SlaveModel()
        for name, want in (("APRD", 1), ("APWR", 1), ("APRW", 3),
                           ("FPRD", 1), ("FPWR", 1), ("FPRW", 3),
                           ("BRD", 1), ("BWR", 1), ("BRW", 3)):
            s.station_address = 0x0001
            adp = 0x0001 if name.startswith("FP") else 0
            out = run(s, [Datagram(CMD[name], 1, adp_ado(adp, 0x0010), b"\x01\x00")])
            self.assertEqual(out[0].wkc, want, name)

    def test_existing_wkc_is_added_to(self):
        s = SlaveModel()
        dg = Datagram(CMD["BRD"], 1, adp_ado(0, 0x0004), b"\x00", wkc=5)
        self.assertEqual(run(s, [dg])[0].wkc, 6)

    def test_failed_access_does_not_count(self):
        s = SlaveModel()
        out = run(s, [Datagram(CMD["BRD"], 1, adp_ado(0, 0x1000 + 2000), b"\x00")])
        self.assertEqual(out[0].wkc, 0)

    def test_nop_changes_nothing(self):
        s = SlaveModel()
        out = run(s, [Datagram(CMD["NOP"], 1, 0, b"\x07")])
        self.assertEqual((out[0].data, out[0].wkc), (b"\x07", 0))


class MemoryTest(unittest.TestCase):
    def test_process_ram_roundtrip(self):
        s = SlaveModel(ram_bytes=1024)
        run(s, [Datagram(CMD["APWR"], 1, adp_ado(0, 0x1000), b"\xde\xad\xbe\xef")])
        out = run(s, [Datagram(CMD["APRD"], 2, adp_ado(0, 0x1000), b"\x00" * 4)])
        self.assertEqual(out[0].data, b"\xde\xad\xbe\xef")

    def test_write_crossing_ram_end_rejected(self):
        # Review Focus 5: no wrap and no partial write past the end of RAM.
        s = SlaveModel(ram_bytes=16)
        out = run(s, [Datagram(CMD["APWR"], 1, adp_ado(0, 0x1000 + 14), b"\x11\x22\x33\x44")])
        self.assertEqual(out[0].wkc, 0)
        self.assertEqual(bytes(s.ram[14:16]), b"\x00\x00")
        self.assertEqual(bytes(s.ram[0:2]), b"\x00\x00")

    def test_write_crossing_register_ram_boundary_rejected(self):
        s = SlaveModel(ram_bytes=16)
        out = run(s, [Datagram(CMD["APWR"], 1, adp_ado(0, 0x0FFE), b"\x11\x22\x33\x44")])
        self.assertEqual(out[0].wkc, 0)
        self.assertEqual(bytes(s.ram[0:2]), b"\x00\x00")

    def test_read_only_register_ignores_write(self):
        s = SlaveModel()
        run(s, [Datagram(CMD["APWR"], 1, adp_ado(0, 0x0004), b"\x09")])
        self.assertEqual(s.regs[0x0004], 3)


class LogicalTest(unittest.TestCase):
    def test_lrw_through_fmmu(self):
        s = SlaveModel(ram_bytes=64)
        set_fmmu(s, 0, logical=0x0001_0000, length=4, physical=0x1000, wr=True)  # outputs
        set_fmmu(s, 1, logical=0x0001_0010, length=2, physical=0x1010, rd=True)  # inputs
        s.ram[0x10:0x12] = b"\xaa\xbb"
        dg = Datagram(CMD["LRW"], 1, 0x0001_0000, bytes([1, 2, 3, 4]) + bytes(12) + b"\x00\x00")
        out = run(s, [dg])[0]
        self.assertEqual(bytes(s.ram[0:4]), bytes([1, 2, 3, 4]))      # write mapped into RAM
        self.assertEqual(out.data[0x10:0x12], b"\xaa\xbb")           # read mapped into frame
        self.assertEqual(out.wkc, 3)                                  # 1 (read) + 2 (write)

    def test_lrd_and_lwr_wkc(self):
        s = SlaveModel(ram_bytes=64)
        set_fmmu(s, 0, logical=0x100, length=4, physical=0x1000, rd=True, wr=True)
        self.assertEqual(run(s, [Datagram(CMD["LRD"], 1, 0x100, bytes(4))])[0].wkc, 1)
        self.assertEqual(run(s, [Datagram(CMD["LWR"], 2, 0x100, bytes(4))])[0].wkc, 1)

    def test_logical_outside_any_window_does_nothing(self):
        s = SlaveModel(ram_bytes=64)
        set_fmmu(s, 0, logical=0x100, length=4, physical=0x1000, rd=True)
        out = run(s, [Datagram(CMD["LRD"], 1, 0x200, b"\x55\x55")])[0]
        self.assertEqual((out.data, out.wkc), (b"\x55\x55", 0))

    def test_inactive_fmmu_ignored(self):
        s = SlaveModel(ram_bytes=64)
        set_fmmu(s, 0, logical=0x100, length=4, physical=0x1000, rd=True)
        s.regs[0x060C] = 0     # activate bit off
        self.assertEqual(run(s, [Datagram(CMD["LRD"], 1, 0x100, bytes(4))])[0].wkc, 0)

    def test_fmmu_window_partly_overlapping_datagram(self):
        s = SlaveModel(ram_bytes=64)
        set_fmmu(s, 0, logical=0x102, length=4, physical=0x1000, rd=True)
        s.ram[0:4] = b"\x01\x02\x03\x04"
        out = run(s, [Datagram(CMD["LRD"], 1, 0x100, bytes(8))])[0]
        self.assertEqual(out.data, b"\x00\x00\x01\x02\x03\x04\x00\x00")
        self.assertEqual(out.wkc, 1)

    def test_fmmu_window_beyond_ram_fails(self):
        s = SlaveModel(ram_bytes=8)
        set_fmmu(s, 0, logical=0x100, length=4, physical=0x1000 + 6, rd=True)
        self.assertEqual(run(s, [Datagram(CMD["LRD"], 1, 0x100, bytes(4))])[0].wkc, 0)


class FrameValidityTest(unittest.TestCase):
    def test_two_datagrams_processed_in_order(self):
        s = SlaveModel()
        out = run(s, [Datagram(CMD["APWR"], 1, adp_ado(0, 0x0010), b"\x01\x00", more=True),
                      Datagram(CMD["FPRD"], 2, adp_ado(0x0001, 0x0010), b"\x00\x00")])
        # APWR incremented nothing for the second datagram's own address field.
        self.assertEqual([d.wkc for d in out], [1, 1])
        self.assertEqual(out[1].data, b"\x01\x00")

    def test_malformed_length_dropped(self):
        # Review Focus 4: a datagram length that runs past the frame end.
        s = SlaveModel()
        raw = bytearray(build_frame([Datagram(CMD["APWR"], 1, adp_ado(0, 0x0010), b"\x34\x12")]))
        raw[14 + 2 + 6:14 + 2 + 8] = struct.pack("<H", 1400)       # datagram length field
        raw[-4:] = zlib.crc32(bytes(raw[:-4])).to_bytes(4, "little")
        self.assertIsNone(s.process(bytes(raw)))
        self.assertEqual(s.station_address, 0)
        self.assertEqual(s.counters.malformed, 1)

    def test_ethercat_length_disagreeing_with_frame_dropped(self):
        s = SlaveModel()
        raw = bytearray(build_frame([Datagram(CMD["APWR"], 1, adp_ado(0, 0x0010), b"\x34\x12")]))
        raw[14:16] = struct.pack("<H", (1 << 12) | 1200)           # EtherCAT length too big
        raw[-4:] = zlib.crc32(bytes(raw[:-4])).to_bytes(4, "little")
        self.assertIsNone(s.process(bytes(raw)))
        self.assertEqual((s.station_address, s.counters.malformed), (0, 1))

    def test_bad_fcs_flagged(self):
        s = SlaveModel()
        raw = bytearray(build_frame([Datagram(CMD["APWR"], 1, adp_ado(0, 0x0010), b"\x34\x12")]))
        raw[-1] ^= 0xFF
        self.assertIsNone(s.process(bytes(raw)))
        self.assertEqual(s.station_address, 0)               # writes blocked by default
        self.assertEqual(s.counters.crc_failures, 1)

    def test_bad_fcs_policy_commit_writes_when_enabled(self):
        s = SlaveModel(commit_on_bad_fcs=True)
        raw = bytearray(build_frame([Datagram(CMD["APWR"], 1, adp_ado(0, 0x0010), b"\x34\x12")]))
        raw[-1] ^= 0xFF
        out = s.process(bytes(raw))
        self.assertEqual(s.station_address, 0x1234)
        self.assertEqual(s.counters.crc_failures, 1)
        self.assertNotEqual(zlib.crc32(out[:-4]).to_bytes(4, "little"), out[-4:])  # stays invalid

    def test_runt_and_oversize_counted(self):
        s = SlaveModel()
        self.assertIsNone(s.process(bytes(40)))
        self.assertIsNone(s.process(bytes(1600)))
        self.assertEqual((s.counters.runts, s.counters.oversize), (1, 1))

    def test_non_ethercat_frame_counted_and_dropped(self):
        s = SlaveModel()
        raw = bytearray(build_frame([Datagram(CMD["NOP"], 1, 0, b"\x00")]))
        raw[12:14] = b"\x08\x00"
        raw[-4:] = zlib.crc32(bytes(raw[:-4])).to_bytes(4, "little")
        self.assertIsNone(s.process(bytes(raw)))
        self.assertEqual(s.counters.non_ethercat, 1)

    def test_counters_track_datagrams(self):
        s = SlaveModel()
        run(s, [Datagram(CMD["APRD"], 1, adp_ado(0, 0x0004), b"\x00", more=True),
                Datagram(CMD["FPRD"], 2, adp_ado(0x7777, 0x0004), b"\x00")])
        c = s.counters
        self.assertEqual((c.frames, c.datagrams, c.datagrams_addressed, c.wkc_increments),
                         (1, 2, 1, 1))

    def test_frame_roundtrip(self):
        dgs = [Datagram(CMD["LRW"], 9, 0x12345678, bytes(range(10)), irq=0x0102, more=True, wkc=3),
               Datagram(CMD["BRD"], 10, 0, b"\xff")]
        self.assertEqual(parse_frame(build_frame(dgs)), dgs)


if __name__ == "__main__":
    unittest.main()
