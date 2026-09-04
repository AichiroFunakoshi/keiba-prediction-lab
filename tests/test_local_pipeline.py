import contextlib
import csv
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from keiba_prediction_lab.cli import main
from keiba_prediction_lab.frozen import load_frozen_prediction
from keiba_prediction_lab.input_templates import (
    INPUT_TEMPLATE_FILES,
    create_local_input_templates,
)
from keiba_prediction_lab.local_adapter import (
    HISTORY_COLUMNS,
    TARGET_COLUMNS,
    TRAINING_COLUMNS,
)
from keiba_prediction_lab.local_pipeline import (
    PACE_PROFILE_COLUMNS,
    build_local_race_prediction,
    save_local_pipeline_run,
)
from keiba_prediction_lab.model_artifact import (
    ModelTrainingParameters,
    save_trained_model_artifact,
    train_local_model_artifact,
)


def _write_csv(
    path: Path, columns: frozenset[str], rows: list[dict[str, str]]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted(columns))
        writer.writeheader()
        writer.writerows(rows)


def _training_rows() -> list[dict[str, str]]:
    rows = []
    for race_number, day in enumerate((1, 8, 15), start=1):
        for horse in range(1, 6):
            rows.append({
                "race_id": f"past-{race_number}",
                "scheduled_at": f"2026-01-{day:02d}T12:00:00+09:00",
                "observed_at": f"2026-01-{day:02d}T10:00:00+09:00",
                "result_known_at": f"2026-01-{day:02d}T12:10:00+09:00",
                "horse_id": f"horse-{horse}",
                "jockey_id": f"jockey-{horse}",
                "trainer_id": f"trainer-{horse}",
                "venue": "Tokyo", "surface": "turf",
                "track_condition": "good", "distance_m": "1600",
                "post_position": str(horse), "carried_weight_kg": "56",
                "body_weight_kg": str(470 + horse),
                "finish_position": str(horse),
            })
    return rows


def _target_rows() -> list[dict[str, str]]:
    return [{
        "race_id": "target-1",
        "scheduled_at": "2026-02-01T12:00:00+09:00",
        "observed_at": "2026-02-01T10:00:00+09:00",
        "horse_id": f"horse-{horse}",
        "jockey_id": f"jockey-{horse}",
        "trainer_id": f"trainer-{horse}",
        "venue": "Tokyo", "surface": "turf", "track_condition": "good",
        "distance_m": "1600", "post_position": str(horse),
        "carried_weight_kg": "56", "body_weight_kg": str(480 + horse),
    } for horse in range(1, 6)]


def _profile_rows() -> list[dict[str, str]]:
    styles = ("leader", "presser", "stalker", "closer", "closer")
    return [{
        "race_id": "target-1", "horse_id": f"horse-{horse}",
        "observed_at": "2026-02-01T10:00:00+09:00",
        "running_style": styles[horse - 1],
        "early_speed": str(1.0 - horse * 0.1),
        "late_speed": str(horse * 0.15), "pace_resilience": "0.7",
    } for horse in range(1, 6)]


def _files(root: Path) -> tuple[Path, Path, Path, Path, Path]:
    training = root / "training.csv"
    history = root / "history.csv"
    targets = root / "targets.csv"
    profiles = root / "pace-profiles.csv"
    scenario = root / "pace-scenario.json"
    _write_csv(training, TRAINING_COLUMNS, _training_rows())
    _write_csv(
        history, HISTORY_COLUMNS,
        [{key: value for key, value in row.items() if key != "observed_at"}
         for row in _training_rows()],
    )
    _write_csv(targets, TARGET_COLUMNS, _target_rows())
    _write_csv(profiles, PACE_PROFILE_COLUMNS, _profile_rows())
    scenario.write_text(json.dumps({
        "race_id": "target-1",
        "observed_at": "2026-02-01T10:00:00+09:00",
        "expected_pace": "fast", "confidence": 0.8,
    }), encoding="utf-8")
    model = root / "model.json"
    artifact = train_local_model_artifact(
        training, parameters=ModelTrainingParameters(epochs=5)
    )
    save_trained_model_artifact(artifact, model)
    return model, history, targets, profiles, scenario


