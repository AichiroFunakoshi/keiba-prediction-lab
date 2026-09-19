"""Faithful additive score explanations, not causal claims or invented race facts."""
import hashlib
import json
from pathlib import Path
from .model import _raw_features
from .local_adapter import build_local_feature_bundle
from .model_artifact import load_trained_model_artifact
from .race_day_pipeline import load_local_race_day_plan
from .bundle_audit import load_audited_prediction_bundle

LABELS = {
    'opponent_rating':'過去の対戦相手を考慮した評価',
    'recent_field_percentile':'頭数を考慮した近走成績',
    'course_form':'同競馬場・同路面での成績',
    'distance_surface_form':'同距離帯・同路面での成績',
    'jockey_course_win_rate':'騎手の同競馬場・同路面成績',
    'carried_weight_change':'前走からの斤量差',
    'recent_reciprocal_finish':'直近5走の着順',
    'recent_top3_rate':'直近5走の3着内率',
    'recent_form_missing':'近走履歴の欠測',
    'horse_jockey_top3_rate':'馬と騎手の組み合わせ成績',
    'distance_change_km':'前走からの距離変更',
    'surface_changed':'前走からの路面変更',
    'body_weight_change_pct':'馬体重の増減',
    'body_weight_change_missing':'馬体重増減の欠測',
    'horse_win_rate':'馬の過去勝率','horse_top3_rate':'馬の過去3着内率',
    'horse_venue_win_rate':'同競馬場の勝率','horse_surface_win_rate':'同路面の勝率',
    'horse_distance_band_win_rate':'同距離帯の勝率',
    'horse_surface_track_condition_win_rate':'同路面・馬場状態の勝率',
    'horse_surface_track_condition_top3_rate':'同路面・馬場状態の3着内率',
    'jockey_win_rate':'騎手の過去勝率','trainer_win_rate':'調教師の過去勝率',
    'post_position':'馬番','carried_weight_kg':'斤量','body_weight_kg':'馬体重',
    'body_weight_missing':'馬体重の欠測','days_since_last_run':'前走からの日数',
    'days_since_last_run_missing':'休養期間の欠測',
}


def _canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def explain_rows(model, rows):
    predictions = {p.horse_id:p for p in model.predict(rows)}
    vectors = [_raw_features(r,model.feature_names) for r in rows]
    center = [sum(v[i] for v in vectors)/len(vectors) for i in range(len(model.feature_names))]
    temperature = getattr(model,'temperature',1.0)
    result = {}
    for row, vector in zip(rows,vectors,strict=True):
        factors=[dict(feature=name,label=LABELS.get(name,name),value=value,
                      contribution=coefficient*(value-average)/scale/temperature)
                 for name,value,average,scale,coefficient in zip(model.feature_names,vector,center,model.scales,model.coefficients,strict=True)]
        result[row.horse_id] = dict(win_probability=predictions[row.horse_id].win_probability,
            factors=sorted(factors,key=lambda f:-abs(f['contribution'])),
            history_starts=row.horse_starts, recent_form_missing=row.recent_form_missing,
            body_weight_missing=row.body_weight_kg is None)
    return result


def save_day_explanations(model_path, history, plan_path, day_directory):
    day=Path(day_directory);model=load_trained_model_artifact(model_path)
    plan=load_local_race_day_plan(plan_path);races={}
    audited={}
    manifest=json.loads((day/'race-day.json').read_text())
    for venue in manifest['venues']:
        for race in venue['races']:
            item=load_audited_prediction_bundle(day/race['prediction_bundle'])
            audited[item.audit.race_id]=item
    for entry in plan.races:
        fb=build_local_feature_bundle(history,entry.targets,prior_strength=model.parameters.prior_strength)
        rid=fb.features[0].race_id;explanations=explain_rows(model.model,fb.features)
        original={r.horse_id:r.win_probability for r in audited[rid].bundle.actual_prediction.predictions}
        if set(original)!=set(explanations) or any(abs(original[h]-explanations[h]['win_probability'])>1e-12 for h in original):
            raise ValueError('explanation inputs differ from frozen prediction')
        races[rid]=explanations
    payload=dict(manifest_sha256=hashlib.sha256((day/'race-day.json').read_bytes()).hexdigest(),
                 model_sha256=hashlib.sha256(Path(model_path).read_bytes()).hexdigest(),races=races)
    with (day/'prediction-explanations.json').open('x') as f:
        json.dump(dict(schema_version='1.0',sha256=hashlib.sha256(_canonical(payload)).hexdigest(),payload=payload),f,ensure_ascii=False,indent=2)


def load_day_explanations(day_directory, race_day):
    day=Path(day_directory);path=day/'prediction-explanations.json'
    if not path.is_file():return None
    data=json.loads(path.read_text());payload=data['payload']
    if data.get('schema_version')!='1.0' or hashlib.sha256(_canonical(payload)).hexdigest()!=data.get('sha256'):
        raise ValueError('invalid explanation integrity')
    if payload['manifest_sha256']!=hashlib.sha256((day/'race-day.json').read_bytes()).hexdigest():
        raise ValueError('explanations belong to a different day manifest')
    provenance=json.loads((day/'race-day-provenance.json').read_text())['payload']
    if payload['model_sha256'] != provenance['model_sha256']:
        raise ValueError('explanation model mismatch')
    expected={r.prediction.race_id:r.prediction for v in race_day.venues for r in v.races}
    if set(payload['races'])!=set(expected):raise ValueError('explanation race mismatch')
    for rid,pred in expected.items():
        by_id=payload['races'][rid]
        if set(by_id)!={r.horse_id for r in pred.runners}:raise ValueError('explanation runner mismatch')
        if any(abs(by_id[r.horse_id]['win_probability']-r.win_probability)>1e-12 for r in pred.runners):
            raise ValueError('explanation probabilities differ')
    return payload['races']
