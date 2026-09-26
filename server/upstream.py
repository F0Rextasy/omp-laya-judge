"""Optional upstream System One backend.

The sidecar already speaks `POST /v1/systemone`, so swapping the model behind
it is a transport change, not a rewrite: point the sidecar at any endpoint
that speaks the same contract and it answers instead of loading laya. That is
the same seam `benchmark/compare_backends.py` scores against, so "which model
is better" is a measurement rather than an opinion.

Chosen by environment, never by a hook:

    LAYA_BACKEND          laya (default) | systemone
    LAYA_UPSTREAM_URL     full systemone URL of the upstream judge
    LAYA_UPSTREAM_KEY_ENV name of the env var holding the key; the key itself
                          never goes in a config file
                          (default: LAYA_UPSTREAM_API_KEY)

The key is read from the environment on every call so a rotated key takes
effect without a restart, and a missing key fails with the variable's name
rather than a stack trace.
"""
import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

DEFAULT_KEY_ENV = "LAYA_UPSTREAM_API_KEY"
TIMEOUT_S = 15.0


class UpstreamError(RuntimeError):
    """The configured upstream could not be reached or refused the request."""


def backend() -> str:
    return (os.environ.get("LAYA_BACKEND") or "laya").strip().lower()


def upstream_url() -> str:
    return (os.environ.get("LAYA_UPSTREAM_URL") or "").strip()


def key_env() -> str:
    return (os.environ.get("LAYA_UPSTREAM_KEY_ENV") or DEFAULT_KEY_ENV).strip()


def configured() -> bool:
    return backend() == "systemone" and bool(upstream_url())


def describe() -> Dict[str, Any]:
    """What `/info` reports, so the banner never claims laya while jev answers."""
    if not configured():
        return {"backend": "laya", "local": True}
    return {
        "backend": "systemone",
        "local": False,
        "url": upstream_url(),
        "key_env": key_env(),
        "key_present": bool(os.environ.get(key_env())),
    }


def _endpoint() -> Tuple[str, Optional[str]]:
    url = upstream_url()
    if not url:
        raise UpstreamError("LAYA_BACKEND=systemone but LAYA_UPSTREAM_URL is not set")
    token = os.environ.get(key_env())
    if not token:
        raise UpstreamError(f"{key_env()} is not set; the upstream key is required")
    return url, token


def judge(state: Any, questions: Dict[str, Any]) -> Dict[str, Any]:
    """One systemone call -> {answers, model, usage, latency_ms}."""
    url, token = _endpoint()
    body = json.dumps({"state": state, "questions": questions}).encode()
    request = urllib.request.Request(
        url, data=body,
        headers={"content-type": "application/json", "authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:200].decode("utf-8", "ignore")
        raise UpstreamError(f"upstream {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpstreamError(f"upstream unreachable: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
        raise UpstreamError("upstream response has no answers map")
    return payload


def answers_for(state: Any, questions: Dict[str, Any]) -> Dict[str, Any]:
    """The answers map alone, for callers that only need the picks."""
    return judge(state, questions).get("answers") or {}
