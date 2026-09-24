"""Benchmark: laya-judge accuracy + latency on known-answer questions.

Run: python benchmark/run.py  (writes benchmark/results.json)
"""
import json
import math
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

CASES = [
    ({"text": "You were charged twice for the same invoice."},
     {"dept": {"type": "choice", "instructions": "Which department?",
               "criteria": {"billing": "invoices, payments, refunds",
                            "support": "technical help, bug reports"}}},
     ("dept", "billing")),
    ({"text": "The app crashes on startup with a null pointer."},
     {"dept": {"type": "choice", "instructions": "Which department?",
               "criteria": {"billing": "invoices, payments, refunds",
                            "support": "technical help, bug reports"}}},
     ("dept", "support")),
    ({"value": 4}, {"even": {"type": "bool", "instructions": "Is the value even?"}},
     ("even", True)),
    ({"value": 7}, {"even": {"type": "bool", "instructions": "Is the value even?"}},
     ("even", False)),
    ({"text": "The login page takes 12 seconds to load on mobile."},
     {"sev": {"type": "score", "instructions": "How severe?",
              "criteria": ["trivial", "minor", "major", "critical"]}},
     ("sev", 2)),
    ({"text": "Typo in the footer copyright year."},
     {"sev": {"type": "score", "instructions": "How severe?",
              "criteria": ["trivial", "minor", "major", "critical"]}},
     ("sev", 0)),
    ({"subject": "Refund my duplicate payment", "body": "Please return the extra charge."},
     {"spam": {"type": "bool", "instructions": "Is this message spam?"}},
     ("spam", False)),
    ({"subject": "WIN $$$ FREE MONEY click now", "body": "Claim your prize today."},
     {"spam": {"type": "bool", "instructions": "Is this message spam?"}},
     ("spam", True)),
    ({"text": "How do I reset my password?"},
     {"dept": {"type": "choice", "instructions": "Which department?",
               "criteria": {"billing": "invoices, payments, refunds",
                            "support": "technical help, bug reports"}}},
     ("dept", "support")),
    ({"text": "Data loss: the export deleted all customer records."},
     {"sev": {"type": "score", "instructions": "How severe?",
              "criteria": ["trivial", "minor", "major", "critical"]}},
     ("sev", 3)),
    ({"value": 0}, {"even": {"type": "bool", "instructions": "Is the value even?"}},
     ("even", True)),
    ({"text": "The button color looks slightly off on dark mode."},
     {"sev": {"type": "score", "instructions": "How severe?",
              "criteria": ["trivial", "minor", "major", "critical"]}},
     ("sev", 1)),
]


def main() -> None:
    t_start = time.time()
    import server as srv
    import_s = round(time.time() - t_start, 1)

    # warmup (loads checkpoint, excluded from timing)
    t0 = time.time()
    srv.judge(json.dumps({"text": "warmup"}), json.dumps(
        {"q": {"type": "choice", "instructions": "Pick one.",
               "criteria": {"a": "first", "b": "second"}}}))
    warmup_s = round(time.time() - t0, 1)

    rows = []
    correct = 0
    for state, questions, (qid, expected) in CASES:
        t0 = time.time()
        out = json.loads(srv.judge(json.dumps(state), json.dumps(questions)))
        ms = round((time.time() - t0) * 1000)
        ans = out["answers"][qid]
        if "choice" in ans:
            got, ok = ans["choice"], ans["choice"] == expected
        elif "bool" in ans:
            got = ans["bool"] >= 0.5
            ok = got == expected
        else:
            got = int(round(ans["score"]))
            ok = got == expected
        correct += ok
        rows.append({"qid": qid, "expected": expected, "got": ans, "ok": ok, "ms": ms})
        mark = "✓" if ok else "✗"
        print(f"{mark} {qid:6s} expected={expected!r:10} ms={ms}")

    total_ms = sum(r["ms"] for r in rows)
    ms_values = [r["ms"] for r in rows]
    p50 = round(statistics.median(ms_values))
    p95 = round(sorted(ms_values)[min(len(ms_values) - 1, math.ceil(0.95 * len(ms_values)) - 1)])
    import laya
    import torch
    result = {
        "schema": 2,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "protocol": {
            "note": "warmup excluded; wall time per judge() call; CPU only",
            "threads": torch.get_num_threads(),
            "laya": getattr(laya, "__version__", "unknown"),
            "torch": getattr(torch, "__version__", "unknown"),
        },
        "import_s": import_s,
        "warmup_s": warmup_s,
        "cases": len(rows),
        "correct": correct,
        "accuracy": round(correct / len(rows), 3),
        "total_ms": total_ms,
        "mean_ms": round(total_ms / len(rows)),
        "p50_ms": p50,
        "p95_ms": p95,
        "min_ms": min(r["ms"] for r in rows),
        "max_ms": max(r["ms"] for r in rows),
        "llm_tokens_burned": 0,
        "rows": rows,
    }
    out_path = os.path.join(os.path.dirname(__file__), "results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    print(f"\n{correct}/{len(rows)} correct, mean {result['mean_ms']}ms "
          f"(p50 {p50} / p95 {p95}) per judgment, 0 LLM tokens. Results -> {out_path}")


if __name__ == "__main__":
    main()
