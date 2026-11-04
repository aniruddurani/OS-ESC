"""Tests for the reference model. Vectors are derived from the standard and the
Beckhoff documentation, not from hardware: NOT VERIFIED AGAINST REAL HARDWARE
until Task 11 replays recorded captures."""
import struct
import unittest
import zlib

import ecat
from ecat import CMD, Datagram, SlaveModel, build_frame, parse_frame


def fcs_ok(frame):
    return zlib.crc32(frame[:-4]).to_bytes(4, "little") == frame[-4:]


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


class RegisterAccessTest(unittest.TestCase):
    """Register map details confirmed against TI's ESC register list
    (software-dl.ti.com ... ETHERCAT_SUBDEVICE_CONTROLLER_REGISTER_LIST)."""

    def write(self, s, address, data):
        run(s, [Datagram(CMD["APWR"], 1, adp_ado(0, address), data)])

    def test_fmmu_reserved_bytes_are_read_only(self):
        s = SlaveModel()
        self.write(s, 0x0600, bytes(range(1, 17)))
        self.assertEqual(bytes(s.regs[0x0600:0x060D]), bytes(range(1, 14)))
        self.assertEqual(bytes(s.regs[0x060D:0x0610]), b"\x00\x00\x00")

    def test_syncmanager_status_and_pdi_activate_are_read_only(self):
        s = SlaveModel()
        self.write(s, 0x0800, bytes(range(1, 9)))
        self.assertEqual(bytes(s.regs[0x0800:0x0808]), bytes([1, 2, 3, 4, 5, 0, 7, 0]))

    def test_station_alias_is_not_writable_from_ethercat(self):
        s = SlaveModel()
        self.write(s, 0x0012, b"\x34\x12")
        self.assertEqual(bytes(s.regs[0x0012:0x0014]), b"\x00\x00")


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

    def test_overlapping_fmmus_first_one_wins_per_byte(self):
        # Ruling (Task 9): where same-direction windows overlap, the lowest-numbered
        # FMMU owns the byte. A real master never overlaps them; this just pins
        # the behaviour so the RTL and the model agree.
        s = SlaveModel(ram_bytes=64)
        set_fmmu(s, 0, logical=0x100, length=4, physical=0x1000, rd=True)
        set_fmmu(s, 1, logical=0x102, length=4, physical=0x1010, rd=True)
        s.ram[0:4] = b"\x01\x02\x03\x04"
        s.ram[0x10:0x14] = b"\x09\x08\x07\x06"
        out = run(s, [Datagram(CMD["LRD"], 1, 0x100, bytes(6))])[0]
        self.assertEqual(out.data, b"\x01\x02\x03\x04\x07\x06")
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

    # Policy (ruling, Task 8): the datapath is cut-through, so writes land as the
    # bytes pass. A frame that turns out bad is still forwarded, but with a
    # deliberately wrong FCS so the next device and the master discard it, and
    # the cause is counted. A frame shorter than 5 bytes produces no output.

    def test_malformed_length_is_poisoned_and_not_executed(self):
        # Review Focus 4: a datagram length that runs past the frame end.
        s = SlaveModel()
        raw = bytearray(build_frame([Datagram(CMD["APWR"], 1, adp_ado(0, 0x0010), b"\x34\x12")]))
        raw[14 + 2 + 6:14 + 2 + 8] = struct.pack("<H", 1400)       # datagram length field
        raw[-4:] = zlib.crc32(bytes(raw[:-4])).to_bytes(4, "little")
        out = s.process(bytes(raw))
        self.assertFalse(fcs_ok(out))
        self.assertEqual(s.station_address, 0)                     # the bad datagram never ran
        self.assertEqual(s.counters.malformed, 1)
        # Cut-through: the position field of an auto-increment datagram has already
        # been incremented when its bad length is seen.
        self.assertEqual(out[16 + 2], 1)

    def test_ethercat_length_disagreeing_with_frame_is_poisoned(self):
        s = SlaveModel()
        raw = bytearray(build_frame([Datagram(CMD["APWR"], 1, adp_ado(0, 0x0010), b"\x34\x12")]))
        raw[14:16] = struct.pack("<H", (1 << 12) | 1200)           # EtherCAT length too big
        raw[-4:] = zlib.crc32(bytes(raw[:-4])).to_bytes(4, "little")
        out = s.process(bytes(raw))
        self.assertFalse(fcs_ok(out))
        self.assertEqual(s.counters.malformed, 1)

    def test_leading_datagrams_run_before_a_malformed_one(self):
        s = SlaveModel()
        raw = bytearray(build_frame([
            Datagram(CMD["APWR"], 1, adp_ado(0, 0x0010), b"\x34\x12", more=True),
            Datagram(CMD["NOP"], 2, 0, b"\x00")]))
        raw[16 + 14 + 6:16 + 14 + 8] = struct.pack("<H", 1400)    # break the second datagram
        raw[-4:] = zlib.crc32(bytes(raw[:-4])).to_bytes(4, "little")
        out = s.process(bytes(raw))
        self.assertEqual(s.station_address, 0x1234)
        self.assertFalse(fcs_ok(out))
        self.assertEqual(s.counters.malformed, 1)

    # A frame that ends inside a datagram (a runt, or a frame cut short) after the datagram's
    # header has passed and fit the EtherCAT length: like rtl/dgram_exec.v, the bytes that passed
    # were read or written as they went by, the four the core takes for the FCS included. The
    # datagram never finished, so it is not counted.
    @staticmethod
    def cut_frame(dg, keep):
        raw = build_frame([dg])
        return raw[:keep]

    def test_frame_cut_inside_a_write_lands_the_bytes_that_passed(self):
        s = SlaveModel(ram_bytes=256)
        data = bytes(range(1, 33))
        raw = self.cut_frame(Datagram(CMD["APWR"], 1, adp_ado(0, 0x1000), data), 50)
        out = s.process(raw)
        self.assertEqual(bytes(s.ram[:32]), data[:24] + bytes(8))    # bytes 26..49 passed
        self.assertEqual(out[:46], raw[:18] + b"\x01\x00" + raw[20:46])
        self.assertFalse(fcs_ok(out))
        c = s.counters
        self.assertEqual((c.datagrams, c.datagrams_addressed, c.wkc_increments, c.malformed, c.runts),
                         (0, 0, 0, 1, 1))
        self.assertEqual(s.trace, [])

    def test_frame_cut_inside_a_read_inserts_the_bytes_that_passed(self):
        s = SlaveModel(ram_bytes=256)
        s.ram[:32] = bytes(range(100, 132))
        raw = self.cut_frame(Datagram(CMD["FPRD"], 1, adp_ado(0, 0x1000), bytes(32)), 50)
        out = s.process(raw)
        self.assertEqual(out[26:46], bytes(range(100, 120)))
        self.assertEqual(len(out), 50)

    def test_frame_cut_inside_a_logical_read_write(self):
        s = SlaveModel(ram_bytes=256)
        set_fmmu(s, 0, 0x10000, 32, 0x1000, rd=True, wr=True)
        s.ram[:32] = bytes(range(100, 132))
        data = bytes(range(1, 33))
        raw = self.cut_frame(Datagram(CMD["LRW"], 1, 0x10000, data), 52)
        out = s.process(raw)
        self.assertEqual(out[26:48], bytes(range(100, 122)))       # old memory read out
        self.assertEqual(bytes(s.ram[:32]), data[:26] + bytes(range(126, 132)))
        self.assertEqual(s.counters.wkc_increments, 0)

    def test_datagram_reaching_into_the_fcs_does_not_crash(self):
        # The parser (like frame_parse.v) counts the FCS bytes as frame bytes, so a datagram
        # can end inside them; what it writes there is dropped with the old FCS.
        s = SlaveModel()
        raw = bytearray(build_frame([Datagram(CMD["APWR"], 1, adp_ado(0, 0x0010), b"\x34\x12")]))
        self.assertEqual(len(raw), 64)
        raw[14:16] = struct.pack("<H", (1 << 12) | (14 + 12 + 22))
        raw[30:40] = struct.pack("<BBIHH", CMD["NOP"], 2, 0, 22, 0)  # 30 + 10 + 22 + 2 = 64
        raw[-4:] = zlib.crc32(bytes(raw[:-4])).to_bytes(4, "little")
        out = s.process(bytes(raw))
        self.assertEqual(len(out), len(raw))
        self.assertEqual(s.station_address, 0x1234)
        # position field incremented, WKC 1, the NOP's bytes untouched
        self.assertEqual(out[:60], bytes(raw[:18]) + b"\x01\x00" + bytes(raw[20:28]) + b"\x01\x00"
                         + bytes(raw[30:60]))

    def test_bad_fcs_writes_land_and_frame_is_poisoned(self):
        s = SlaveModel()
        raw = bytearray(build_frame([Datagram(CMD["APWR"], 1, adp_ado(0, 0x0010), b"\x34\x12")]))
        raw[-1] ^= 0xFF
        out = s.process(bytes(raw))
        self.assertEqual(s.station_address, 0x1234)
        self.assertEqual(s.counters.crc_failures, 1)
        self.assertFalse(fcs_ok(out))

    def test_good_frame_keeps_a_valid_fcs(self):
        s = SlaveModel()
        out = s.process(build_frame([Datagram(CMD["NOP"], 1, 0, b"\x00")]))
        self.assertTrue(fcs_ok(out))

    def test_runt_and_oversize_are_counted_and_poisoned(self):
        s = SlaveModel()
        runt, big = s.process(bytes(40)), s.process(bytes(1600))
        self.assertEqual((len(runt), len(big)), (40, 1600))
        self.assertFalse(fcs_ok(runt) or fcs_ok(big))
        self.assertEqual((s.counters.runts, s.counters.oversize), (1, 1))
        self.assertIsNone(s.process(bytes(4)))

    def test_non_ethercat_frame_is_counted_and_poisoned(self):
        s = SlaveModel()
        raw = bytearray(build_frame([Datagram(CMD["NOP"], 1, 0, b"\x00")]))
        raw[12:14] = b"\x08\x00"
        raw[-4:] = zlib.crc32(bytes(raw[:-4])).to_bytes(4, "little")
        out = s.process(bytes(raw))
        self.assertEqual(out[:-4], bytes(raw[:-4]))
        self.assertFalse(fcs_ok(out))
        self.assertEqual(s.counters.non_ethercat, 1)

    def test_counters_track_datagrams(self):
        s = SlaveModel()
        run(s, [Datagram(CMD["APRD"], 1, adp_ado(0, 0x0004), b"\x00", more=True),
                Datagram(CMD["FPRD"], 2, adp_ado(0x7777, 0x0004), b"\x00")])
        c = s.counters
        self.assertEqual((c.frames, c.datagrams, c.datagrams_addressed, c.wkc_increments),
                         (1, 2, 1, 1))

    def test_trace_records_each_datagram(self):
        # (cmd, original address, length, wkc increment, addressed): what diag logs.
        s = SlaveModel()
        run(s, [Datagram(CMD["APRD"], 1, adp_ado(0, 0x0004), b"\x00", more=True),
                Datagram(CMD["FPRD"], 2, adp_ado(0x7777, 0x0004), b"\x00\x00")])
        self.assertEqual(s.trace, [(CMD["APRD"], adp_ado(0, 0x0004), 1, 1, True),
                                   (CMD["FPRD"], adp_ado(0x7777, 0x0004), 2, 0, False)])

    def test_rx_err_flag_counts_and_poisons(self):
        s = SlaveModel()
        out = s.process(build_frame([Datagram(CMD["NOP"], 1, 0, b"\x00")]), rx_err=True)
        self.assertEqual(s.counters.rx_errors, 1)
        self.assertFalse(fcs_ok(out))

    def test_frame_roundtrip(self):
        dgs = [Datagram(CMD["LRW"], 9, 0x12345678, bytes(range(10)), irq=0x0102, more=True, wkc=3),
               Datagram(CMD["BRD"], 10, 0, b"\xff")]
        self.assertEqual(parse_frame(build_frame(dgs)), dgs)


if __name__ == "__main__":
    unittest.main()
