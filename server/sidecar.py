"""laya-judge HTTP sidecar: POST /judge for oh-my-pi's resolveJudge chain.

Same predict() as the MCP server, stdlib only. Request shapes mirror
oh-my-pi's Judge interface 1:1 (state + choice/bool/noul/score questions),
so the TS LayaJudge adapter forwards them verbatim.

  python server/sidecar.py [--port 3777]

POST /judge {"state": ..., "questions": {...}}
  -> {"answers": {...}, "model": ..., "routing": ..., "usage": ..., "latency_ms": ...}
POST /judge [{"state": ..., "questions": {...}}, ...]   (batch)
  -> {"results": [judge payloads], "count": n, "latency_ms": ...}
GET /info -> {"model": ..., "device": ..., "types": [...], "loaded": ..., "startup_error": ...}
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402  (pure mapping, no heavy deps)

router = None  # set by __main__ (eager load) or by tests (fake router)


def _ensure_router():
    global router
    if router is None:
        from server import get_router  # deferred: keeps `import sidecar` light
        router = get_router()
    return router


def predict(state, questions):
    t0 = time.time()
    rt = _ensure_router()
    laya_q, _kinds = core.to_laya_questions(questions or {})
    raw = core.predict_locked(
        rt, core.coerce_state(state), laya_q,
        model=os.environ.get("LAYA_MODEL") or None,
    )
    print(f"judge: {len(laya_q)} question(s) {[q.get('type') for q in laya_q.values()]}",
          flush=True)
    return core.raw_result(raw, round((time.time() - t0) * 1000))


def predict_batch(items):
    t0 = time.time()
    rt = _ensure_router()
    results = core.run_batch(rt, items, os.environ.get("LAYA_MODEL") or None)
    print(f"judge-batch: {len(results)} state(s)", flush=True)
    return {"results": results, "count": len(results),
            "latency_ms": round((time.time() - t0) * 1000)}


class Handler(BaseHTTPRequestHandler):
    server_version = "laya-judge-http/1"

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") in ("", "/info"):
            pinned = os.environ.get("LAYA_MODEL")
            server_mod = sys.modules.get("server")
            self._send(200, {
                "model": f"laya/{pinned}" if pinned else "laya/auto",
                "device": "cpu",
                "types": ["choice", "bool", "noul", "score"],
                "loaded": router is not None,
                "startup_error": getattr(server_mod, "_startup_error", None) if server_mod else None,
            })
        else:
            self._send(404, {"error": "unknown path"})

    def do_POST(self):  # noqa: N802
        if self.path.rstrip("/") != "/judge":
            self._send(404, {"error": "unknown path"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        try:
            req = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if isinstance(req, list):
                self._send(200, predict_batch(req))
            else:
                self._send(200, predict(req.get("state", ""), req.get("questions", {})))
        except Exception as exc:  # per-call failure, stay up
            self._send(500, {"error": str(exc)})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 3777
    _ensure_router()  # eager checkpoint load in the main thread (Windows/OpenMP)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"laya-judge http on 127.0.0.1:{port}", flush=True)
    httpd.serve_forever()
