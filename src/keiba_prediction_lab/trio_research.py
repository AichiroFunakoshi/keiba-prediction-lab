"""Reproducible offline trio learning and fixed chronological evaluation."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from collections import defaultdict

from .trio_model import fit_trio_model, groups, score_distribution, save_model, load_model, VERSION
from .local_adapter import build_time_safe_training_bundle, build_local_feature_bundle, load_targets_csv
from .model import fit_conditional_logit, ABILITY_V5_FEATURE_NAMES
from .calibration import CalibrationRow, fit_temperature_scaling
from .bet_type_forecast import build_bet_type_forecast
from .domain import BetType

TEMPERATURES=(.5,.75,1.,1.5,2.,3.,5.)


def eligible(rows):
    raw=defaultdict(list)
    for row in rows:raw[row.features.race_id].append(row)
    kept=[];excluded=[]
    for rid,rs in sorted(raw.items()):
        try:groups(rs)
        except ValueError as e:excluded.append(dict(race_id=rid,reason=str(e)))
        else:kept.extend(rs)
    return tuple(kept),excluded


def aggregate(rows):
    if not rows:raise ValueError('evaluation races required')
    return dict(races=len(rows),hits=sum(r['hit'] for r in rows),accuracy=sum(r['hit'] for r in rows)/len(rows),
        log_loss=sum(r['log_loss'] for r in rows)/len(rows),brier=sum(r['brier'] for r in rows)/len(rows),
        mean_overlap=sum(r['overlap'] for r in rows)/len(rows))


def evaluate_fixed(training, windows_path, output):
    """Fit both objectives on identical past inputs; calibrate only on middle period."""
    output=Path(output)
    if output.exists():raise FileExistsError(output)
    windows=json.loads(Path(windows_path).read_text())
    parsed=[tuple(datetime.fromisoformat(w[k]) for k in ('train_end','calibration_end','evaluation_end')) for w in windows]
    if not parsed or any(any(t.tzinfo is None for t in w) or not w[0]<w[1]<w[2] for w in parsed) or any(a[2]>b[0] for a,b in zip(parsed,parsed[1:])):
        raise ValueError('invalid or overlapping chronological windows')
    bundle=build_time_safe_training_bundle(training);rows,excluded=eligible(bundle.rows);races=groups(rows)
    output.mkdir(parents=True)
    plan=dict(created_at=datetime.now(timezone.utc).isoformat(),training_sha256=bundle.training_sha256,windows_sha256=hashlib.sha256(Path(windows_path).read_bytes()).hexdigest(),windows=windows,temperatures=TEMPERATURES,
        candidate=VERSION,features='ability-v5 shared with baseline',parameters=dict(epochs=500,learning_rate=.1,l2_strength=.01),status='development_evaluation_not_untouched',exclusions=excluded)
    write_json(output/'plan.json',plan)
    folds=[];all_rows={'winner_v5':[],'trio_set':[]}
    for index,(train_end,cal_end,test_end) in enumerate(parsed):
        parts=[[r for r in races if condition(r[0].features.observed_at)] for condition in (lambda t:t<=train_end,lambda t:train_end<t<=cal_end,lambda t:cal_end<t<=test_end)]
        if any(not rs for rs in parts):raise ValueError('each time period requires eligible races')
        train,cal,test=parts;flat=[r for race in train for r in race]
        print(f'fold {index}: train={len(train)} calibration={len(cal)} test={len(test)}',flush=True)
        trio=fit_trio_model(flat)
        base=fit_conditional_logit(flat,feature_names=ABILITY_V5_FEATURE_NAMES,model_version='conditional-logit-ability-v5')
        winner=fit_temperature_scaling(base,[CalibrationRow(r.features,r.finish_position) for race in cal for r in race])
        calibration=[]
        for temp in TEMPERATURES:
            scores=[score_distribution(trio.predict([r.features for r in race],temperature=temp),[r.features.horse_id for r in race if r.finish_position<=3]) for race in cal]
            calibration.append(dict(temperature=temp,**aggregate(scores)))
        temperature=min(calibration,key=lambda x:(x['log_loss'],x['temperature']))['temperature']
        save_model(trio,output/f'trio-fold-{index}.json',training_sha256=bundle.training_sha256)
        detail={name:[] for name in all_rows}
        for race in test:
            fs=[r.features for r in race];actual=tuple(sorted(r.features.horse_id for r in race if r.finish_position<=3))
            distributions=dict(winner_v5=build_bet_type_forecast(winner.predict(fs)).for_bet_type(BetType.TRIO),trio_set=trio.predict(fs,temperature=temperature))
            for name,dist in distributions.items():
                row=dict(race_id=fs[0].race_id,actual=list(actual),selection=list(dist[0].selection),**score_distribution(dist,actual));detail[name].append(row);all_rows[name].append(row)
        folds.append(dict(window=windows[index],counts=dict(train=len(train),calibration=len(cal),evaluation=len(test)),winner_temperature=winner.temperature,trio_temperature=temperature,calibration=calibration,summary={k:aggregate(v) for k,v in detail.items()},rows=detail))
        print(folds[-1]['summary'],flush=True)
    report=dict(**plan,folds=folds,summary={k:aggregate(v) for k,v in all_rows.items()},decision='comparison_only_pending_future_meetings')
    write_json(output/'evaluation.json',report)
    return report


def write_json(path,payload):
    with Path(path).open('x') as f:json.dump(payload,f,ensure_ascii=False,indent=2,allow_nan=False)


def predict_race(model_path,history,targets,output,*,frozen_at):
    """New shadow prediction, never backdate or overwrite a saved race."""
    now=datetime.now(timezone.utc)
    if frozen_at.tzinfo is None or frozen_at<now:
        raise ValueError('freeze time must not be in the past')
    paths=dict(model=model_path,history=history,targets=targets)
    initial={k:hashlib.sha256(Path(v).read_bytes()).hexdigest() for k,v in paths.items()}
    model=load_model(model_path);target_rows=load_targets_csv(targets)
    if any(not r.observed_at<=frozen_at<r.scheduled_at for r in target_rows):raise ValueError('freeze must follow inputs and precede the race')
    fb=build_local_feature_bundle(history,targets)
    dist=model.predict(fb.features)
    if datetime.now(timezone.utc)>=frozen_at:raise ValueError('freeze deadline elapsed during generation')
    final={k:hashlib.sha256(Path(v).read_bytes()).hexdigest() for k,v in paths.items()}
    if initial!=final:raise ValueError('prediction inputs changed during generation')
    payload=dict(policy_version=VERSION,status='pre_race_shadow',created_at=now.isoformat(),frozen_at=frozen_at.isoformat(),race_id=fb.features[0].race_id,
        source_sha256=initial,
        stake_yen=0,candidate=asdict(dist[0]),probabilities=[asdict(x) for x in dist])
    write_json(output,payload)
    return payload


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    ev=sub.add_parser('evaluate');ev.add_argument('training',type=Path);ev.add_argument('windows',type=Path);ev.add_argument('output',type=Path)
    train=sub.add_parser('train');train.add_argument('training',type=Path);train.add_argument('output',type=Path)
    pred=sub.add_parser('predict');pred.add_argument('model',type=Path);pred.add_argument('history',type=Path);pred.add_argument('targets',type=Path);pred.add_argument('output',type=Path);pred.add_argument('--frozen-at',required=True)
    args=parser.parse_args(argv)
    if args.command=='evaluate':evaluate_fixed(args.training,args.windows,args.output)
    elif args.command=='train':
        if args.output.exists():raise FileExistsError(args.output)
        bundle=build_time_safe_training_bundle(args.training);rows,excluded=eligible(bundle.rows)
        if excluded:raise ValueError('training contains unsupported races; prepare and document an eligible source first')
        save_model(fit_trio_model(rows),args.output,training_sha256=bundle.training_sha256)
    else:predict_race(args.model,args.history,args.targets,args.output,frozen_at=datetime.fromisoformat(args.frozen_at))

if __name__=='__main__':main()
