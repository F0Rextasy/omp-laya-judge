"""Single-model laya HTTP sidecar for oh-my-pi and the stdio bridge.

The sidecar binds before importing torch/laya, serves status while the model
loads in the background, and owns the only router in this plugin.

  python server/sidecar.py [--port 3777]

POST /judge {"state": ..., "questions": {...}}
  -> {"answers": {...}, "model": ..., "routing": ..., "usage": ..., "latency_ms": ...}
POST /judge [{"state": ..., "questions": {...}}, ...]
  -> {"results": [judge payloads], "count": n, "latency_ms": ...}
GET /info -> model, device, types, loaded, resident, and startup_error
"""
import errno
import json
import os
import sys
import threading
import time
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections import deque
import upstream

# Cache-first: a local judge must answer without the hub. A fresh install can
# set HF_HUB_OFFLINE=0 for its first download.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402  (pure mapping, no heavy deps)

DEFAULT_PORT = 3777
LOAD_TIMEOUT_SECONDS = 590.0
# Measured: reloading the checkpoints costs ~20s, which lands entirely on the
# next turn's first judgment. A 10-minute window made that stall routine, so
# the default holds the model warm through a reading/thinking pause and still
# exits eventually so an abandoned sidecar never leaks the RAM. Override with
# LAYA_SIDECAR_IDLE (seconds; 0 disables the idle exit entirely).
DEFAULT_IDLE_SECONDS = 3600.0

router = None
startup_error = None
_load_complete = threading.Event()

# Every System One decision is logged so the UI can show what laya actually
# chose. The harness has no judgment event, so this ring buffer plus
# `GET /v1/decisions?since=N` is how a hook surfaces the picks in the chat.
_DECISIONS = deque(maxlen=256)
_decision_lock = threading.Lock()

# laya allocates a fixed budget per question, so one forward pass is bounded.
# Measured on CPU: 8 questions answer in <=810ms against the harness's 10s
# judgment budget, while 58 in a single pass overran it. Oversized System One
# requests are therefore chunked at MAX_QUESTIONS and merged; MAX_TOTAL caps
# the work so a pathological request cannot monopolize the model lock.
MAX_QUESTIONS = int(os.environ.get("LAYA_MAX_QUESTIONS", "8"))
MAX_TOTAL_QUESTIONS = int(os.environ.get("LAYA_MAX_TOTAL_QUESTIONS", "256"))


class RequestTooLarge(Exception):
    """More questions than one call will ever answer."""
_load_lock = threading.Lock()
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get_router():
    """Build the one router with every needed checkpoint resident."""
    global router, startup_error
    if router is not None:
        return router
    with _load_lock:
        if router is not None:
            return router
        from laya import Router
        import torch

        threads = int(os.environ.get("LAYA_THREADS") or os.cpu_count() or 1)
        torch.set_num_threads(threads)
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass
        names = [name.strip() for name in os.environ.get(
            "LAYA_MODELS", "english,multilingual").split(",") if name.strip()]
        pinned = (os.environ.get("LAYA_MODEL") or "").strip()
        if pinned and pinned not in names:
            names.append(pinned)
        candidate = Router(device="cpu")
        for name in [name for name in list(candidate.models) if name not in names]:
            del candidate.models[name]
        candidate.preload(names)
        router = candidate
        startup_error = None
        return router


def _resident():
    if router is None:
        return []
    try:
        return sorted(getattr(router, "_agents", {}) or {})
    except Exception:
        return []


def _info():
    backend = upstream.describe()
    if not backend["local"]:
        # An upstream answers instead of laya, so the banner must not claim
        # "laya active" and the cards must not claim "0 tokens".
        return {
            "model": f"upstream/{upstream.upstream_url()}",
            "backend": "systemone",
            "url": backend["url"],
            "key_present": backend["key_present"],
            "device": "remote",
            "types": list(core.SUPPORTED_TYPES),
            "loaded": True,
            "resident": [],
            "startup_error": None,
        }
    pinned = os.environ.get("LAYA_MODEL")
    return {
        "model": f"laya/{pinned}" if pinned else "laya/auto",
        "backend": "laya",
        "device": "cpu",
        "types": list(core.SUPPORTED_TYPES),
        "loaded": router is not None,
        "resident": _resident(),
        "startup_error": startup_error,
    }


