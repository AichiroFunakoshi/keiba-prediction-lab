"""Loopback-only HTTP transport for audited read-only app snapshots."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TypeAlias

from .app_snapshot import ReadOnlyAppSnapshot, build_read_only_app_snapshot


LOOPBACK_HOST = "127.0.0.1"
DEFAULT_READ_ONLY_API_PORT = 8765
_ALLOWED_HOSTS = frozenset((LOOPBACK_HOST, "localhost"))
HandlerClass: TypeAlias = type[BaseHTTPRequestHandler]


class _LoopbackServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def _handler(snapshot: ReadOnlyAppSnapshot) -> HandlerClass:
    state = json.dumps(
        {"is_valid": True, **snapshot.to_dict()},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    health = b'{"status":"ok","mode":"read-only"}'
    service = json.dumps({
        "service": "keiba-prediction-lab",
        "mode": "read-only",
        "endpoints": ["/health", "/api/v1/state"],
    }, separators=(",", ":")).encode("utf-8")

    class ReadOnlyHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            if not self._allowed_host():
                self._send_json(421, b'{"error":"invalid host"}')
                return
            path = self.path.partition("?")[0]
            if path == "/health":
                self._send_json(200, health)
            elif path == "/api/v1/state":
                self._send_json(200, state)
            elif path == "/":
                self._send_json(200, service)
            else:
                self._send_json(404, b'{"error":"not found"}')

        def do_POST(self) -> None:
            self._method_not_allowed()

        def do_PUT(self) -> None:
            self._method_not_allowed()

        def do_PATCH(self) -> None:
            self._method_not_allowed()

        def do_DELETE(self) -> None:
            self._method_not_allowed()

        def _allowed_host(self) -> bool:
            host = self.headers.get("Host", "")
            hostname = host.rsplit(":", 1)[0].lower()
            return hostname in _ALLOWED_HOSTS

        def _method_not_allowed(self) -> None:
            self._send_json(
                405,
                b'{"error":"read-only service"}',
                extra_headers=(("Allow", "GET"),),
            )

        def _send_json(
            self,
            status: int,
            content: bytes,
            *,
            extra_headers: tuple[tuple[str, str], ...] = (),
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            for name, value in extra_headers:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, format: str, *args: object) -> None:
            return

    return ReadOnlyHandler


def create_read_only_server(
    snapshot: ReadOnlyAppSnapshot,
    *,
    port: int = DEFAULT_READ_ONLY_API_PORT,
) -> ThreadingHTTPServer:
    """Create an unstarted server bound only to the IPv4 loopback address."""
    if not isinstance(snapshot, ReadOnlyAppSnapshot):
        raise ValueError("snapshot must be a ReadOnlyAppSnapshot")
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("port must be an integer between 0 and 65535")
    return _LoopbackServer((LOOPBACK_HOST, port), _handler(snapshot))


def serve_read_only_api(
    *,
    prediction_directory: str | Path | None = None,
    walk_forward_report: str | Path | None = None,
    port: int = DEFAULT_READ_ONLY_API_PORT,
) -> None:
    """Audit selected artifacts, then serve their immutable snapshot forever."""
    snapshot = build_read_only_app_snapshot(
        prediction_directory=prediction_directory,
        walk_forward_report=walk_forward_report,
    )
    with create_read_only_server(snapshot, port=port) as server:
        actual_port = server.server_address[1]
        print(f"Read-only API: http://{LOOPBACK_HOST}:{actual_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
