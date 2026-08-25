"""One-race orchestration with a fixed actual-ticket policy."""

import json
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .bet_type_forecast import (
    BET_TYPE_GENERATOR_VERSION,
    FrozenBetTypeForecast,
    build_bet_type_forecast_from_combinations,
    freeze_built_bet_type_forecast,
    save_frozen_bet_type_forecast,
)
from .domain import BetType
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
PIPELINE_MANIFEST_SCHEMA_VERSION = "1.1"


@dataclass(frozen=True)
class RacePredictionBundle:
    """Actual one-ticket record plus explicitly non-purchased forecasts."""

    policy_version: str
    actual_prediction: FrozenPrediction
    baseline_shadow: FrozenShadowForecast
    pace_shadow: FrozenShadowForecast
    bet_type_shadow: FrozenBetTypeForecast

    def __post_init__(self) -> None:
        if self.policy_version != PIPELINE_POLICY_VERSION:
            raise ValueError("unsupported pipeline policy_version")
        actual = self.actual_prediction
        shadows = (
            self.baseline_shadow,
            self.pace_shadow,
            self.bet_type_shadow,
        )
        if len(actual.trifecta_tickets) != 1:
            raise ValueError("pipeline actual prediction must contain exactly one ticket")
        predicted_times = {row.predicted_at for row in actual.predictions}
        if len(predicted_times) != 1:
            raise ValueError("pipeline predictions must share predicted_at")
        source_predicted_at = next(iter(predicted_times))
        for shadow in shadows:
            if (
                shadow.forecast.race_id != actual.race_id
                or shadow.scheduled_at != actual.scheduled_at
                or shadow.frozen_at != actual.frozen_at
                or shadow.source_predicted_at != source_predicted_at
                or shadow.phase != actual.phase
                or shadow.input_data_version != actual.input_data_version
                or shadow.model_version != actual.model_version
            ):
                raise ValueError("pipeline artifacts must share race and freeze metadata")
        if self.baseline_shadow.generator_version != DEFAULT_GENERATOR_VERSION:
            raise ValueError("actual ticket policy requires the baseline generator")
        if self.pace_shadow.generator_version != PACE_GENERATOR_VERSION:
            raise ValueError("pace forecast must remain a versioned shadow artifact")
        if self.bet_type_shadow.generator_version != BET_TYPE_GENERATOR_VERSION:
            raise ValueError("bet type forecast must use the baseline marginals")
        bet_type_forecast = self.bet_type_shadow.forecast
        expected_win = {
            (row.horse_id,): row.win_probability for row in actual.predictions
        }
        expected_place = {
            (row.horse_id,): row.top3_probability for row in actual.predictions
        }
        forecast_win = {
            row.selection: row.probability
            for row in bet_type_forecast.for_bet_type(BetType.WIN)
        }
        forecast_place = {
            row.selection: row.probability
            for row in bet_type_forecast.for_bet_type(BetType.PLACE)
        }
        if forecast_win.keys() != expected_win.keys() or any(
            abs(forecast_win[key] - expected_win[key]) > 1e-8
            for key in expected_win
        ):
            raise ValueError("bet type win probabilities must equal the source model")
        if (
            bet_type_forecast.candidate_for(BetType.WIN).selection[0]
            != actual.trifecta_tickets[0].selection[0]
        ):
            raise ValueError(
                "actual trifecta winner anchor must equal the top win candidate"
            )
        if bet_type_forecast.place_payout_slots == 3 and (
            forecast_place.keys() != expected_place.keys()
            or any(
                abs(forecast_place[key] - expected_place[key]) > 1e-8
                for key in expected_place
            )
        ):
            raise ValueError("three-place probabilities must equal the source model")
        baseline_trifectas = {
            row.selection: row.probability
            for row in self.baseline_shadow.forecast.all_combinations
        }
        bet_type_trifectas = {
            row.selection: row.probability
            for row in bet_type_forecast.for_bet_type(BetType.TRIFECTA)
        }
        if bet_type_trifectas != baseline_trifectas:
            raise ValueError("bet type forecast must derive from the baseline distribution")
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
    place_payout_slots: int | None = None,
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
    bet_type_forecast = build_bet_type_forecast_from_combinations(
        predictions,
        baseline_shadow.forecast.all_combinations,
        place_payout_slots=place_payout_slots,
    )
    bet_type_shadow = freeze_built_bet_type_forecast(
        bet_type_forecast,
        scheduled_at=scheduled_at,
        frozen_at=frozen_at,
        source_predicted_at=predictions[0].predicted_at,
        phase=phase,
        input_data_version=input_data_version,
        model_version=predictions[0].model_version,
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
        bet_type_shadow=bet_type_shadow,
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
        bet_type_digest = save_frozen_bet_type_forecast(
            bundle.bet_type_shadow, target / "bet-types-shadow.json"
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
                    "artifact_type": "trifecta_portfolios",
                    "generator_version": bundle.baseline_shadow.generator_version,
                    "stake_yen": 0,
                },
                {
                    "file": "pace-shadow.json",
                    "sha256": pace_digest,
                    "artifact_type": "trifecta_portfolios",
                    "generator_version": bundle.pace_shadow.generator_version,
                    "stake_yen": 0,
                },
                {
                    "file": "bet-types-shadow.json",
                    "sha256": bet_type_digest,
                    "artifact_type": "bet_type_candidates",
                    "generator_version": bundle.bet_type_shadow.generator_version,
                    "candidate_count": len(
                        bundle.bet_type_shadow.forecast.candidates
                    ),
                    "place_payout_slots": (
                        bundle.bet_type_shadow.forecast.place_payout_slots
                    ),
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
