import http.client
import tempfile
import unittest
from pathlib import Path

from keiba_prediction_lab.app_snapshot import build_read_only_app_snapshot
from keiba_prediction_lab.desktop_app import (
    APP_IDENTIFIER,
    APP_NAME,
    default_demo_directory,
    load_or_create_demo_snapshot,
    run_desktop_window,
)
from tests.test_bundle_audit import _saved_bundle


class _FakeWebview:
    def __init__(self) -> None:
        self.window: tuple[object, ...] | None = None
        self.options: dict[str, object] = {}
        self.health_status: int | None = None

    def create_window(self, *args: object, **kwargs: object) -> None:
        self.window = args
        self.options = kwargs

    def start(self) -> None:
        assert self.window is not None
        url = str(self.window[1])
        host_port = url.removeprefix("http://").rstrip("/")
        host, port = host_port.split(":")
        connection = http.client.HTTPConnection(host, int(port), timeout=2)
        connection.request("GET", "/health")
        response = connection.getresponse()
        self.health_status = response.status
        response.read()
        connection.close()


class DesktopAppTest(unittest.TestCase):
    def test_brand_constants_and_private_default_location(self) -> None:
        path = default_demo_directory("/Users/example")

        self.assertEqual(APP_NAME, "RaceWeave")
        self.assertEqual(APP_IDENTIFIER, "jp.aichiro.raceweave")
        self.assertEqual(
            path,
            Path("/Users/example/Library/Application Support/RaceWeave/ui-demo-v1"),
        )

    def test_default_demo_is_created_once_and_reaudited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ui-demo"
            first = load_or_create_demo_snapshot(root)
            before = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            second = load_or_create_demo_snapshot(root)
            after = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }

        self.assertIsNotNone(first.race_day)
        self.assertIsNotNone(second.race_day)
        self.assertEqual(before, after)

    def test_native_window_uses_ephemeral_loopback_server(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prediction = _saved_bundle(Path(directory))
            snapshot = build_read_only_app_snapshot(
                prediction_directory=prediction
            )
            webview = _FakeWebview()

            run_desktop_window(snapshot, webview_module=webview)  # type: ignore[arg-type]

        self.assertEqual(webview.health_status, 200)
        self.assertIsNotNone(webview.window)
        self.assertTrue(str(webview.window[0]).startswith("RaceWeave"))
        self.assertTrue(str(webview.window[1]).startswith("http://127.0.0.1:"))
        self.assertEqual(webview.options["min_size"], (1051, 720))


if __name__ == "__main__":
    unittest.main()
