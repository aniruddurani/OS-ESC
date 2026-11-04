"""run_netsim.run_one against the built bench: what a failing run leaves behind.
Needs the built bench: run with `make dnettest` (sets NETSIM_BIN)."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, ".."), os.path.join(HERE, "..", "..", "model"),
                os.path.join(HERE, "..", "..", "tb")]

from master import Master  # noqa: E402
from pcap import read_pcap  # noqa: E402
import run_netsim  # noqa: E402

BIN = os.environ.get("NETSIM_BIN", "build/ecat_esc/netsim/obj/Vesc_top")


class FailureArtifactsTest(unittest.TestCase):
    def test_failure_pcap_holds_the_failing_frame_and_its_reply(self):
        # Spec section 4: on a failure, a pcap of the failing frame and the frames before it.
        spec = dict(scenario="cyclic", k=0, seed=1, frames=200, sabotage="checker")
        first = Master("cyclic", 1, k=0, frames=200).next_batch(1)[0].frame
        with tempfile.TemporaryDirectory() as out:
            res = run_netsim.run_one(spec, BIN, out, write_pcaps=False)
            self.assertEqual(res["failures"][0]["index"], 0)
            name = run_netsim.run_name(spec)
            frames = [f for _, f in read_pcap(os.path.join(out, f"fail_{name}.pcap"))]
            got = bytes.fromhex(json.load(open(os.path.join(out, f"fail_{name}.json")))["failures"][0]["got"])
        self.assertTrue(first in frames, f"the failing frame's input is not among {len(frames)} frames")
        self.assertTrue(got in frames, "the failing reply is not in the pcap")
        self.assertLess(frames.index(first), frames.index(got))


if __name__ == "__main__":
    unittest.main()
