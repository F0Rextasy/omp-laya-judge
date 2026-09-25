"""Concurrency: parallel judgments against the sidecar's shared router.

Laya 0.3.20 made model lifecycle thread-safe upstream; this pins the plugin
side: N threads judging at once must all answer without deadlocking. Skipped
unless LAYA_SLOW_TESTS=1.
"""
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
        import core
        from tests.support import post_json, start_real_sidecar
        cls.core = core
        cls.post_json = staticmethod(post_json)
        cls.url, _sidecar = start_real_sidecar()

    def _one(self, i):
        questions = {"dept": {
            "type": "choice", "instructions": "Which department?",
            "criteria": {"billing": "invoices, payments, refunds",
                         "support": "technical help, bugs"}}}
        state = {"text": "ticket %d: charged twice, refund the duplicate" % i}
        raw = self.post_json(self.url, {"state": state, "questions": questions})
        _converted, kinds = self.core.to_laya_questions(questions)
        out = self.core.judge_result(raw, kinds, raw["latency_ms"])
        return out["answers"]["dept"]["choice"], out["model"]

    def test_parallel_judgments_all_answer(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(self._one, range(12)))
        self.assertEqual(len(results), 12)
        for choice, model in results:
            self.assertIn(choice, ("billing", "support"))
            self.assertTrue(model.startswith("laya/"))

    def test_parallel_batches(self):
        items = [{"state": {"text": "mail %d about a duplicate charge" % i},
                  "questions": {"dept": {
                      "type": "choice", "instructions": "dept?",
                      "criteria": {"billing": "money", "support": "bugs"}}}}
                 for i in range(6)]

        def run(_):
            return self.post_json(self.url, items)

        with ThreadPoolExecutor(max_workers=3) as pool:
            outputs = list(pool.map(run, range(3)))
        for out in outputs:
            self.assertEqual(out["count"], 6)
            self.assertEqual(len(out["results"]), 6)


if __name__ == "__main__":
    unittest.main()
