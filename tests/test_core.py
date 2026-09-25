import json
import os
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

SERVER_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server")
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

import core  # noqa: E402


class FakeRouter:
    """Sequential-only fake: answers every question with a canned shape.

    Deliberately has NO predict_batch attribute, so run_batch must fall
    back to sequential predicts (the hasattr branch).
    """

    def __init__(self):
        self.calls = []

    def predict(self, state, questions, model=None):
        self.calls.append({"state": state, "questions": questions, "model": model})
        answers = {}
        for qid, q in questions.items():
            if q["type"] == "noul":
                answers[qid] = {"noul": 0.75, "confidence": 0.7}
            elif q["type"] == "score":
                answers[qid] = {"score": 2.0, "legend": {"2": "major"},
                                "probabilities": {"2": 0.6}, "confidence": 0.6}
            else:
                answers[qid] = {"choice": "billing", "probabilities": {"billing": 0.9},
                                "confidence": 0.9}
        return {"answers": answers, "model": "laya-rl-agent",
                "routing": {"model": "english"},
                "usage": {"input_tokens": 7, "output_tokens": 0}}


class FakeBatchRouter(FakeRouter):
    """Adds the predict_batch method laya >= 0.3.20 exposes."""

    def __init__(self):
        super().__init__()
        self.batch_calls = 0

    def predict_batch(self, requests):
        self.batch_calls += 1
        return [self.predict(r["state"], r["questions"], r.get("model"))
                for r in requests]


class TestCoerceState(unittest.TestCase):
    def test_plain_text_passes_through(self):
        self.assertEqual(core.coerce_state("hello world"), "hello world")

    def test_json_string_becomes_document(self):
        self.assertEqual(core.coerce_state('{"a": 1}'), {"a": 1})
        self.assertEqual(core.coerce_state("[1, 2]"), [1, 2])

    def test_invalid_json_stays_text(self):
        self.assertEqual(core.coerce_state("not { json"), "not { json")

    def test_dict_passes_through(self):
        doc = {"a": 1}
        self.assertIs(core.coerce_state(doc), doc)


class TestToLayaQuestions(unittest.TestCase):
    def test_choice_maps_and_drops_unknown_keys(self):
        laya_q, kinds = core.to_laya_questions({
            "dept": {"type": "choice", "instructions": "which?", "criteria": {"a": "x"}, "extra": 1},
        })
        self.assertEqual(kinds["dept"], "choice")
        self.assertEqual(laya_q["dept"], {"type": "choice", "instructions": "which?",
                                          "criteria": {"a": "x"}})

    def test_bool_becomes_noul_with_crit_passthrough(self):
        laya_q, kinds = core.to_laya_questions({
            "even": {"type": "bool", "instructions": "even?", "criteria": {"hint": "parity"}},
        })
        self.assertEqual(kinds["even"], "bool")
        self.assertEqual(laya_q["even"]["type"], "noul")
        self.assertEqual(laya_q["even"]["crit"], {"hint": "parity"})

    def test_native_noul_kept(self):
        laya_q, kinds = core.to_laya_questions({"q": {"type": "noul", "instructions": "ok?"}})
        self.assertEqual(kinds["q"], "noul")
        self.assertEqual(laya_q["q"]["type"], "noul")

    def test_score_maps_verbatim(self):
        laya_q, kinds = core.to_laya_questions({
            "sev": {"type": "score", "instructions": "sev?", "criteria": ["a", "b"]},
        })
        self.assertEqual(kinds["sev"], "score")
        self.assertEqual(laya_q["sev"], {"type": "score", "instructions": "sev?",
                                         "criteria": ["a", "b"]})

    def test_unknown_type_falls_back_to_choice(self):
        laya_q, kinds = core.to_laya_questions({"q": {"type": "weird", "instructions": "?"}})
        self.assertEqual(kinds["q"], "choice")
        self.assertEqual(laya_q["q"]["type"], "choice")

    def test_missing_type_defaults_to_choice(self):
        laya_q, kinds = core.to_laya_questions({"q": {"instructions": "?"}})
        self.assertEqual(kinds["q"], "choice")

    def test_empty_and_none(self):
        self.assertEqual(core.to_laya_questions(None), ({}, {}))
        self.assertEqual(core.to_laya_questions({}), ({}, {}))


class TestUnpack(unittest.TestCase):
    def test_bool_reads_noul_as_p_true(self):
        out = core.unpack_judge_answer({"noul": 0.8, "confidence": 0.55}, "bool")
        self.assertEqual(out, {"bool": 0.8, "confidence": 0.55})

    def test_native_noul_alias(self):
        out = core.unpack_judge_answer({"noul": 0.8, "confidence": 0.55}, "noul")
        self.assertEqual(out["bool"], 0.8)

    def test_score_shape(self):
        out = core.unpack_judge_answer(
            {"score": 2.0, "legend": {"2": "m"}, "probabilities": {"2": 0.6},
             "confidence": 0.6}, "score")
        self.assertEqual(out["score"], 2.0)
        self.assertEqual(out["legend"], {"2": "m"})

    def test_choice_shape(self):
        out = core.unpack_judge_answer(
            {"choice": "billing", "probabilities": {"billing": 1.0},
             "confidence": 0.9}, "choice")
        self.assertEqual(out["choice"], "billing")

    def test_missing_confidence_defaults_zero(self):
        out = core.unpack_judge_answer({"choice": "x"}, "choice")
        self.assertEqual(out["confidence"], 0.0)


