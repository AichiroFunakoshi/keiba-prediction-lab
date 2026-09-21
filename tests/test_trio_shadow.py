import csv
import hashlib
import json
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch

from tests.test_prediction_profile import PredictionProfileTest
from keiba_prediction_lab.trio_model import TrioSetModel,FEATURES,save_model
from keiba_prediction_lab.prediction_profile import predict_profile_day,load_prediction_profile


class TrioShadowTests(unittest.TestCase):
    def test_prospective_pair_is_saved_and_past_race_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);_,history,plan,profile,snapshot,payload=PredictionProfileTest().setup_files(root)
            m=TrioSetModel((0.,)*len(FEATURES),(0.,)*len(FEATURES),(1.,)*len(FEATURES),datetime(2025,1,1,tzinfo=timezone.utc))
            model=root/'trio.json';save_model(m,model,training_sha256='a'*64)
            p={**payload,'market_weight':0,'trio_shadow_model':model.name,'trio_shadow_sha256':hashlib.sha256(model.read_bytes()).hexdigest()};profile.write_text(json.dumps(p))
            self.assertTrue(load_prediction_profile(profile).to_dict()['trio_shadow_enabled'])
            odds={}
            for row in json.loads(plan.read_text())['races']:
                with (root/row['targets']).open() as handle:rs=list(csv.DictReader(handle))
                odds[rs[0]['race_id']]={r['horse_id']:float(i+2) for i,r in enumerate(rs)}
            frozen=datetime.fromisoformat('2026-02-01T10:05:00+09:00')
            with patch('keiba_prediction_lab.trio_shadow._load_cards',return_value=odds),patch('keiba_prediction_lab.trio_shadow.datetime') as clock:
                clock.now.return_value=frozen;clock.fromisoformat.side_effect=datetime.fromisoformat
                predict_profile_day(profile,history,plan,snapshot,root/'good',frozen_at=frozen)
                artifact=json.loads((root/'good/trio-model-shadow.json').read_text())['payload']
                self.assertTrue(all([x['market_weight'] for x in row['variants']]==[0.,.2] for row in artifact['races']))
                self.assertTrue(all(x['stake_yen']==0 for row in artifact['races'] for x in row['variants']))
                clock.now.return_value=datetime(2027,1,1,tzinfo=timezone.utc)
                with self.assertRaises(ValueError):predict_profile_day(profile,history,plan,snapshot,root/'past',frozen_at=frozen)
                self.assertFalse((root/'past').exists())
            profile.write_text(json.dumps({**p,'trio_shadow_sha256':'bad'}))
            with self.assertRaises(ValueError):load_prediction_profile(profile)
