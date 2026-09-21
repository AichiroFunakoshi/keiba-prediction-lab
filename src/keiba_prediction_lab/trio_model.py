"""Conditional set model: learn the unordered three finishers directly."""
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from itertools import combinations
import hashlib
import json
import math
from pathlib import Path

from .features import FeatureRow
from .model import ABILITY_V5_FEATURE_NAMES, TrainingRow, _raw_features, _softmax

VERSION = 'conditional-trio-set-v1'
FEATURES = ABILITY_V5_FEATURE_NAMES


def groups(rows):
    races = defaultdict(list)
    seen = set()
    for row in rows:
        key = (row.features.race_id, row.features.horse_id)
        if key in seen:
            raise ValueError('duplicate race/horse')
        seen.add(key)
        _raw_features(row.features, FEATURES)
        races[key[0]].append(row)
    for race in races.values():
        if len(race) < 5 or len({r.features.observed_at for r in race}) != 1:
            raise ValueError('race needs five runners at a shared observation time')
        if sorted(r.finish_position for r in race) != list(range(1, len(race)+1)):
            raise ValueError('trio learning requires complete unique finish positions; ties/censored races must be excluded explicitly')
    return [sorted(races[k], key=lambda r:r.features.horse_id) for k in sorted(races)]


def inclusion_probabilities(scores):
    """Exact size-three subset marginals via elementary symmetric polynomials."""
    if len(scores) < 3 or not all(math.isfinite(s) for s in scores):
        raise ValueError('finite scores for at least three runners required')
    weights = [math.exp(s-max(scores)) for s in scores]
    prefix = [[1.,0.,0.,0.]]
    for w in weights:
        old = prefix[-1]
        prefix.append([1., *(old[k]+w*old[k-1] for k in range(1,4))])
    z = prefix[-1][3]
    if z < 1e-200:
        indices = list(combinations(range(len(scores)),3))
        probs = _softmax([sum(scores[i] for i in c) for c in indices])
        return [sum(p for c,p in zip(indices,probs) if i in c) for i in range(len(scores))]
    suffix = [1.,0.,0.]
    result = [0.]*len(scores)
    for i in range(len(scores)-1,-1,-1):
        result[i] = weights[i]*sum(prefix[i][k]*suffix[2-k] for k in range(3))/z
        suffix = [1.,suffix[1]+weights[i],suffix[2]+weights[i]*suffix[1]]
    return result


@dataclass(frozen=True)
class TrioOutcome:
    selection: tuple[str,str,str]
    probability: float


@dataclass(frozen=True)
class TrioSetModel:
    coefficients: tuple[float,...]
    means: tuple[float,...]
    scales: tuple[float,...]
    trained_through: datetime
    model_version: str = VERSION
    temperature: float = 1.0
    calibrated_through: datetime | None = None

    def __post_init__(self):
        if self.model_version != VERSION or any(len(v)!=len(FEATURES) for v in (self.coefficients,self.means,self.scales)):
            raise ValueError('invalid trio model schema or vector length')
        if any(not math.isfinite(x) for v in (self.coefficients,self.means,self.scales) for x in v) or any(x<=0 for x in self.scales):
            raise ValueError('invalid trio model values')
        if self.trained_through.tzinfo is None or self.trained_through.utcoffset() is None:
            raise ValueError('aware training cutoff required')

        if not math.isfinite(self.temperature) or self.temperature<=0:
            raise ValueError('invalid calibration temperature')
        if self.calibrated_through is not None and (self.calibrated_through.tzinfo is None or self.calibrated_through<=self.trained_through):
            raise ValueError('calibration must follow training')

    def predict(self, rows, *, temperature=None):
        rows=sorted(rows,key=lambda r:r.horse_id)
        if len(rows)<5 or len({r.race_id for r in rows})!=1 or len({r.horse_id for r in rows})!=len(rows) or len({r.observed_at for r in rows})!=1:
            raise ValueError('one complete race of unique runners required')
        if rows[0].observed_at <= (self.calibrated_through or self.trained_through):
            raise ValueError('prediction must follow model training')
        temperature=self.temperature if temperature is None else temperature
        if not math.isfinite(temperature) or temperature<=0:
            raise ValueError('positive finite temperature required')
        scores=[sum(w*(x-m)/s for w,x,m,s in zip(self.coefficients,_raw_features(r,FEATURES),self.means,self.scales,strict=True))/temperature for r in rows]
        combos=list(combinations(range(len(rows)),3))
        probs=_softmax([sum(scores[i] for i in c) for c in combos])
        return tuple(sorted((TrioOutcome(tuple(rows[i].horse_id for i in c),p) for c,p in zip(combos,probs)),key=lambda r:(-r.probability,r.selection)))


