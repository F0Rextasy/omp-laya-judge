"""laya-judge HTTP sidecar: POST /judge for oh-my-pi's resolveJudge chain.

Same predict() as the MCP server, stdlib only. Request shapes mirror
oh-my-pi's Judge interface 1:1 (state + choice/noul/score questions),
so the TS LayaJudge adapter forwards them verbatim.

  python server/http.py [--port 3777]

POST /judge {"state": ..., "questions": {...}}
  -> {"answers": {...}, "model": ..., "latency_ms": ...}
GET /info -> {"model": ..., "device": ..., "types": [...], "loaded": ...}
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from server import get_router  # noqa: E402  (eager checkpoint load lives there)


def predict(state, questions):
    t0 = time.time()
    # Accept the MCP tool's "bool" alias alongside laya's native "noul".
    norm = {}
    for qid, qdef in (questions or {}).items():
        qdef = dict(qdef)
        if qdef.get("type") == "bool":
            qdef["type"] = "noul"
        norm[qid] = qdef
    raw = router.predict(
        state,
        norm,
        model=os.environ.get("LAYA_MODEL") or None,
    )
    print(f"judge: {len(norm)} question(s) {[q.get('type') for q in norm.values()]}",
          flush=True)
    return {
        "answers": raw.get("answers", {}) if isinstance(raw, dict) else {},
        "model": raw.get("model", "laya-rl-agent") if isinstance(raw, dict) else "laya-rl-agent",
        "latency_ms": round((time.time() - t0) * 1000),
    }


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
            from server import _router

            self._send(200, {
                "model": f"laya/{os.environ.get('LAYA_MODEL', 'english')}",
                "device": "cpu",
                "types": ["choice", "noul", "score"],
                "loaded": _router is not None,
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
            req = json.loads(self.rfile.read(length) or b"{}")
            state = req.get("state", "")
            if isinstance(state, str):
                try:
                    state = json.loads(state)
                except json.JSONDecodeError:
                    pass
            self._send(200, predict(state, req.get("questions", {})))
        except Exception as exc:  # per-call failure, stay up
            self._send(500, {"error": str(exc)})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 3777
    router = get_router()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"laya-judge http on 127.0.0.1:{port}", flush=True)
    httpd.serve_forever()
