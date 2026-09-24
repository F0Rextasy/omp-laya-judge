"""Model-level contract: accuracy floor, provenance, latency ceiling.

Skipped unless LAYA_SLOW_TESTS=1 — needs both checkpoints cached and
takes ~1-2 minutes. CI runs the fast suite only.
"""
import json
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(REPO, "server")
BENCH = os.path.join(REPO, "benchmark")

SLOW = os.environ.get("LAYA_SLOW_TESTS") == "1"


@unittest.skipUnless(SLOW, "set LAYA_SLOW_TESTS=1 (needs cached checkpoints)")
class TestModelContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for path in (SERVER, BENCH):
            if path not in sys.path:
                sys.path.insert(0, path)
        os.environ.pop("LAYA_MODEL", None)  # exercise auto-routing
        import server
        from run import CASES
        cls.server = server
        cls.cases = CASES
        cls.runs = []
        for state, questions, (qid, expected) in CASES:
            out = json.loads(server.judge(json.dumps(state), json.dumps(questions)))
            ans = out["answers"][qid]
            if "choice" in ans:
                ok = ans["choice"] == expected
            elif "bool" in ans:
                ok = (ans["bool"] >= 0.5) == expected
            else:
                ok = int(round(ans["score"])) == expected
            cls.runs.append({"qid": qid, "ok": ok, "out": out, "expected": expected})

    def test_accuracy_floor(self):
        correct = sum(1 for r in self.runs if r["ok"])
        self.assertGreaterEqual(correct, 8, f"accuracy floor 8/12, got {correct}")

    def test_every_answer_carries_routing_provenance(self):
        for r in self.runs:
            self.assertTrue(r["out"]["model"].startswith("laya/"),
                            f"missing checkpoint provenance: {r['out']['model']}")

    def test_latency_ceiling_per_judgment(self):
        # The v0.2.0 results file shipped a 5546ms outlier mid-run.
        for r in self.runs:
            self.assertLess(r["out"]["latency_ms"], 3000, r["qid"])

    def test_judge_info_healthy(self):
        info = json.loads(self.server.judge_info())
        self.assertTrue(info["loaded"])
        self.assertIsNone(info["startup_error"])
        self.assertIn("english", info["resident"])
        self.assertIn("multilingual", info["resident"])

    def test_judge_batch_contract(self):
        items = [{"state": s, "questions": q} for s, q, _ in self.cases[:3]]
        out = json.loads(self.server.judge_batch(json.dumps(items)))
        self.assertEqual(out["count"], 3)
        self.assertEqual(len(out["results"]), 3)
        for res in out["results"]:
            self.assertTrue(res["model"].startswith("laya/"))


if __name__ == "__main__":
    unittest.main()
