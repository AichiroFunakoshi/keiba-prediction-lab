import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from datetime import datetime
import test_prediction_profile as profile_tests
from keiba_prediction_lab.prediction_profile import predict_profile_day
from keiba_prediction_lab.prediction_explanation import explain_rows, load_day_explanations
from keiba_prediction_lab.model_artifact import load_trained_model_artifact
from keiba_prediction_lab.local_adapter import build_local_feature_bundle
from keiba_prediction_lab.desktop_app import load_audited_race_day_snapshot


class IndependentExplanationTest(unittest.TestCase):
    def test_independent_never_reads_odds_and_factors_reconstruct_score(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);model,history,plan,profile,snapshot,payload=profile_tests.PredictionProfileTest().setup_files(root)
            profile.write_text(json.dumps({**payload,'market_weight':0}))
            # Deliberately absent market input: independent operation must succeed.
            output=root/'independent';predict_profile_day(profile,history,plan,root/'absent-market',output,frozen_at=datetime.fromisoformat('2026-02-01T10:05:00+09:00'))
            self.assertFalse((output/'market-blend.json').exists())
            state=load_audited_race_day_snapshot(output/'race-day.json')
            self.assertIsNotNone(state.prediction_explanations)
            fb=build_local_feature_bundle(history,root/'targets.csv')
            explained=explain_rows(load_trained_model_artifact(model).model,fb.features)
            average=sum(math.log(r['win_probability']) for r in explained.values())/len(explained)
            for item in explained.values():
                self.assertAlmostEqual(sum(f['contribution'] for f in item['factors']),math.log(item['win_probability'])-average)
            path=output/'prediction-explanations.json';data=json.loads(path.read_text());data['payload']['races']['target-1']['horse-1']['history_starts']=10000;path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):load_day_explanations(output,state.race_day)
