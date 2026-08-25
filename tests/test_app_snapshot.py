import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from keiba_prediction_lab.app_snapshot import build_read_only_app_snapshot
from keiba_prediction_lab.cli import main
from tests.test_bundle_audit import _saved_bundle
from tests.test_walk_forward_report import _training, _windows


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


class ReadOnlyAppSnapshotTest(unittest.TestCase):
    def test_builds_ui_data_without_modifying_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _saved_bundle(root)
            walk_forward = _walk_forward_report(root)
            before_prediction = {
                path.name: path.read_bytes() for path in prediction.iterdir()
            }
            before_walk_forward = walk_forward.read_bytes()
            snapshot = build_read_only_app_snapshot(
                prediction_directory=prediction,
                walk_forward_report=walk_forward,
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


if __name__ == "__main__":
    unittest.main()
