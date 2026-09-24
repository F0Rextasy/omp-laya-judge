"""README must state exactly what the shipped artifacts say.

v0.2.0 shipped three conflicting latency numbers (intro ~1s, table ~0.35s,
results.json 1025ms) and a falsified escalation claim ("misses escalate <
0.6" while results.json held two misses at 0.80/0.84). Every number the
README publishes is therefore re-derived here from the JSON files.
"""
import json
import os
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return json.load(f)


class TestReadmeMatchesArtifacts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "README.md"), encoding="utf-8") as f:
            cls.readme = f.read()
        cls.results = load("benchmark", "results.json")
        cls.quiz = load("demo", "quiz-stats.json")
        cls.snake = load("demo", "snake-stats.json")
        cls.tetris = load("demo", "tetris-stats.json")

    def test_bench_accuracy(self):
        self.assertIn(f"{self.results['correct']}/{self.results['cases']}", self.readme)

    def test_latency_protocol_numbers(self):
        for key, fmt in (("mean_ms", "mean {mean}ms"), ("p50_ms", "p50 {p50}ms"),
                         ("p95_ms", "p95 {p95}ms")):
            expected = fmt.format(**{key.replace("_ms", ""): self.results[key]})
            self.assertIn(expected, self.readme,
                          f"README must quote {key}={self.results[key]} as '{expected}'")

    def test_quiz_score(self):
        self.assertIn(f"{self.quiz['score']}/{self.quiz['total']}", self.readme)

    def test_snake_numbers(self):
        self.assertIn(f"score {self.snake['score']}, {self.snake['steps']} moves", self.readme)
        self.assertIn(f"{self.snake['shields']} shield", self.readme)

    def test_tetris_numbers(self):
        self.assertIn(
            f"score {self.tetris['score']}, {self.tetris['lines']} lines, "
            f"{self.tetris['pieces']} pieces", self.readme)

    def test_falsified_claim_stays_gone(self):
        # The old claim contradicted results.json itself; it must not return.
        self.assertNotIn("misses escalate", self.readme.lower())


if __name__ == "__main__":
    unittest.main()
