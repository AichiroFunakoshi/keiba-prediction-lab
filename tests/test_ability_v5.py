import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from datetime import timedelta
from test_features import performance, targets, history, OBSERVED
from test_model_artifact import _write_training
from keiba_prediction_lab.features import generate_features
from keiba_prediction_lab.model import ABILITY_V5_FEATURE_NAMES, RECENT_FORM_V4_FEATURE_NAMES, _raw_features
from keiba_prediction_lab.model_artifact import ModelTrainingParameters, train_local_model_artifact, save_trained_model_artifact, load_trained_model_artifact


class AbilityV5Test(unittest.TestCase):
    def test_field_size_ties_opponents_and_permutation(self):
        t=OBSERVED-timedelta(days=30)
        old=(performance('r',t,'horse-a',1),performance('r',t,'other',2),performance('r',t,'third',2))
        rows=generate_features(old,targets())
        self.assertAlmostEqual(rows[0].opponent_rating,.125)
        self.assertEqual(rows[0].recent_field_percentile,1)
        self.assertEqual(rows,generate_features(tuple(reversed(old)),targets()))
        self.assertEqual(rows[1].opponent_rating,0)
        self.assertEqual(rows[1].recent_field_percentile,.5)
        # A later loss against a previously beaten opponent lowers the rating.
        later=(performance('r2',t+timedelta(days=10),'horse-a',2),performance('r2',t+timedelta(days=10),'other',1))
        self.assertLess(generate_features(old+later,targets())[0].opponent_rating,rows[0].opponent_rating)
        with self.assertRaises(ValueError):generate_features((replace(old[0],result_known_at=OBSERVED+timedelta(seconds=1)),*old[1:]),targets())

    def test_vector_compatibility_finite_and_artifact_round_trip(self):
        row=generate_features(history(),targets())[0]
        changed=replace(row,opponent_rating=math.inf)
        self.assertEqual(_raw_features(row,RECENT_FORM_V4_FEATURE_NAMES),_raw_features(changed,RECENT_FORM_V4_FEATURE_NAMES))
        with self.assertRaises(ValueError):_raw_features(changed,ABILITY_V5_FEATURE_NAMES)
        self.assertEqual(len(_raw_features(row,ABILITY_V5_FEATURE_NAMES)),29)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);training=root/'training.csv';_write_training(training)
            model=train_local_model_artifact(training,parameters=ModelTrainingParameters(ability_v5=True,epochs=5))
            path=root/'model.json';save_trained_model_artifact(model,path)
            self.assertEqual(load_trained_model_artifact(path),model)
        with self.assertRaises(ValueError):ModelTrainingParameters(ability_v5=True,recent_form_v4=True)
