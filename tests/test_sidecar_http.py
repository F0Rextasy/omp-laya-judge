"""HTTP sidecar contract: real server, real sockets and lifecycle.

Some machines drop new 127.0.0.1 connections (strict AV/WFP loopback
policies — observed while developing this suite), so the handler contract
class probes loopback first and falls back to a local interface address.
"""
import contextlib
import io
import json
import os
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

SERVER_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server")
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

import sidecar  # noqa: E402
from tests.support import probe_loopback  # noqa: E402
from tests.test_core import FakeBatchRouter  # noqa: E402


def _test_host():
    if probe_loopback(ThreadingHTTPServer, sidecar.Handler):
        return "127.0.0.1"
    ip = socket.gethostbyname(socket.gethostname())
    return "127.0.0.1" if ip.startswith("127.") else ip


class SidecarHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.pop("LAYA_MODEL", None)
        cls.host = _test_host()
        sidecar.router = FakeBatchRouter()
        cls.httpd = ThreadingHTTPServer((cls.host, 0), sidecar.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        sidecar.router = None

    def _request(self, path, data=None):
        url = f"http://{self.host}:{self.port}{path}"
        body = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request(
            url, data=body, method="GET" if body is None else "POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_single_judgment_shape(self):
        status, out = self._request("/judge", {
            "state": {"text": "charged twice"},
            "questions": {"even": {"type": "bool", "instructions": "Is it even?"}},
        })
        self.assertEqual(status, 200)
        self.assertEqual(out["answers"]["even"]["noul"], 0.75)
        self.assertEqual(out["model"], "laya/english")
        self.assertIn("routing", out)
        self.assertIn("usage", out)
        self.assertIsInstance(out["latency_ms"], int)

    def test_oversized_systemone_is_chunked_and_merged(self):
        # The `find` cascade sends 58 questions in one call, so the chunked
        # path is a live route, not an edge case: every id must come back in
        # one envelope with the model's provenance and summed usage.
        questions = {f"q{i}": {"type": "bool", "instructions": f"is it case {i}?"}
                     for i in range(sidecar.MAX_QUESTIONS + 4)}
        status, out = self._request("/v1/systemone", {"state": "text", "questions": questions})
        self.assertEqual(status, 200)
        self.assertEqual(set(out["answers"]), set(questions))
        self.assertTrue(all(a["type"] == "noul" for a in out["answers"].values()))
        self.assertEqual(out["model"], "laya/english")
        self.assertGreaterEqual(out["usage"]["input_tokens"], 0)

    def test_oversized_systemone_over_total_is_422(self):
        questions = {f"q{i}": {"type": "bool", "instructions": "?"}
                     for i in range(sidecar.MAX_TOTAL_QUESTIONS + 1)}
        status, out = self._request("/v1/systemone", {"state": "text", "questions": questions})
        self.assertEqual(status, 422)
        self.assertIn("error", out)

    def test_batch_list(self):
        status, out = self._request("/judge", [
            {"state": {"text": "a"}, "questions": {"q": {"type": "choice", "instructions": "?"}}},
            {"state": {"text": "b"}, "questions": {"q": {"type": "noul", "instructions": "?"}}},
        ])
        self.assertEqual(status, 200)
        self.assertEqual(out["count"], 2)
        self.assertEqual(len(out["results"]), 2)
        self.assertIn("latency_ms", out)
        self.assertEqual(sidecar.router.batch_calls, 1)

    def test_info_shape(self):
        status, out = self._request("/info")
        self.assertEqual(status, 200)
        self.assertEqual(out["model"], "laya/auto")
        self.assertTrue(out["loaded"])
        self.assertIn("bool", out["types"])
        self.assertIsInstance(out["resident"], list)
        self.assertIn("startup_error", out)

    def test_load_failure_reports_and_keeps_serving(self):
        original_router = sidecar.router
        original_error = sidecar.startup_error
        event_was_set = sidecar._load_complete.is_set()
        sidecar.router = None
        sidecar.startup_error = "load failed"
        sidecar._load_complete.set()
        try:
            status, error = self._request("/judge", {
                "state": "anything", "questions": {},
            })
            self.assertEqual(status, 500)
            self.assertEqual(error["startup_error"], "load failed")
            info_status, info = self._request("/info")
            self.assertEqual(info_status, 200)
            self.assertEqual(info["startup_error"], "load failed")
        finally:
            sidecar.router = original_router
            sidecar.startup_error = original_error
            if event_was_set:
                sidecar._load_complete.set()
            else:
                sidecar._load_complete.clear()

    def test_unknown_path_404(self):
        status, _ = self._request("/nope", {"x": 1})
        self.assertEqual(status, 404)

    def test_malformed_json_stays_up(self):
        url = f"http://{self.host}:{self.port}/judge"
        request = urllib.request.Request(
            url, data=b"{not json", method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10):
                self.fail("expected HTTP 500")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 500)
            error = json.loads(exc.read())
            self.assertIn("error", error)
            self.assertIn("detail", error)
            self.assertIn("startup_error", error)
        status, _ = self._request("/info")
        self.assertEqual(status, 200)


class SidecarLifecycleTest(unittest.TestCase):
    def test_bind_conflict_probes_running_info(self):
        existing = ThreadingHTTPServer(("127.0.0.1", 0), sidecar.Handler)
        thread = threading.Thread(target=existing.serve_forever, daemon=True)
        thread.start()
        try:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertIsNone(sidecar._bind(existing.server_address[1]))
            self.assertIn("already running", output.getvalue())
        finally:
            existing.shutdown()
            existing.server_close()
            thread.join(timeout=2)

    def test_bind_conflict_without_info_is_clear_error(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        try:
            with self.assertRaisesRegex(RuntimeError, "no valid sidecar answered"):
                sidecar._bind(listener.getsockname()[1])
        finally:
            listener.close()

    def test_idle_exit_and_request_touch(self):
        httpd = sidecar.SidecarHTTPServer(("127.0.0.1", 0), sidecar.Handler)
        httpd.idle_seconds = 1.0
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        url = "http://127.0.0.1:%d/info" % httpd.server_address[1]
        try:
            time.sleep(0.6)
            with urllib.request.urlopen(url, timeout=1):
                pass
            time.sleep(0.6)
            with urllib.request.urlopen(url, timeout=1):
                pass
            time.sleep(0.2)
            self.assertTrue(thread.is_alive(), "request did not reset idle timer")
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive(), "idle sidecar did not exit")
        finally:
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
