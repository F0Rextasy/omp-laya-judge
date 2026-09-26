"""Run one case set against any System-One backend and report accuracy + speed.

Every backend is exercised through the same wire contract - `POST
/v1/systemone` with {state, questions} - so a hosted judge, a locally served
model, and a checkpoint swap are compared on identical input, with identical
scoring. Adapters only describe *how to reach* a backend; the scoring here is
the single definition of correct.

Usage:
    python benchmark/compare_backends.py --backend laya
    python benchmark/compare_backends.py --backend http://127.0.0.1:3777
    python benchmark/compare_backends.py --backend name=url --backend name=url

Writes benchmark/backends.json.
"""
import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from cases import BY_GROUP, CASES  # noqa: E402

TIMEOUT_S = 30.0
WARMUP = 2


def call_endpoint(url: str, state: Any, questions: Dict[str, Any]) -> Dict[str, Any]:
    """POST /v1/systemone. Returns the decoded answers map, or raises."""
    body = json.dumps({"state": state, "questions": questions}).encode()
    request = urllib.request.Request(
        url.rstrip("/") + "/v1/systemone", data=body,
        headers={"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return json.loads(response.read())


def grade(expected: Any, answer: Dict[str, Any]) -> Tuple[bool, Any]:
    """One answer against one expectation. The only definition of correct.

    choice -> the label must match exactly.
    bool   -> P(true) at 0.5, matching the gate's own threshold.
    score  -> the rubric bucket with the most mass. Rounding a regression
              instead would let a model that is 0.01 from a boundary pass or
              fail on noise, which is exactly what the old bench measured.
    """
    kind = answer.get("type")
    if kind == "noul" or "bool" in answer:
        got = float(answer.get("noul", answer.get("bool", 0.0))) >= 0.5
        return got == bool(expected), got
    if kind == "score":
        probs = answer.get("probabilities") or {}
        if probs:
            got = max(probs, key=lambda key: probs[key])
            return int(got) == int(expected), int(got)
        score = float(answer.get("score", 0.0))
        return int(round(score)) == int(expected), score
    got = answer.get("choice")
    return got == expected, got


def in_process() -> Dict[str, Any]:
    """Adapter for a local laya: no HTTP, the same predict path in-process."""
    import core
    import sidecar

    def call(state: Any, questions: Dict[str, Any]) -> Dict[str, Any]:
        laya_questions, kinds = core.to_laya_questions(questions)
        raw = core.predict_locked(sidecar.get_router(), core.coerce_state(state), laya_questions)
        return core.systemone_result(raw, kinds, raw.get("latency_ms", 0))["answers"]
    return call


def run_backend(name: str, call) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for index in range(WARMUP):
        call(CASES[0]["state"], CASES[0]["questions"])
    for case in CASES:
        started = time.perf_counter()
        try:
            answers = call(case["state"], case["questions"])
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            rows.append({"id": case["id"], "group": case["group"], "error": str(exc)[:120]})
            continue
        wall = (time.perf_counter() - started) * 1000
        checks = {qid: grade(expected, answers.get(qid, {})) for qid, expected in case["expected"].items()}
        rows.append({
            "id": case["id"], "group": case["group"], "ms": round(wall, 1),
            "ok": all(ok for ok, _ in checks.values()),
            "detail": {qid: {"expected": exp, "got": got, "ok": ok} for qid, (ok, got) in checks.items()},
            "note": case["note"],
        })
    return {"backend": name, "rows": rows}


def summarize(result: Dict[str, Any]) -> Dict[str, Any]:
    rows = result["rows"]
    scored = [row for row in rows if "error" not in row]
    latencies = [row["ms"] for row in scored]
    groups = {}
    for group, cases in BY_GROUP.items():
        group_rows = [row for row in scored if row["group"] == group]
        groups[group] = {
            "cases": len(group_rows),
            "correct": sum(1 for row in group_rows if row["ok"]),
            "errors": sum(1 for row in rows if row.get("error") and row["group"] == group),
        }
    ordered = sorted(latencies)
    return {
        "backend": result["backend"],
        "cases": len(scored),
        "correct": sum(1 for row in scored if row["ok"]),
        "errors": len(rows) - len(scored),
        "groups": groups,
        "latency_ms": {
            "median": round(statistics.median(ordered)) if ordered else None,
            "p95": round(ordered[max(0, int(len(ordered) * 0.95) - 1)]) if ordered else None,
            "mean": round(statistics.mean(ordered)) if ordered else None,
            "max": round(max(ordered)) if ordered else None,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", action="append", default=[],
                        help="name=url, or bare url, or 'laya' for in-process")
    parser.add_argument("--out", default=os.path.join(HERE, "backends.json"))
    args = parser.parse_args()

    backends = args.backend or ["laya"]
    results = []
    for spec in backends:
        if spec == "laya":
            name, call = "laya (in-process)", in_process()
        elif "=" in spec:
            name, url = spec.split("=", 1)
            call = (lambda bound: lambda state, questions: call_endpoint(bound, state, questions))(url)
        else:
            name, call = spec, (lambda bound: lambda state, questions: call_endpoint(bound, state, questions))(spec)
        print(f"== {name}", flush=True)
        result = run_backend(name, call)
        results.append(result)
        summary = summarize(result)
        print(f"   dogru {summary['correct']}/{summary['cases']}  "
              f"hata {summary['errors']}  medyan {summary['latency_ms']['median']}ms  "
              f"p95 {summary['latency_ms']['p95']}ms", flush=True)
        for group, stats in summary["groups"].items():
            print(f"     {group:9s} {stats['correct']}/{stats['cases']}", flush=True)

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=1)
    print("wrote", args.out)


if __name__ == "__main__":
    main()
