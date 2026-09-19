import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch
from tests.test_race_day_pipeline import _race_day_files
from keiba_prediction_lab.prediction_profile import load_prediction_profile, predict_profile_day
from keiba_prediction_lab.market_blend import load_market_blend_forecast
from keiba_prediction_lab.desktop_app import load_audited_race_day_snapshot, latest_audited_race_day_manifest
from keiba_prediction_lab.jra_web_fetch import SOURCE_ID


class PredictionProfileTest(unittest.TestCase):
    def setup_files(self, root):
        model,history,plan = _race_day_files(root)
        report = root/'evaluation.json';report.write_text('{}')
        payload = dict(schema_version='1.0',profile_id='synthetic',model_path=model.name,
            model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),market_weight=.8,
            activated_at='2026-01-31T10:00:00+09:00',validation_path=report.name,
            validation_sha256=hashlib.sha256(report.read_bytes()).hexdigest(),validation_summary='合成テスト')
        profile = root/'profile.json';profile.write_text(json.dumps(payload))
        snapshot = root/'snapshot';snapshot.mkdir();(snapshot/'cards.json').write_text('[]')
        (snapshot/'acquisition-manifest.json').write_text(json.dumps(dict(source_id=SOURCE_ID,private_use_only=True,
            acquired_at='2026-02-01T10:02:00+09:00',outputs={'cards.json':{'sha256':hashlib.sha256(b'[]').hexdigest()}})))
        return model,history,plan,profile,snapshot,payload

    def test_rejects_tampering_nonfinite_and_early_activation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);model,_,_,p,_,payload=self.setup_files(root)
            self.assertEqual(load_prediction_profile(p).market_weight,.8)
            for changes in [dict(market_weight=True),dict(market_weight=float('nan')),dict(market_weight=1),dict(activated_at='2020-01-01T00:00:00+09:00'),dict(model_sha256='bad'),dict(validation_sha256='bad')]:
                p.write_text(json.dumps({**payload,**changes}))
                with self.assertRaises(ValueError):load_prediction_profile(p)
            p.write_text(json.dumps(payload));model.write_text('{}')
            with self.assertRaises(ValueError):load_prediction_profile(p)

    def test_atomic_paired_output_and_display(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);_,history,plan,p,snap,payload=self.setup_files(root)
            comparison=root/'comparison-profile.json';comparison.write_text(json.dumps({**payload,'profile_id':'challenger','market_weight':.95}))
            p.write_text(json.dumps({**payload,'comparison_profile':comparison.name}))
            import csv
            targets=json.loads(plan.read_text())['races'];odds={}
            for row in targets:
                with (root/row['targets']).open() as handle:
                    runners=list(csv.DictReader(handle))
                odds[runners[0]['race_id']]={r['horse_id']:float(2+i) for i,r in enumerate(runners)}
            output=root/'output'
            with patch('keiba_prediction_lab.market_blend._load_cards',return_value=odds):
                receipt=predict_profile_day(p,history,plan,snap,output,frozen_at=datetime.fromisoformat('2026-02-01T10:05:00+09:00'))
                self.assertEqual(load_market_blend_forecast(output/'market-blend.json').market_weight,.8)
                self.assertEqual(load_market_blend_forecast(output/'comparison/market-blend.json').market_weight,.95)
                with self.assertRaises(FileExistsError):predict_profile_day(p,history,plan,snap,output,frozen_at=datetime.fromisoformat('2026-02-01T10:05:00+09:00'))
                failed=root/'failed'
                with self.assertRaises(ValueError):predict_profile_day(p,history,plan,snap,failed,frozen_at=datetime.fromisoformat('2026-02-01T10:01:00+09:00'))
                self.assertFalse(failed.exists())
            state=load_audited_race_day_snapshot(output/'race-day.json',market_blend_forecast=output/'market-blend.json')
            self.assertIsNotNone(state.comparison_race_day)
            self.assertEqual(latest_audited_race_day_manifest(root),output/'race-day.json')
            self.assertEqual(receipt['comparison']['market_weight'],.95)
            p.write_text(json.dumps({**payload,'market_weight':0,'comparison_profile':comparison.name}))
            independent=root/'independent-with-market-comparison'
            with patch('keiba_prediction_lab.market_blend._load_cards',return_value=odds):
                predict_profile_day(p,history,plan,snap,independent,frozen_at=datetime.fromisoformat('2026-02-01T10:05:00+09:00'))
            state=load_audited_race_day_snapshot(independent/'race-day.json')
            self.assertIsNotNone(state.comparison_race_day)
            self.assertIsNotNone(state.prediction_explanations)
            self.assertFalse((independent/'market-blend.json').exists())
