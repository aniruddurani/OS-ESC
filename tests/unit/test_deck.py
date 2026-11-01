import os
import tempfile
import unittest

from flowlib.deck import make_deck


class MakeDeckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def dut(self, text):
        path = os.path.join(self.tmp.name, "dut.spice")
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_deck_includes_lib_dut_and_tb_in_order(self):
        dut = self.dut(".subckt inv in out vdd vss\nXM1 out in vss vss nfet\n.ends\n")
        deck = make_deck(dut, "designs/inv/tb/inv_tb.spice", "/pdk/sky130.lib.spice", "tt", "inv")
        lines = [l for l in deck.splitlines() if l.startswith(".")]
        self.assertEqual(lines, [
            '.lib "/pdk/sky130.lib.spice" tt',
            f'.include "{dut}"',
            '.include "designs/inv/tb/inv_tb.spice"',
            ".end",
        ])

    def test_temperature_is_written_before_the_includes(self):
        dut = self.dut(".subckt inv in out vdd vss\nXM1 out in vss vss nfet\n.ends\n")
        deck = make_deck(dut, "tb.spice", "lib.spice", "ss", "inv", temp=125)
        lines = [l for l in deck.splitlines() if l.startswith(".")]
        self.assertEqual(lines[:3], [".temp 125", '.lib "lib.spice" ss', f'.include "{dut}"'])

    def test_wrong_port_order_is_refused(self):
        # Swapped ports would silently swap nets in the testbench (Review Focus 1).
        dut = self.dut(".subckt inv out in vdd vss\n.ends\n")
        with self.assertRaises(ValueError):
            make_deck(dut, "tb.spice", "lib.spice", "tt", "inv")

    def test_missing_subckt_is_refused(self):
        dut = self.dut(".subckt other a b\n.ends\n")
        with self.assertRaises(ValueError):
            make_deck(dut, "tb.spice", "lib.spice", "tt", "inv")


if __name__ == "__main__":
    unittest.main()
