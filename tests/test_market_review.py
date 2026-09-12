import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from keiba_prediction_lab.market_review import review_rows, score_weight, save_market_review, reviewed_weight


def row(identity, day, winner='b', version='v1'):
    return dict(race_id=identity, scheduled_at=f'2099-01-{day:02d}T12:00:00+09:00',
                observed_at=f'2099-01-{day:02d}T09:00:00+09:00',
                result_acquired_at=f'2099-01-{day:02d}T18:00:00+09:00',
                model_version=version, model_sha256='synthetic-model-hash', model={'a':.6,'b':.3,'c':.1},odds={'a':8.,'b':2.,'c':5.},
                winners=[winner],top3=[winner,*[h for h in ['a','b','c'] if h!=winner]])


class MarketReviewTest(unittest.TestCase):
    def test_holdout_cannot_select_weight_and_small_sample_keeps_default(self):
        rows=[row('train',1),row('test',2)]
        first=review_rows(rows,'2099-01-01')
        rows[1]=row('test',2,'a')
        second=review_rows(rows,'2099-01-01')
        self.assertEqual(first['candidate_weight'],second['candidate_weight'])
        self.assertEqual(first['usable_weight'],.35)
        self.assertFalse(first['adoption_eligible'])

    def test_endpoints_are_controls(self):
        self.assertEqual(score_weight([row('r',1)],0)['top1_hits'],0)
        self.assertEqual(score_weight([row('r',1)],1)['top1_hits'],1)

    def test_rejects_duplicate_mismatch_missing_odds_and_time_leakage(self):
        valid=[row('a',1),row('b',2)]
        variants=[]
        v=copy.deepcopy(valid);v[1]['race_id']='a';variants.append(v)
        v=copy.deepcopy(valid);v[0]['odds']['b']=None;variants.append(v)
        v=copy.deepcopy(valid);v[0]['winners']=['unknown'];variants.append(v)
        v=copy.deepcopy(valid);v[0]['observed_at']=v[0]['scheduled_at'];variants.append(v)
        v=copy.deepcopy(valid);v[0]['result_acquired_at']='2099-01-03T00:00:00+09:00';variants.append(v)
        for v in variants:
            with self.subTest(v=v), self.assertRaises(ValueError):review_rows(v,'2099-01-01')

    def test_cross_model_comparison_is_not_adoptable(self):
        report=review_rows([row('a',1),row('b',2,version='v2')],'2099-01-01')
        self.assertIn('different_source_models_not_a_controlled_weight_comparison',report['reasons'])

    def test_review_must_reproduce_and_cannot_be_used_in_its_past(self):
        rows=[row('a',1),row('b',2)]
        payload={'summary':review_rows(rows,'2099-01-01'),'rows':rows,'source_hashes':[]}
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'review.json';save_market_review(payload,p)
            with self.assertRaises(FileExistsError):save_market_review(payload,p)
            with self.assertRaises(ValueError):reviewed_weight(p,'v1',datetime.fromisoformat('2099-01-02T09:00:00+09:00'))
            self.assertEqual(reviewed_weight(p,'v1',datetime.fromisoformat('2099-01-03T09:00:00+09:00')),.35)
            value=json.loads(p.read_text());value['payload']['summary']['usable_weight']=.8;p.write_text(json.dumps(value))
            with self.assertRaises(ValueError):reviewed_weight(p,'v1',datetime.fromisoformat('2099-01-03T09:00:00+09:00'))

    def test_sufficient_matched_data_can_enable_future_weight_only(self):
        rows=[row(str(i),1 if i<300 else 2,('aaabbbbbcc')[i%10]) for i in range(400)]
        summary=review_rows(rows,'2099-01-01')
        self.assertTrue(summary['adoption_eligible'])
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'review.json'
            save_market_review({'summary':summary,'rows':rows,'source_hashes':[]},p)
            future=datetime.fromisoformat('2099-01-03T09:00:00+09:00')
            self.assertEqual(reviewed_weight(p,'v1',future,'synthetic-model-hash'),summary['candidate_weight'])
            with self.assertRaises(ValueError):reviewed_weight(p,'v2',future)
