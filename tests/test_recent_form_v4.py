import json
import math
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from keiba_prediction_lab.features import generate_features, Surface
from keiba_prediction_lab.model import RECENT_FORM_V4_FEATURE_NAMES, EVIDENCE_NEUTRAL_V3_FEATURE_NAMES, _raw_features
from keiba_prediction_lab.model_artifact import ModelTrainingParameters, train_local_model_artifact, save_trained_model_artifact, load_trained_model_artifact
from test_features import performance, targets, history, OBSERVED
from test_model_artifact import _write_training


class RecentFormV4Test(unittest.TestCase):
    def test_recent_limit_decay_and_order_independence(self):
        past = []
        for i in range(6):
            t = OBSERVED - timedelta(days=180 - i * 20)
            past += [performance(f'r{i}', t, 'horse-a', 1 if i == 0 else 4),
                     performance(f'r{i}', t, 'other', 2 if i == 0 else 1)]
        row = generate_features(tuple(past), targets())[0]
        # The only win is outside the most recent five starts.
        self.assertAlmostEqual(row.recent_reciprocal_finish, .25)
        self.assertEqual(row.recent_top3_rate, 0)
        self.assertEqual(generate_features(tuple(reversed(past)), targets())[0], row)
        # A recent win carries more weight than an older loss.
        past[-2] = replace(past[-2], finish_position=1)
        newer = generate_features(tuple(past), targets())[0]
        weights = [2 ** (-days / 90) for days in [160, 140, 120, 100, 80]]
        expected = (sum(weights[:-1]) / 4 + weights[-1]) / sum(weights)
        self.assertAlmostEqual(newer.recent_reciprocal_finish, expected)

    def test_unknown_history_and_body_change_are_distinct(self):
        a, new = generate_features(history(), targets())
        self.assertFalse(a.recent_form_missing)
        self.assertTrue(new.recent_form_missing)
        self.assertEqual(new.recent_reciprocal_finish, 0)
        self.assertIsNone(new.body_weight_change_pct)
        self.assertAlmostEqual(a.body_weight_change_pct, 100 * (482 / 480 - 1))
        self.assertAlmostEqual(a.distance_change_km, .2)

    def test_pair_and_surface_change_are_from_past_only(self):
        ts = (replace(targets()[0], horse_id='horse-b', jockey_id='jockey-b'), targets()[1])
        same = generate_features(history(), ts)[0]
        changed = generate_features(history(), (replace(ts[0], jockey_id='unseen'), ts[1]))[0]
        self.assertTrue(same.surface_changed)
        self.assertAlmostEqual(same.distance_change_km, .6)
        self.assertGreater(same.horse_jockey_top3_rate, changed.horse_jockey_top3_rate)
        future = replace(history()[0], result_known_at=OBSERVED + timedelta(seconds=1))
        with self.assertRaisesRegex(ValueError, 'known'):
            generate_features((future, *history()[1:]), ts)

    def test_old_vectors_unchanged_and_v4_rejects_nonfinite_values(self):
        row = generate_features(history(), targets())[0]
        altered = replace(row, recent_reciprocal_finish=.2, body_weight_change_pct=4)
        self.assertEqual(_raw_features(row, EVIDENCE_NEUTRAL_V3_FEATURE_NAMES), _raw_features(altered, EVIDENCE_NEUTRAL_V3_FEATURE_NAMES))
        self.assertEqual(len(_raw_features(row, RECENT_FORM_V4_FEATURE_NAMES)), 23)
        for changed in [replace(row, distance_change_km=math.inf), replace(row, recent_top3_rate=math.nan), replace(row, body_weight_change_pct=math.inf)]:
            with self.assertRaises(ValueError):
                _raw_features(changed, RECENT_FORM_V4_FEATURE_NAMES)

    def test_artifact_round_trip_and_schema_flags(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td); _write_training(p/'training.csv')
            artifact = train_local_model_artifact(p/'training.csv', parameters=ModelTrainingParameters(epochs=5, recent_form_v4=True, calibration_races=1))
            save_trained_model_artifact(artifact,p/'model.json')
            loaded = load_trained_model_artifact(p/'model.json')
            self.assertEqual(loaded.model, artifact.model)
            self.assertEqual(loaded.model.feature_names, RECENT_FORM_V4_FEATURE_NAMES)
            self.assertEqual(json.loads((p/'model.json').read_text())['schema_version'],'1.4')
            with self.assertRaises(ValueError):
                replace(loaded, parameters=replace(loaded.parameters,recent_form_v4=False))
        for kwargs in [dict(track_condition_v2=True),dict(evidence_neutral_v3=True),dict(recent_form_v4=1)]:
            with self.assertRaises(ValueError):
                ModelTrainingParameters(**({'recent_form_v4':True} | kwargs))
