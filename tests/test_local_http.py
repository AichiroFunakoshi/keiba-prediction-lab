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


class LocalReadOnlyHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.prediction = _saved_bundle(Path(self.temporary.name))
        self.before = {
            path.name: path.read_bytes() for path in self.prediction.iterdir()
        }
        snapshot = build_read_only_app_snapshot(
            prediction_directory=self.prediction
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
    ) -> tuple[int, dict[str, str], dict[str, object]]:
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
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        return status, response_headers, payload

    def test_serves_audited_state_on_loopback_without_mutation(self) -> None:
        status, headers, payload = self._request("GET", "/api/v1/state")
        after = {
            path.name: path.read_bytes() for path in self.prediction.iterdir()
        }

        self.assertEqual(self.server.server_address[0], LOOPBACK_HOST)
        self.assertEqual(status, 200)
        self.assertTrue(payload["is_valid"])
        self.assertEqual(payload["prediction"]["actual"]["stake_yen"], 100)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["content-security-policy"], "default-src 'none'")
        self.assertEqual(after, self.before)

    def test_health_and_root_disclose_no_file_paths(self) -> None:
        health_status, _, health = self._request("GET", "/health")
        root_status, _, root = self._request("GET", "/")

        self.assertEqual(health_status, 200)
        self.assertEqual(health, {"status": "ok", "mode": "read-only"})
        self.assertEqual(root_status, 200)
        self.assertEqual(root["mode"], "read-only")
        self.assertNotIn(self.temporary.name, json.dumps(root))

    def test_rejects_mutating_methods_and_unknown_routes(self) -> None:
        post_status, post_headers, post = self._request("POST", "/api/v1/state")
        missing_status, _, missing = self._request("GET", "/missing")

        self.assertEqual(post_status, 405)
        self.assertEqual(post_headers["allow"], "GET")
        self.assertEqual(post["error"], "read-only service")
        self.assertEqual(missing_status, 404)
        self.assertEqual(missing["error"], "not found")

    def test_rejects_non_loopback_host_header(self) -> None:
        status, _, payload = self._request(
            "GET", "/api/v1/state", host="evil.example"
        )

        self.assertEqual(status, 421)
        self.assertEqual(payload["error"], "invalid host")

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
