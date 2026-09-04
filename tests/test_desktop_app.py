import http.client
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from keiba_prediction_lab.app_snapshot import build_read_only_app_snapshot
from keiba_prediction_lab.desktop_app import (
    APP_IDENTIFIER,
    APP_NAME,
    choose_race_day_manifest,
    default_demo_directory,
    load_audited_race_day_snapshot,
    load_or_create_demo_snapshot,
    main,
    run_desktop_window,
)
from keiba_prediction_lab.ui_demo import create_ui_demo
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

    def test_macos_chooser_returns_selected_manifest(self) -> None:
        completed = type("Completed", (), {"stdout": "/tmp/day/race-day.json\n"})()
        with (
            patch("keiba_prediction_lab.desktop_app.sys.platform", "darwin"),
            patch(
                "keiba_prediction_lab.desktop_app.subprocess.run",
                return_value=completed,
            ) as run,
        ):
            selected = choose_race_day_manifest()

        self.assertEqual(selected, Path("/tmp/day/race-day.json"))
        self.assertEqual(run.call_args.args[0][0], "/usr/bin/osascript")
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertTrue(run.call_args.kwargs["capture_output"])

    def test_macos_chooser_cancel_returns_none(self) -> None:
        completed = type("Completed", (), {"stdout": "\n"})()
        with (
            patch("keiba_prediction_lab.desktop_app.sys.platform", "darwin"),
            patch(
                "keiba_prediction_lab.desktop_app.subprocess.run",
                return_value=completed,
            ),
        ):
            self.assertIsNone(choose_race_day_manifest())

    def test_selected_race_day_requires_whole_day_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ui-demo"
            demo = create_ui_demo(root)
            snapshot = load_audited_race_day_snapshot(demo.race_day_manifest)

            provenance = root / "race-day-provenance.json"
            envelope = json.loads(provenance.read_text(encoding="utf-8"))
            envelope["sha256"] = "0" * 64
            provenance.write_text(
                json.dumps(envelope, ensure_ascii=False), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "integrity check failed"):
                load_audited_race_day_snapshot(demo.race_day_manifest)

        self.assertIsNotNone(snapshot.race_day)

    def test_selected_race_day_rejects_another_json_file(self) -> None:
        with self.assertRaisesRegex(ValueError, "race-day.json"):
            load_audited_race_day_snapshot("/tmp/manifest.json")

    def test_no_argument_launch_opens_selected_audited_race_day(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            demo = create_ui_demo(Path(directory) / "ui-demo")
            with (
                patch(
                    "keiba_prediction_lab.desktop_app.choose_race_day_manifest",
                    return_value=demo.race_day_manifest,
                ),
                patch(
                    "keiba_prediction_lab.desktop_app.run_desktop_window"
                ) as open_window,
            ):
                status = main([])

        self.assertEqual(status, 0)
        snapshot = open_window.call_args.args[0]
        self.assertIsNotNone(snapshot.race_day)

    def test_no_argument_cancel_opens_private_demo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            demo_root = Path(directory) / "private-demo"
            with (
                patch(
                    "keiba_prediction_lab.desktop_app.choose_race_day_manifest",
                    return_value=None,
                ),
                patch(
                    "keiba_prediction_lab.desktop_app.default_demo_directory",
                    return_value=demo_root,
                ),
                patch(
                    "keiba_prediction_lab.desktop_app.run_desktop_window"
                ) as open_window,
            ):
                status = main([])

            self.assertTrue(demo_root.exists())

        self.assertEqual(status, 0)
        snapshot = open_window.call_args.args[0]
        self.assertIsNotNone(snapshot.race_day)

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
