import csv
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from keiba_prediction_lab.local_adapter import (
    HISTORY_COLUMNS,
    TARGET_COLUMNS,
    build_local_feature_bundle,
    load_targets_csv,
    save_local_feature_bundle,
)
from keiba_prediction_lab.cli import main


def _write(path: Path, columns: frozenset[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted(columns))
        writer.writeheader()
        writer.writerows(rows)


def _history_rows() -> list[dict[str, str]]:
    return [
        {
            "race_id": "past-1", "scheduled_at": "2026-01-01T12:00:00+09:00",
            "result_known_at": "2026-01-01T12:10:00+09:00", "horse_id": f"horse-{i}",
            "jockey_id": f"jockey-{i}", "trainer_id": f"trainer-{i}",
            "venue": "Tokyo", "surface": "turf", "track_condition": "good",
            "distance_m": "1600", "post_position": str(i),
            "carried_weight_kg": "56", "body_weight_kg": str(470 + i),
            "finish_position": str(i),
        }
        for i in range(1, 4)
    ]


def _target_rows() -> list[dict[str, str]]:
    return [
        {
            "race_id": "target-1", "scheduled_at": "2026-02-01T12:00:00+09:00",
            "observed_at": "2026-02-01T10:00:00+09:00", "horse_id": f"horse-{i}",
            "jockey_id": f"jockey-{i}", "trainer_id": f"trainer-{i}",
            "venue": "Tokyo", "surface": "turf", "track_condition": "good",
            "distance_m": "1600", "post_position": str(i),
            "carried_weight_kg": "56", "body_weight_kg": "" if i == 3 else str(480 + i),
        }
        for i in range(1, 4)
    ]


class LocalAdapterTest(unittest.TestCase):
    def test_cli_prepares_feature_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "history.csv"
            targets = root / "targets.csv"
            output = root / "features.json"
            _write(history, HISTORY_COLUMNS, _history_rows())
            _write(targets, TARGET_COLUMNS, _target_rows())
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "prepare-features", str(history), str(targets),
                    "--output", str(output),
                ])
            summary = json.loads(stdout.getvalue())
            artifact = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(summary["feature_count"], 3)
        self.assertEqual(
            summary["input_data_version"], artifact["input_data_version"]
        )

    def test_builds_versioned_features_from_separate_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "history.csv"
            targets = root / "targets.csv"
            output = root / "features.json"
            _write(history, HISTORY_COLUMNS, _history_rows())
            _write(targets, TARGET_COLUMNS, _target_rows())

            bundle = build_local_feature_bundle(history, targets)
            save_local_feature_bundle(bundle, output)
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(len(bundle.features), 3)
        self.assertEqual(bundle.features[0].horse_starts, 1)
        self.assertIsNone(bundle.features[2].body_weight_kg)
        self.assertTrue(bundle.input_data_version.startswith("sha256:"))
        self.assertEqual(payload["input_data_version"], bundle.input_data_version)

    def test_target_file_rejects_result_column(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "targets.csv"
            columns = TARGET_COLUMNS | {"finish_position"}
            rows = [dict(_target_rows()[0], finish_position="1")]
            _write(path, columns, rows)

            with self.assertRaisesRegex(ValueError, "unexpected=.*finish_position"):
                load_targets_csv(path)

    def test_rejects_history_result_not_known_at_observation_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "history.csv"
            targets = root / "targets.csv"
            rows = _history_rows()
            rows[0]["result_known_at"] = "2026-02-01T10:00:01+09:00"
            _write(history, HISTORY_COLUMNS, rows)
            _write(targets, TARGET_COLUMNS, _target_rows())

            with self.assertRaisesRegex(ValueError, "known by observed_at"):
                build_local_feature_bundle(history, targets)

    def test_output_cannot_be_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "history.csv"
            targets = root / "targets.csv"
            output = root / "features.json"
            _write(history, HISTORY_COLUMNS, _history_rows())
            _write(targets, TARGET_COLUMNS, _target_rows())
            bundle = build_local_feature_bundle(history, targets)
            save_local_feature_bundle(bundle, output)

            with self.assertRaises(FileExistsError):
                save_local_feature_bundle(bundle, output)


if __name__ == "__main__":
    unittest.main()