class TestResults(unittest.TestCase):
    def test_judge_result_uses_routing_for_provenance(self):
        raw = FakeRouter().predict("s", {"q": {"type": "choice", "instructions": "?"}})
        out = core.judge_result(raw, {"q": "choice"}, 5)
        self.assertEqual(out["model"], "laya/english")  # routing.model, not the constant
        self.assertEqual(out["latency_ms"], 5)
        self.assertEqual(out["usage"]["input_tokens"], 7)
        self.assertEqual(out["answers"]["q"]["choice"], "billing")

    def test_judge_result_falls_back_to_default_model(self):
        out = core.judge_result({"answers": {}}, {}, 1)
        self.assertEqual(out["model"], core.DEFAULT_MODEL)

    def test_raw_result_keeps_verbatim_answers(self):
        raw = FakeRouter().predict("s", {"q": {"type": "noul", "instructions": "?"}})
        out = core.raw_result(raw, 9)
        self.assertEqual(out["answers"]["q"]["noul"], 0.75)  # laya shape, verbatim
        self.assertEqual(out["model"], "laya/english")
        self.assertEqual(out["latency_ms"], 9)


class TestRunBatch(unittest.TestCase):
    ITEMS = [
        {"state": {"text": "a"}, "questions": {"q": {"type": "choice", "instructions": "?"}}},
        {"state": {"text": "b"}, "questions": {"q": {"type": "bool", "instructions": "?"}}},
        {"state": {"text": "c"}, "questions": {"q": {"type": "score", "instructions": "?",
                                                     "criteria": ["l", "h"]}}},
    ]

    def test_batch_uses_predict_batch(self):
        rt = FakeBatchRouter()
        results = core.run_batch(rt, self.ITEMS, None)
        self.assertEqual(len(results), 3)
        self.assertEqual(rt.batch_calls, 1)  # one batched forward pass
        self.assertEqual(len(rt.calls), 3)
        self.assertEqual(results[0]["answers"]["q"]["choice"], "billing")
        self.assertEqual(results[1]["answers"]["q"]["bool"], 0.75)
        self.assertEqual(results[2]["answers"]["q"]["score"], 2.0)
        # per-item latency is the batch wall time split evenly
        self.assertTrue(all(isinstance(r["latency_ms"], int) for r in results))

    def test_sequential_fallback_without_predict_batch(self):
        rt = FakeRouter()  # no predict_batch attribute at all
        self.assertFalse(hasattr(rt, "predict_batch"))
        results = core.run_batch(rt, self.ITEMS, "english")
        self.assertEqual(len(results), 3)
        self.assertEqual(len(rt.calls), 3)  # three predicts, model pin forwarded
        self.assertTrue(all(c["model"] == "english" for c in rt.calls))

    def test_empty_items(self):
        for router in (FakeRouter(), FakeBatchRouter()):
            self.assertEqual(core.run_batch(router, [], None), [])


class TestNormalizeBatch(unittest.TestCase):
    QUESTIONS = {"q": {"type": "bool", "instructions": "is it billing?"}}

    def test_list_and_single_object(self):
        items = core.normalize_batch([{"state": {"text": "a"}, "questions": self.QUESTIONS}])
        self.assertEqual(len(items), 1)
        self.assertEqual(len(core.normalize_batch({"state": {"text": "a"}, "questions": self.QUESTIONS})), 1)

    def test_states_shape_expands(self):
        # A live probe hit this: the {states, questions} object was swallowed
        # as one item with an empty state, so every question was answered
        # against nothing and the call still reported success.
        items = core.normalize_batch({"states": [{"text": "a"}, {"text": "b"}], "questions": self.QUESTIONS})
        self.assertEqual([item["state"] for item in items], [{"text": "a"}, {"text": "b"}])
        self.assertTrue(all(item["questions"] == self.QUESTIONS for item in items))

    def test_json_string_payload_and_nested_questions(self):
        items = core.normalize_batch(json.dumps([{"state": {"text": "a"}, "questions": json.dumps(self.QUESTIONS)}]))
        self.assertEqual(items[0]["questions"], self.QUESTIONS)

    def test_empty_state_is_an_error_not_a_confident_answer(self):
        for payload in ([{"state": "", "questions": self.QUESTIONS}],
                        [{"questions": self.QUESTIONS}],
                        {"states": [{"text": "a"}]}):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    core.normalize_batch(payload)

    def test_empty_batch_is_an_error(self):
        with self.assertRaises(ValueError):
            core.normalize_batch([])


class TestPredictLock(unittest.TestCase):
    def test_parallel_predicts_serialize(self):
        """Regression: laya's tokenizer raises RuntimeError: Already borrowed
        when two predicts run at once — every router call must hold the lock."""
        OverlapProbe.peak = OverlapProbe.active = 0
        rt = OverlapProbe()
        q = {"q": {"type": "choice", "instructions": "?"}}
        with ThreadPoolExecutor(max_workers=6) as pool:
            outs = list(pool.map(
                lambda i: core.predict_locked(rt, {"text": str(i)}, q, None),
                range(12)))
        self.assertEqual(len(outs), 12)
        self.assertEqual(OverlapProbe.peak, 1, "predicts overlapped")


class OverlapProbe(FakeRouter):
    active = 0
    peak = 0

    def predict(self, state, questions, model=None):
        cls = type(self)
        cls.active += 1
        cls.peak = max(cls.peak, cls.active)
        time.sleep(0.02)  # widen the race window if the lock disappears
        try:
            return super().predict(state, questions, model)
        finally:
            cls.active -= 1


if __name__ == "__main__":
    unittest.main()
