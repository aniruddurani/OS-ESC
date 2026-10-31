import unittest

from flowlib.parsers import (
    drc_status,
    parse_lyrdb,
    parse_magic_drc,
    parse_netgen,
    parse_ngspice_meas,
    parse_ports,
    parse_subckt_ports,
)


class NgspiceMeasTest(unittest.TestCase):
    LOG = (
        "Circuit: * generated deck\n"
        "vdd = 1.8\n"
        "tphl                =  2.500000e-11 targ=  1.035000e-09 trig=  1.010000e-09\n"
        "Error: measure  tplh  (TRIG) : out of interval\n"
    )

    def test_reads_requested_names_only(self):
        got = parse_ngspice_meas(self.LOG, ["tphl", "tplh"])
        self.assertEqual(got, {"tphl": 2.5e-11, "tplh": None})

    def test_name_match_is_case_insensitive(self):
        got = parse_ngspice_meas("TPHL = 1.0e-11\n", ["tphl"])
        self.assertEqual(got, {"tphl": 1.0e-11})

    def test_failed_value_is_none(self):
        got = parse_ngspice_meas("vm = failed\n", ["vm"])
        self.assertEqual(got, {"vm": None})

    def test_name_must_match_whole_word(self):
        got = parse_ngspice_meas("xvm = 0.5\n", ["vm"])
        self.assertEqual(got, {"vm": None})


class MagicDrcTest(unittest.TestCase):
    def test_parses_count_bbox_and_errors(self):
        log = (
            "Loading inv\n"
            "DRC_COUNT 1\n"
            "BBOX -1.5 -2.0 3.25 4.0\n"
            "DRC_ERR 0.1 0.2 0.3 0.4 Metal1 spacing < 0.14um (met1.2)\n"
        )
        got = parse_magic_drc(log)
        self.assertEqual(got["count"], 1)
        self.assertEqual(got["bbox"], (-1.5, -2.0, 3.25, 4.0))
        self.assertEqual(got["violations"], [
            {"rule": "Metal1 spacing < 0.14um (met1.2)", "box": (0.1, 0.2, 0.3, 0.4)},
        ])

    def test_missing_lines_give_none(self):
        got = parse_magic_drc("magic crashed\n")
        self.assertIsNone(got["count"])
        self.assertIsNone(got["bbox"])
        self.assertEqual(got["violations"], [])


class DrcStatusTest(unittest.TestCase):
    def test_zero_area_bbox_is_error(self):
        # An empty or unloaded cell reports 0 errors; that is not a pass (Review Focus 2).
        self.assertEqual(drc_status(0, (0, 0, 0, 0), []), "error")

    def test_clean_layout_passes(self):
        self.assertEqual(drc_status(0, (0, 0, 5, 5), []), "pass")

    def test_magic_violations_fail(self):
        self.assertEqual(drc_status(2, (0, 0, 5, 5), []), "fail")

    def test_klayout_violations_fail(self):
        self.assertEqual(drc_status(0, (0, 0, 5, 5), [{"rule": "m1.2"}]), "fail")

    def test_missing_klayout_report_is_error(self):
        self.assertEqual(drc_status(0, (0, 0, 5, 5), None), "error")

    def test_missing_count_is_error(self):
        self.assertEqual(drc_status(None, (0, 0, 5, 5), []), "error")


class LyrdbTest(unittest.TestCase):
    XML = """<?xml version="1.0" encoding="utf-8"?>
<report-database>
 <categories><category><name>m1.2</name></category></categories>
 <items>
  <item><category>'m1.2'</category><cell>inv</cell>
   <values><value>edge-pair: (0,0;1,0)/(0,0.1;1,0.1)</value></values></item>
  <item><category>'li.3'</category><cell>inv</cell>
   <values><value>polygon: (0,0;1,0;1,1)</value></values></item>
 </items>
</report-database>
"""

    def test_one_entry_per_item(self):
        got = parse_lyrdb(self.XML)
        self.assertEqual(got, [
            {"rule": "m1.2", "where": "edge-pair: (0,0;1,0)/(0,0.1;1,0.1)"},
            {"rule": "li.3", "where": "polygon: (0,0;1,0;1,1)"},
        ])

    def test_empty_database_is_empty_list(self):
        self.assertEqual(parse_lyrdb("<report-database><items/></report-database>"), [])

    def test_not_a_report_database_is_none(self):
        self.assertIsNone(parse_lyrdb("<html/>"))

    def test_malformed_xml_is_none(self):
        self.assertIsNone(parse_lyrdb("<report-database><items>"))


class NetgenTest(unittest.TestCase):
    MATCH = (
        "Subcircuit summary:\n"
        "Circuit 1: inv                  |Circuit 2: inv\n"
        "Netlists match uniquely.\n"
        "Final result: Circuits match uniquely.\n"
    )

    def test_unique_match(self):
        self.assertEqual(parse_netgen(self.MATCH), {"match": True, "errors": []})

    def test_pin_mismatch_is_not_a_match(self):
        got = parse_netgen(self.MATCH + "Top level cell failed pin matching.\n")
        self.assertFalse(got["match"])
        self.assertIn("Top level cell failed pin matching.", got["errors"])

    def test_netlists_do_not_match(self):
        got = parse_netgen("Netlists do not match.\nFinal result: Netlists do not match.\n")
        self.assertFalse(got["match"])

    def test_property_errors_are_not_a_match(self):
        got = parse_netgen(self.MATCH + "Property errors were found.\n")
        self.assertFalse(got["match"])

    def test_no_final_result_is_none(self):
        self.assertIsNone(parse_netgen("")["match"])


class PortsTest(unittest.TestCase):
    def test_ports_ordered_by_index(self):
        log = "PORT 3 vdd\nPORT 1 in\nnoise\nPORT 4 vss\nPORT 2 out\n"
        self.assertEqual(parse_ports(log), ["in", "out", "vdd", "vss"])

    def test_no_ports(self):
        self.assertEqual(parse_ports("nothing\n"), [])


class SubcktPortsTest(unittest.TestCase):
    def test_follows_continuation_lines(self):
        text = "* netlist\n.subckt inv in out\n+ vdd vss\nXM1 out in vss vss nfet\n.ends\n"
        self.assertEqual(parse_subckt_ports(text, "inv"), ["in", "out", "vdd", "vss"])

    def test_case_insensitive_and_lowercased(self):
        text = ".SUBCKT INV IN OUT VDD VSS\n.ENDS\n"
        self.assertEqual(parse_subckt_ports(text, "inv"), ["in", "out", "vdd", "vss"])

    def test_ignores_parameters(self):
        text = ".subckt inv in out vdd vss w=1 l=0.15\n.ends\n"
        self.assertEqual(parse_subckt_ports(text, "inv"), ["in", "out", "vdd", "vss"])

    def test_other_subckt_ignored(self):
        text = ".subckt other a b\n.ends\n"
        self.assertIsNone(parse_subckt_ports(text, "inv"))


if __name__ == "__main__":
    unittest.main()