class LocalPipelineTest(unittest.TestCase):
    def test_cli_creates_incomplete_protected_input_templates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "inputs"
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "init-input-templates", "--output", str(output),
                ])
            summary = json.loads(stdout.getvalue())
            scenario = json.loads(
                (output / "pace-scenario.json").read_text(encoding="utf-8")
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(tuple(summary["files"]), INPUT_TEMPLATE_FILES)
        self.assertTrue(scenario["race_id"].startswith("_REPLACE_"))

    def test_template_creation_preserves_existing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing"
            output.mkdir()
            marker = output / "user-file.txt"
            marker.write_text("keep", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                create_local_input_templates(output)

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_cli_audits_valid_inputs_without_writing_prediction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _files(root)
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "audit-race-inputs", *(str(path) for path in paths),
                    "--frozen-at", "2026-02-01T10:05:00+09:00",
                    "--require-complete-body-weight",
                ])
            report = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertTrue(report["is_valid"])
        self.assertFalse(report["prediction_saved"])
        self.assertEqual(report["runner_count"], 5)
        self.assertNotIn("actual_ticket", report)

    def test_final_input_audit_rejects_missing_body_weight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = list(_files(root))
            rows = _target_rows()
            rows[2]["body_weight_kg"] = ""
            _write_csv(paths[2], TARGET_COLUMNS, rows)
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "audit-race-inputs", *(str(path) for path in paths),
                    "--frozen-at", "2026-02-01T10:05:00+09:00",
                    "--require-complete-body-weight",
                ])
            report = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["is_valid"])
        self.assertIn("missing 1/5 runners", report["error"])

    def test_preliminary_prediction_still_allows_missing_body_weight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = list(_files(root))
            rows = _target_rows()
            for row in rows:
                row["body_weight_kg"] = ""
            _write_csv(paths[2], TARGET_COLUMNS, rows)

            run = build_local_race_prediction(
                *paths,
                frozen_at=datetime.fromisoformat(
                    "2026-02-01T10:05:00+09:00"
                ),
            )

        self.assertEqual(run.prediction.actual_prediction.race_id, "target-1")

    def test_cli_audit_returns_structured_invalid_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = list(_files(root))
            paths[4].write_text("{}", encoding="utf-8")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "audit-race-inputs", *(str(path) for path in paths),
                    "--frozen-at", "2026-02-01T10:05:00+09:00",
                ])
            report = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["is_valid"])
        self.assertIn("missing", report["error"])

    def test_cli_audit_rejects_header_only_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = list(_files(root))
            _write_csv(paths[1], HISTORY_COLUMNS, [])
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "audit-race-inputs", *(str(path) for path in paths),
                    "--frozen-at", "2026-02-01T10:05:00+09:00",
                ])
            report = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 1)
        self.assertIn("at least one history row", report["error"])

    def test_cli_saves_actual_one_ticket_and_zero_stake_shadows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, history, targets, profiles, scenario = _files(root)
            output = root / "prediction"
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "predict-race", str(model), str(history), str(targets),
                    str(profiles), str(scenario),
                    "--frozen-at", "2026-02-01T10:05:00+09:00",
                    "--require-complete-body-weight",
                    "--output", str(output),
                ])
            summary = json.loads(stdout.getvalue())
            actual = load_frozen_prediction(output / "actual.json")
            provenance = json.loads(
                (output / "input-provenance.json").read_text(encoding="utf-8")
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(summary["stake_yen"], 100)
        self.assertEqual(summary["shadow_stake_yen"], 0)
        self.assertEqual(len(actual.trifecta_tickets), 1)
        self.assertEqual(
            provenance["payload"]["input_data_version"],
            actual.input_data_version,
        )

    def test_same_snapshots_produce_the_same_prediction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = _files(Path(directory))
            frozen_at = datetime.fromisoformat("2026-02-01T10:05:00+09:00")

            first = build_local_race_prediction(*paths, frozen_at=frozen_at)
            second = build_local_race_prediction(*paths, frozen_at=frozen_at)

        self.assertEqual(first, second)

    def test_rejects_pace_snapshot_from_a_different_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = list(_files(root))
            profiles = paths[3]
            rows = _profile_rows()
            rows[0]["observed_at"] = "2026-02-01T10:01:00+09:00"
            _write_csv(profiles, PACE_PROFILE_COLUMNS, rows)

            with self.assertRaisesRegex(ValueError, "target observed_at"):
                build_local_race_prediction(
                    *paths,
                    frozen_at=datetime.fromisoformat(
                        "2026-02-01T10:05:00+09:00"
                    ),
                )

    def test_rejects_duplicate_pace_scenario_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = list(_files(root))
            scenario = paths[4]
            scenario.write_text(
                '{"race_id":"target-1","race_id":"other",'
                '"observed_at":"2026-02-01T10:00:00+09:00",'
                '"expected_pace":"fast","confidence":0.8}',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "duplicate key: race_id"):
                build_local_race_prediction(
                    *paths,
                    frozen_at=datetime.fromisoformat(
                        "2026-02-01T10:05:00+09:00"
                    ),
                )

    def test_rejects_freeze_before_target_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = _files(Path(directory))

            with self.assertRaisesRegex(ValueError, "at or after observed_at"):
                build_local_race_prediction(
                    *paths,
                    frozen_at=datetime.fromisoformat(
                        "2026-02-01T09:59:00+09:00"
                    ),
                )

    def test_existing_output_directory_is_not_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _files(root)
            output = root / "existing"
            output.mkdir()
            marker = output / "user-file.txt"
            marker.write_text("keep", encoding="utf-8")
            run = build_local_race_prediction(
                *paths,
                frozen_at=datetime.fromisoformat(
                    "2026-02-01T10:05:00+09:00"
                ),
            )

            with self.assertRaises(FileExistsError):
                save_local_pipeline_run(run, output)

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
