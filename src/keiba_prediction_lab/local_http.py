"""Loopback-only HTTP transport for audited read-only app snapshots."""

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
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
        "endpoints": ["/", "/health", "/api/v1/state"],
    }, separators=(",", ":")).encode("utf-8")
    static_root = files("keiba_prediction_lab").joinpath("static")
    index = static_root.joinpath("index.html").read_bytes()
    stylesheet = static_root.joinpath("app.css").read_bytes()
    script = static_root.joinpath("app.js").read_bytes()

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
                self._send_content(
                    200,
                    index,
                    content_type="text/html; charset=utf-8",
                    content_security_policy=(
                        "default-src 'none'; style-src 'self'; "
                        "script-src 'self'; connect-src 'self'; "
                        "img-src 'self'; base-uri 'none'; frame-ancestors 'none'"
                    ),
                )
            elif path == "/app.css":
                self._send_content(
                    200, stylesheet, content_type="text/css; charset=utf-8"
                )
            elif path == "/app.js":
                self._send_content(
                    200,
                    script,
                    content_type="text/javascript; charset=utf-8",
                )
            elif path == "/service":
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
            self._send_content(
                status,
                content,
                content_type="application/json; charset=utf-8",
                extra_headers=extra_headers,
            )

        def _send_content(
            self,
            status: int,
            content: bytes,
            *,
            content_type: str,
            content_security_policy: str = "default-src 'none'",
            extra_headers: tuple[tuple[str, str], ...] = (),
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", content_security_policy)
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
    win5_forecast: str | Path | None = None,
    race_day_manifest: str | Path | None = None,
    port: int = DEFAULT_READ_ONLY_API_PORT,
    open_browser: bool = False,
) -> None:
    """Audit selected artifacts, then serve their immutable snapshot forever."""
    snapshot = build_read_only_app_snapshot(
        prediction_directory=prediction_directory,
        walk_forward_report=walk_forward_report,
        win5_forecast=win5_forecast,
        race_day_manifest=race_day_manifest,
    )
    with create_read_only_server(snapshot, port=port) as server:
        actual_port = server.server_address[1]
        url = f"http://{LOOPBACK_HOST}:{actual_port}/"
        print(f"Read-only UI: {url}", flush=True)
        if open_browser and not webbrowser.open(url, new=2):
            print("ブラウザを自動で開けませんでした。上のURLを手動で開いてください。", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
