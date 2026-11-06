import os
import sys
import tempfile
import unittest

TB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "designs", "ecat_esc", "tb")
sys.path.insert(0, TB)

from replay import REQUIRED_CAPTURES, capture_files  # noqa: E402


class CaptureFilesTest(unittest.TestCase):
    def folder(self, names):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        for name in names:
            open(os.path.join(d.name, name), "wb").close()
        return d.name

    def test_the_repo_holds_every_required_capture(self):
        names = [os.path.basename(p) for p in capture_files(os.path.join(TB, "..", "captures"))]
        self.assertEqual(sorted(REQUIRED_CAPTURES), [n for n in names if n in REQUIRED_CAPTURES])

    def test_a_missing_capture_is_a_failure(self):
        with self.assertRaises(AssertionError) as ctx:
            capture_files(self.folder([REQUIRED_CAPTURES[0]]))
        self.assertIn(REQUIRED_CAPTURES[1], str(ctx.exception))

    def test_an_empty_folder_is_a_failure(self):
        with self.assertRaises(AssertionError):
            capture_files(self.folder([]))

    def test_a_further_capture_replays_too(self):
        got = capture_files(self.folder(list(REQUIRED_CAPTURES) + ["a_third.pcap", "notes.txt"]))
        self.assertEqual([os.path.basename(p) for p in got], sorted(list(REQUIRED_CAPTURES) + ["a_third.pcap"]))
