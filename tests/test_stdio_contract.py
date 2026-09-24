"""MCP stdio contract: spawn the real server, shake hands, call every tool.

Exercises the exact path oh-my-pi uses (FastMCP worker threads + eager
main-thread checkpoint load — the Windows/OpenMP pairing v0.2.0 deadlocked
on). Skipped unless LAYA_SLOW_TESTS=1.
"""
import collections
import json
import os
import subprocess
import sys
import threading
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(REPO, "server")

SLOW = os.environ.get("LAYA_SLOW_TESTS") == "1"


@unittest.skipUnless(SLOW, "set LAYA_SLOW_TESTS=1 (needs cached checkpoints)")
class TestStdioContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # FastMCP runs on asyncio, and ProactorEventLoop's socketpair() is a
        # loopback connect — no working loopback means the server can never
        # answer here (fails on this machine's AV/WFP policy, passes on CI).
        from http.server import ThreadingHTTPServer
        from tests.support import probe_loopback
        if not probe_loopback(ThreadingHTTPServer):
            raise unittest.SkipTest(
                "no working loopback on this machine: asyncio socketpair "
                "cannot connect, FastMCP never answers")
        cls.proc = None
        cls.stderr_tail = collections.deque(maxlen=30)
        cls.queue = __import__("queue").Queue()
        env = {k: v for k, v in os.environ.items() if k != "LAYA_MODEL"}
        env["PYTHONUNBUFFERED"] = "1"
        cls.proc = subprocess.Popen(
            [sys.executable, "server.py"], cwd=SERVER,
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
        }, timeout=120)
        cls._notify("notifications/initialized")

    @classmethod
    def tearDownClass(cls):
        if cls.proc is not None:
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
        rid = cls._next_id
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        cls._send(msg)
        import queue as queue_mod
        deadline_errors = []
        while True:
            try:
                item = cls.queue.get(timeout=timeout)
            except queue_mod.Empty:
                raise AssertionError(
                    f"no response to {method} in {timeout}s; stderr tail: "
                    f"{list(cls.stderr_tail)}")
            if item.get("id") == rid:
                self_check = item
                if "error" in self_check:
                    raise AssertionError(f"{method} -> error {self_check['error']}")
                return self_check["result"]
            deadline_errors.append(item)

    @classmethod
    def _call_tool(cls, name, arguments, timeout=120):
        result = cls._request("tools/call", {"name": name, "arguments": arguments},
                              timeout=timeout)
        if result.get("isError"):
            raise AssertionError(f"tool {name} failed: {result}")
        text = result["content"][0]["text"]
        try:
            return json.loads(text)
        except (KeyError, json.JSONDecodeError, IndexError):
            return text

    def test_a_initialize_handshake(self):
        self.assertIn("serverInfo", self.hello)
        self.assertIn("tools", self.hello.get("capabilities", {}))

    def test_b_tools_listed(self):
        result = self._request("tools/list")
        names = {t["name"] for t in result["tools"]}
        self.assertEqual(names, {"judge", "judge_batch", "judge_info"})

    def test_c_judge_info_reports_healthy_startup(self):
        info = self._call_tool("judge_info", {})
        self.assertTrue(info["loaded"])
        self.assertIsNone(info["startup_error"])
        self.assertIn("english", info["resident"])

    def test_d_judge_smoke(self):
        out = self._call_tool("judge", {
            "state": json.dumps({"text": "charged twice, refund the duplicate"}),
            "questions": json.dumps({"dept": {
                "type": "choice", "instructions": "Which department?",
                "criteria": {"billing": "invoices, payments, refunds",
                             "support": "technical help, bugs"}}}),
        })
        self.assertEqual(out["answers"]["dept"]["choice"], "billing")
        self.assertTrue(out["model"].startswith("laya/"))

    def test_e_judge_batch_smoke(self):
        out = self._call_tool("judge_batch", {
            "queries": json.dumps([
                {"state": {"text": "password reset page"},
                 "questions": {"dept": {"type": "choice", "instructions": "dept?",
                                        "criteria": {"billing": "money",
                                                     "support": "bugs"}}}},
            ]),
        })
        self.assertEqual(out["count"], 1)
        self.assertIn("answers", out["results"][0])

    def test_f_judge_batch_nested_json_strings(self):
        # MCP clients mirror judge()'s inputs: state and questions as JSON strings.
        # Fails on 'str' object has no attribute 'items' without judge_batch's
        # nested normalization (caught by smoke after v0.3.0).
        out = self._call_tool("judge_batch", {
            "queries": json.dumps([
                {"state": json.dumps({"text": "charged twice, refund the duplicate"}),
                 "questions": json.dumps({"dept": {
                     "type": "choice", "instructions": "Which department?",
                     "criteria": {"billing": "invoices, payments, refunds",
                                  "support": "technical help, bugs"}}})},
            ]),
        })
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["results"][0]["answers"]["dept"]["choice"], "billing")


if __name__ == "__main__":
    unittest.main()
