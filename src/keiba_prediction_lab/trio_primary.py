"""Explicit, hash-bound display of prospective direct-set trio forecasts."""
from dataclasses import replace
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
import hashlib
import json
import math


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    def unique(pairs):
        out = {}
        for k, v in pairs:
            if k in out:
                raise ValueError('duplicate direct trio key')
            out[k] = v
        return out
    return json.loads(Path(path).read_text(), object_pairs_hook=unique)


def _envelope(path):
    e = _read(path)
    raw = json.dumps(e['payload'], sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
    if hashlib.sha256(raw).hexdigest() != e['sha256']:
        raise ValueError('direct trio artifact hash mismatch')
    return e['payload']


def save_direct_trio_display(root, *, note):
    """Opt in while races are still unfired; never alter legacy bundles."""
    root = Path(root)
    shadow = _envelope(root / 'trio-model-shadow.json')
    now = datetime.now(timezone.utc)
    if any(now >= datetime.fromisoformat(r['scheduled_at']) for r in shadow['races']):
        raise ValueError('cannot select a direct trio policy after a race starts')
    value = dict(schema_version='1.0', created_at=now.isoformat(),
                 race_day_sha256=_sha(root/'race-day.json'),
                 shadow_sha256=_sha(root/'trio-model-shadow.json'),
                 note=note, primary_market_weight=0., comparison_market_weight=.2)
    with (root/'direct-trio-display.json').open('x') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def apply_direct_trio(snapshot, root):
    root = Path(root)
    pointer = root/'direct-trio-display.json'
    if not pointer.exists():
        return snapshot
    config = _read(pointer)
    if (config.get('schema_version') != '1.0' or config.get('primary_market_weight') != 0.
        or config.get('comparison_market_weight') != .2
        or config['race_day_sha256'] != _sha(root/'race-day.json')
        or config['shadow_sha256'] != _sha(root/'trio-model-shadow.json')):
        raise ValueError('direct trio display source mismatch')
    shadow = _envelope(root/'trio-model-shadow.json')
    provenance = _envelope(root/'race-day-provenance.json')
    if (shadow['policy_version'] != 'trio-set-shadow-v1'
        or shadow['status'] != 'prospective_shadow_not_primary'
        or any(shadow[k] != provenance[k] for k in ('frozen_at', 'history_sha256', 'plan_sha256'))):
        raise ValueError('direct trio inputs differ from audited race day')
    base = {r.prediction.race_id: r.prediction for v in snapshot.race_day.venues for r in v.races}
    rows = {r['race_id']: r for r in shadow['races']}
    if len(rows) != len(shadow['races']) or rows.keys() != base.keys():
        raise ValueError('direct trio race coverage mismatch')
    manifest = _read(root / "race-day.json")
    target_hashes = {}
    for venue in manifest["venues"]:
        for race in venue["races"]:
            bundle = root / race["prediction_bundle"]
            actual = _read(bundle / "manifest.json")
            target_hashes[actual["race_id"]] = _envelope(bundle / "input-provenance.json")["targets_sha256"]
    candidates = {0.: {}, .2: {}}
    for rid, row in rows.items():
        p = base[rid]
        if row['targets_sha256'] != target_hashes[rid]:
            raise ValueError('direct trio target input mismatch')
        scheduled = datetime.fromisoformat(p.scheduled_at)
        stamps = [datetime.fromisoformat(x) for x in (shadow['frozen_at'], shadow['created_at'], config['created_at'])]
        if row['scheduled_at'] != p.scheduled_at or any(t.tzinfo is None or t >= scheduled for t in stamps):
            raise ValueError('direct trio must be saved before the matching race')
        expected = set(combinations(sorted(r.horse_id for r in p.runners), 3))
        if [v['market_weight'] for v in row['variants']] != [0., .2]:
            raise ValueError('direct trio variants mismatch')
        for variant in row['variants']:
            outcomes = variant['probabilities']
            probs = {tuple(o['selection']): o['probability'] for o in outcomes}
            if (len(probs) != len(outcomes) or probs.keys() != expected
                or any(type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 1 for x in probs.values())
                or abs(sum(probs.values())-1) > 1e-8):
                raise ValueError('invalid complete trio distribution')
            best = min(probs, key=lambda c: (-probs[c], c))
            if tuple(variant['selection']) != best or variant['probability'] != probs[best] or variant['stake_yen'] != 0:
                raise ValueError('direct trio selection mismatch')
            marginal = {h: sum(prob for combo, prob in probs.items() if h in combo) for h in sorted({h for c in probs for h in c})}
            candidates[variant['market_weight']][rid] = dict(
                bet_type='trio', selection=list(best), probability=probs[best], stake_yen=100,
                frozen_at=shadow['frozen_at'], policy_version='conditional-trio-set-v1',
                market_weight=variant['market_weight'], inclusion_probabilities=marginal,
                model_sha256=shadow['model_sha256'], note=config['note'])
    def overlay(weight):
        return replace(snapshot.race_day, venues=tuple(replace(v, races=tuple(
            replace(r, prediction=replace(r.prediction, direct_trio=candidates[weight][r.prediction.race_id]))
            for r in v.races)) for v in snapshot.race_day.venues))
    return replace(snapshot, race_day=overlay(0.), comparison_race_day=overlay(.2),
                   comparison_win5=None, comparison_explanations=snapshot.prediction_explanations)