def _load_model():
    global startup_error
    try:
        get_router()
    except Exception as exc:
        startup_error = str(exc)
        print(f"laya-judge model load failed: {startup_error}", file=sys.stderr, flush=True)
    finally:
        _load_complete.set()


def _wait_for_router():
    if router is not None:
        _load_complete.set()
        return router
    if not _load_complete.wait(LOAD_TIMEOUT_SECONDS):
        raise RuntimeError(startup_error or f"model load timed out after {LOAD_TIMEOUT_SECONDS:g} seconds")
    if router is None:
        raise RuntimeError(startup_error or "model unavailable")
    return router


def _split_arithmetic(state, questions):
    """(questions laya must answer, booleans arithmetic already settled)."""
    questions = questions or {}
    resolved = core.resolve_arithmetic(state, questions)
    if not resolved:
        return questions, resolved
    return {qid: qdef for qid, qdef in questions.items() if qid not in resolved}, resolved


def _merge_arithmetic(raw, resolved):
    """Fold exact answers into a raw laya payload the mappers already own."""
    if not resolved:
        return raw
    if not isinstance(raw, dict):
        raw = {"answers": {}}
    raw.setdefault("answers", {}).update(core.arithmetic_answer_dicts(resolved))
    return raw


def _empty_payload():
    """Provenance for a request no model call was needed for."""
    resident = _resident() or ["auto"]
    return {"answers": {}, "routing": {"model": resident[0]}}


def predict(state, questions):
    t0 = time.time()
    if len(questions or {}) > MAX_QUESTIONS:
        raise RequestTooLarge(
            "laya serves at most %d questions per call (got %d)"
            % (MAX_QUESTIONS, len(questions or {}))
        )
    rest, resolved = _split_arithmetic(state, questions)
    if upstream.configured():
        # Arithmetic stays local on every backend: it is exact everywhere and
        # free everywhere, so there is nothing to outsource.
        payload = upstream.judge(state, rest) if rest else {"answers": {}}
        merged = dict(payload.get("answers") or {})
        merged.update(core.arithmetic_answer_dicts(resolved))
        print("judge: upstream %d question(s)%s" % (
            len(rest), ", %d answered by arithmetic" % len(resolved) if resolved else ""), flush=True)
        return {"answers": merged, "model": payload.get("model") or "upstream",
                "routing": {"model": "upstream"}, "usage": payload.get("usage"),
                "latency_ms": round((time.time() - t0) * 1000)}
    active_router = _wait_for_router()
    laya_questions, _kinds = core.to_laya_questions(rest)
    raw = core.predict_locked(
        active_router, core.coerce_state(state), laya_questions,
        model=os.environ.get("LAYA_MODEL") or None,
    ) if laya_questions else _empty_payload()
    print("judge: %d question(s) %s%s" % (
        len(laya_questions), [question.get("type") for question in laya_questions.values()],
        ", %d answered by arithmetic" % len(resolved) if resolved else ""), flush=True)
    return core.raw_result(_merge_arithmetic(raw, resolved), round((time.time() - t0) * 1000))


def predict_batch(items):
    t0 = time.time()
    active_router = _wait_for_router()
    results = core.run_batch(active_router, items, os.environ.get("LAYA_MODEL") or None)
    print("judge-batch: %d state(s)" % len(results), flush=True)
    return {"results": results, "count": len(results),
            "latency_ms": round((time.time() - t0) * 1000)}


