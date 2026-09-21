"""Synthetic source binding and unordered distribution validation."""
import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import csv
from tests import test_prediction_profile as fixtures
from keiba_prediction_lab.trio_model import TrioSetModel, FEATURES, save_model
from keiba_prediction_lab.prediction_profile import predict_profile_day
from keiba_prediction_lab.desktop_app import load_audited_race_day_snapshot
from keiba_prediction_lab.trio_primary import save_direct_trio_display


class DirectTrioTests(unittest.TestCase):
    def test_primary_and_comparison_use_new_set_distribution_and_reject_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, history, plan, profile, market, settings = fixtures.PredictionProfileTest().setup_files(root)
            model = root/'trio.json'
            save_model(TrioSetModel((0.,)*len(FEATURES), (0.,)*len(FEATURES), (1.,)*len(FEATURES), datetime.fromisoformat('2025-01-01T00:00:00+00:00')), model, training_sha256='a'*64)
            settings.update(market_weight=0, selection_objective='trio', trio_shadow_model=model.name, trio_shadow_sha256=hashlib.sha256(model.read_bytes()).hexdigest())
            profile.write_text(json.dumps(settings))
            odds = {}
            for entry in json.loads(plan.read_text())['races']:
                with (root/entry['targets']).open() as handle:
                    rows = list(csv.DictReader(handle))
                odds[rows[0]['race_id']] = {r['horse_id']:float(i+2) for i,r in enumerate(rows)}
            frozen = datetime.fromisoformat('2026-02-01T10:05:00+09:00')
            output = root/'day'
            with patch('keiba_prediction_lab.trio_shadow._load_cards', return_value=odds), patch('keiba_prediction_lab.trio_shadow.datetime') as clock, patch('keiba_prediction_lab.trio_primary.datetime') as primary_clock:
                for c in (clock, primary_clock):
                    c.now.return_value=frozen
                    c.fromisoformat.side_effect=datetime.fromisoformat
                predict_profile_day(profile, history, plan, market, output, frozen_at=frozen)
                save_direct_trio_display(output, note='synthetic weather assumption')
            before=(output/'race-day.json').read_bytes()
            state=load_audited_race_day_snapshot(output/'race-day.json').to_dict()
            for day, weight in ((state['race_day'],0.),(state['comparison_race_day'],.2)):
                for venue in day['venues']:
                    for race in venue['races']:
                        trio=race['prediction']['trio']
                        self.assertEqual(trio['market_weight'], weight)
                        self.assertAlmostEqual(sum(trio['inclusion_probabilities'].values()),3.)
                        self.assertEqual(trio['policy_version'],'conditional-trio-set-v1')
            self.assertEqual(before,(output/'race-day.json').read_bytes())
            shadow=output/'trio-model-shadow.json'
            shadow.write_text(shadow.read_text()+' ')
            with self.assertRaises(ValueError):load_audited_race_day_snapshot(output/'race-day.json')
            pointer=output/'direct-trio-display.json'
            value=json.loads(pointer.read_text());value['shadow_sha256']=hashlib.sha256(shadow.read_bytes()).hexdigest();pointer.write_text(json.dumps(value))
            e=json.loads(shadow.read_text());e['payload']['races'][0]['variants'][0]['probabilities'].pop()
            raw=json.dumps(e['payload'],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode();e['sha256']=hashlib.sha256(raw).hexdigest();shadow.write_text(json.dumps(e));value['shadow_sha256']=hashlib.sha256(shadow.read_bytes()).hexdigest();pointer.write_text(json.dumps(value))
            with self.assertRaises(ValueError):load_audited_race_day_snapshot(output/'race-day.json')
