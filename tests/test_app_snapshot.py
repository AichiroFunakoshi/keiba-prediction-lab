import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime
from pathlib import Path

from keiba_prediction_lab.app_snapshot import build_read_only_app_snapshot
from keiba_prediction_lab.cli import main
from keiba_prediction_lab.win5 import (
    build_win5_forecast_from_legs,
    save_win5_forecast,
)
from tests.test_bundle_audit import _saved_bundle
from tests.test_walk_forward_report import _training, _windows
from tests.test_win5 import JST, _legs


def _walk_forward_report(root: Path) -> Path:
    training = root / "training.csv"
    windows = root / "windows.json"
    report = root / "walk-forward.json"
    _training(training)
    _windows(windows)
    with contextlib.redirect_stdout(io.StringIO()):
        exit_code = main([
            "evaluate-walk-forward", str(training), str(windows),
            "--report", str(report),
        ])
    if exit_code != 0:
        raise AssertionError("walk-forward setup failed")
    return report


def _win5_forecast(root: Path) -> Path:
    path = root / "win5.json"
    forecast = build_win5_forecast_from_legs(
        _legs(), frozen_at=datetime(2026, 8, 30, 13, 0, tzinfo=JST)
    )
    save_win5_forecast(forecast, path)
    return path


def _race_day_manifest(root: Path, prediction: Path) -> Path:
    path = root / "race-day.json"
    path.write_text(json.dumps({
        "schema_version": "1.0",
        "race_date": "2026-02-01",
        "venues": [{
            "venue": "東京",
            "races": [{
                "race_number": 1,
                "prediction_bundle": str(prediction),
                "runner_display": [{
                    "horse_id": "horse-1",
                    "horse_number": 5,
                    "horse_name": "サクラエンパイア",
                    "frame_number": 3,
                }],
            }],
        }],
    }), encoding="utf-8")
    return path


