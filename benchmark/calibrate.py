"""Compute the 0.6 escalation-gate metrics from results.json -> calibration.json.

The gate is the plugin's core policy: answers with confidence >= 0.6 are used
directly, anything below escalates to the LLM judge. This script publishes what
that policy actually bought on the 12-case bench — auto-accept share, false
accepts (misses the gate let through), and caught misses. README quotes these
numbers verbatim; tests/test_calibration.py re-derives them from results.json.

Run: python benchmark/calibrate.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = 0.6


def compute(rows, gate=GATE):
    """Gate metrics for bench rows (each: {qid, expected, got, ok, ms})."""
    auto = [r for r in rows if float(r["got"].get("confidence", 0.0)) >= gate]
    esc = [r for r in rows if float(r["got"].get("confidence", 0.0)) < gate]
    false_accept = [r for r in auto if not r["ok"]]
    caught = [r for r in esc if not r["ok"]]
    return {
        "gate": gate,
        "source": "benchmark/results.json",
        "cases": len(rows),
        "correct": sum(1 for r in rows if r["ok"]),
        "auto_accept": len(auto),
        "escalated": len(esc),
        "auto_correct": len(auto) - len(false_accept),
        "false_accept": len(false_accept),
        "false_accept_rate": round(len(false_accept) / len(auto), 3) if auto else 0.0,
        "misses_total": sum(1 for r in rows if not r["ok"]),
        "misses_caught_by_gate": len(caught),
        "misses_escaped": len(false_accept),
        "escaped": [
            {"qid": r["qid"], "expected": r["expected"],
             "confidence": r["got"].get("confidence")}
            for r in false_accept
        ],
    }


def main():
    with open(os.path.join(HERE, "results.json"), encoding="utf-8") as f:
        res = json.load(f)
    metrics = compute(res["rows"])
    out_path = os.path.join(HERE, "calibration.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=1)
    print(f"gate {metrics['gate']}: auto-accept {metrics['auto_accept']}/{metrics['cases']}, "
          f"escalated {metrics['escalated']}, false-accept {metrics['false_accept_rate']:.0%} "
          f"({metrics['false_accept']}/{metrics['auto_accept']}) -> {out_path}")


if __name__ == "__main__":
    main()
