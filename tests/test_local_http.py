import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from keiba_prediction_lab.app_snapshot import build_read_only_app_snapshot
from keiba_prediction_lab.local_http import (
    LOOPBACK_HOST,
    create_read_only_server,
)
from tests.test_bundle_audit import _saved_bundle
from tests.test_app_snapshot import _race_day_manifest, _win5_forecast


class LocalReadOnlyHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.prediction = _saved_bundle(Path(self.temporary.name))
        self.before = {
            path.name: path.read_bytes() for path in self.prediction.iterdir()
        }
        self.win5_forecast = _win5_forecast(Path(self.temporary.name))
        self.race_day_manifest = _race_day_manifest(
            Path(self.temporary.name), self.prediction
        )
        snapshot = build_read_only_app_snapshot(
            prediction_directory=self.prediction,
            win5_forecast=self.win5_forecast,
            race_day_manifest=self.race_day_manifest,
        )
        self.server = create_read_only_server(snapshot, port=0)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def _request(
        self, method: str, path: str, *, host: str | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection(
            LOOPBACK_HOST, self.port, timeout=2
        )
        headers = {"Host": host} if host is not None else {}
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        status = response.status
        response_headers = {
            key.lower(): value for key, value in response.getheaders()
        }
        payload = response.read()
        connection.close()
        return status, response_headers, payload

    def test_serves_audited_state_on_loopback_without_mutation(self) -> None:
        status, headers, payload = self._request("GET", "/api/v1/state")
        state = json.loads(payload.decode("utf-8"))
        after = {
            path.name: path.read_bytes() for path in self.prediction.iterdir()
        }

        self.assertEqual(self.server.server_address[0], LOOPBACK_HOST)
        self.assertEqual(status, 200)
        self.assertTrue(state["is_valid"])
        self.assertEqual(state["prediction"]["actual"]["stake_yen"], 100)
        self.assertEqual(state["win5"]["stake_yen"], 0)
        self.assertEqual(len(state["win5"]["legs"]), 5)
        self.assertEqual(state["race_day"]["venues"][0]["venue"], "東京")
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["content-security-policy"], "default-src 'none'")
        self.assertEqual(after, self.before)

    def test_serves_health_and_visual_read_only_app(self) -> None:
        health_status, _, health_payload = self._request("GET", "/health")
        root_status, root_headers, root = self._request("GET", "/")
        css_status, css_headers, css = self._request("GET", "/app.css")
        js_status, js_headers, script = self._request("GET", "/app.js")
        health = json.loads(health_payload.decode("utf-8"))

        self.assertEqual(health_status, 200)
        self.assertEqual(health, {"status": "ok", "mode": "read-only"})
        self.assertEqual(root_status, 200)
        self.assertEqual(root_headers["content-type"], "text/html; charset=utf-8")
        self.assertIn(b"RaceWeave", root)
        self.assertIn("三連単 1点100円".encode(), root)
        self.assertIn("WIN5影予測".encode(), root)
        self.assertIn("全レース予測一覧".encode(), root)
        self.assertNotIn(self.temporary.name.encode(), root)
        self.assertEqual(css_status, 200)
        self.assertEqual(css_headers["content-type"], "text/css; charset=utf-8")
        self.assertIn(b".official-ticket", css)
        self.assertIn(b"--turf-deep: #123d2b", css)
        self.assertIn(b"--earth: #795548", css)
        self.assertIn(b"--ivory: #f5f0e6", css)
        self.assertNotIn(b"background: #031f4b", css)
        self.assertEqual(js_status, 200)
        self.assertEqual(js_headers["content-type"], "text/javascript; charset=utf-8")
        self.assertIn(b'/api/v1/state', script)
        self.assertIn(b'renderWin5', script)
        self.assertIn(b'renderDashboard', script)
        self.assertIn(b'runnerDisplayMap', script)
        self.assertIn(b'winnerDisplay.horse_number', script)
        self.assertIn(b'event.key === "ArrowRight"', script)
        self.assertIn(b'findRunnerDisplay', script)

    def test_rejects_mutating_methods_and_unknown_routes(self) -> None:
        post_status, post_headers, post = self._request("POST", "/api/v1/state")
        missing_status, _, missing = self._request("GET", "/missing")
        post_payload = json.loads(post.decode("utf-8"))
        missing_payload = json.loads(missing.decode("utf-8"))

        self.assertEqual(post_status, 405)
        self.assertEqual(post_headers["allow"], "GET")
        self.assertEqual(post_payload["error"], "read-only service")
        self.assertEqual(missing_status, 404)
        self.assertEqual(missing_payload["error"], "not found")

    def test_rejects_non_loopback_host_header(self) -> None:
        status, _, payload = self._request(
            "GET", "/api/v1/state", host="evil.example"
        )

        self.assertEqual(status, 421)
        response = json.loads(payload.decode("utf-8"))
        self.assertEqual(response["error"], "invalid host")

    def test_rejects_invalid_ports(self) -> None:
        snapshot = build_read_only_app_snapshot(
            prediction_directory=self.prediction
        )

        for invalid_port in (-1, 65536, True, 1.5):
            with self.subTest(port=invalid_port):
                with self.assertRaisesRegex(ValueError, "port must be"):
                    create_read_only_server(snapshot, port=invalid_port)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
