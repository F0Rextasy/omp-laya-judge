"""Pure mapping between oh-my-pi judge() shapes and laya predict() shapes.

No torch/laya/mcp imports — unit-testable without model dependencies.
server.py (MCP tool) and sidecar.py (HTTP) both build their requests and
answers through here so the two surfaces cannot drift apart.

Request types accepted from callers:
  choice -> laya "choice"   criteria {label: rubric}
  bool   -> laya "noul"     yes/no head, probabilities ordered [false, true]
  noul   -> laya "noul"     native alias, request passed through as-is
  score  -> laya "score"    criteria [lowest..highest]

Answer shapes (two consumers, both preserved from v0.2.0 contracts):
  judge_result()  -> MCP judge() shape:  bool {bool: P(true)} (unpacks noul)
  raw_result()    -> sidecar shape:      laya's answer dicts verbatim
"""
import json
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

SUPPORTED_TYPES = ("choice", "bool", "noul", "score")
DEFAULT_MODEL = "laya-rl-agent"

# laya's fast tokenizer is not reentrant: concurrent predict() calls raise
# "RuntimeError: Already borrowed" (reproduced with 4 threads on the bench
# model). Every router call goes through this lock so parallel MCP/HTTP
# callers queue instead of crashing; torch still parallelizes inside one
# predict (LAYA_THREADS), so throughput keeps the measured 6-thread speedup.
PREDICT_LOCK = threading.Lock()


# A semantic encoder is a guesser, and on arithmetic it guesses confidently
# wrong: both parity misses in the 12-case bench landed at confidence 0.80-0.84,
# above the 0.6 escalation gate, so the gate could not catch them. Questions
# exact arithmetic can settle never reach the model. The resolver stays
# deliberately narrow - a state carrying exactly one distinct number and a
# yes/no question about that number - because guessing is worse than asking
# laya whenever the shape is ambiguous.
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

_THRESHOLD_PREDICATES = (
    (r"(?:divisible by|multiple of)", lambda value, bound: value % bound == 0),
    (r"(?:greater than|more than|larger than|higher than|at least|above|exceeds|over)",
     lambda value, bound: value > bound),
    (r"(?:less than|smaller than|lower than|fewer than|at most|below|under)",
     lambda value, bound: value < bound),
    (r"(?:equal to|equals)", lambda value, bound: value == bound),
)


def _sole_number(state: Any) -> Optional[float]:
    """The single number a state carries, or None when it is ambiguous."""
    text = state if isinstance(state, str) else json.dumps(state, default=str)
    values = {float(match) for match in _NUMBER_RE.findall(text)}
    return values.pop() if len(values) == 1 else None


def _is_prime(value: float) -> bool:
    if value != int(value) or value < 2:
        return False
    candidate = int(value)
    divisor = 2
    while divisor * divisor <= candidate:
        if candidate % divisor == 0:
            return False
        divisor += 1
    return True


def _arithmetic_answer(instructions: str, value: float) -> Optional[bool]:
    text = instructions.strip().lower()
    for keyword, predicate in (("even", lambda n: n % 2 == 0),
                               ("odd", lambda n: n % 2 != 0),
                               ("prime", _is_prime)):
        if re.search(r"\bnot\s+%s\b" % keyword, text):
            return not predicate(value)
        if re.search(r"\b%s\b" % keyword, text):
            return predicate(value)
    for pattern, predicate in _THRESHOLD_PREDICATES:
        match = re.search(pattern + r"\s+(-?\d+(?:\.\d+)?)", text)
        if match:
            bound = float(match.group(1))
            if bound == 0:
                return None
            return predicate(value, bound)
    return None


def resolve_arithmetic(state: Any, questions: Optional[Dict[str, Any]]) -> Dict[str, bool]:
    """yes/no questions about a state's only number, answered exactly.

    Returns a sparse map of question id -> P(true) as a bool. Anything the
    resolver does not recognize is simply absent, and the caller asks laya.
    """
    resolved: Dict[str, bool] = {}
    if not questions:
        return resolved
    value = _sole_number(state)
    if value is None:
        return resolved
    for qid, qdef in questions.items():
        qdef = qdef or {}
        if qdef.get("type") not in ("bool", "noul"):
            continue
        answer = _arithmetic_answer(str(qdef.get("instructions", "")), value)
        if answer is not None:
            resolved[qid] = answer
    return resolved


def arithmetic_answer_dicts(resolved: Dict[str, bool]) -> Dict[str, Dict[str, Any]]:
    """Resolved booleans as laya noul answers, so every downstream consumer
    (judge_result, systemone_result, the decision feed) stays unchanged."""
    return {qid: {"noul": 1.0 if value else 0.0, "confidence": 1.0}
            for qid, value in resolved.items()}


