"""Chronological odds-weight research with a conservative deployment gate.

Review uses saved pre-race probabilities, never final odds or reconstructed
predictions. A small or cross-model comparison cannot change production weights.
"""
import hashlib
import json
import math
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

from .market_blend import DEFAULT_MARKET_WEIGHT, blend_probabilities, load_market_blend_forecast
from .snapshot_adapter import _normalized_name
from .bundle_audit import load_audited_prediction_bundle

WEIGHTS = (0.0, 0.2, 0.35, 0.5, 0.65, 0.8, 1.0)
MIN_SELECTION_RACES = 300


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _strict(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_strict)


def model_identities(race_day, expected_hash):
    """Bind model artifacts to the exact saved day and audited bundles."""
    path = Path(race_day)
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
        raise ValueError('race day does not match market forecast')
    result = {}
    for venue in _read(path)['venues']:
        for race in venue['races']:
            audit = load_audited_prediction_bundle(path.parent / race['prediction_bundle'])
            result[audit.bundle.actual_prediction.race_id] = audit.audit.model_sha256
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
        raise ValueError('race day changed during model verification')
    return result


def score_weight(rows, weight):
    if isinstance(weight, bool) or not math.isfinite(weight) or not 0 <= weight <= 1:
        raise ValueError('weight must be finite and between zero and one')
    if not rows:
        raise ValueError('evaluation requires races')
    hits = exact = cover = 0
    loss = brier = 0.0
    for row in rows:
        model, odds = row['model'], row['odds']
        market, pool = blend_probabilities(model, odds, market_weight=weight if 0 < weight < 1 else DEFAULT_MARKET_WEIGHT)
        probabilities = model if weight == 0 else market if weight == 1 else pool
        ordered = sorted(probabilities, key=lambda h: (-probabilities[h], h))
        winners = row['winners']
        hits += ordered[0] in winners
        cover += bool(set(ordered[:3]) & set(winners))
        exact += ordered[:3] == row['top3']
        loss += -sum(math.log(probabilities[h]) for h in winners) / len(winners)
        brier += sum((p - (1 / len(winners) if h in winners else 0)) ** 2 for h, p in probabilities.items())
    n = len(rows)
    return dict(weight=weight, race_count=n, top1_hits=hits, top1_accuracy=hits/n,
                winner_top3_coverage=cover/n, trifecta_hits=exact,
                winner_log_loss=loss/n, multiclass_brier=brier/n)


def review_rows(rows, selection_end):
    """Choose solely on earlier dates; holdout scores never choose the weight."""
    cutoff = date.fromisoformat(selection_end)
    if not rows or len({r['race_id'] for r in rows}) != len(rows):
        raise ValueError('empty or duplicate race identities')
    for r in rows:
        scheduled = datetime.fromisoformat(r['scheduled_at'])
        observed = datetime.fromisoformat(r['observed_at'])
        known = datetime.fromisoformat(r['result_acquired_at'])
        if any(x.tzinfo is None or x.utcoffset() is None for x in (scheduled, observed, known)):
            raise ValueError('all times must be timezone-aware')
        if observed >= scheduled or known <= scheduled:
            raise ValueError('invalid pre-race prediction or result timestamp')
        if not r['winners'] or len(set(r['winners'])) != len(r['winners']) or not set(r['winners']) <= r['model'].keys():
            raise ValueError('result winners do not match forecast')
        if len(r['top3']) != 3 or len(set(r['top3'])) != 3 or not set(r['top3']) <= r['model'].keys():
            raise ValueError('invalid actual top three')
        # Also validates complete normalized probabilities and positive odds.
        blend_probabilities(r['model'], r['odds'])
    jst = timezone(timedelta(hours=9))
    train = [r for r in rows if datetime.fromisoformat(r['scheduled_at']).astimezone(jst).date() <= cutoff]
    holdout = [r for r in rows if datetime.fromisoformat(r['scheduled_at']).astimezone(jst).date() > cutoff]
    if not train or not holdout:
        raise ValueError('both selection and later holdout races are required')
    first_holdout = min(datetime.fromisoformat(r['scheduled_at']) for r in holdout)
    if any(datetime.fromisoformat(r['result_acquired_at']) >= first_holdout for r in train):
        raise ValueError('selection outcomes were not available before holdout')
    training_scores = [score_weight(train, w) for w in WEIGHTS]
    best = min(training_scores, key=lambda s:(s['winner_log_loss'], abs(s['weight']-DEFAULT_MARKET_WEIGHT), s['weight']))['weight']
    holdout_scores = [score_weight(holdout, w) for w in WEIGHTS]
    versions = sorted({r['model_version'] for r in rows})
    identities = sorted({r.get('model_sha256') or '' for r in rows})
    reasons = []
    if len(train) < MIN_SELECTION_RACES:
        reasons.append('selection_sample_below_300')
    if len(holdout) < 100:
        reasons.append('holdout_sample_below_100')
    if len(identities) != 1 or not identities[0]:
        reasons.append('missing_or_different_model_artifacts')
    if len(versions) != 1:
        reasons.append('different_source_models_not_a_controlled_weight_comparison')
    if best in (0, 1):
        reasons.append('endpoint_is_a_control_not_a_blended_model')
    candidate = next(s for s in holdout_scores if s['weight'] == best)
    baseline = next(s for s in holdout_scores if s['weight'] == DEFAULT_MARKET_WEIGHT)
    if candidate['winner_log_loss'] >= baseline['winner_log_loss'] or candidate['multiclass_brier'] > baseline['multiclass_brier']:
        reasons.append('no_joint_holdout_probability_improvement')
    return dict(selection_end=selection_end, selection_race_count=len(train), holdout_race_count=len(holdout),
                source_model_versions=versions, source_model_sha256=identities, candidate_weight=best,
                usable_weight=DEFAULT_MARKET_WEIGHT if reasons else best,
                adoption_eligible=not reasons, reasons=reasons,
                selection_scores=training_scores, holdout_scores=holdout_scores,
                evaluated_through=max(datetime.fromisoformat(r['scheduled_at']) for r in rows).isoformat(),
                results_available_through=max(datetime.fromisoformat(r['result_acquired_at']) for r in rows).isoformat())


