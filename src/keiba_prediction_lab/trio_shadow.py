"""Optional next-meeting trio candidates, never replace published selections."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
from .trio_model import load_model, blend_trios
from .local_adapter import build_local_feature_bundle, load_targets_csv
from .race_day_pipeline import load_local_race_day_plan
from .market_guard import _load_cards
from .market_blend import blend_probabilities
from .model import _top3_probabilities
from .domain import PredictionRecord, BetType
from .bet_type_forecast import build_bet_type_forecast
from .jra_web_fetch import SOURCE_ID
from .trio_research import write_json


def save_shadow_day(model_path, history, plan_path, market_snapshot, output, *, frozen_at):
    model_path=Path(model_path);model_hash=hashlib.sha256(model_path.read_bytes()).hexdigest();model=load_model(model_path)
    history=Path(history);history_hash=hashlib.sha256(history.read_bytes()).hexdigest()
    plan_path=Path(plan_path);plan_hash=hashlib.sha256(plan_path.read_bytes()).hexdigest();plan=load_local_race_day_plan(plan_path)
    market_snapshot=Path(market_snapshot);manifest_content=(market_snapshot/'acquisition-manifest.json').read_bytes();manifest=json.loads(manifest_content)
    cards=(market_snapshot/'cards.json').read_bytes();cards_hash=hashlib.sha256(cards).hexdigest()
    observed=datetime.fromisoformat(manifest['acquired_at'])
    if manifest.get('source_id')!=SOURCE_ID or manifest.get('private_use_only') is not True or manifest['outputs']['cards.json']['sha256']!=cards_hash or observed.tzinfo is None or observed>frozen_at:
        raise ValueError('invalid trio market snapshot')
    odds=_load_cards(cards);rows=[];hashes={}
    for entry in plan.races:
        target=Path(entry.targets);h=hashlib.sha256(target.read_bytes()).hexdigest();hashes[str(target)]=h
        targets=load_targets_csv(target);fb=build_local_feature_bundle(history,target);rid=fb.features[0].race_id
        if any(not t.observed_at<=frozen_at<t.scheduled_at or datetime.now(timezone.utc)>=t.scheduled_at for t in targets):raise ValueError('trio shadow requires an unfired race and frozen pre-race inputs')
        if rid not in odds or set(odds[rid])!={f.horse_id for f in fb.features}:raise ValueError('market runners mismatch')
        dist=model.predict(fb.features);uniform={f.horse_id:1/len(fb.features) for f in fb.features};market,_=blend_probabilities(uniform,odds[rid],market_weight=.5)
        marginals=_top3_probabilities(list(market.values()));rank={h:i+1 for i,(h,v) in enumerate(sorted(market.items(),key=lambda x:(-x[1],x[0])))}
        records=[PredictionRecord(rid,h,frozen_at,'market-only',p,marginals[i],rank[h]) for i,(h,p) in enumerate(market.items())]
        md=build_bet_type_forecast(records).for_bet_type(BetType.TRIO);variants=[]
        for weight in (0.,.2):
            combined=blend_trios(dist,md,weight)
            variants.append(dict(market_weight=weight,selection=list(combined[0].selection),probability=combined[0].probability,stake_yen=0,
                                 probabilities=[dict(selection=list(x.selection),probability=x.probability) for x in combined]))
        rows.append(dict(race_id=rid,scheduled_at=targets[0].scheduled_at.isoformat(),targets_sha256=h,variants=variants))
    now=datetime.now(timezone.utc)
    if any(now>=datetime.fromisoformat(x['scheduled_at']) for x in rows):raise ValueError('race began during trio generation')
    if (model_hash!=hashlib.sha256(model_path.read_bytes()).hexdigest() or history_hash!=hashlib.sha256(history.read_bytes()).hexdigest()
        or plan_hash!=hashlib.sha256(plan_path.read_bytes()).hexdigest() or any(hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h for p,h in hashes.items())):
        raise ValueError('trio shadow sources changed')
    payload=dict(schema_version='1.0',policy_version='trio-set-shadow-v1',status='prospective_shadow_not_primary',created_at=now.isoformat(),frozen_at=frozen_at.isoformat(),market_observed_at=observed.isoformat(),model_sha256=model_hash,history_sha256=history_hash,plan_sha256=plan_hash,cards_sha256=cards_hash,acquisition_sha256=hashlib.sha256(manifest_content).hexdigest(),races=rows)
    canonical=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
    write_json(output,dict(payload=payload,sha256=hashlib.sha256(canonical).hexdigest()))
