"""Arithmetic the model gets wrong, answered exactly.

The bench measured the failure this guards: parity questions came back at
confidence 0.80-0.84 - above the 0.6 escalation gate - with the wrong answer.
The resolver therefore only claims a question when the state carries exactly
one distinct number and the instruction names an operation on it. Everything
else must fall through to laya untouched.
"""
import os
import sys
import unittest

SERVER_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server")
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

import core  # noqa: E402


def ask(state, instructions, qtype="bool"):
    return core.resolve_arithmetic(state, {instructions: {"type": qtype, "instructions": instructions}})


class TestArithmeticResolver(unittest.TestCase):
    def test_parity_is_exact(self):
        # 4 and 0 are even, 7 is not - the two cases laya missed.
        for value, expected in ((4, True), (0, True), (7, False), ({"value": 4}, True)):
            with self.subTest(value=value):
                resolved = core.resolve_arithmetic(
                    {"value": value}, {"even": {"type": "bool", "instructions": "Is the value even?"}})
                self.assertEqual(resolved, {"even": expected})

    def test_threshold_relations(self):
        cases = [
            ({"value": 12}, "is the value greater than 10?", True),
            ({"value": 3}, "is the value greater than 10?", False),
            ({"value": 3}, "is the value less than 10?", True),
            ({"value": 10}, "is the value equal to 10?", True),
            ({"value": 9}, "is the value divisible by 3?", True),
            ({"value": 8}, "is the value divisible by 3?", False),
        ]
        for state, instruction, expected in cases:
            with self.subTest(instruction=instruction, state=state):
                self.assertEqual(ask(state, instruction), {instruction: expected})

    def test_primality(self):
        self.assertEqual(ask({"value": 7}, "is the value prime?"), {"is the value prime?": True})
        self.assertEqual(ask({"value": 8}, "is the value prime?"), {"is the value prime?": False})
        self.assertEqual(ask({"value": 1}, "is the value prime?"), {"is the value prime?": False})

    def test_negation_inverts(self):
        self.assertEqual(ask({"value": 7}, "is the value not even?"), {"is the value not even?": True})
        self.assertEqual(ask({"value": 4}, "is the value not even?"), {"is the value not even?": False})

    def test_ambiguous_states_are_left_to_laya(self):
        # Two numbers: which one is "the value"? Guessing would be worse.
        self.assertEqual(core.resolve_arithmetic(
            {"value": 4, "limit": 10}, {"even": {"type": "bool", "instructions": "Is the value even?"}}), {})
        self.assertEqual(core.resolve_arithmetic(
            {"value": None}, {"even": {"type": "bool", "instructions": "Is the value even?"}}), {})

    def test_non_arithmetic_questions_stay_with_laya(self):
        for instruction in ("is this a billing issue?", "is the text in english?",
                            "how severe is this?", "should I retry?"):
            with self.subTest(instruction=instruction):
                self.assertEqual(ask({"value": 4, "text": "charged twice"}, instruction), {})

    def test_only_yes_no_questions_are_claimed(self):
        self.assertEqual(core.resolve_arithmetic(
            {"value": 4}, {"pick": {"type": "choice", "instructions": "is the value even?",
                                    "criteria": {"yes": "even", "no": "odd"}}}), {})

    def test_zero_bound_is_not_divided_by(self):
        self.assertEqual(ask({"value": 4}, "is the value divisible by 0?"), {})

    def test_answer_dicts_are_laya_shaped(self):
        self.assertEqual(core.arithmetic_answer_dicts({"even": True, "prime": False}),
                         {"even": {"noul": 1.0, "confidence": 1.0},
                          "prime": {"noul": 0.0, "confidence": 1.0}})


if __name__ == "__main__":
    unittest.main()
