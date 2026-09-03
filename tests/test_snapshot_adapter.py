import contextlib
import io
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from keiba_prediction_lab.cli import main
from keiba_prediction_lab.local_adapter import (
    build_local_feature_bundle,
    build_time_safe_training_bundle,
    load_targets_csv,
)
from keiba_prediction_lab.snapshot_adapter import (
    IDENTITY_SCHEME,
    _write_outputs,
    convert_history_snapshot,
    convert_target_snapshot,
)


JST = ZoneInfo("Asia/Tokyo")


def _history_snapshot() -> list[dict[str, object]]:
    races = []
    for race_number, day in enumerate((1, 8, 15), start=1):
        races.append({
            "race_id": f"202601{day:02d}-Tokyo-01",
            "date": f"202601{day:02d}",
            "venue": "Tokyo",
            "race": 1,
            "start": "12:00",
            "distance": 1600,
            "surface": "turf",
            "track_condition": "good",
            "runners": [
                {
                    "finish": horse,
                    "number": horse,
                    "name": name,
                    "jockey": f"Jockey {horse}",
                    "trainer": f"Trainer {horse}",
                    "carried_weight_kg": 56.0,
                    "body_weight_kg": 470 + horse,
                }
                for horse, name in enumerate(("Alpha", "Beta", "Gamma"), start=1)
            ],
        })
    return races