def _record_decision(payload, latency_ms):
    """Keep a display-shaped record of one System One answer: the pick plus
    the distribution it was picked from, so the UI can draw what the model
    chose *among* instead of just naming the winner."""
    picks = {}
    for qid, answer in (payload.get("answers") or {}).items():
        kind = answer.get("type")
        if kind == "noul":
            picks[qid] = {"pick": "%.2f" % answer.get("noul", 0.0), "p": float(answer.get("noul", 0.0))}
        elif kind == "choice":
            probs = {str(k): float(v) for k, v in (answer.get("probabilities") or {}).items()}
            picks[qid] = {"pick": str(answer.get("choice")), "probs": probs,
                          "conf": float(answer.get("confidence", 0.0))}
        else:
            picks[qid] = {"pick": "%.1f" % answer.get("score", 0.0), "p": float(answer.get("confidence", 0.0))}
    with _decision_lock:
        _DECISIONS.append({"picks": picks, "model": payload.get("model"), "ms": latency_ms, "at": time.time()})


def decisions_since(cursor):
    """`GET /v1/decisions?since=N` - ring-buffer slice plus the next cursor."""
    with _decision_lock:
        entries = list(_DECISIONS)
    start = max(0, min(int(cursor or 0), len(entries)))
    return {"cursor": start, "next": len(entries), "decisions": entries[start:]}



