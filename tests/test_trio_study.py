import hashlib,json,tempfile,unittest
from pathlib import Path
from keiba_prediction_lab.trio_study import load_trio_study
from keiba_prediction_lab.trio_research import aggregate

class TrioStudyTests(unittest.TestCase):
    def test_report_is_paired_recomputed_and_hash_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pointer=root/'pointer.json';report=root/'report.json'
            rows=[dict(race_id='synthetic',hit=1,log_loss=.5,brier=.2,overlap=3)]
            payload=dict(status='development_evaluation_not_untouched',folds=[dict(counts={'evaluation':1},rows={'winner_v5':rows,'trio_set':rows})],summary={k:aggregate(rows) for k in ('winner_v5','trio_set')})
            def save():
                report.write_text(json.dumps(payload));pointer.write_text(json.dumps(dict(schema_version='1.0',report_path=report.name,report_sha256=hashlib.sha256(report.read_bytes()).hexdigest())))
            save();self.assertEqual(load_trio_study(pointer)['summary']['trio_set']['hits'],1)
            payload['summary']['trio_set']['hits']=2;save()
            with self.assertRaises(ValueError):load_trio_study(pointer)
            report.write_text('{}')
            with self.assertRaises(ValueError):load_trio_study(pointer)
