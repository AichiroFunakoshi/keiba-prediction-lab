import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from keiba_prediction_lab.cli import main
from keiba_prediction_lab.features import generate_features
from keiba_prediction_lab.model import ConditionalLogitModel, EVIDENCE_NEUTRAL_V3_FEATURE_NAMES
from keiba_prediction_lab.model_artifact import ModelTrainingParameters, train_local_model_artifact, save_trained_model_artifact, load_trained_model_artifact
from keiba_prediction_lab.walk_forward_report import evaluate_local_walk_forward
from tests.test_features import history, targets, OBSERVED
from tests.test_model_artifact import _write_training


class EvidenceNeutralV3Test(unittest.TestCase):
    def test_counts_do_not_directly_change_probabilities_but_rates_do(self):
        rows = generate_features(history(), targets())
        n = len(EVIDENCE_NEUTRAL_V3_FEATURE_NAMES)
        model = ConditionalLogitModel((1.0,)*n, (0.0,)*n, (1.0,)*n,
                                      OBSERVED-timedelta(days=1), 'conditional-logit-evidence-neutral-v3')
        original = model.predict(rows)
        changed = (replace(rows[0], horse_starts=1000, jockey_starts=2000,
                           trainer_starts=3000, horse_surface_track_condition_starts=1000), *rows[1:])
        self.assertEqual(original, model.predict(changed))
        changed_rate = (replace(rows[0], jockey_win_rate=1.0), *rows[1:])
        self.assertNotEqual(original, model.predict(changed_rate))
        with self.assertRaisesRegex(ValueError, 'vectors'):
            replace(model, coefficients=(1.0,)*19).predict(rows)

    def test_artifact_round_trip_calibrated_and_uncalibrated_and_version_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory); training = p/'training.csv'; _write_training(training)
            for calibration in (0, 1):
                artifact = train_local_model_artifact(training, parameters=ModelTrainingParameters(
                    epochs=5, calibration_races=calibration, evidence_neutral_v3=True))
                out = p/f'model-{calibration}.json'; save_trained_model_artifact(artifact, out)
                self.assertEqual(artifact, load_trained_model_artifact(out))
                self.assertEqual(len(artifact.model.feature_names), 15)
                envelope = json.loads(out.read_text()); envelope['schema_version']='1.2'
                out.write_text(json.dumps(envelope))
                with self.assertRaisesRegex(ValueError, 'requires artifact schema 1.3'):
                    load_trained_model_artifact(out)

    def test_conflicting_flags_rejected(self):
        with self.assertRaises(ValueError):
            ModelTrainingParameters(track_condition_v2=True, evidence_neutral_v3=True)
        with self.assertRaises(ValueError):
            ModelTrainingParameters(evidence_neutral_v3=1)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(['train-model','unused.csv','--output','unused.json',
                  '--track-condition-v2','--evidence-neutral-v3'])

    def test_local_evaluation_forwards_candidate_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory); training=p/'training.csv'; _write_training(training)
            windows=p/'windows.json'
            windows.write_text(json.dumps([dict(train_end='2026-01-02T00:00:00+09:00',
                calibration_end='2026-01-09T00:00:00+09:00',evaluation_end='2026-01-16T00:00:00+09:00')]))
            report=evaluate_local_walk_forward(training, windows, evidence_neutral_v3=True)
            self.assertEqual(report.result.aggregate_model_score.model_version,
                             'conditional-logit-evidence-neutral-v3-temperature-v1')
            self.assertEqual(report.result.aggregate_model_score.race_count, 1)
