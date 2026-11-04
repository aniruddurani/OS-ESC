"""Live view of the network simulation: serves index.html, streams events to it as
server-sent events, and takes pause / step / speed from it. Standard library only.

    GET  /         the page
    GET  /events   text/event-stream, one `data: <json>` per event (recent history first)
    POST /control  {"pause": bool} | {"step": n} | {"speed": x}  -> 204, or 400
"""
import collections
import http.server
import json
import os
import queue
import threading

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
HISTORY = 4000                   # events replayed to a page that connects late


class GuiServer:
    def __init__(self, port=8765, host="0.0.0.0", page=PAGE):
        self._page = page
        self._lock = threading.Lock()
        self._subs = []
        self._history = collections.deque(maxlen=HISTORY)
        self.controls = {"paused": False, "step": 0, "speed": 1.0}
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    with open(server._page, "rb") as f:
                        body = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/events":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    q = server._subscribe()
                    try:
                        while not server._stopping.is_set():
                            try:
                                ev = q.get(timeout=1.0)
                                self.wfile.write(b"data: " + json.dumps(ev).encode() + b"\n\n")
                            except queue.Empty:
                                self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    finally:
                        server._unsubscribe(q)
                else:
                    self.send_error(404)

            def do_POST(self):
                if self.path != "/control":
                    self.send_error(404)
                    return
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    server._control(json.loads(self.rfile.read(n) or b"{}"))
                except (ValueError, TypeError) as e:
                    self.send_error(400, str(e))
                    return
                self.send_response(204)
                self.end_headers()

        self._stopping = threading.Event()
        self._httpd = http.server.ThreadingHTTPServer((host, port), Handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]

    def _subscribe(self):
        q = queue.Queue(maxsize=20000)
        with self._lock:
            for ev in self._history:
                q.put_nowait(ev)
            self._subs.append(q)
        return q

    def _unsubscribe(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def _control(self, body):
        if not isinstance(body, dict) or not body:
            raise ValueError("expected a JSON object")
        with self._lock:
            for key, value in body.items():
                if key == "pause" and isinstance(value, bool):
                    self.controls["paused"] = value
                elif key == "step" and isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    self.controls["step"] += value
                elif key == "speed" and isinstance(value, (int, float)) and not isinstance(value, bool) \
                        and value > 0:
                    self.controls["speed"] = float(value)
                else:
                    raise ValueError(f"bad control {key}={value!r}")

    def take_step(self):
        """True (and one step used) if a single step was requested while paused."""
        with self._lock:
            if self.controls["step"] > 0:
                self.controls["step"] -= 1
                return True
            return False

    def publish(self, event):
        with self._lock:
            self._history.append(event)
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:                 # a stalled page loses events, never the simulation
                pass

    def start(self):
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def stop(self):
        self._stopping.set()
        self._httpd.shutdown()
        self._httpd.server_close()