def _target_snapshot() -> list[dict[str, object]]:
    return [{
        "venue": "Tokyo",
        "race": 1,
        "url": "https://example.invalid/race-1",
        "race_name": "Synthetic race",
        "distance": 1600,
        "surface": "turf",
        "start": "12:00",
        "horses": [
            {
                "number": number,
                "name": name,
                "odds": None,
                "popularity": None,
                "trainer": f"Trainer {number}",
                "sire": "Synthetic sire",
                "mare": "Synthetic mare",
                "sex_age": "4",
                "weight": "56.0",
                "jockey": f"☆Jockey {number}",
                "pasts": [],
            }
            for number, name in enumerate(("Alpha", "Beta", "Gamma"), start=1)
        ],
    }]


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class SnapshotAdapterTest(unittest.TestCase):
    def test_failed_output_write_removes_partial_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "converted"
            with patch(
                "keiba_prediction_lab.snapshot_adapter.Path.open",
                side_effect=OSError("synthetic write failure"),
            ):
                with self.assertRaisesRegex(OSError, "synthetic write failure"):
                    _write_outputs(output, {"history.csv": b"header\n"}, {})
            self.assertFalse(output.exists())

    def test_history_and_targets_share_normalized_name_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history_source = root / "history.json"
            target_source = root / "cards.json"
            conditions = root / "conditions.json"
            _write_json(history_source, _history_snapshot())
            _write_json(target_source, _target_snapshot())
            _write_json(conditions, {"default": "good"})

            history_result = convert_history_snapshot(
                history_source,
                root / "converted-history",
                source_id="synthetic-private-snapshot",
                acquired_at=datetime(2026, 2, 1, 9, 0, tzinfo=JST),
            )
            target_result = convert_target_snapshot(
                target_source,
                conditions,
                root / "converted-targets",
                source_id="synthetic-private-snapshot",
                acquired_at=datetime(2026, 2, 1, 9, 0, tzinfo=JST),
                race_date=date(2026, 2, 1),
                observed_at=datetime(2026, 2, 1, 10, 0, tzinfo=JST),
            )
            target_path = target_result.output_paths[0]
            targets = load_targets_csv(target_path)
            feature_bundle = build_local_feature_bundle(
                history_result.output_directory / "history.csv", target_path
            )
            manifest = json.loads(
                history_result.manifest_path.read_text(encoding="utf-8")
            )

        self.assertEqual(targets[0].horse_id, "horse:name:alpha")
        self.assertEqual(targets[0].jockey_id, "jockey:name:jockey 1")
        self.assertNotEqual(targets[0].horse_id, "1")
        self.assertEqual({row.horse_starts for row in feature_bundle.features}, {3})
        self.assertEqual(feature_bundle.horse_history_coverage_count, 3)
        self.assertEqual(feature_bundle.jockey_history_coverage_count, 3)
        self.assertEqual(feature_bundle.trainer_history_coverage_count, 3)
        self.assertEqual(manifest["identity_scheme"], IDENTITY_SCHEME)
        self.assertEqual(
            manifest["assumptions"]["result_known_at"],
            "scheduled_at plus 20 minutes (conservative proxy)",
        )
        self.assertFalse(manifest["network_access_performed"])

    def test_training_conversion_is_time_safe_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "history.json"
            _write_json(source, _history_snapshot())
            result = convert_history_snapshot(
                source,
                root / "converted",
                source_id="synthetic-private-snapshot",
                acquired_at=datetime(2026, 2, 1, 9, 0, tzinfo=JST),
                observation_offset_minutes=5,
                result_delay_minutes=10,
            )
            bundle = build_time_safe_training_bundle(
                result.output_directory / "training.csv"
            )

        starts = {
            race_id: {
                row.features.horse_starts
                for row in bundle.rows
                if row.features.race_id == race_id
            }
            for race_id in ("20260101-Tokyo-01", "20260108-Tokyo-01", "20260115-Tokyo-01")
        }
        self.assertEqual(starts, {
            "20260101-Tokyo-01": {0},
            "20260108-Tokyo-01": {1},
            "20260115-Tokyo-01": {2},
        })

    def test_output_directory_cannot_be_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "history.json"
            output = root / "converted"
            _write_json(source, _history_snapshot())
            kwargs = {
                "source_id": "synthetic-private-snapshot",
                "acquired_at": datetime(2026, 2, 1, 9, 0, tzinfo=JST),
            }
            convert_history_snapshot(source, output, **kwargs)
            with self.assertRaises(FileExistsError):
                convert_history_snapshot(source, output, **kwargs)

    def test_duplicate_normalized_horse_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "history.json"
            payload = _history_snapshot()
            payload[0]["runners"][1]["name"] = "Ａｌｐｈａ"
            _write_json(source, payload)
            with self.assertRaisesRegex(ValueError, "duplicate horse identity"):
                convert_history_snapshot(
                    source,
                    root / "converted",
                    source_id="synthetic-private-snapshot",
                    acquired_at=datetime(2026, 2, 1, 9, 0, tzinfo=JST),
                )

    def test_cli_converts_history_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "history.json"
            output = root / "converted"
            _write_json(source, _history_snapshot())
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "convert-local-history-snapshot",
                    str(source),
                    "--source-id", "synthetic-private-snapshot",
                    "--acquired-at", "2026-02-01T09:00:00+09:00",
                    "--output", str(output),
                ])
            summary = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertEqual(summary["race_count"], 3)
        self.assertEqual(summary["runner_count"], 9)
        self.assertFalse(summary["network_access_performed"])

    def test_cli_conversion_reports_invalid_timestamp_as_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "history.json"
            _write_json(source, _history_snapshot())
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "convert-local-history-snapshot",
                    str(source),
                    "--source-id", "synthetic-private-snapshot",
                    "--acquired-at", "invalid",
                    "--output", str(root / "converted"),
                ])
            summary = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 1)
        self.assertFalse(summary["is_valid"])

    def test_cli_target_conversion_reports_invalid_date_as_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "cards.json"
            conditions = root / "conditions.json"
            _write_json(source, _target_snapshot())
            _write_json(conditions, {"default": "good"})
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "convert-local-target-snapshot",
                    str(source), str(conditions),
                    "--source-id", "synthetic-private-snapshot",
                    "--acquired-at", "2026-02-01T09:00:00+09:00",
                    "--race-date", "invalid",
                    "--observed-at", "2026-02-01T10:00:00+09:00",
                    "--output", str(root / "converted"),
                ])
            summary = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 1)
        self.assertFalse(summary["is_valid"])


if __name__ == "__main__":
    unittest.main()
