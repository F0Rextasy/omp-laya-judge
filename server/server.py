"""laya-judge MCP server: local System-1 judgments for oh-my-pi.

Exposes laya's Router.predict as MCP tools with oh-my-pi judge() shapes:
  judge(state, questions)   one judgment  (choice / bool / noul / score)
  judge_batch(queries)      N judgments in one predict_batch call
  judge_info()              checkpoint / device / status probe

Requires: pip install -r requirements.txt
(laya, mcp<2, torch, transformers>=4.48,<5)
"""
import json
import os
import time

# Cache-first: a local judge must answer without the hub (the "nothing leaves
# the machine" claim includes this revision round-trip). Fresh install without
# a cache: run once with HF_HUB_OFFLINE=0 to fetch checkpoints.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import core
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("laya-judge")
_router = None
_startup_error = None


def get_router():
    """Build the router once, in the main thread, with every needed checkpoint resident.

    - torch/OpenMP must initialize in this thread: v0.2.0 deadlocked when a
      checkpoint first loaded inside FastMCP's worker thread on Windows. This
      function therefore runs at import time (eager), never per call.
    - Threads: measured on the 12-case bench (CPU, 6 cores): 1 thread = 534ms
      mean, 6 threads = 200ms mean per judgment (2.7x). LAYA_THREADS overrides;
      set it to the physical core count on hyperthreaded CPUs.
    - The registry is pruned to the preload list (default english+multilingual)
      so the router can never lazily fetch a checkpoint inside a worker thread.
      LAYA_MODEL pins an extra checkpoint and preloads it here as well.
    """
    global _router, _startup_error
    if _router is None:
        from laya import Router
        import torch

        threads = int(os.environ.get("LAYA_THREADS") or os.cpu_count() or 1)
        torch.set_num_threads(threads)
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass  # settable once per process; tuning is best-effort, not a precondition
        names = [n.strip() for n in os.environ.get("LAYA_MODELS", "english,multilingual").split(",") if n.strip()]
        pinned = (os.environ.get("LAYA_MODEL") or "").strip()
        if pinned and pinned not in names:
            names.append(pinned)
        router = Router(device="cpu")
        for name in [m for m in list(router.models) if m not in names]:
            del router.models[name]
        router.preload(names)
        _router = router
        _startup_error = None
    return _router


def take_router():
    """Router for this call; retries a failed eager startup (e.g. network back)."""
    global _startup_error
    router = get_router()
    _startup_error = None
    return router


# Eager load in the main thread at startup: torch must initialize here, not in
# FastMCP's worker thread pool.
try:
    get_router()
except Exception as exc:  # model download needs network; stay up, fail per-call
    _router = None
    _startup_error = str(exc)


@mcp.tool()
def judge(state: str, questions: str) -> str:
    """Judge typed questions over a state with the local Laya model.

    state: text or JSON document (dict/list or JSON string).
    questions: JSON object mapping id -> {type, instructions, criteria?}.
      type "choice": criteria {label: rubric} -> {choice, probabilities, confidence}
      type "bool": instructions phrased as a yes/no statement
        -> {bool: P(true)}  (laya "noul" head is [false, true])
      type "noul": native alias for "bool"
      type "score": criteria [lowest..highest]
        -> {score: expected level index, legend, probabilities, confidence}
    Returns JSON: {answers: {id: answer}, model, routing, usage, latency_ms}.
    No LLM call is made; nothing leaves the machine.
    """
    t0 = time.time()
    router = take_router()
    q = json.loads(questions) if isinstance(questions, str) else questions
    laya_q, kinds = core.to_laya_questions(q)
    # model=None lets laya route per request (Turkish/non-Latin goes
    # multilingual); set LAYA_MODEL=english to pin the English checkpoint.
    raw = core.predict_locked(
        router, core.coerce_state(state), laya_q,
        model=os.environ.get("LAYA_MODEL") or None,
    )
    return json.dumps(core.judge_result(raw, kinds, round((time.time() - t0) * 1000)))


@mcp.tool()
def judge_batch(queries: str) -> str:
    """Judge many states in one call: pre-filter a candidate set locally.

    queries: JSON list of {state, questions} entries (a single object is
    accepted and wrapped). Returns {results: [judge() payload, ...], count,
    latency_ms}. Per-item results carry split batch latency; batching wins on
    GPU, on CPU it is a modest gain — measure before relying on it.
    """
    t0 = time.time()
    router = take_router()
    items = json.loads(queries) if isinstance(queries, str) else queries
    if isinstance(items, dict):
        items = [items]
    for item in items:
        # Nested params arrive as JSON strings from MCP clients, same as judge()'s.
        if isinstance(item.get("questions"), str):
            item["questions"] = json.loads(item["questions"])
    results = core.run_batch(router, items, os.environ.get("LAYA_MODEL") or None)
    return json.dumps({
        "results": results,
        "count": len(results),
        "latency_ms": round((time.time() - t0) * 1000),
    })


@mcp.tool()
def judge_info() -> str:
    """Show loaded checkpoint(s), device, supported question types, startup state."""
    resident = []
    if _router is not None:
        try:
            resident = sorted(getattr(_router, "_agents", {}) or {})
        except Exception:
            resident = []
    pinned = os.environ.get("LAYA_MODEL")
    return json.dumps({
        "model": f"laya/{pinned}" if pinned else "laya/auto",
        "device": "cpu",
        "types": ["choice", "bool", "noul", "score"],
        "loaded": _router is not None,
        "resident": resident,
        "startup_error": _startup_error,
    })


if __name__ == "__main__":
    mcp.run()
