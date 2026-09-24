"""calibration.json must equal a fresh recomputation from results.json, and
the README must publish the gate numbers verbatim."""
import json
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(REPO, "benchmark")
if BENCH not in sys.path:
    sys.path.insert(0, BENCH)

import calibrate  # noqa: E402


def load(name):
    with open(os.path.join(BENCH, name), encoding="utf-8") as f:
        return json.load(f)


class TestCalibration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "README.md"), encoding="utf-8") as f:
            cls.readme = f.read()
        cls.results = load("results.json")
        cls.published = load("calibration.json")

    def test_calibration_is_reproducible_from_results(self):
        self.assertEqual(calibrate.compute(self.results["rows"]), self.published)

    def test_escaped_misses_are_above_the_gate_by_definition(self):
        for item in self.published["escaped"]:
            self.assertGreaterEqual(item["confidence"], calibrate.GATE)
        self.assertEqual(self.published["false_accept"], len(self.published["escaped"]))
        self.assertEqual(self.published["misses_escaped"], self.published["false_accept"])
        self.assertEqual(
            self.published["misses_caught_by_gate"] + self.published["misses_escaped"],
            self.published["misses_total"])

    def test_readme_quotes_gate_numbers(self):
        expected = [
            f"auto-accept {self.published['auto_accept']}/{self.published['cases']}",
            f"false-accept {self.published['false_accept_rate']:.0%}",
        ]
        for phrase in expected:
            self.assertIn(phrase, self.readme,
                          f"README must publish gate metric '{phrase}'")


if __name__ == "__main__":
    unittest.main()
