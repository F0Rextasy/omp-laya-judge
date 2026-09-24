"""HTTP sidecar contract: real server, real sockets.

Some machines drop new 127.0.0.1 connections (strict AV/WFP loopback
policies — observed while developing this suite), so the class probes
loopback first and falls back to a local interface address.
"""
import json
import os
import socket
import sys
import threading
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
        sidecar.router = FakeBatchRouter()  # light: no torch/laya import
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
        req = urllib.request.Request(url, data=body, method="GET" if body is None else "POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_single_judgment_shape(self):
        status, out = self._request("/judge", {
            "state": {"text": "charged twice"},
            "questions": {"even": {"type": "bool", "instructions": "Is it even?"}},
        })
        self.assertEqual(status, 200)
        self.assertEqual(out["answers"]["even"]["noul"], 0.75)  # verbatim laya shape
        self.assertEqual(out["model"], "laya/english")          # routing provenance
        self.assertIn("routing", out)
        self.assertIn("usage", out)
        self.assertIsInstance(out["latency_ms"], int)

    def test_batch_list(self):
        status, out = self._request("/judge", [
            {"state": {"text": "a"}, "questions": {"q": {"type": "choice", "instructions": "?"}}},
            {"state": {"text": "b"}, "questions": {"q": {"type": "noul", "instructions": "?"}}},
        ])
        self.assertEqual(status, 200)
        self.assertEqual(out["count"], 2)
        self.assertEqual(len(out["results"]), 2)
        self.assertIn("latency_ms", out)
        self.assertEqual(sidecar.router.batch_calls, 1)  # one batched forward pass

    def test_info_shape(self):
        status, out = self._request("/info")
        self.assertEqual(status, 200)
        self.assertEqual(out["model"], "laya/auto")  # no pin
        self.assertTrue(out["loaded"])
        self.assertIn("bool", out["types"])
        self.assertIn("startup_error", out)

    def test_unknown_path_404(self):
        status, _ = self._request("/nope", {"x": 1})
        self.assertEqual(status, 404)

    def test_malformed_json_stays_up(self):
        url = f"http://{self.host}:{self.port}/judge"
        req = urllib.request.Request(url, data=b"{not json", method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10):
                self.fail("expected HTTP 500")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 500)
            self.assertIn("error", json.loads(exc.read()))
        # server still answers after the failure
        status, _ = self._request("/info")
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
