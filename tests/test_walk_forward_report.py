import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from keiba_prediction_lab.cli import main
from keiba_prediction_lab.local_adapter import TRAINING_COLUMNS
from tests.test_local_pipeline import _write_csv


def _training(path: Path) -> None:
    rows = []
    for day in range(1, 13):
        for horse in range(1, 6):
            rows.append({
                "race_id": f"race-{day:02d}",
                "scheduled_at": f"2026-01-{day:02d}T12:00:00+09:00",
                "observed_at": f"2026-01-{day:02d}T10:00:00+09:00",
                "result_known_at": f"2026-01-{day:02d}T12:10:00+09:00",
                "horse_id": f"horse-{horse}",
                "jockey_id": f"jockey-{horse}",
                "trainer_id": f"trainer-{horse}",
                "venue": "Tokyo" if day % 2 else "Kyoto",
                "surface": "turf", "track_condition": "good",
                "distance_m": "1600", "post_position": str(horse),
                "carried_weight_kg": "56", "body_weight_kg": str(470 + horse),
                "finish_position": str(horse),
            })
    _write_csv(path, TRAINING_COLUMNS, rows)


def _windows(path: Path) -> None:
    path.write_text(json.dumps([
        {
            "train_end": "2026-01-04T10:00:00+09:00",
            "calibration_end": "2026-01-06T10:00:00+09:00",
            "evaluation_end": "2026-01-08T10:00:00+09:00",
        },
        {
            "train_end": "2026-01-08T10:00:00+09:00",
            "calibration_end": "2026-01-10T10:00:00+09:00",
            "evaluation_end": "2026-01-12T10:00:00+09:00",
        },
    ]), encoding="utf-8")


class WalkForwardReportTest(unittest.TestCase):
    def test_cli_runs_local_walk_forward_and_saves_protected_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            training = root / "training.csv"
            windows = root / "windows.json"
            report = root / "report.json"
            _training(training)
            _windows(windows)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "evaluate-walk-forward", str(training), str(windows),
                    "--report", str(report),
                ])
            envelope = json.loads(report.read_text(encoding="utf-8"))
            canonical = json.dumps(
                envelope["payload"], ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            )

            second_stdout = io.StringIO()
            with contextlib.redirect_stdout(second_stdout):
                second_exit_code = main([
                    "evaluate-walk-forward", str(training), str(windows),
                    "--report", str(report),
                ])

        self.assertEqual(exit_code, 0)
        self.assertIn("評価レース数: 4", stdout.getvalue())
        self.assertIn("条件付きロジット", stdout.getvalue())
        self.assertEqual(envelope["schema_version"], "1.0")
        self.assertEqual(
            envelope["sha256"],
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(len(envelope["payload"]["folds"]), 2)
        self.assertEqual(second_exit_code, 1)
        self.assertFalse(json.loads(second_stdout.getvalue())["is_valid"])

    def test_cli_rejects_overlapping_windows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            training = root / "training.csv"
            windows = root / "windows.json"
            _training(training)
            _windows(windows)
            values = json.loads(windows.read_text(encoding="utf-8"))
            values[1]["train_end"] = "2026-01-07T10:00:00+09:00"
            windows.write_text(json.dumps(values), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "evaluate-walk-forward", str(training), str(windows),
                ])

        self.assertEqual(exit_code, 1)
        self.assertIn("must not overlap", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
