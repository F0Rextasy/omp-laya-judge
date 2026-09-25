"""MCP bridge contract: a real sidecar, a real stdio handshake, every tool.

The model process starts first on an isolated port. The bridge then receives
that URL and must initialize without loading any model itself. Skipped unless
LAYA_SLOW_TESTS=1.
"""
import ast
import collections
import json
import os
import subprocess
import sys
import threading
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(REPO, "server")
BRIDGE = os.path.join(SERVER, "bridge.py")


SLOW = os.environ.get("LAYA_SLOW_TESTS") == "1"


class TestBridgeSourceContract(unittest.TestCase):
    def test_bridge_has_no_model_imports(self):
        with open(BRIDGE, encoding="utf-8") as stream:
            tree = ast.parse(stream.read(), filename=BRIDGE)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertFalse(imported & {"laya", "model", "server", "torch"})

    def test_old_model_server_is_gone(self):
        self.assertFalse(os.path.exists(os.path.join(SERVER, "server.py")))


@unittest.skipUnless(SLOW, "set LAYA_SLOW_TESTS=1 (needs cached checkpoints)")
class TestStdioContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        from tests.support import probe_loopback, start_real_sidecar
        if not probe_loopback(ThreadingHTTPServer):
            raise unittest.SkipTest(
                "no working loopback on this machine: asyncio socketpair cannot connect")
        cls.url, _sidecar = start_real_sidecar()
        cls.stderr_tail = collections.deque(maxlen=30)
        cls.queue = __import__("queue").Queue()
        env = {key: value for key, value in os.environ.items() if key != "LAYA_MODEL"}
        env["PYTHONUNBUFFERED"] = "1"
        env["LAYA_SIDECAR_URL"] = cls.url
        started = time.monotonic()
        cls.proc = subprocess.Popen(
            [sys.executable, "bridge.py"], cwd=SERVER,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", env=env)

        def drain_out():
            for line in cls.proc.stdout:
                line = line.strip()
                if line:
                    try:
                        cls.queue.put(json.loads(line))
                    except json.JSONDecodeError:
                        pass

        def drain_err():
            for line in cls.proc.stderr:
                cls.stderr_tail.append(line.rstrip())

        threading.Thread(target=drain_out, daemon=True).start()
        threading.Thread(target=drain_err, daemon=True).start()
        cls._next_id = 0
        cls.hello = cls._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "laya-judge-tests", "version": "1"},
        }, timeout=10)
        cls.handshake_seconds = time.monotonic() - started
        cls._notify("notifications/initialized")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "proc", None) is not None:
            try:
                cls.proc.stdin.close()
                cls.proc.wait(timeout=10)
            except Exception:
                cls.proc.kill()

    @classmethod
    def _send(cls, payload):
        cls.proc.stdin.write(json.dumps(payload) + "\n")
        cls.proc.stdin.flush()

    @classmethod
    def _notify(cls, method):
        cls._send({"jsonrpc": "2.0", "method": method})

    @classmethod
    def _request(cls, method, params=None, timeout=60):
        cls._next_id += 1
        request_id = cls._next_id
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        cls._send(message)
        import queue as queue_module
        while True:
            try:
                item = cls.queue.get(timeout=timeout)
            except queue_module.Empty:
                raise AssertionError(
                    "no response to %s in %ss; stderr tail: %s" % (
                        method, timeout, list(cls.stderr_tail)))
            if item.get("id") == request_id:
                if "error" in item:
                    raise AssertionError("%s -> error %s" % (method, item["error"]))
                return item["result"]

    @classmethod
    def _call_tool(cls, name, arguments, timeout=120):
        result = cls._request("tools/call", {"name": name, "arguments": arguments}, timeout)
        if result.get("isError"):
            raise AssertionError("tool %s failed: %s" % (name, result))
        return json.loads(result["content"][0]["text"])

    def test_a_initialize_handshake_is_instant(self):
        self.assertIn("serverInfo", self.hello)
        self.assertIn("tools", self.hello.get("capabilities", {}))
        self.assertLess(self.handshake_seconds, 10)

    def test_b_tools_listed(self):
        result = self._request("tools/list")
        self.assertEqual({tool["name"] for tool in result["tools"]},
                         {"judge", "judge_batch", "judge_info"})

    def test_c_judge_info_reports_healthy_startup(self):
        info = self._call_tool("judge_info", {})
        self.assertTrue(info["loaded"])
        self.assertIsNone(info["startup_error"])
        self.assertIn("english", info["resident"])

    def test_d_judge_smoke_and_mcp_shapes(self):
        out = self._call_tool("judge", {
            "state": json.dumps({"text": "charged twice, refund the duplicate"}),
            "questions": json.dumps({
                "dept": {"type": "choice", "instructions": "Which department?",
                         "criteria": {"billing": "invoices, payments, refunds",
                                      "support": "technical help, bugs"}},
                "urgent": {"type": "bool", "instructions": "Is this urgent?"},
                "severity": {"type": "score", "instructions": "How severe?",
                             "criteria": ["low", "medium", "high"]},
            }),
        })
        self.assertEqual(out["answers"]["dept"]["choice"], "billing")
        self.assertIn("confidence", out["answers"]["dept"])
        self.assertIn("bool", out["answers"]["urgent"])
        self.assertIn("confidence", out["answers"]["urgent"])
        self.assertIn("score", out["answers"]["severity"])
        self.assertTrue(out["model"].startswith("laya/"))

    def test_e_judge_batch_smoke(self):
        out = self._call_tool("judge_batch", {"queries": json.dumps([
            {"state": {"text": "password reset page"},
             "questions": {"dept": {"type": "choice", "instructions": "dept?",
                                    "criteria": {"billing": "money", "support": "bugs"}}}},
        ])})
        self.assertEqual(out["count"], 1)
        self.assertIn("answers", out["results"][0])

    def test_f_judge_batch_nested_json_strings(self):
        out = self._call_tool("judge_batch", {"queries": json.dumps([
            {"state": json.dumps({"text": "charged twice, refund the duplicate"}),
             "questions": json.dumps({"dept": {
                 "type": "choice", "instructions": "Which department?",
                 "criteria": {"billing": "invoices, payments, refunds",
                              "support": "technical help, bugs"}}})},
        ])})
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["results"][0]["answers"]["dept"]["choice"], "billing")


if __name__ == "__main__":
    unittest.main()