def fit_trio_model(rows, *, epochs=500, learning_rate=.1, l2_strength=.01):
    if type(epochs) is not int or epochs<=0 or not math.isfinite(learning_rate) or learning_rate<=0 or not math.isfinite(l2_strength) or l2_strength<0:
        raise ValueError('invalid training parameters')
    races=groups(rows)
    if not races:raise ValueError('training races required')
    raw=[_raw_features(r.features,FEATURES) for race in races for r in race]
    means=tuple(sum(v[j] for v in raw)/len(raw) for j in range(len(FEATURES)))
    scales=tuple(max(math.sqrt(sum((v[j]-means[j])**2 for v in raw)/len(raw)),1.) for j in range(len(FEATURES)))
    data=[([(tuple((x-m)/s for x,m,s in zip(_raw_features(r.features,FEATURES),means,scales,strict=True))) for r in race], [float(r.finish_position<=3) for r in race]) for race in races]
    coef=[0.]*len(FEATURES)
    for _ in range(epochs):
        grad=[l2_strength*w/len(data) for w in coef]
        for vectors,target in data:
            q=inclusion_probabilities([sum(w*x for w,x in zip(coef,v,strict=True)) for v in vectors])
            for v,actual,pred in zip(vectors,target,q,strict=True):
                delta=(pred-actual)/len(data)
                for j,x in enumerate(v):grad[j]+=delta*x
        coef=[w-learning_rate*g for w,g in zip(coef,grad,strict=True)]
    return TrioSetModel(tuple(coef),means,scales,max(r.features.observed_at for race in races for r in race))


def score_distribution(distribution, actual):
    actual=tuple(sorted(actual))
    if len(actual)!=3 or len(set(actual))!=3:raise ValueError('three distinct finishers required')
    pm={r.selection:r.probability for r in distribution}
    if len(pm)!=len(distribution) or actual not in pm or any(not math.isfinite(p) or p<0 for p in pm.values()) or abs(sum(pm.values())-1)>1e-8:
        raise ValueError('invalid trio distribution')
    top=min(distribution,key=lambda r:(-r.probability,r.selection))
    return dict(hit=int(top.selection==actual),log_loss=-math.log(max(pm[actual],1e-300)),
                brier=sum((p-float(c==actual))**2 for c,p in pm.items()),overlap=len(set(top.selection)&set(actual)))


def blend_trios(independent, market, weight):
    if type(weight) not in (int,float) or not math.isfinite(weight) or not 0<=weight<=1:raise ValueError('invalid market weight')
    left={r.selection:r.probability for r in independent};right={r.selection:r.probability for r in market}
    if left.keys()!=right.keys() or not left:raise ValueError('trio markets must cover identical outcomes')
    if any(not math.isfinite(p) or p<0 for pm in (left,right) for p in pm.values()) or any(abs(sum(pm.values())-1)>1e-8 for pm in (left,right)):
        raise ValueError('invalid input probabilities')
    keys=sorted(left)
    if weight in (0,1):probs=[(left if weight==0 else right)[k] for k in keys]
    else:probs=_softmax([(1-weight)*math.log(max(left[k],1e-300))+weight*math.log(max(right[k],1e-300)) for k in keys])
    return tuple(sorted((TrioOutcome(k,p) for k,p in zip(keys,probs)),key=lambda r:(-r.probability,r.selection)))


def save_model(model, path, *, training_sha256):
    if len(training_sha256)!=64 or any(c not in '0123456789abcdef' for c in training_sha256):raise ValueError('invalid training hash')
    payload={**asdict(model),'trained_through':model.trained_through.isoformat(),'calibrated_through':model.calibrated_through.isoformat() if model.calibrated_through else None,'feature_names':FEATURES,'training_sha256':training_sha256}
    canonical=json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    with Path(path).open('x') as f:json.dump(dict(schema_version='1.0',sha256=hashlib.sha256(canonical).hexdigest(),payload=payload),f,indent=2)


def load_model(path):
    def unique(pairs):
        out={}
        for k,v in pairs:
            if k in out:raise ValueError('duplicate key')
            out[k]=v
        return out
    envelope=json.loads(Path(path).read_bytes(),object_pairs_hook=unique)
    if not isinstance(envelope,dict) or set(envelope)!={'schema_version','sha256','payload'} or envelope['schema_version']!='1.0':raise ValueError('invalid trio artifact')
    p=envelope['payload'];expected={'coefficients','means','scales','trained_through','model_version','feature_names','training_sha256'}
    if not isinstance(p,dict) or set(p) not in (expected,expected|{'temperature','calibrated_through'}) or tuple(p['feature_names'])!=FEATURES:raise ValueError('invalid trio payload')
    canonical=json.dumps(p,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    if hashlib.sha256(canonical).hexdigest()!=envelope['sha256']:raise ValueError('trio integrity check failed')
    return TrioSetModel(*(tuple(p[k]) for k in ('coefficients','means','scales')),datetime.fromisoformat(p['trained_through']),p['model_version'],p.get('temperature',1.),datetime.fromisoformat(p['calibrated_through']) if p.get('calibrated_through') else None)
