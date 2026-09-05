import http.client
import json
import shutil
import tempfile
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from keiba_prediction_lab.app_snapshot import build_read_only_app_snapshot
from keiba_prediction_lab.desktop_app import (
    APP_IDENTIFIER,
    APP_NAME,
    adjacent_related_artifacts,
    choose_race_day_manifest,
    default_demo_directory,
    latest_audited_race_day_manifest,
    load_audited_race_day_snapshot,
    load_or_create_demo_snapshot,
    main,
    repository_local_directory,
    run_desktop_window,
    verified_runner_display_by_race,
)
from keiba_prediction_lab.ui_demo import create_ui_demo
from tests.test_bundle_audit import _saved_bundle


class _FakeWebview:
    def __init__(self) -> None:
        self.window: tuple[object, ...] | None = None
        self.options: dict[str, object] = {}
        self.health_status: int | None = None
        self.loaded_url: str | None = None

    def create_window(self, *args: object, **kwargs: object):
        self.window = args
        self.options = kwargs
        return self

    def start(self, func=None, args=None) -> None:
        if func is not None:
            func(*(args or ()))

    def load_url(self, url: str) -> None:
        self.loaded_url = url
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

    def test_repository_local_directory_is_found_from_bundled_app(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "checkout"
            executable = root / "dist" / "RaceWeave.app" / "Contents" / "MacOS" / "RaceWeave"
            executable.parent.mkdir(parents=True)
            (root / "AGENTS.md").write_text("test", encoding="utf-8")
            (root / "src" / "keiba_prediction_lab").mkdir(parents=True)
            local = root / "local"
            local.mkdir()

            discovered = repository_local_directory(executable)

        self.assertEqual(discovered, local.resolve())

    def test_latest_valid_local_race_day_is_selected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "old" / "race-day.json"
            newest = root / "new" / "race-day.json"
            invalid = root / "invalid" / "race-day.json"
            for path in (old, newest, invalid):
                path.parent.mkdir()
                path.write_text("{}", encoding="utf-8")
            audits = {
                old.parent: SimpleNamespace(
                    race_date=date(2026, 9, 4),
                    frozen_at=datetime(2026, 9, 4, 8, tzinfo=timezone.utc),
                ),
                newest.parent: SimpleNamespace(
                    race_date=date(2026, 9, 5),
                    frozen_at=datetime(2026, 9, 5, 8, tzinfo=timezone.utc),
                ),
            }

            def audit(path):
                if path not in audits:
                    raise ValueError("invalid")
                return audits[path]

            with patch(
                "keiba_prediction_lab.desktop_app.audit_local_race_day",
                side_effect=audit,
            ):
                selected = latest_audited_race_day_manifest(root)

        self.assertEqual(selected, newest)

    def test_adjacent_related_artifacts_never_search_elsewhere(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "day" / "race-day.json"
            manifest.parent.mkdir()
            manifest.write_text("{}", encoding="utf-8")
            walk_forward = manifest.parent / "walk-forward.json"
            walk_forward.write_text("{}", encoding="utf-8")
            (root / "win5.json").write_text("{}", encoding="utf-8")

            related = adjacent_related_artifacts(manifest)

        self.assertEqual(related, (walk_forward, None))

    def test_adjacent_related_artifacts_prefer_revised_win5(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "race-day.json"
            manifest.write_text("{}", encoding="utf-8")
            original = root / "win5.json"
            revised = root / "win5-market-blend.json"
            original.write_text("original", encoding="utf-8")
            revised.write_text("revised", encoding="utf-8")

            _, win5 = adjacent_related_artifacts(manifest)

        self.assertEqual(win5, revised)

    def test_runner_display_joins_only_exact_hashed_target_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            demo = create_ui_demo(root / "demo")
            payload = json.loads(
                demo.race_day_manifest.read_text(encoding="utf-8")
            )
            first_race = payload["venues"][0]["races"][0]
            bundle = demo.race_day_manifest.parent / first_race["prediction_bundle"]
            actual = json.loads((bundle / "actual.json").read_text(encoding="utf-8"))
            race_id = actual["payload"]["race_id"]
            source = sorted((root / "demo" / "inputs").glob("targets-*.csv"))[0]
            matched = root / "search" / f"{race_id}.csv"
            matched.parent.mkdir()
            shutil.copyfile(source, matched)

            display = verified_runner_display_by_race(
                demo.race_day_manifest, matched.parent
            )
            matched.write_text("changed", encoding="utf-8")
            rejected = verified_runner_display_by_race(
                demo.race_day_manifest, matched.parent
            )

        self.assertEqual(len(display[race_id]), 5)
        self.assertEqual(display[race_id][0].horse_number, 1)
        self.assertEqual(display[race_id][0].frame_number, 1)
        self.assertNotIn(race_id, rejected)

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
                    "keiba_prediction_lab.desktop_app.latest_audited_race_day_manifest",
                    return_value=None,
                ),
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
                    "keiba_prediction_lab.desktop_app.latest_audited_race_day_manifest",
                    return_value=None,
                ),
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
        self.assertTrue(str(webview.loaded_url).startswith("http://127.0.0.1:"))
        self.assertIn("監査済みデータ", webview.options["html"])
        self.assertEqual(webview.options["min_size"], (1051, 720))


if __name__ == "__main__":
    unittest.main()
