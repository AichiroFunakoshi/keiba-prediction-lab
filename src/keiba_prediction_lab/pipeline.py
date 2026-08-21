"""One-race orchestration with a fixed actual-ticket policy."""

import json
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .features import FeatureRow
from .frozen import (
    FrozenPrediction,
    FrozenTrifectaTicket,
    PredictionPhase,
    save_frozen_prediction,
)
from .model import ConditionalLogitModel
from .pace import (
    PACE_GENERATOR_VERSION,
    PaceRunnerProfile,
    RacePaceScenario,
    build_pace_conditioned_forecast,
)
from .shadow_snapshot import (
    DEFAULT_GENERATOR_VERSION,
    FrozenShadowForecast,
    freeze_built_shadow_forecast,
    freeze_shadow_forecast,
    save_frozen_shadow_forecast,
)


PIPELINE_POLICY_VERSION = "research-one-ticket-v1"
PIPELINE_MANIFEST_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class RacePredictionBundle:
    """Actual one-ticket record plus two explicitly non-purchased forecasts."""

    policy_version: str
    actual_prediction: FrozenPrediction
    baseline_shadow: FrozenShadowForecast
    pace_shadow: FrozenShadowForecast

    def __post_init__(self) -> None:
        if self.policy_version != PIPELINE_POLICY_VERSION:
            raise ValueError("unsupported pipeline policy_version")
        actual = self.actual_prediction
        shadows = (self.baseline_shadow, self.pace_shadow)
        if len(actual.trifecta_tickets) != 1:
            raise ValueError("pipeline actual prediction must contain exactly one ticket")
        for shadow in shadows:
            if (
                shadow.forecast.race_id != actual.race_id
                or shadow.scheduled_at != actual.scheduled_at
                or shadow.frozen_at != actual.frozen_at
                or shadow.phase != actual.phase
                or shadow.input_data_version != actual.input_data_version
                or shadow.model_version != actual.model_version
            ):
                raise ValueError("pipeline artifacts must share race and freeze metadata")
        if self.baseline_shadow.generator_version != DEFAULT_GENERATOR_VERSION:
            raise ValueError("actual ticket policy requires the baseline generator")
        if self.pace_shadow.generator_version != PACE_GENERATOR_VERSION:
            raise ValueError("pace forecast must remain a versioned shadow artifact")
        if (
            actual.trifecta_tickets[0].selection
            != self.baseline_shadow.forecast.primary_ticket.selection
        ):
            raise ValueError("actual ticket must equal the baseline primary ticket")


def run_race_prediction_pipeline(
    model: ConditionalLogitModel,
    feature_rows: Sequence[FeatureRow],
    pace_profiles: Sequence[PaceRunnerProfile],
    pace_scenario: RacePaceScenario,
    *,
    scheduled_at: datetime,
    frozen_at: datetime,
    phase: PredictionPhase,
    input_data_version: str,
) -> RacePredictionBundle:
    """Generate and freeze one actual candidate and non-purchased shadows."""
    predictions = model.predict(feature_rows)
    baseline_shadow = freeze_shadow_forecast(
        predictions,
        scheduled_at=scheduled_at,
        frozen_at=frozen_at,
        phase=phase,
        input_data_version=input_data_version,
    )
    pace_forecast = build_pace_conditioned_forecast(
        predictions, pace_profiles, pace_scenario
    )
    pace_shadow = freeze_built_shadow_forecast(
        pace_forecast,
        scheduled_at=scheduled_at,
        frozen_at=frozen_at,
        source_predicted_at=predictions[0].predicted_at,
        phase=phase,
        input_data_version=input_data_version,
        model_version=predictions[0].model_version,
        generator_version=PACE_GENERATOR_VERSION,
    )
    actual = FrozenPrediction(
        race_id=predictions[0].race_id,
        scheduled_at=scheduled_at,
        frozen_at=frozen_at,
        phase=phase,
        input_data_version=input_data_version,
        predictions=predictions,
        trifecta_tickets=(
            FrozenTrifectaTicket(baseline_shadow.forecast.primary_ticket.selection),
        ),
    )
    return RacePredictionBundle(
        policy_version=PIPELINE_POLICY_VERSION,
        actual_prediction=actual,
        baseline_shadow=baseline_shadow,
        pace_shadow=pace_shadow,
    )


def save_race_prediction_bundle(
    bundle: RacePredictionBundle, directory: str | Path
) -> Path:
    """Save a new bundle directory; existing paths are never overwritten."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=False)
    try:
        actual_digest = save_frozen_prediction(
            bundle.actual_prediction, target / "actual.json"
        )
        baseline_digest = save_frozen_shadow_forecast(
            bundle.baseline_shadow, target / "baseline-shadow.json"
        )
        pace_digest = save_frozen_shadow_forecast(
            bundle.pace_shadow, target / "pace-shadow.json"
        )
        manifest = {
            "schema_version": PIPELINE_MANIFEST_SCHEMA_VERSION,
            "policy_version": bundle.policy_version,
            "race_id": bundle.actual_prediction.race_id,
            "actual": {
                "file": "actual.json",
                "sha256": actual_digest,
                "generator_version": bundle.baseline_shadow.generator_version,
                "ticket_count": 1,
                "stake_yen": 100,
            },
            "shadows": [
                {
                    "file": "baseline-shadow.json",
                    "sha256": baseline_digest,
                    "generator_version": bundle.baseline_shadow.generator_version,
                    "stake_yen": 0,
                },
                {
                    "file": "pace-shadow.json",
                    "sha256": pace_digest,
                    "generator_version": bundle.pace_shadow.generator_version,
                    "stake_yen": 0,
                },
            ],
        }
        manifest_path = target / "manifest.json"
        with manifest_path.open("x", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
        return manifest_path
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
