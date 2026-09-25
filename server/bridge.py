"""Thin FastMCP bridge to the local HTTP decision sidecar.

This process owns no model. It starts the sidecar when needed and forwards the
three MCP tools without changing their established JSON shapes.
"""
import json
import os
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import urllib.parse

import core
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("laya-judge")
HERE = os.path.dirname(os.path.abspath(__file__))
URL = os.environ.get("LAYA_SIDECAR_URL", "http://127.0.0.1:3777").rstrip("/")
INFO_TIMEOUT = 0.5
JUDGE_TIMEOUT = 595.0
START_TIMEOUT = 60.0
RESPAWN_TIMEOUT = 15.0
_SIDECAR_LOG = os.path.join(tempfile.gettempdir(), "omp-laya-judge-sidecar.log")
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_start_lock = threading.Lock()
_sidecar_process = None
_last_startup_error = None


def _stderr_tail(limit=4000):
    try:
        with open(_SIDECAR_LOG, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            return stream.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _error(message, startup_error=None):
    return {
        "error": message,
        "startup_error": startup_error or _last_startup_error,
        "stderr_tail": _stderr_tail(),
    }


def _request(method, path, payload=None, timeout=INFO_TIMEOUT):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        URL + path, data=body, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with _opener.open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except Exception:
            detail = {"error": "HTTP %d" % exc.code}
        return exc.code, detail


def _is_connection_refused(exc):
    reason = getattr(exc, "reason", exc)
    return (isinstance(reason, ConnectionRefusedError)
            or getattr(reason, "winerror", None) == 10061
            or "connection refused" in str(reason).lower()
            or "10061" in str(reason))


def _spawn_sidecar():
    global _sidecar_process, _last_startup_error
    port = urllib.parse.urlparse(URL).port or 3777
    commands = [["py", "-3", "sidecar.py", "--port", str(port)],
                ["python3", "sidecar.py", "--port", str(port)]]
    last_error = None
    for command in commands:
        try:
            output = open(_SIDECAR_LOG, "ab", buffering=0)
            try:
                kwargs = {
                    "cwd": HERE,
                    "stdin": subprocess.DEVNULL,
                    "stdout": output,
                    "stderr": subprocess.STDOUT,
                    "close_fds": True,
                }
                if os.name == "nt":
                    kwargs["creationflags"] = (
                        subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)
                else:
                    kwargs["start_new_session"] = True
                _sidecar_process = subprocess.Popen(command, **kwargs)
                _last_startup_error = None
                return True
            finally:
                output.close()
        except OSError as exc:
            last_error = exc
    _last_startup_error = str(last_error or "sidecar spawn failed")
    return False


def _poll_info(timeout):
    deadline = time.monotonic() + timeout
    while True:
        try:
            status, info = _request("GET", "/info", timeout=INFO_TIMEOUT)
            if status == 200 and isinstance(info, dict):
                return info
        except (OSError, urllib.error.URLError, TimeoutError):
            pass
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.1)


def _ensure_sidecar(timeout=START_TIMEOUT):
    global _last_startup_error
    with _start_lock:
        info = _poll_info(0.0)
        if info is not None:
            return info
        if not _spawn_sidecar():
            return None
        info = _poll_info(timeout)
        if info is None:
            _last_startup_error = (
                "sidecar did not answer /info within %.0f seconds" % timeout)
        return info


def _post_with_recovery(path, payload, timeout):
    try:
        status, response = _request("POST", path, payload, timeout)
    except (OSError, urllib.error.URLError) as exc:
        if not _is_connection_refused(exc):
            return None, _error("sidecar request failed: %s" % exc)
        _ensure_sidecar(RESPAWN_TIMEOUT)
        try:
            status, response = _request("POST", path, payload, timeout)
        except (OSError, urllib.error.URLError) as retry_exc:
            return None, _error("sidecar unavailable after respawn: %s" % retry_exc)
    if status != 200:
        message = response.get("detail") or response.get("error") or "HTTP %d" % status
        return None, _error(str(message), response.get("startup_error"))
    return response, None


def _get_info_with_recovery():
    try:
        status, response = _request("GET", "/info", timeout=INFO_TIMEOUT)
    except (OSError, urllib.error.URLError) as exc:
        if not _is_connection_refused(exc):
            return _error("sidecar status failed: %s" % exc)
        _ensure_sidecar(RESPAWN_TIMEOUT)
        try:
            status, response = _request("GET", "/info", timeout=INFO_TIMEOUT)
        except (OSError, urllib.error.URLError) as retry_exc:
            return _error("sidecar unavailable after respawn: %s" % retry_exc)
    if status != 200:
        message = response.get("detail") or response.get("error") or "HTTP %d" % status
        return _error(str(message), response.get("startup_error"))
    return response


@mcp.tool()
def judge(state: str, questions: str) -> str:
    """Judge typed questions over a state with the local model.

    ``questions`` is a JSON object mapping ids to choice, bool/noul, or score
    definitions. Returns {answers, model, routing, usage, latency_ms}.
    """
    started = time.monotonic()
    try:
        question_map = json.loads(questions) if isinstance(questions, str) else questions
        _laya_questions, kinds = core.to_laya_questions(question_map)
    except Exception as exc:
        return json.dumps(_error("invalid questions: %s" % exc))
    response, error = _post_with_recovery(
        "/judge", {"state": state, "questions": question_map}, JUDGE_TIMEOUT)
    if error is not None:
        return json.dumps(error)
    latency = response.get("latency_ms")
    if not isinstance(latency, int):
        latency = round((time.monotonic() - started) * 1000)
    return json.dumps(core.judge_result(response, kinds, latency))


@mcp.tool()
def judge_batch(queries: str) -> str:
    """Judge many states in one call and return {results, count, latency_ms}.

    `queries` is a JSON list of {state, questions}, or a single such object.
    A {states: [...], questions: {...}} object is accepted too and means "these
    questions against each of these states". Anything else is an error: a
    batch that silently judges empty state answers a question nobody asked.
    """
    try:
        items = core.normalize_batch(queries)
    except Exception as exc:
        return json.dumps(_error("invalid batch queries: %s" % exc))
    response, error = _post_with_recovery("/judge", items, JUDGE_TIMEOUT)
    if error is not None:
        return json.dumps(error)
    return json.dumps({
        "results": response.get("results", []),
        "count": response.get("count", 0),
        "latency_ms": response.get("latency_ms", 0),
    })


@mcp.tool()
def judge_info() -> str:
    """Show loaded checkpoint(s), device, supported question types, and errors."""
    return json.dumps(_get_info_with_recovery())


_ensure_sidecar()

if __name__ == "__main__":
    mcp.run()
