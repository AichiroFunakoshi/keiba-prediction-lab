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
    TRAINING_COLUMNS,
    build_local_feature_bundle,
    build_time_safe_training_bundle,
    load_training_csv,
    load_targets_csv,
    save_local_feature_bundle,
    save_local_training_bundle,
)
from keiba_prediction_lab.model import fit_conditional_logit
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


def _training_race(
    race_id: str, scheduled_at: str, observed_at: str, result_known_at: str
) -> list[dict[str, str]]:
    return [
        {
            **row,
            "race_id": race_id,
            "scheduled_at": scheduled_at,
            "observed_at": observed_at,
            "result_known_at": result_known_at,
        }
        for row in _history_rows()
    ]


def _training_rows() -> list[dict[str, str]]:
    return (
        _training_race(
            "race-1", "2026-01-01T12:00:00+09:00",
            "2026-01-01T10:00:00+09:00", "2026-01-01T12:10:00+09:00",
        )
        + _training_race(
            "race-2", "2026-01-08T12:00:00+09:00",
            "2026-01-08T10:00:00+09:00", "2026-01-08T12:10:00+09:00",
        )
        + _training_race(
            "race-3", "2026-01-15T12:00:00+09:00",
            "2026-01-15T10:00:00+09:00", "2026-01-15T12:10:00+09:00",
        )
    )


class LocalAdapterTest(unittest.TestCase):
    def test_builds_time_safe_training_rows_and_fits_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "training.csv"
            _write(path, TRAINING_COLUMNS, _training_rows())

            bundle = build_time_safe_training_bundle(path)
            model = fit_conditional_logit(bundle.rows, epochs=5)

        starts_by_race = {
            race_id: {row.features.horse_starts for row in bundle.rows
                      if row.features.race_id == race_id}
            for race_id in ("race-1", "race-2", "race-3")
        }
        self.assertEqual(starts_by_race, {
            "race-1": {0}, "race-2": {1}, "race-3": {2},
        })
        self.assertEqual(model.trained_through.isoformat(), "2026-01-15T10:00:00+09:00")

    def test_delayed_result_is_not_partially_available(self) -> None:
        rows = _training_rows()
        for row in rows:
            if row["race_id"] == "race-1":
                row["result_known_at"] = "2026-01-08T11:00:00+09:00"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "training.csv"
            _write(path, TRAINING_COLUMNS, rows)
            bundle = build_time_safe_training_bundle(path)

        race_2 = [row for row in bundle.rows if row.features.race_id == "race-2"]
        race_3 = [row for row in bundle.rows if row.features.race_id == "race-3"]
        self.assertEqual({row.features.horse_starts for row in race_2}, {0})
        self.assertEqual({row.features.horse_starts for row in race_3}, {2})

    def test_training_output_is_deterministic_when_rows_are_shuffled(self) -> None:
        rows = _training_rows()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ordered = root / "ordered.csv"
            reversed_path = root / "reversed.csv"
            _write(ordered, TRAINING_COLUMNS, rows)
            _write(reversed_path, TRAINING_COLUMNS, list(reversed(rows)))

            first = build_time_safe_training_bundle(ordered)
            second = build_time_safe_training_bundle(reversed_path)

        self.assertEqual(first.rows, second.rows)
        self.assertNotEqual(first.training_sha256, second.training_sha256)

    def test_training_requires_explicit_valid_observation_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "training.csv"
            rows = _training_rows()
            rows[0]["observed_at"] = "2026-01-01T12:00:00+09:00"
            _write(path, TRAINING_COLUMNS, rows)

            with self.assertRaisesRegex(ValueError, "before scheduled_at"):
                load_training_csv(path)

    def test_cli_prepares_immutable_training_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "training.csv"
            output = root / "training.json"
            _write(source, TRAINING_COLUMNS, _training_rows())
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "prepare-training", str(source), "--output", str(output),
                ])
            summary = json.loads(stdout.getvalue())
            artifact = json.loads(output.read_text(encoding="utf-8"))
            bundle = build_time_safe_training_bundle(source)
            with self.assertRaises(FileExistsError):
                save_local_training_bundle(bundle, output)

        self.assertEqual(exit_code, 0)
        self.assertEqual(summary["training_row_count"], 9)
        self.assertEqual(summary["training_sha256"], artifact["training_sha256"])

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

    def test_target_file_rejects_duplicate_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "targets.csv"
            _write(path, TARGET_COLUMNS, _target_rows())
            lines = path.read_text(encoding="utf-8").splitlines()
            lines[0] += ",observed_at"
            lines[1] += ",2026-02-01T10:00:00+09:00"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "duplicates=.*observed_at"):
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
