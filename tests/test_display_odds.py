import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from keiba_prediction_lab.display_odds import latest_display_odds
from keiba_prediction_lab.jra_web_fetch import SOURCE_ID


class DisplayOddsTest(unittest.TestCase):
    def snapshot(self,root,name,time,odds,pop=None):
        d=root/name;d.mkdir();cards=[{'race_id':'r','horses':[{'name':'A','number':1,'odds':odds,'popularity':pop},{'name':'B','number':2,'odds':2.5}]}];raw=json.dumps(cards).encode();(d/'cards.json').write_bytes(raw)
        (d/'acquisition-manifest.json').write_text(json.dumps({'source_id':SOURCE_ID,'private_use_only':True,'acquired_at':time,'outputs':{'cards.json':{'sha256':hashlib.sha256(raw).hexdigest()}}}))
        return d
    def test_newest_verified_snapshot_and_official_rank(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.snapshot(root,'old','2026-01-01T08:00:00+09:00',8)
            newer=self.snapshot(root,'new','2026-01-01T09:00:00+09:00',2.5,2)
            x=latest_display_odds(root)['r'];self.assertEqual(x['horse:name:a']['popularity'],2);self.assertEqual(x['horse:name:a']['popularity_source'],'jra')
            (newer/'cards.json').write_text('[]');x=latest_display_odds(root)['r'];self.assertEqual(x['horse:name:a']['odds'],8)
    def test_missing_odds_and_future_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.snapshot(root,'missing','2026-01-01T09:00:00+09:00',None)
            self.snapshot(root,'future','2099-01-01T09:00:00+09:00',1.1)
            x=latest_display_odds(root)['r'];self.assertIsNone(x['horse:name:a']['popularity']);self.assertIsNone(x['horse:name:b']['popularity'])
