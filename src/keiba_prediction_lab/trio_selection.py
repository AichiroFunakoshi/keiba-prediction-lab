"""Order-free top-three selection, separate from immutable legacy tickets."""
from dataclasses import replace
from datetime import datetime
import hashlib
import json
from pathlib import Path

from .bet_type_forecast import build_bet_type_forecast
from .domain import BetType, PredictionRecord

POLICY_VERSION = "trio-joint-probability-v1"


def trio_distribution(prediction):
    """Sum all six finish orders per trio; never multiply marginal place rates."""
    records = tuple(PredictionRecord(
        prediction.race_id, r.horse_id, datetime.fromisoformat(prediction.frozen_at),
        prediction.model_version, r.win_probability, r.top3_probability, r.predicted_rank
    ) for r in prediction.runners)
    return build_bet_type_forecast(records).for_bet_type(BetType.TRIO)


def trio_candidate(prediction):
    best = trio_distribution(prediction)[0]
    return dict(bet_type="trio", selection=list(best.selection), probability=best.probability,
                stake_yen=100, policy_version=POLICY_VERSION)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def _payload(day, frozen_at):
    frozen = datetime.fromisoformat(frozen_at)
    if frozen.tzinfo is None or frozen.utcoffset() is None:
        raise ValueError('trio freeze time must be timezone-aware')
    rows = []
    for venue in day.venues:
        for race in venue.races:
            p = race.prediction
            if not datetime.fromisoformat(p.frozen_at) <= frozen < datetime.fromisoformat(p.scheduled_at):
                raise ValueError('trio selection must be frozen after inputs and before race')
            source = dict(race_id=p.race_id, scheduled_at=p.scheduled_at,
                          frozen_at=p.frozen_at, model_version=p.model_version,
                          input_data_version=p.input_data_version,
                          runners=[vars(r) for r in p.runners])
            rows.append(dict(race_id=p.race_id, source_sha256=hashlib.sha256(_canonical(source)).hexdigest(),
                             **trio_candidate(p)))
    return dict(policy_version=POLICY_VERSION, frozen_at=frozen_at, races=rows)


def save_trio_selection(day, path, *, frozen_at):
    """Persist a separate pre-race trio policy artifact without overwriting."""
    payload = _payload(day, frozen_at.isoformat())
    envelope = dict(schema_version='1.0', payload=payload, sha256=hashlib.sha256(_canonical(payload)).hexdigest())
    with Path(path).open('x', encoding='utf-8') as handle:
        json.dump(envelope, handle, ensure_ascii=False, indent=2)


def load_trio_selection(day, path):
    """Recompute against audited source vectors and reject stale or changed data."""
    path = Path(path)
    if not path.exists():
        return day
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate trio key')
            result[key] = value
        return result
    envelope = json.loads(path.read_text(), object_pairs_hook=unique)
    if set(envelope) != {'schema_version', 'payload', 'sha256'} or envelope['schema_version'] != '1.0':
        raise ValueError('invalid trio schema')
    payload = envelope['payload']
    if not isinstance(payload, dict) or not isinstance(payload.get('frozen_at'), str):
        raise ValueError('invalid trio payload')
    if hashlib.sha256(_canonical(payload)).hexdigest() != envelope['sha256'] or payload != _payload(day, payload['frozen_at']):
        raise ValueError('trio source or selection mismatch')
    return replace(day, venues=tuple(replace(v, races=tuple(replace(r,
        prediction=replace(r.prediction, trio_frozen_at=payload['frozen_at'])) for r in v.races)) for v in day.venues))
