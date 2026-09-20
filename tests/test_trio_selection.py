import itertools
import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from keiba_prediction_lab.app_snapshot import (PredictionAppSnapshot, RunnerSnapshot,
    RaceDayAppSnapshot, RaceDayVenueAppSnapshot, RaceDayRaceAppSnapshot)
from keiba_prediction_lab.trio_selection import trio_distribution, trio_candidate, save_trio_selection, load_trio_selection


class TrioSelectionTests(unittest.TestCase):
    def prediction(self):
        return PredictionAppSnapshot('race', '2026-02-01T12:00:00+09:00',
            '2026-02-01T10:00:00+09:00', 'synthetic', 'input',
            tuple(RunnerSnapshot(i+1, str(i+1), p, [.9,.8,.6,.45,.25][i]) for i,p in enumerate([.4,.3,.15,.1,.05])),
            ('1','2','3'), 100, (), ())

    def day(self):
        return RaceDayAppSnapshot('2026-02-01', (RaceDayVenueAppSnapshot('Synthetic',
            (RaceDayRaceAppSnapshot(1,self.prediction()),)),))

    def test_full_order_free_distribution_and_six_order_sum(self):
        p=self.prediction(); rows=trio_distribution(p)
        self.assertEqual(len(rows),10)
        self.assertAlmostEqual(sum(r.probability for r in rows),1.)
        weights={r.horse_id:r.win_probability for r in p.runners}
        expected=sum(weights[a]*weights[b]/(1-weights[a])*weights[c]/(1-weights[a]-weights[b])
                     for a,b,c in itertools.permutations(('1','2','3')))
        self.assertAlmostEqual(rows[0].probability,expected)
        self.assertEqual(rows[0].selection,('1','2','3'))
        self.assertNotAlmostEqual(expected, .4*.3*.15)
        for order in itertools.permutations(rows[0].selection):
            self.assertEqual(set(order),set(trio_candidate(p)['selection']))

    def test_artifact_is_pre_race_bound_to_sources_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'trio.json'; day=self.day()
            frozen=datetime.fromisoformat('2026-02-01T10:05:00+09:00')
            save_trio_selection(day,path,frozen_at=frozen)
            loaded=load_trio_selection(day,path)
            self.assertEqual(loaded.venues[0].races[0].prediction.trio_frozen_at,frozen.isoformat())
            self.assertIsNone(day.venues[0].races[0].prediction.trio_frozen_at)
            self.assertEqual(loaded.venues[0].races[0].prediction.actual_selection,('1','2','3'))
            with self.assertRaises(FileExistsError):save_trio_selection(day,path,frozen_at=frozen)
            for delta in [timedelta(hours=-1),timedelta(hours=2)]:
                with self.assertRaises(ValueError):save_trio_selection(day,Path(temp)/'bad.json',frozen_at=frozen+delta)
            bad=replace(day,venues=(replace(day.venues[0],races=(replace(day.venues[0].races[0],
                prediction=replace(self.prediction(),model_version='changed')),)),))
            with self.assertRaises(ValueError):load_trio_selection(bad,path)
            envelope=json.loads(path.read_text());envelope['payload']['races'][0]['selection']=['1','2','5']
            path.write_text(json.dumps(envelope))
            with self.assertRaises(ValueError):load_trio_selection(day,path)

    def test_absent_artifact_does_not_claim_pre_race_freeze(self):
        with tempfile.TemporaryDirectory() as temp:
            day=self.day()
            self.assertIs(load_trio_selection(day,Path(temp)/'absent'),day)