def systemone(state, questions):
    """`POST /v1/systemone` - the wire oh-my-pi's judge role speaks.

    Same model, same heads; only the envelope differs from `/judge`. This is
    what lets the harness route its own judgments to laya as a native System
    One backend instead of calling it as a tool.

    The harness's own decision points batch far wider than one forward pass
    can take - the `find` cascade asked for 58 relevance questions in a single
    call. Oversized requests are answered in chunks and merged rather than
    refused: the caller sent one question set and expects one answer set, and
    a 422 here would fail the whole judgment instead of degrading to the next
    candidate (a ChainJudge timeout throws rather than falling through).
    """
    t0 = time.time()
    active_router = _wait_for_router()
    questions = questions or {}
    if len(questions) > MAX_QUESTIONS:
        if len(questions) > MAX_TOTAL_QUESTIONS:
            raise RequestTooLarge(
                "laya serves at most %d questions per call (got %d)"
                % (MAX_TOTAL_QUESTIONS, len(questions))
            )
        items = list(questions.items())
        answers = {}
        usage = {"input_tokens": 0, "output_tokens": 0}
        model = None
        for start in range(0, len(items), MAX_QUESTIONS):
            part = systemone(state, dict(items[start:start + MAX_QUESTIONS]))
            answers.update(part["answers"])
            usage["input_tokens"] += part["usage"]["input_tokens"]
            usage["output_tokens"] += part["usage"]["output_tokens"]
            model = part["model"]
        print("systemone: %d question(s) in %d chunk(s)" % (
            len(questions), -(-len(items) // MAX_QUESTIONS)), flush=True)
        merged = {"model": model or "laya", "answers": answers, "usage": usage}
        _record_decision(merged, round((time.time() - t0) * 1000))
        return merged
    rest, resolved = _split_arithmetic(state, questions)
    laya_questions, _rest_kinds = core.to_laya_questions(rest)
    _all_questions, kinds = core.to_laya_questions(questions)
    raw = core.predict_locked(
        active_router, core.coerce_state(state), laya_questions,
        model=os.environ.get("LAYA_MODEL") or None,
    ) if rest else _empty_payload()
    print("systemone: %d question(s) %s%s" % (
        len(laya_questions), [question.get("type") for question in laya_questions.values()],
        ", %d answered by arithmetic" % len(resolved) if resolved else ""), flush=True)
    raw = _merge_arithmetic(raw, resolved)
    result = core.systemone_result(raw, kinds, round((time.time() - t0) * 1000))
    _record_decision(result, round((time.time() - t0) * 1000))
    return result


def systemone_cards():
    """`GET /v1/models` roster - oh-my-pi turns each card into a native judge
    model, so this discovery response is what puts laya in the judge chain."""
    resident = _resident() or ["laya"]
    return [
        {
            "name": model,
            "description": "laya local System-1 judge (%s)" % model,
            "release_date": "2026-01-01",
        }
        for model in resident
    ]




class SidecarHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, handler):
        super().__init__(address, handler)
        self.last_request = time.monotonic()
        self.active_requests = 0
        self._active_lock = threading.Lock()
        try:
            self.idle_seconds = max(0.0, float(
                os.environ.get("LAYA_SIDECAR_IDLE", DEFAULT_IDLE_SECONDS)))
        except ValueError:
            self.idle_seconds = DEFAULT_IDLE_SECONDS

    def begin_request(self):
        with self._active_lock:
            self.last_request = time.monotonic()
            self.active_requests += 1

    def end_request(self):
        with self._active_lock:
            self.last_request = time.monotonic()
            self.active_requests = max(0, self.active_requests - 1)

    def service_actions(self):
        if self.idle_seconds <= 0:
            return
        with self._active_lock:
            idle = self.active_requests == 0 and (
                time.monotonic() - self.last_request >= self.idle_seconds)
        if idle:
            print("laya-judge sidecar idle; exiting", flush=True)
            threading.Thread(target=self.shutdown, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    server_version = "laya-judge-http/1"

    def setup(self):
        track = getattr(self.server, "begin_request", None)
        if track:
            track()
        try:
            super().setup()
        except Exception:
            if track:
                getattr(self.server, "end_request")()
            raise

    def finish(self):
        try:
            super().finish()
        finally:
            track = getattr(self.server, "end_request", None)
            if track:
                track()

    def _send(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # The caller gave up (judgment timeout, aborted turn). The work is
            # still done; only the reply has nowhere to go.
            self.close_connection = True

    def do_GET(self):  # noqa: N802
        route, _, query = self.path.partition("?")
        path = route.rstrip("/")
        if path in ("", "/info"):
            self._send(200, _info())
        elif path == "/v1/models":
            self._send(200, {"models": systemone_cards()})
        elif path == "/v1/decisions":
            params = urllib.parse.parse_qs(query)
            self._send(200, decisions_since((params.get("since") or ["0"])[0]))
        else:
            self._send(404, {"error": "unknown path"})

    def do_POST(self):  # noqa: N802
        path = self.path.rstrip("/")
        if path not in ("/judge", "/v1/systemone"):
            self._send(404, {"error": "unknown path"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        try:
            request = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if path == "/v1/systemone":
                response = systemone(request.get("state", ""), request.get("questions", {}))
            elif isinstance(request, list):
                response = predict_batch(request)
            else:
                response = predict(request.get("state", ""), request.get("questions", {}))
            self._send(200, response)
        except RequestTooLarge as exc:
            # 422 is non-transient for the client: it surfaces at once and the
            # role chain moves to its next candidate instead of retrying.
            self._send(422, {"detail": str(exc), "error": str(exc)})
        except Exception as exc:  # per-call failure, stay available
            self._send(500, {"detail": str(exc), "error": str(exc), "startup_error": startup_error})

    def log_message(self, *args):
        pass


def _probe_info(port, timeout=0.5):
    try:
        with _opener.open("http://127.0.0.1:%d/info" % port, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def _bind(port):
    try:
        return SidecarHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        if exc.errno not in (errno.EADDRINUSE, 10048):
            raise
        if _probe_info(port) is not None:
            print("already running")
            return None
        raise RuntimeError(
            "cannot bind 127.0.0.1:%d and no valid sidecar answered /info" % port
        ) from exc


if __name__ == "__main__":
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else DEFAULT_PORT
    httpd = _bind(port)
    if httpd is None:
        raise SystemExit(0)
    loader = threading.Thread(target=_load_model, name="laya-model-loader", daemon=True)
    loader.start()
    print("laya-judge http on 127.0.0.1:%d" % port, flush=True)
    try:
        httpd.serve_forever(poll_interval=0.2)
    finally:
        httpd.server_close()
