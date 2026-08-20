"""Explicit pace-scenario baseline for conditional trifecta probabilities."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import exp

from .domain import PredictionRecord, validate_race_predictions
from .trifecta import (
    DEFAULT_PORTFOLIO_SIZES,
    TrifectaCombination,
    TrifectaForecast,
    build_trifecta_forecast_from_combinations,
)


PACE_GENERATOR_VERSION = "pace-scenario-v1"


class RunningStyle(str, Enum):
    LEADER = "leader"
    PRESSER = "presser"
    STALKER = "stalker"
    CLOSER = "closer"


class ExpectedPace(str, Enum):
    SLOW = "slow"
    AVERAGE = "average"
    FAST = "fast"


@dataclass(frozen=True)
class PaceRunnerProfile:
    race_id: str
    horse_id: str
    observed_at: datetime
    running_style: RunningStyle
    early_speed: float
    late_speed: float
    pace_resilience: float

    def __post_init__(self) -> None:
        if not self.race_id.strip() or not self.horse_id.strip():
            raise ValueError("race_id and horse_id must not be empty")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if any(
            not 0.0 <= value <= 1.0
            for value in (self.early_speed, self.late_speed, self.pace_resilience)
        ):
            raise ValueError("pace profile values must be between 0 and 1")


@dataclass(frozen=True)
class RacePaceScenario:
    race_id: str
    observed_at: datetime
    expected_pace: ExpectedPace
    confidence: float

    def __post_init__(self) -> None:
        if not self.race_id.strip():
            raise ValueError("race_id must not be empty")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("pace confidence must be between 0 and 1")


_BASE_PACE = {
    ExpectedPace.SLOW: -1.0,
    ExpectedPace.AVERAGE: 0.0,
    ExpectedPace.FAST: 1.0,
}

_WINNER_SCENARIO_SHIFT = {
    RunningStyle.LEADER: -0.50,
    RunningStyle.PRESSER: -0.20,
    RunningStyle.STALKER: 0.20,
    RunningStyle.CLOSER: 0.50,
}


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


def _realized_pace(
    scenario: RacePaceScenario, winner: PaceRunnerProfile
) -> float:
    return _clamp(
        _BASE_PACE[scenario.expected_pace] * scenario.confidence
        + _WINNER_SCENARIO_SHIFT[winner.running_style],
        -1.0,
        1.0,
    )


def _remaining_strength(
    base_probability: float,
    profile: PaceRunnerProfile,
    realized_pace: float,
    *,
    third_place: bool,
) -> float:
    pace_fit = realized_pace * (profile.late_speed - profile.early_speed)
    endurance = profile.pace_resilience * (0.45 if third_place else 0.25)
    late_bonus = profile.late_speed * (0.20 if third_place else 0.0)
    return base_probability * exp(0.85 * pace_fit + endurance + late_bonus)


def rank_pace_conditioned_trifectas(
    predictions: Sequence[PredictionRecord],
    profiles: Sequence[PaceRunnerProfile],
    scenario: RacePaceScenario,
) -> tuple[TrifectaCombination, ...]:
    """Return a normalized joint distribution whose lower places depend on the winner."""
    if len(predictions) < 3:
        raise ValueError("at least three runners are required")
    validate_race_predictions(predictions, tolerance=1e-8)
    if any(row.win_probability <= 0.0 for row in predictions):
        raise ValueError("pace-conditioned ranking requires positive win probabilities")
    race_ids = {row.race_id for row in predictions}
    predicted_times = {row.predicted_at for row in predictions}
    if len(race_ids) != 1 or len(predicted_times) != 1:
        raise ValueError("predictions must share race_id and predicted_at")
    race_id = next(iter(race_ids))
    predicted_at = next(iter(predicted_times))
    if scenario.race_id != race_id:
        raise ValueError("pace scenario race_id must match predictions")
    if scenario.observed_at > predicted_at:
        raise ValueError("pace scenario must be observed by predicted_at")

    profile_by_horse = {row.horse_id: row for row in profiles}
    runner_ids = {row.horse_id for row in predictions}
    if len(profile_by_horse) != len(profiles) or profile_by_horse.keys() != runner_ids:
        raise ValueError("pace profiles must match prediction runners exactly")
    if any(row.race_id != race_id for row in profiles):
        raise ValueError("pace profile race_id must match predictions")
    if any(row.observed_at > predicted_at for row in profiles):
        raise ValueError("pace profiles must be observed by predicted_at")

    probability_by_horse = {
        row.horse_id: row.win_probability for row in predictions
    }
    combinations = []
    for first in sorted(runner_ids):
        realized_pace = _realized_pace(scenario, profile_by_horse[first])
        second_strengths = {
            horse_id: _remaining_strength(
                probability_by_horse[horse_id], profile_by_horse[horse_id],
                realized_pace, third_place=False,
            )
            for horse_id in runner_ids if horse_id != first
        }
        second_total = sum(second_strengths.values())
        for second in sorted(second_strengths):
            third_strengths = {
                horse_id: _remaining_strength(
                    probability_by_horse[horse_id], profile_by_horse[horse_id],
                    realized_pace, third_place=True,
                )
                for horse_id in runner_ids if horse_id not in (first, second)
            }
            third_total = sum(third_strengths.values())
            for third in sorted(third_strengths):
                combinations.append(TrifectaCombination(
                    (first, second, third),
                    probability_by_horse[first]
                    * second_strengths[second] / second_total
                    * third_strengths[third] / third_total,
                ))
    return tuple(sorted(combinations, key=lambda row: (-row.probability, row.selection)))


def build_pace_conditioned_forecast(
    predictions: Sequence[PredictionRecord],
    profiles: Sequence[PaceRunnerProfile],
    scenario: RacePaceScenario,
    *,
    portfolio_sizes: Sequence[int] = DEFAULT_PORTFOLIO_SIZES,
) -> TrifectaForecast:
    combinations = rank_pace_conditioned_trifectas(
        predictions, profiles, scenario
    )
    return build_trifecta_forecast_from_combinations(
        predictions, combinations, portfolio_sizes=portfolio_sizes
    )