class ReadOnlyAppSnapshotTest(unittest.TestCase):
    def test_builds_ui_data_without_modifying_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _saved_bundle(root)
            walk_forward = _walk_forward_report(root)
            win5 = _win5_forecast(root)
            race_day = _race_day_manifest(root, prediction)
            before_prediction = {
                path.name: path.read_bytes() for path in prediction.iterdir()
            }
            before_walk_forward = walk_forward.read_bytes()
            snapshot = build_read_only_app_snapshot(
                prediction_directory=prediction,
                walk_forward_report=walk_forward,
                win5_forecast=win5,
                race_day_manifest=race_day,
            )
            payload = snapshot.to_dict()
            after_prediction = {
                path.name: path.read_bytes() for path in prediction.iterdir()
            }
            after_walk_forward = walk_forward.read_bytes()

        prediction_payload = payload["prediction"]
        self.assertEqual(prediction_payload["race_id"], "target-1")
        self.assertEqual(prediction_payload["actual"]["stake_yen"], 100)
        self.assertEqual(
            {row["stake_yen"] for row in prediction_payload["shadow_portfolios"]},
            {0},
        )
        self.assertEqual(
            {row["stake_yen"] for row in prediction_payload["bet_type_candidates"]},
            {0},
        )
        self.assertEqual(payload["walk_forward"]["evaluation_race_count"], 4)
        self.assertEqual(payload["win5"]["stake_yen"], 0)
        self.assertEqual(len(payload["win5"]["legs"]), 5)
        self.assertEqual(payload["win5"]["selection"][0], "winner-1")
        self.assertEqual(payload["race_day"]["venues"][0]["venue"], "東京")
        self.assertEqual(
            payload["race_day"]["venues"][0]["races"][0]["race_number"], 1
        )
        display = payload["race_day"]["venues"][0]["races"][0]["runner_display"][0]
        self.assertEqual(display["horse_number"], 5)
        self.assertEqual(display["horse_name"], "サクラエンパイア")
        self.assertEqual(display["frame_number"], 3)
        self.assertEqual(before_prediction, after_prediction)
        self.assertEqual(before_walk_forward, after_walk_forward)

    def test_cli_emits_structured_state_from_prediction_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prediction = _saved_bundle(Path(directory))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "inspect-app-state",
                    "--prediction-bundle", str(prediction),
                ])
            payload = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["is_valid"])
        self.assertIsNone(payload["walk_forward"])
        self.assertEqual(payload["prediction"]["actual"]["stake_yen"], 100)

    def test_cli_emits_win5_shadow_without_purchase_stake(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            win5 = _win5_forecast(Path(directory))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "inspect-app-state",
                    "--win5-forecast", str(win5),
                ])
            payload = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertIsNone(payload["prediction"])
        self.assertEqual(payload["win5"]["purchase_status"], "shadow_only")
        self.assertEqual(payload["win5"]["stake_yen"], 0)
        self.assertEqual(len(payload["win5"]["selection"]), 5)

    def test_rejects_missing_artifact_selection(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["inspect-app-state"])

        self.assertEqual(exit_code, 1)
        self.assertFalse(json.loads(stdout.getvalue())["is_valid"])

    def test_rejects_tampered_prediction_before_building_ui_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prediction = _saved_bundle(Path(directory))
            (prediction / "actual.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                build_read_only_app_snapshot(prediction_directory=prediction)

    def test_rejects_tampered_win5_before_building_ui_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            win5 = _win5_forecast(Path(directory))
            win5.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                build_read_only_app_snapshot(win5_forecast=win5)

    def test_rejects_race_day_date_mismatch_and_duplicate_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _saved_bundle(root)
            manifest = _race_day_manifest(root, prediction)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["race_date"] = "2026-02-02"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match"):
                build_read_only_app_snapshot(race_day_manifest=manifest)

            payload["race_date"] = "2026-02-01"
            payload["venues"][0]["races"].append(
                payload["venues"][0]["races"][0].copy()
            )
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unique"):
                build_read_only_app_snapshot(race_day_manifest=manifest)

    def test_race_day_resolves_relative_bundle_and_rejects_duplicate_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _saved_bundle(root)
            manifest = _race_day_manifest(root, prediction)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["venues"][0]["races"][0]["prediction_bundle"] = "prediction"
            manifest.write_text(json.dumps(payload), encoding="utf-8")

            snapshot = build_read_only_app_snapshot(race_day_manifest=manifest)
            self.assertEqual(snapshot.race_day.venues[0].races[0].race_number, 1)

            manifest.write_text(
                '{"schema_version":"1.0","schema_version":"1.0",'
                '"race_date":"2026-02-01","venues":[]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate key"):
                build_read_only_app_snapshot(race_day_manifest=manifest)

    def test_rejects_invalid_runner_display_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _saved_bundle(root)
            manifest = _race_day_manifest(root, prediction)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            display = payload["venues"][0]["races"][0]["runner_display"]

            display[0]["horse_id"] = "unknown"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "predicted runner"):
                build_read_only_app_snapshot(race_day_manifest=manifest)

            display[0]["horse_id"] = "horse-1"
            display[0]["frame_number"] = 9
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "frame_number"):
                build_read_only_app_snapshot(race_day_manifest=manifest)

    def test_preserves_every_configured_venue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _saved_bundle(root)
            manifest = _race_day_manifest(root, prediction)
            original = build_read_only_app_snapshot(
                race_day_manifest=manifest
            ).race_day.venues[0].races[0].prediction
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            template = payload["venues"][0]
            payload["venues"] = [
                {"venue": venue, "races": [{
                    **template["races"][0],
                    "prediction_bundle": f"bundle-{index}",
                }]}
                for index, venue in enumerate(("新潟", "中京", "札幌"), start=1)
            ]
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            predictions = [
                original.__class__(
                    race_id=f"race-{index}",
                    scheduled_at=original.scheduled_at,
                    frozen_at=original.frozen_at,
                    model_version=original.model_version,
                    input_data_version=original.input_data_version,
                    runners=original.runners,
                    actual_selection=original.actual_selection,
                    actual_stake_yen=original.actual_stake_yen,
                    shadow_portfolios=original.shadow_portfolios,
                    bet_type_candidates=original.bet_type_candidates,
                )
                for index in range(1, 4)
            ]
            with patch(
                "keiba_prediction_lab.app_snapshot._prediction_snapshot",
                side_effect=predictions,
            ):
                snapshot = build_read_only_app_snapshot(race_day_manifest=manifest)

        self.assertEqual(
            [venue.venue for venue in snapshot.race_day.venues],
            ["新潟", "中京", "札幌"],
        )

if __name__ == "__main__":
    unittest.main()