def review_market_files(dataset, selection_end):
    spec = _read(dataset)
    if not isinstance(spec, list) or not spec:
        raise ValueError('dataset must be a non-empty explicit source list')
    rows = []; sources = []
    for entry in spec:
        if set(entry) != {'forecast','results','result_manifest'}:
            raise ValueError('unexpected dataset fields')
        paths = {k: (Path(dataset).parent / v).resolve() for k,v in entry.items()}
        raw = {k:p.read_bytes() for k,p in paths.items()}
        manifest = json.loads(raw['result_manifest'], object_pairs_hook=_strict)
        if hashlib.sha256(raw['results']).hexdigest() != manifest['results_sha256']:
            raise ValueError('official results hash mismatch')
        forecast = load_market_blend_forecast(paths['forecast'])
        identities = model_identities(paths['forecast'].parent / 'race-day.json', forecast.race_day_manifest_sha256)
        results = json.loads(raw['results'], object_pairs_hook=_strict)
        by_id = {r['race_id']:r for r in results}
        if len(by_id) != len(results):
            raise ValueError('duplicate result race identity')
        sources.append({k:hashlib.sha256(v).hexdigest() for k,v in raw.items()})
        for race in forecast.races:
            r = by_id[race.race_id]
            if r['date'].replace('-', '') != race.scheduled_at.strftime('%Y%m%d'):
                raise ValueError('result date mismatch')
            ordered = sorted(r['runners'], key=lambda h:(h['finish'],h['number']))
            winners = [_normalized_name('horse', h['name']) for h in ordered if h['finish']==1]
            model = {h.horse_id:h.model_probability for h in race.runners}
            odds = {h.horse_id:h.market_odds for h in race.runners}
            _, expected = blend_probabilities(model, odds, market_weight=forecast.market_weight)
            if any(abs(expected[h.horse_id]-h.blended_probability)>1e-10 for h in race.runners):
                raise ValueError('market forecast components are inconsistent')
            rows.append(dict(race_id=race.race_id, scheduled_at=race.scheduled_at.isoformat(),
                observed_at=forecast.observed_at.isoformat(), result_acquired_at=manifest['acquired_at'],
                model_version=race.source_model_version,model_sha256=identities[race.race_id],model=model,odds=odds,winners=winners,
                top3=[_normalized_name('horse', h['name']) for h in ordered[:3]]))
        if any(path.read_bytes() != raw[key] for key, path in paths.items()):
            raise ValueError('review inputs changed during verification')
    return dict(summary=review_rows(rows, selection_end), rows=rows, source_hashes=sources)


def save_market_review(payload, output):
    envelope = dict(schema_version='1.0',payload=payload,sha256=hashlib.sha256(_canonical(payload)).hexdigest())
    with Path(output).open('x', encoding='utf-8') as f:
        json.dump(envelope,f,ensure_ascii=False,indent=2,allow_nan=False)


def reviewed_weight(path, source_model_version, prediction_time, model_sha256=None):
    envelope = _read(path)
    if envelope.get('schema_version') != '1.0' or hashlib.sha256(_canonical(envelope['payload'])).hexdigest()!=envelope.get('sha256'):
        raise ValueError('invalid market review integrity')
    payload = envelope['payload']; saved = payload['summary']
    recomputed = review_rows(payload['rows'],saved['selection_end'])
    if recomputed != saved:
        raise ValueError('market review scores do not reproduce')
    if prediction_time.tzinfo is None or prediction_time.utcoffset() is None:
        raise ValueError('prediction_time must be timezone-aware')
    if prediction_time <= max(datetime.fromisoformat(saved[k]) for k in ('evaluated_through','results_available_through')):
        raise ValueError('review cannot be used before its evaluation results')
    if not saved['adoption_eligible']:
        return DEFAULT_MARKET_WEIGHT
    if saved['source_model_versions'] != [source_model_version] or saved['source_model_sha256'] != [model_sha256]:
        raise ValueError('review was calibrated for a different model')
    return saved['usable_weight']
