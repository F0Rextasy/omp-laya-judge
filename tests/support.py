"""Shared helpers for the test suite."""
import atexit
import collections
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(REPO, "server")
_sidecar = None
_sidecar_url = None
_sidecar_stderr = collections.deque(maxlen=60)
_sidecar_lock = threading.Lock()


class _NoopHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass


def probe_loopback(server_cls, handler_cls=_NoopHandler) -> bool:
    """True when a fresh 127.0.0.1 listener accepts a connection here.

    Some machines drop new loopback connections (strict AV/WFP policy —
    observed while developing this suite). That also breaks anything
    asyncio-based, since ProactorEventLoop's socketpair() is itself a
    loopback connect. Callers fall back or skip accordingly.
    """
    srv = server_cls(("127.0.0.1", 0), handler_cls)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False
    finally:
        srv.shutdown()
        srv.server_close()


def _stop_shared_sidecar():
    global _sidecar
    if _sidecar is None:
        return
    _sidecar.terminate()
    try:
        _sidecar.wait(timeout=10)
    except Exception:
        _sidecar.kill()
    _sidecar = None


def start_real_sidecar():
    """Start one cached-model sidecar for all slow real-model test classes."""
    global _sidecar, _sidecar_url
    with _sidecar_lock:
        if _sidecar is not None and _sidecar.poll() is None:
            return _sidecar_url, _sidecar
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        url = "http://127.0.0.1:%d" % port
        env = {key: value for key, value in os.environ.items() if key != "LAYA_MODEL"}
        env["PYTHONUNBUFFERED"] = "1"
        env["LAYA_SIDECAR_IDLE"] = "999999"
        process = subprocess.Popen(
            [sys.executable, "sidecar.py", "--port", str(port)], cwd=SERVER,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env)

        def drain_stderr():
            for line in process.stderr:
                _sidecar_stderr.append(line.rstrip())

        threading.Thread(target=drain_stderr, daemon=True).start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 600
        last_error = None
        try:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError(
                        "sidecar exited during model load: %s" % list(_sidecar_stderr))
                try:
                    with opener.open(url + "/info", timeout=1) as response:
                        info = json.loads(response.read())
                    last_error = info.get("startup_error")
                    if info.get("loaded"):
                        if last_error is not None:
                            raise AssertionError("model load failed: %s" % last_error)
                        _sidecar = process
                        _sidecar_url = url
                        atexit.register(_stop_shared_sidecar)
                        return url, process
                except AssertionError:
                    raise
                except Exception as exc:
                    last_error = str(exc)
                time.sleep(0.2)
            raise AssertionError("sidecar did not load within 600s: %s" % last_error)
        except Exception:
            process.terminate()
            try:
                process.wait(timeout=10)
            except Exception:
                process.kill()
            raise


def post_json(url, payload, timeout=595):
    request = urllib.request.Request(
        url + "/judge", data=json.dumps(payload).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read())


def get_json(url, path="/info", timeout=1):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url + path, timeout=timeout) as response:
        return json.loads(response.read())
