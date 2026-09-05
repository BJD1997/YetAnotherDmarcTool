"""Minimal liveness endpoint for the headless `worker` process.

The worker has no HTTP server otherwise, so an orchestrator (an ACA liveness
probe, a compose healthcheck) has nothing to probe. This exposes `GET /health`
that reports OK only while the worker's async heartbeat has fired recently — so a
*hung* worker (event loop wedged, not just a crashed process) is detected and
recycled, not just one that exited.

Runs the HTTP server in a daemon thread, deliberately: if the asyncio loop is
wedged, a loop-based responder would wedge too and always look healthy. The
thread keeps answering, and the heartbeat (updated from the loop) goes stale —
which is exactly the signal we want. `heartbeat()` is driven by a small async
task (app/workers/scheduler.py), so it reflects event-loop liveness rather than
whether any single job happens to be mid-run.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Considered stale (unhealthy) if the loop hasn't beaten in this long. The
# heartbeat task beats every few seconds, so this is generous margin.
HEALTH_STALE_SECONDS = 60

_last_beat = time.monotonic()
_is_leader = False


def heartbeat(*, is_leader: bool = False) -> None:
    global _last_beat, _is_leader
    _last_beat = time.monotonic()
    _is_leader = is_leader


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") not in ("", "/health", "/healthz"):
            self.send_response(404)
            self.end_headers()
            return
        age = time.monotonic() - _last_beat
        healthy = age < HEALTH_STALE_SECONDS
        body = json.dumps(
            {"status": "ok" if healthy else "stale", "loop_age_seconds": round(age, 1), "leader": _is_leader}
        ).encode()
        self.send_response(200 if healthy else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence per-request logging
        pass


def start_health_server(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    threading.Thread(target=server.serve_forever, name="worker-health", daemon=True).start()
    return server