def predict_locked(router: Any, state: Any, questions: Dict[str, Any],
                   model: Optional[str] = None) -> Any:
    """router.predict() under PREDICT_LOCK — safe from any number of threads."""
    with PREDICT_LOCK:
        return router.predict(state, questions, model=model)


def coerce_state(state: Any) -> Any:
    """Text or JSON document -> what laya predict() expects (str | dict | list)."""
    if isinstance(state, str):
        try:
            return json.loads(state)
        except json.JSONDecodeError:
            return state
    return state


def to_laya_questions(questions: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """judge() question shapes -> laya question shapes.

    Returns (laya_questions, kinds); kinds[qid] remembers the original type so
    answers can be unpacked back into the caller's shape. Unknown types fall
    back to "choice" (same behavior as v0.2.0).
    """
    laya_q: Dict[str, Any] = {}
    kinds: Dict[str, str] = {}
    for qid, qdef in (questions or {}).items():
        qdef = qdef or {}
        qtype = qdef.get("type", "choice")
        if qtype not in SUPPORTED_TYPES:
            qtype = "choice"
        kinds[qid] = qtype
        instructions = qdef.get("instructions", "")
        if qtype in ("bool", "noul"):
            entry: Dict[str, Any] = {"type": "noul", "instructions": instructions}
            if isinstance(qdef.get("criteria"), dict):
                entry["crit"] = qdef["criteria"]
            laya_q[qid] = entry
        elif qtype == "score":
            laya_q[qid] = {
                "type": "score",
                "instructions": instructions,
                "criteria": qdef.get("criteria", []),
            }
        else:
            laya_q[qid] = {
                "type": "choice",
                "instructions": instructions,
                "criteria": qdef.get("criteria", {}),
            }
    return laya_q, kinds


def unpack_judge_answer(res: Dict[str, Any], kind: str) -> Dict[str, Any]:
    """One laya answer -> the MCP judge() response shape for this type."""
    if kind in ("bool", "noul"):
        # laya "noul" head is [false, true]: noul == P(true).
        return {
            "bool": float(res.get("noul", 0.0)),
            "confidence": float(res.get("confidence", 0.0)),
        }
    if kind == "score":
        return {
            "score": float(res.get("score", 0.0)),
            "legend": res.get("legend", {}),
            "probabilities": res.get("probabilities", {}),
            "confidence": float(res.get("confidence", 0.0)),
        }
    return {
        "choice": res.get("choice"),
        "probabilities": res.get("probabilities", {}),
        "confidence": float(res.get("confidence", 0.0)),
    }


def _model_of(raw: Any) -> str:
    """Checkpoint provenance: laya's own `model` label is a constant
    ("laya-rl-agent"); the real pick lives in routing.model."""
    if isinstance(raw, dict):
        routing = raw.get("routing")
        if isinstance(routing, dict) and routing.get("model"):
            return f"laya/{routing['model']}"
        m = raw.get("model")
        if m and m != DEFAULT_MODEL:
            return m
    return DEFAULT_MODEL


def judge_result(raw: Any, kinds: Dict[str, str], latency_ms: int) -> Dict[str, Any]:
    """Full MCP judge() payload: answers in judge() shapes + provenance."""
    answers: Dict[str, Any] = {}
    raw_answers = raw.get("answers", {}) if isinstance(raw, dict) else {}
    for qid, res in raw_answers.items():
        answers[qid] = unpack_judge_answer(res, kinds.get(qid, "choice"))
    return {
        "answers": answers,
        "model": _model_of(raw),
        "routing": raw.get("routing") if isinstance(raw, dict) else None,
        "usage": raw.get("usage") if isinstance(raw, dict) else None,
        "latency_ms": latency_ms,
    }


def raw_result(raw: Any, latency_ms: int) -> Dict[str, Any]:
    """Sidecar payload: laya's answer dicts verbatim + provenance (v0.2.0 shape)."""
    return {
        "answers": raw.get("answers", {}) if isinstance(raw, dict) else {},
        "model": _model_of(raw),
        "routing": raw.get("routing") if isinstance(raw, dict) else None,
        "usage": raw.get("usage") if isinstance(raw, dict) else None,
        "latency_ms": latency_ms,
    }


def systemone_result(raw: Any, kinds: Dict[str, str], latency_ms: int) -> Dict[str, Any]:
    """System One (`POST /v1/systemone`) envelope for oh-my-pi's judge role.

    pi-ai's TypeSafeJudge rejects the whole response unless every answer
    carries a `type` equal to the type that was asked for, so each answer is
    rebuilt from the requested kind instead of passing laya's dicts through.
    This wire shape is what laya speaks natively, and that is what makes it a
    System One backend rather than another chat model in the chain.
    """
    raw_answers = raw.get("answers", {}) if isinstance(raw, dict) else {}
    answers: Dict[str, Any] = {}
    for qid, kind in kinds.items():
        res = raw_answers.get(qid) or {}
        if kind in ("bool", "noul"):
            answers[qid] = {"type": "noul", "noul": float(res.get("noul", 0.0))}
        elif kind == "score":
            answers[qid] = {
                "type": "score",
                "score": float(res.get("score", 0.0)),
                "probabilities": res.get("probabilities", {}),
                "confidence": float(res.get("confidence", 0.0)),
            }
        else:
            answers[qid] = {
                "type": "choice",
                "choice": res.get("choice"),
                "probabilities": res.get("probabilities", {}),
                "confidence": float(res.get("confidence", 0.0)),
            }
    usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
    return {
        "model": _model_of(raw),
        "answers": answers,
        "usage": {
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
        },
    }


def run_batch(router: Any, items: List[Dict[str, Any]], model: Optional[str]) -> List[Dict[str, Any]]:
    """Judge a list of {state, questions} items and return judge() payloads.

    Uses router.predict_batch (laya >= 0.3.20) when available — one forward
    pass for the whole batch; otherwise falls back to sequential predicts.
    Per-item latency_ms is the batch wall time split evenly across the items
    that actually reached the model, so the numbers add up to what the caller
    waited. Questions arithmetic settles never enter the batch at all.
    """
    if not items:
        return []
    pending: List[Dict[str, Any]] = []
    settled_per_item: List[Dict[str, bool]] = []
    for item in items:
        questions = item.get("questions") or {}
        settled = resolve_arithmetic(item.get("state", ""), questions)
        settled_per_item.append(settled)
        pending.append({**item,
                        "questions": {qid: qdef for qid, qdef in questions.items() if qid not in settled}})
    requests, all_kinds = batch_requests(pending, model)
    live = [req for req in requests if req["questions"]]
    raws: Dict[int, Any] = {}
    per_item = 0
    if live:
        if hasattr(router, "predict_batch"):
            t0 = time.time()
            with PREDICT_LOCK:
                batch_raws = router.predict_batch(live)
            raws = {id(req): raw for req, raw in zip(live, batch_raws)}
            per_item = round((time.time() - t0) * 1000 / len(live))
        else:
            for req in live:
                t0 = time.time()
                raws[id(req)] = predict_locked(router, req["state"], req["questions"],
                                               model=req.get("model"))
                per_item = max(per_item, round((time.time() - t0) * 1000))
    results: List[Dict[str, Any]] = []
    for req, kinds, settled in zip(requests, all_kinds, settled_per_item):
        raw = raws.get(id(req)) or {"answers": {}}
        if settled and isinstance(raw, dict):
            raw.setdefault("answers", {}).update(arithmetic_answer_dicts(settled))
        results.append(judge_result(raw, kinds, per_item))
    return results


def batch_requests(states_questions: List[Dict[str, Any]], model: Optional[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """[{state, questions}, ...] -> (predict_batch requests, per-item kinds).

    Predicts nothing; server decides predict_batch vs sequential fallback.
    """
    requests: List[Dict[str, Any]] = []
    all_kinds: List[Dict[str, str]] = []
    for item in states_questions:
        laya_q, kinds = to_laya_questions(item.get("questions") or {})
        requests.append({"state": coerce_state(item.get("state", "")), "questions": laya_q, "model": model})
        all_kinds.append(kinds)
    return requests, all_kinds


def normalize_batch(payload: Any) -> List[Dict[str, Any]]:
    """A judge_batch payload -> the item list `run_batch` expects.

    Accepts a list of {state, questions}, a single such object, or
    {states: [...], questions: {...}} meaning "these questions against each
    of these states" - a shape callers reach for naturally and which used to
    be swallowed as one item with an empty state, answering every question
    against nothing while reporting success. An empty state is now an error
    rather than a confident wrong answer.
    """
    items = payload
    if isinstance(items, (str, bytes)):
        items = json.loads(items)
    if isinstance(items, dict):
        states = items.get("states")
        if isinstance(states, list):
            shared = items.get("questions") or {}
            if not shared:
                raise ValueError("states given without questions")
            items = [{"state": state, "questions": shared} for state in states]
        else:
            items = [items]
    if not isinstance(items, list) or not items:
        raise ValueError("batch queries must be a non-empty list")
    normalized: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("every batch item must be an object")
        questions = item.get("questions")
        if isinstance(questions, (str, bytes)):
            questions = json.loads(questions)
        if not str(item.get("state", "")).strip():
            raise ValueError("every batch item needs a non-empty state")
        if not questions:
            raise ValueError("every batch item needs questions")
        normalized.append({**item, "questions": questions})
    return normalized
