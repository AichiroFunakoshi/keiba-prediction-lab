import contextlib
import csv
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from keiba_prediction_lab.cli import main
from keiba_prediction_lab.local_adapter import TRAINING_COLUMNS
from keiba_prediction_lab.model_artifact import (
    ModelTrainingParameters,
    load_trained_model_artifact,
    save_trained_model_artifact,
    train_local_model_artifact,
)


def _training_rows() -> list[dict[str, str]]:
    rows = []
    for race_number, day in enumerate((1, 8, 15), start=1):
        for horse in range(1, 4):
            rows.append({
                "race_id": f"race-{race_number}",
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


def _write_training(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted(TRAINING_COLUMNS))
        writer.writeheader()
        writer.writerows(_training_rows())


class ModelArtifactTest(unittest.TestCase):
    def test_parameters_reject_non_numeric_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "prior_strength"):
            ModelTrainingParameters(prior_strength="10")  # type: ignore[arg-type]

    def test_round_trip_preserves_model_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            training = root / "training.csv"
            output = root / "model.json"
            _write_training(training)
            parameters = ModelTrainingParameters(epochs=5)

            artifact = train_local_model_artifact(
                training, parameters=parameters
            )
            digest = save_trained_model_artifact(artifact, output)
            loaded = load_trained_model_artifact(output)

        self.assertEqual(loaded, artifact)
        self.assertEqual(loaded.training_row_count, 9)
        self.assertEqual(loaded.training_race_count, 3)
        self.assertEqual(len(digest), 64)

    def test_cli_trains_immutable_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            training = root / "training.csv"
            output = root / "model.json"
            _write_training(training)
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "train-model", str(training), "--output", str(output),
                    "--epochs", "5",
                ])
            summary = json.loads(stdout.getvalue())
            artifact = load_trained_model_artifact(output)
            with self.assertRaises(FileExistsError):
                save_trained_model_artifact(artifact, output)

        self.assertEqual(exit_code, 0)
        self.assertEqual(summary["training_row_count"], 9)
        self.assertEqual(summary["trained_through"], "2026-01-15T10:00:00+09:00")
        self.assertIsNone(summary["calibrated_through"])

    def test_time_separated_calibrated_model_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            training = root / "training.csv"
            output = root / "model.json"
            _write_training(training)
            artifact = train_local_model_artifact(
                training,
                parameters=ModelTrainingParameters(epochs=5, calibration_races=1),
            )
            save_trained_model_artifact(artifact, output)
            loaded = load_trained_model_artifact(output)

        self.assertEqual(loaded, artifact)
        self.assertEqual(loaded.parameters.calibration_races, 1)
        self.assertEqual(
            loaded.model.calibrated_through.isoformat(),
            "2026-01-15T10:00:00+09:00",
        )
        self.assertEqual(
            loaded.model.trained_through.isoformat(),
            "2026-01-08T10:00:00+09:00",
        )

    def test_same_input_and_parameters_produce_same_payload_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            training = root / "training.csv"
            first_path = root / "first.json"
            second_path = root / "second.json"
            _write_training(training)
            parameters = ModelTrainingParameters(epochs=5)

            first = train_local_model_artifact(training, parameters=parameters)
            second = train_local_model_artifact(training, parameters=parameters)
            first_digest = save_trained_model_artifact(first, first_path)
            second_digest = save_trained_model_artifact(second, second_path)

        self.assertEqual(first, second)
        self.assertEqual(first_digest, second_digest)

    def test_loads_legacy_uncalibrated_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            training = root / "training.csv"
            output = root / "model.json"
            _write_training(training)
            artifact = train_local_model_artifact(
                training, parameters=ModelTrainingParameters(epochs=5)
            )
            save_trained_model_artifact(artifact, output)
            envelope = json.loads(output.read_text(encoding="utf-8"))
            envelope["schema_version"] = "1.0"
            del envelope["payload"]["parameters"]["calibration_races"]
            canonical = json.dumps(
                envelope["payload"], ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            )
            envelope["sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
            output.write_text(json.dumps(envelope), encoding="utf-8")

            loaded = load_trained_model_artifact(output)

        self.assertEqual(loaded.parameters.calibration_races, 0)
        self.assertEqual(loaded.model.model_version, "conditional-logit-v1")

    def test_calibration_must_leave_training_races(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            training = Path(directory) / "training.csv"
            _write_training(training)

            with self.assertRaisesRegex(ValueError, "leave at least one"):
                train_local_model_artifact(
                    training,
                    parameters=ModelTrainingParameters(
                        epochs=5, calibration_races=3
                    ),
                )

    def test_rejects_payload_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            training = root / "training.csv"
            output = root / "model.json"
            _write_training(training)
            artifact = train_local_model_artifact(
                training, parameters=ModelTrainingParameters(epochs=5)
            )
            save_trained_model_artifact(artifact, output)
            envelope = json.loads(output.read_text(encoding="utf-8"))
            envelope["payload"]["model"]["coefficients"][0] += 1.0
            output.write_text(json.dumps(envelope), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "integrity check failed"):
                load_trained_model_artifact(output)

    def test_rejects_incompatible_feature_schema_even_with_updated_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            training = root / "training.csv"
            output = root / "model.json"
            _write_training(training)
            artifact = train_local_model_artifact(
                training, parameters=ModelTrainingParameters(epochs=5)
            )
            save_trained_model_artifact(artifact, output)
            envelope = json.loads(output.read_text(encoding="utf-8"))
            envelope["payload"]["model"]["feature_names"][0] = "unknown"
            canonical = json.dumps(
                envelope["payload"], ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            )
            envelope["sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
            output.write_text(json.dumps(envelope), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "feature schema is incompatible"):
                load_trained_model_artifact(output)


if __name__ == "__main__":
    unittest.main()
