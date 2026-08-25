import contextlib
import io
import json
import tempfile
import unittest
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


class ReadOnlyAppSnapshotTest(unittest.TestCase):
    def test_builds_ui_data_without_modifying_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _saved_bundle(root)
            walk_forward = _walk_forward_report(root)
            win5 = _win5_forecast(root)
            before_prediction = {
                path.name: path.read_bytes() for path in prediction.iterdir()
            }
            before_walk_forward = walk_forward.read_bytes()
            snapshot = build_read_only_app_snapshot(
                prediction_directory=prediction,
                walk_forward_report=walk_forward,
                win5_forecast=win5,
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


if __name__ == "__main__":
    unittest.main()
