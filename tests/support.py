"""Shared helpers for the test suite."""
import socket
import threading
from http.server import BaseHTTPRequestHandler


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
