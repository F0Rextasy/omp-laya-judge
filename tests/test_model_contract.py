"""Real-model HTTP contract: accuracy, provenance, and latency.

Skipped unless LAYA_SLOW_TESTS=1; the spawned sidecar needs both cached
checkpoints and may take one or two minutes to become ready.
"""
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(REPO, "server")
BENCH = os.path.join(REPO, "benchmark")
if SERVER not in sys.path:
    sys.path.insert(0, SERVER)

import core  # noqa: E402
from tests.support import get_json, post_json, start_real_sidecar  # noqa: E402

SLOW = os.environ.get("LAYA_SLOW_TESTS") == "1"


@unittest.skipUnless(SLOW, "set LAYA_SLOW_TESTS=1 (needs cached checkpoints)")
class TestModelContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if BENCH not in sys.path:
            sys.path.insert(0, BENCH)
        from run import CASES
        cls.cases = CASES
        cls.url, _process = start_real_sidecar()
        cls.runs = []
        for state, questions, (qid, expected) in CASES:
            raw = post_json(cls.url, {"state": state, "questions": questions})
            _converted, kinds = core.to_laya_questions(questions)
            out = core.judge_result(raw, kinds, raw["latency_ms"])
            answer = out["answers"][qid]
            if "choice" in answer:
                ok = answer["choice"] == expected
            elif "bool" in answer:
                ok = (answer["bool"] >= 0.5) == expected
            else:
                ok = int(round(answer["score"])) == expected
            cls.runs.append({"qid": qid, "ok": ok, "out": out, "expected": expected})

    def test_accuracy_floor(self):
        correct = sum(1 for run in self.runs if run["ok"])
        self.assertGreaterEqual(correct, 8, "accuracy floor 8/12, got %d" % correct)

    def test_every_answer_carries_routing_provenance(self):
        for run in self.runs:
            self.assertTrue(run["out"]["model"].startswith("laya/"),
                            "missing checkpoint provenance: %s" % run["out"]["model"])

    def test_latency_ceiling_per_judgment(self):
        for run in self.runs:
            self.assertLess(run["out"]["latency_ms"], 3000, run["qid"])

    def test_info_healthy(self):
        info = get_json(self.url)
        self.assertTrue(info["loaded"])
        self.assertIsNone(info["startup_error"])
        self.assertIn("english", info["resident"])
        self.assertIn("multilingual", info["resident"])

    def test_judge_batch_contract(self):
        items = [{"state": state, "questions": questions}
                 for state, questions, _ in self.cases[:3]]
        out = post_json(self.url, items)
        self.assertEqual(out["count"], 3)
        self.assertEqual(len(out["results"]), 3)
        for result in out["results"]:
            self.assertTrue(result["model"].startswith("laya/"))


if __name__ == "__main__":
    unittest.main()
