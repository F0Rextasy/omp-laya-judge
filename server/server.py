"""laya-judge MCP server: local System-1 judgments for oh-my-pi.

Exposes laya's Router.predict as an MCP tool with oh-my-pi judge() shapes.
The checkpoint loads lazily on first call so server startup stays instant.
Requires: pip install laya mcp "transformers>=4.48,<5" torch
"""
import json
import os
import time

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("laya-judge")
_router = None


def get_router():
    global _router
    if _router is None:
        from laya import Router
        import torch

        # Single-threaded torch: OpenMP runtimes can deadlock when a model
        # loads inside FastMCP's worker thread on Windows. One thread is still
        # ~200ms/judgment on CPU.
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
        # Pin one checkpoint: bare preload() would fetch every bundled model.
        _router = Router(models={"english": ("convaiinnovations/laya", None)}, device="cpu", preload=True)
    return _router


# Eager load in the main thread at startup: torch must initialize here, not in
# FastMCP's worker thread pool.
try:
    get_router()
except Exception as exc:  # model download needs network; stay up, fail per-call
    _router = None
    _startup_error = str(exc)
else:
    _startup_error = None

def _coerce_state(state: str) -> object:
    if isinstance(state, str):
        try:
            return json.loads(state)
        except json.JSONDecodeError:
            return state
    return state


@mcp.tool()
def judge(state: str, questions: str) -> str:
    """Judge typed questions over a state with the local Laya model.

    state: text or JSON document (dict/list or JSON string).
    questions: JSON object mapping id -> {type, instructions, criteria?}.
      type "choice": criteria {label: rubric} -> {choice, probabilities, confidence}
      type "bool": instructions phrased as a yes/no statement
        -> {bool: P(true)}  (laya "noul" head is [false, true])
      type "score": criteria [lowest..highest]
        -> {score: expected level index, legend, probabilities, confidence}
    Returns JSON: {answers: {id: answer}, model, latency_ms}.
    No LLM call is made; nothing leaves the machine.
    """
    t0 = time.time()
    router = get_router()
    q = json.loads(questions) if isinstance(questions, str) else questions
    laya_q: dict = {}
    kinds: dict = {}
    for qid, qdef in q.items():
        qtype = qdef.get("type", "choice")
        kinds[qid] = qtype
        if qtype == "bool":
            entry: dict = {"type": "noul", "instructions": qdef.get("instructions", "")}
            if isinstance(qdef.get("criteria"), dict):
                entry["crit"] = qdef["criteria"]
            laya_q[qid] = entry
        elif qtype == "score":
            laya_q[qid] = {
                "type": "score",
                "instructions": qdef.get("instructions", ""),
                "criteria": qdef.get("criteria", []),
            }
        else:
            laya_q[qid] = {
                "type": "choice",
                "instructions": qdef.get("instructions", ""),
                "criteria": qdef.get("criteria", {}),
            }
    raw = router.predict(
        _coerce_state(state),
        laya_q,
        # None = let laya route per request (Turkish/non-Latin goes multilingual);
        # set LAYA_MODEL=english to pin the English checkpoint.
        model=os.environ.get("LAYA_MODEL") or None,
    )
    answers: dict = {}
    for qid, res in (raw.get("answers", {}).items() if isinstance(raw, dict) else []):
        kind = kinds.get(qid, "choice")
        if kind == "bool":
            answers[qid] = {"bool": float(res.get("noul", 0.0)), "confidence": float(res.get("confidence", 0.0))}
        elif kind == "score":
            answers[qid] = {
                "score": float(res.get("score", 0.0)),
                "legend": res.get("legend", {}),
                "probabilities": res.get("probabilities", {}),
                "confidence": float(res.get("confidence", 0.0)),
            }
        else:
            answers[qid] = {
                "choice": res.get("choice"),
                "probabilities": res.get("probabilities", {}),
                "confidence": float(res.get("confidence", 0.0)),
            }
    return json.dumps({
        "answers": answers,
        "model": raw.get("model", "laya-rl-agent") if isinstance(raw, dict) else "laya-rl-agent",
        "routing": raw.get("routing") if isinstance(raw, dict) else None,
        "usage": raw.get("usage") if isinstance(raw, dict) else None,
        "latency_ms": round((time.time() - t0) * 1000),
    })


@mcp.tool()
def judge_info() -> str:
    """Show the loaded Laya checkpoint, device, and supported question types."""
    return json.dumps({
        "model": f"laya/{os.environ.get('LAYA_MODEL', 'english')}",
        "device": "cpu",
        "types": ["choice", "bool", "score"],
        "loaded": _router is not None,
    })


if __name__ == "__main__":
    mcp.run()
