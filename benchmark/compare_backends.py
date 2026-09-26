"""Run one case set against any System-One backend and report accuracy, speed and calibration.

Every backend is exercised through the same wire contract - `POST
/v1/systemone` with {state, questions} - so a hosted judge, a locally served
model, and a checkpoint swap are compared on identical input with identical
scoring. Adapters only describe *how to reach* a backend; the grading below is
the single definition of correct, and it is the same for all of them.

Usage:
    python benchmark/compare_backends.py --backend laya
    python benchmark/compare_backends.py --backend laya:multilingual
    python benchmark/compare_backends.py --backend von=http://127.0.0.1:8000
    python benchmark/compare_backends.py --backend jev=https://api.typesafe.ai

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
from typing import Any, Callable, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from cases import BY_GROUP, CASES  # noqa: E402

TIMEOUT_S = 30.0
WARMUP = 2
Call = Callable[[Any, Dict[str, Any]], Dict[str, Any]]


def call_endpoint(url: str, state: Any, questions: Dict[str, Any]) -> Dict[str, Any]:
    """POST /v1/systemone and return its `answers` map.

    Every System One backend wraps the answers in an envelope with `model` and
    `usage`; unwrapping here keeps the graders on one shape, and a wrong-shape
    response scores as an error instead of quietly becoming a null.

    The MCP-level name for the yes/no head is `bool`; the System One wire name
    is `noul`. Our sidecar renames it for a caller, so a direct-to-backend
    adapter has to as well - a translation on the wire, never a change to how
    an answer is graded.
    """
    wire = {
        qid: ({"type": "noul", **{k: v for k, v in (qdef or {}).items() if k != "type"}}
              if (qdef or {}).get("type") == "bool" else qdef)
        for qid, qdef in questions.items()
    }
    body = json.dumps({"state": state, "questions": wire}).encode()
    request = urllib.request.Request(
        url.rstrip("/") + "/v1/systemone", data=body,
        headers={"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        payload = json.loads(response.read())
    answers = payload.get("answers") if isinstance(payload, dict) else None
    if not isinstance(answers, dict):
        raise ValueError(f"no answers map in response: {str(payload)[:120]}")
    return answers


def in_process(model: Optional[str] = None) -> Call:
    """Adapter for a local laya: no HTTP, the same predict path in-process.

    `model` pins a checkpoint, which is how a language variant or a per-head
    temperature choice gets measured on the same cases.
    """
    import core
    import sidecar

    def call(state: Any, questions: Dict[str, Any]) -> Dict[str, Any]:
        laya_questions, kinds = core.to_laya_questions(questions)
        raw = core.predict_locked(sidecar.get_router(), core.coerce_state(state),
                                 laya_questions, model=model)
        return core.systemone_result(raw, kinds, raw.get("latency_ms", 0))["answers"]
    return call


def grade(expected: Any, answer: Dict[str, Any]) -> Tuple[bool, Any, Optional[float]]:
    """One answer against one expectation. The only definition of correct.

    choice -> the label must match exactly.
    bool   -> P(true) at 0.5, matching the gate's own threshold.
    score  -> the rubric bucket carrying the most mass. Rounding a regression
              instead would let a model 0.01 from a boundary pass or fail on
              noise, which is what the old 12-case bench actually measured.

    The third value is the stated probability, kept for the calibration
    measurement; only yes/no questions have one.
    """
    kind = answer.get("type")
    if kind == "noul" or "bool" in answer:
        probability = float(answer.get("noul", answer.get("bool", 0.0)))
        got = probability >= 0.5
        return got == bool(expected), got, probability
    if kind == "score":
        probs = answer.get("probabilities") or {}
        if probs:
            got = max(probs, key=lambda key: probs[key])
            return int(got) == int(expected), int(got), None
        score = float(answer.get("score", 0.0))
        return int(round(score)) == int(expected), score, None
    got = answer.get("choice")
    return got == expected, got, None


def calibration(rows: List[Dict[str, Any]], bins: int = 5) -> Dict[str, Any]:
    """Expected calibration error over the yes/no questions.

    A decision layer that gates on confidence is only as trustworthy as that
    number: a backend that says 0.9 and is right 60% of the time escalates the
    wrong work. ECE is |mean stated probability - observed hit rate| per
    probability bin, weighted by bin population: 0.0 is perfect, larger is
    worse, and a value above 0.4 means the stated number is close to noise.
    """
    points: List[Tuple[float, bool]] = []
    for row in rows:
        for check in (row.get("detail") or {}).values():
            probability = check.get("probability")
            if isinstance(check.get("got"), bool) and isinstance(probability, (int, float)):
                points.append((float(probability), bool(check["ok"])))
    if not points:
        return {"points": 0, "ece": None, "mean_stated": None, "accuracy": None}
    buckets: Dict[int, List[Tuple[float, bool]]] = {}
    for stated, hit in points:
        buckets.setdefault(min(bins - 1, int(stated * bins)), []).append((stated, hit))
    total = len(points)
    ece = 0.0
    for group in buckets.values():
        mean_stated = sum(stated for stated, _ in group) / len(group)
        hit_rate = sum(1 for _, hit in group if hit) / len(group)
        ece += len(group) / total * abs(mean_stated - hit_rate)
    return {
        "points": total,
        "ece": round(ece, 3),
        "mean_stated": round(sum(stated for stated, _ in points) / total, 3),
        "accuracy": round(sum(1 for _, hit in points if hit) / total, 3),
    }


def run_backend(name: str, call: Call) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for _ in range(WARMUP):
        call(CASES[0]["state"], CASES[0]["questions"])
    for case in CASES:
        started = time.perf_counter()
        try:
            answers = call(case["state"], case["questions"])
        except Exception as exc:  # noqa: BLE001 - a failed backend is a data point
            rows.append({"id": case["id"], "group": case["group"], "error": f"{type(exc).__name__}: {exc}"[:160]})
            continue
        wall = (time.perf_counter() - started) * 1000
        detail: Dict[str, Any] = {}
        ok = True
        for qid, want in case["expected"].items():
            hit, got, probability = grade(want, answers.get(qid, {}))
            detail[qid] = {"expected": want, "got": got, "ok": hit, "probability": probability}
            ok = ok and hit
        rows.append({
            "id": case["id"], "group": case["group"], "ms": round(wall, 1),
            "ok": ok, "detail": detail, "note": case["note"],
        })
    return {"backend": name, "rows": rows}


def summarize(result: Dict[str, Any]) -> Dict[str, Any]:
    rows = result["rows"]
    scored = [row for row in rows if "error" not in row]
    latencies = [row["ms"] for row in scored]
    groups: Dict[str, Any] = {}
    for group in BY_GROUP:
        group_rows = [row for row in rows if row["group"] == group]
        group_scored = [row for row in group_rows if "error" not in row]
        groups[group] = {
            "cases": len(group_rows),
            "correct": sum(1 for row in group_scored if row["ok"]),
            "errors": sum(1 for row in group_rows if "error" in row),
        }
    ordered = sorted(latencies)
    return {
        "backend": result["backend"],
        "cases": len(rows),
        "correct": sum(1 for row in scored if row["ok"]),
        "errors": len(rows) - len(scored),
        "groups": groups,
        "calibration": calibration(rows),
        "latency_ms": {
            "median": round(statistics.median(ordered)) if ordered else None,
            "p95": round(ordered[max(0, int(len(ordered) * 0.95) - 1)]) if ordered else None,
            "mean": round(statistics.mean(ordered)) if ordered else None,
            "max": round(max(ordered)) if ordered else None,
        },
    }


def build(spec: str) -> Tuple[str, Call]:
    if spec == "laya" or spec.startswith("laya:"):
        _, _, model = spec.partition(":")
        label = f"laya/{model}" if model else "laya/auto"
        return label, in_process(model or None)
    name, url = spec.split("=", 1) if "=" in spec else (spec, spec)
    return name, (lambda bound: lambda state, questions: call_endpoint(bound, state, questions))(url)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", action="append", default=[],
                        help="laya[:checkpoint], name=url, or a bare url")
    parser.add_argument("--out", default=os.path.join(HERE, "backends.json"))
    args = parser.parse_args()

    results = []
    for spec in args.backend or ["laya"]:
        name, call = build(spec)
        print(f"== {name}", flush=True)
        result = run_backend(name, call)
        results.append(result)
        summary = summarize(result)
        latency = summary["latency_ms"]
        print(f"   dogru {summary['correct']}/{summary['cases']}  hata {summary['errors']}  "
              f"medyan {latency['median']}ms  p95 {latency['p95']}ms", flush=True)
        for group, stats in summary["groups"].items():
            print(f"     {group:9s} {stats['correct']}/{stats['cases']}"
                  + (f"  ({stats['errors']} hata)" if stats["errors"] else ""), flush=True)
        cal = summary["calibration"]
        if cal["points"]:
            print(f"     ECE {cal['ece']}  (beyan {cal['mean_stated']} / gercek {cal['accuracy']}, n={cal['points']})", flush=True)

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=1)
    print("wrote", args.out)


if __name__ == "__main__":
    main()
