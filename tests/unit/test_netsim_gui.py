"""The netsim live-view server (designs/ecat_esc/netsim/gui/server.py)."""
import json
import os
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "designs", "ecat_esc", "netsim", "gui"))

from server import GuiServer  # noqa: E402


class GuiServerTest(unittest.TestCase):
    def setUp(self):
        self.srv = GuiServer(port=0, host="127.0.0.1")
        self.srv.start()
        self.base = f"http://127.0.0.1:{self.srv.port}"

    def tearDown(self):
        self.srv.stop()

    def post(self, body):
        req = urllib.request.Request(self.base + "/control", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        return urllib.request.urlopen(req, timeout=5).status

    def test_serves_page(self):
        with urllib.request.urlopen(self.base + "/", timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertIn(b"<svg", r.read())

    def test_streams_published_event(self):
        self.srv.publish({"kind": "x", "n": 1})
        with urllib.request.urlopen(self.base + "/events", timeout=5) as r:
            self.assertEqual(r.headers["Content-Type"], "text/event-stream")
            for _ in range(20):
                line = r.readline().decode().strip()
                if line.startswith("data:"):
                    self.assertEqual(json.loads(line[5:]), {"kind": "x", "n": 1})
                    return
        self.fail("no event received")

    def test_controls(self):
        self.assertEqual(self.post({"pause": True}), 204)
        self.assertTrue(self.srv.controls["paused"])
        self.post({"step": 1})
        self.assertEqual(self.srv.controls["step"], 1)
        self.assertTrue(self.srv.take_step())
        self.assertFalse(self.srv.take_step())
        self.post({"speed": 4})
        self.assertEqual(self.srv.controls["speed"], 4.0)

    def test_bad_control_is_400(self):
        for body in ({"speed": "fast"}, {"speed": 0}, {"pause": "yes"}, {"step": -1}, {"nope": 1}):
            with self.assertRaises(urllib.error.HTTPError) as e:
                self.post(body)
            self.assertEqual(e.exception.code, 400, body)


if __name__ == "__main__":
    unittest.main()
