"""Concurrency: parallel judgments against the shared router.

laya 0.3.20 made model lifecycle thread-safe upstream (#100); this pins the
plugin side: N threads judging at once must all answer, none deadlocking
the FastMCP/worker-thread path. Skipped unless LAYA_SLOW_TESTS=1.
"""
import json
import os
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(REPO, "server")

SLOW = os.environ.get("LAYA_SLOW_TESTS") == "1"


@unittest.skipUnless(SLOW, "set LAYA_SLOW_TESTS=1 (needs cached checkpoints)")
class TestConcurrency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if SERVER not in sys.path:
            sys.path.insert(0, SERVER)
        import server
        cls.server = server

    def _one(self, i):
        out = json.loads(self.server.judge(
            json.dumps({"text": f"ticket {i}: charged twice, refund the duplicate"}),
            json.dumps({"dept": {"type": "choice", "instructions": "Which department?",
                                 "criteria": {"billing": "invoices, payments, refunds",
                                              "support": "technical help, bugs"}}})))
        return out["answers"]["dept"]["choice"], out["model"]

    def test_parallel_judgments_all_answer(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(self._one, range(12)))
        self.assertEqual(len(results), 12)
        for choice, model in results:
            self.assertIn(choice, ("billing", "support"))
            self.assertTrue(model.startswith("laya/"))

    def test_parallel_batches(self):
        items = [{"state": {"text": f"mail {i} about a duplicate charge"},
                  "questions": {"dept": {"type": "choice", "instructions": "dept?",
                                         "criteria": {"billing": "money",
                                                      "support": "bugs"}}}}
                 for i in range(6)]

        def run(_):
            return json.loads(self.server.judge_batch(json.dumps(items)))

        with ThreadPoolExecutor(max_workers=3) as pool:
            outputs = list(pool.map(run, range(3)))
        for out in outputs:
            self.assertEqual(out["count"], 6)
            self.assertEqual(len(out["results"]), 6)


if __name__ == "__main__":
    unittest.main()
