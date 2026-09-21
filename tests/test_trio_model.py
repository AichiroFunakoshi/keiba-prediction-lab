import itertools
import json
import math
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from tests.test_model import feature, START
from keiba_prediction_lab.model import TrainingRow
from keiba_prediction_lab.trio_model import (TrioSetModel,fit_trio_model,inclusion_probabilities,score_distribution,
    blend_trios,save_model,load_model,FEATURES,groups)


def rows():
    return tuple(TrainingRow(feature(f'race-{i}',h,START+timedelta(days=i),p),h)
        for i in range(8) for h,p in enumerate((.8,.6,.4,.2,.1,.05),1))


def targets():
    return tuple(feature('future',h,START+timedelta(days=20),p) for h,p in enumerate((.8,.6,.4,.2,.1,.05),1))


class TrioModelTests(unittest.TestCase):
    def test_dp_matches_enumeration_and_finite_difference(self):
        scores=[.8,-.5,.3,1.2,-2.];m=inclusion_probabilities(scores)
        combos=list(itertools.combinations(range(5),3));w=[math.exp(sum(scores[i] for i in c)) for c in combos];z=sum(w)
        self.assertAlmostEqual(sum(m),3.)
        for i in range(5):
            expected=sum(p for c,p in zip(combos,w) if i in c)/z
            self.assertAlmostEqual(m[i],expected)
            other=scores.copy();other[i]+=1e-6
            dz=(math.log(sum(math.exp(sum(other[j] for j in c)) for c in combos))-math.log(z))/1e-6
            self.assertAlmostEqual(m[i],dz,places=5)
        extreme=inclusion_probabilities([1000.,0.,-1000.,-2000.,-3000.])
        self.assertAlmostEqual(sum(extreme),3.)

    def test_learns_top_three_set_not_the_order(self):
        training=rows();model=fit_trio_model(training,epochs=100)
        permuted=tuple(replace(r,finish_position=4-r.finish_position) if r.finish_position<=3 else r for r in training)
        other=fit_trio_model(permuted,epochs=100)
        self.assertEqual(model.coefficients,other.coefficients)
        dist=model.predict(targets());self.assertEqual(len(dist),20);self.assertAlmostEqual(sum(x.probability for x in dist),1.)
        actual=tuple(f'future-horse-{i}' for i in (1,2,3))
        self.assertEqual(dist[0].selection,actual);self.assertGreater(dist[0].probability,1/20)
        self.assertEqual(score_distribution(dist,actual)['hit'],1)
        self.assertEqual(dist,model.predict(tuple(reversed(targets()))))
        self.assertEqual(blend_trios(dist,dist,0),dist)
        self.assertEqual(blend_trios(dist,dist,1),dist)
        with self.assertRaises(ValueError):blend_trios(dist,dist,float('nan'))

    def test_time_and_integrity_and_shape_guards(self):
        model=fit_trio_model(rows(),epochs=2)
        with self.assertRaises(ValueError):model.predict([r.features for r in rows()[:6]])
        with self.assertRaises(ValueError):replace(model,coefficients=model.coefficients[:-1])
        with self.assertRaises(ValueError):groups(rows()+(rows()[0],))
        with self.assertRaises(ValueError):groups([replace(r,finish_position=1) for r in rows()])
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'model.json';save_model(model,path,training_sha256='a'*64)
            self.assertEqual(model,load_model(path))
            with self.assertRaises(FileExistsError):save_model(model,path,training_sha256='a'*64)
            e=json.loads(path.read_text());e['payload']['coefficients'][0]+=1;path.write_text(json.dumps(e))
            with self.assertRaises(ValueError):load_model(path)
