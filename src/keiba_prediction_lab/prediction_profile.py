"""One local, hash-bound model/market policy shared by prediction and display."""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import tempfile

from .model_artifact import load_trained_model_artifact_bytes
from .race_day_pipeline import build_and_save_local_race_day, _publish_directory_no_replace
from .market_blend import build_market_blend_forecast_from_snapshot, save_market_blend_forecast, load_market_blend_forecast
from .win5 import build_market_blend_win5_forecast, save_win5_forecast


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate profile key')
        result[key] = value
    return result


def _time(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError('profile times must be timezone-aware')
    return dt


@dataclass(frozen=True)
class PredictionProfile:
    profile_id: str
    model_content: bytes
    model_sha256: str
    model_version: str
    market_weight: float
    activated_at: datetime
    validation_summary: str
    profile_content: bytes
    comparison_path: Path | None = None
    comparison: "PredictionProfile | None" = None

    def to_dict(self):
        return dict(profile_id=self.profile_id, model_version=self.model_version,
                    model_weight=1-self.market_weight, market_weight=self.market_weight,
                    activated_at=self.activated_at.isoformat(),
                    validation_summary=self.validation_summary,
                    comparison=self.comparison.to_dict() if self.comparison else None)


def load_prediction_profile(path, *, allow_comparison=True):
    path = Path(path)
    content = path.read_bytes()
    payload = json.loads(content, object_pairs_hook=_unique)
    keys = {'schema_version','profile_id','model_path','model_sha256','market_weight',
            'activated_at','validation_path','validation_sha256','validation_summary'}
    optional = {'comparison_profile'} if isinstance(payload, dict) and 'comparison_profile' in payload else set()
    if not isinstance(payload, dict) or payload.keys() != keys | optional or payload['schema_version'] != '1.0':
        raise ValueError('invalid prediction profile schema')
    for key in keys - {'schema_version','market_weight'}:
        if not isinstance(payload[key], str) or not payload[key].strip():
            raise ValueError('invalid profile text')
    weight = payload['market_weight']
    if type(weight) not in (int,float) or not math.isfinite(weight) or not 0 < weight < 1:
        raise ValueError('profile market weight must be between zero and one')
    def checked_file(field, digest):
        data = (path.parent / payload[field]).read_bytes()
        if hashlib.sha256(data).hexdigest() != payload[digest]:
            raise ValueError('profile file hash mismatch')
        return data
    model_content = checked_file('model_path','model_sha256')
    checked_file('validation_path','validation_sha256')
    model = load_trained_model_artifact_bytes(model_content).model
    activated = _time(payload['activated_at'])
    if activated <= max(model.trained_through, getattr(model,'calibrated_through',model.trained_through)):
        raise ValueError('profile activation must follow training and calibration')
    comparison_path = None
    comparison = None
    if optional:
        if not allow_comparison or not isinstance(payload['comparison_profile'],str) or not payload['comparison_profile'].strip():
            raise ValueError('invalid or nested comparison profile')
        comparison_path = path.parent / payload['comparison_profile']
        comparison = load_prediction_profile(comparison_path, allow_comparison=False)
    return PredictionProfile(payload['profile_id'],model_content,payload['model_sha256'],
                             model.model_version,float(weight),activated,
                             payload['validation_summary'],content,comparison_path,comparison)


def predict_profile_day(profile_path, history, plan, market_snapshot, output, *, frozen_at,
                        win5_race_ids=(), require_complete_body_weight=False, _profile=None):
    """Publish independent, blended and optional WIN5 together, never overwrite."""
    profile = _profile if _profile is not None else load_prediction_profile(profile_path)
    if frozen_at.tzinfo is None or frozen_at < profile.activated_at:
        raise ValueError('prediction predates active profile')
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.profile-day-',dir=output.parent) as temp:
        temp = Path(temp)
        model = temp/'model.json'
        model.write_bytes(profile.model_content)
        staged = temp/'day'
        build_and_save_local_race_day(model,history,plan,staged,frozen_at=frozen_at,
                                     require_complete_body_weight=require_complete_body_weight)
        blend = build_market_blend_forecast_from_snapshot(staged,market_snapshot,market_weight=profile.market_weight)
        if blend.observed_at < profile.activated_at or blend.observed_at > frozen_at:
            raise ValueError('market observation outside profile prediction window')
        independent = json.loads((staged/'race-day.json').read_text())
        expected = sum(len(v['races']) for v in independent['venues'])
        if len(blend.races) != expected:
            raise ValueError('market blend must cover the full profile day')
        blend_path = staged/'market-blend.json'
        save_market_blend_forecast(blend,blend_path)
        if win5_race_ids:
            save_win5_forecast(build_market_blend_win5_forecast(blend_path,win5_race_ids),staged/'win5-market-blend.json')
        if profile.comparison_path:
            predict_profile_day(profile.comparison_path,history,plan,market_snapshot,staged/'comparison',
                                frozen_at=frozen_at,win5_race_ids=win5_race_ids,
                                require_complete_body_weight=require_complete_body_weight, _profile=profile.comparison)
            compared = load_market_blend_forecast(staged/'comparison/market-blend.json')
            if (compared.cards_sha256, compared.observed_at) != (blend.cards_sha256, blend.observed_at):
                raise ValueError('market inputs changed between primary and comparison')
            def input_hashes(directory):
                result = {}
                for file in sorted((directory/'predictions').glob('*/input-provenance.json')):
                    payload = json.loads(file.read_text())['payload']
                    result[file.parent.name] = {k:v for k,v in payload.items()
                        if k not in ('model_sha256','input_data_version')}
                return result
            if input_hashes(staged) != input_hashes(staged/'comparison'):
                raise ValueError('race inputs changed between primary and comparison')
        receipt = {**profile.to_dict(),'model_sha256':profile.model_sha256,
                   'profile_sha256':hashlib.sha256(profile.profile_content).hexdigest()}
        (staged/'prediction-profile-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
        _publish_directory_no_replace(staged,output)
    return receipt
