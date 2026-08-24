"""Dependency-free conditional logit model for race-level probabilities."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from math import exp, isfinite, log1p, sqrt

from .domain import PredictionRecord, validate_race_predictions
from .features import FeatureRow


@dataclass(frozen=True)
class TrainingRow:
    features: FeatureRow
    finish_position: int

    def __post_init__(self) -> None:
        if self.finish_position < 1:
            raise ValueError("finish_position must be positive")


CONDITIONAL_LOGIT_FEATURE_NAMES = (
    "post_position",
    "carried_weight_kg",
    "body_weight_kg",
    "body_weight_missing",
    "days_since_last_run",
    "days_since_last_run_missing",
    "log_horse_starts",
    "horse_win_rate",
    "horse_top3_rate",
    "horse_venue_win_rate",
    "horse_surface_win_rate",
    "horse_track_condition_win_rate",
    "horse_distance_band_win_rate",
    "log_jockey_starts",
    "jockey_win_rate",
    "log_trainer_starts",
    "trainer_win_rate",
)


def _raw_features(row: FeatureRow) -> tuple[float, ...]:
    if not row.race_id.strip() or not row.horse_id.strip():
        raise ValueError("race_id and horse_id must not be empty")
    if row.observed_at.tzinfo is None or row.observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    counts = (
        row.horse_starts,
        row.horse_venue_starts,
        row.horse_surface_starts,
        row.horse_track_condition_starts,
        row.horse_distance_band_starts,
        row.jockey_starts,
        row.trainer_starts,
    )
    rates = (
        row.horse_win_rate,
        row.horse_top3_rate,
        row.horse_venue_win_rate,
        row.horse_surface_win_rate,
        row.horse_track_condition_win_rate,
        row.horse_distance_band_win_rate,
        row.jockey_win_rate,
        row.trainer_win_rate,
    )
    if any(count < 0 for count in counts):
        raise ValueError("feature start counts must not be negative")
    if any(not 0.0 <= rate <= 1.0 for rate in rates):
        raise ValueError("feature rates must be between 0 and 1")
    if row.post_position < 1 or row.carried_weight_kg <= 0:
        raise ValueError("post_position and carried_weight_kg must be positive")
    if row.body_weight_kg is not None and row.body_weight_kg <= 0:
        raise ValueError("body_weight_kg must be positive or None")
    if row.days_since_last_run is not None and row.days_since_last_run < 0:
        raise ValueError("days_since_last_run must not be negative")
    values = (
        float(row.post_position),
        row.carried_weight_kg,
        float(row.body_weight_kg or 0),
        float(row.body_weight_kg is None),
        float(row.days_since_last_run or 0),
        float(row.days_since_last_run is None),
        log1p(row.horse_starts),
        row.horse_win_rate,
        row.horse_top3_rate,
        row.horse_venue_win_rate,
        row.horse_surface_win_rate,
        row.horse_track_condition_win_rate,
        row.horse_distance_band_win_rate,
        log1p(row.jockey_starts),
        row.jockey_win_rate,
        log1p(row.trainer_starts),
        row.trainer_win_rate,
    )
    if any(not isfinite(value) for value in values):
        raise ValueError("model features must be finite")
    return values


def _softmax(scores: Sequence[float]) -> list[float]:
    largest = max(scores)
    weights = [exp(score - largest) for score in scores]
    total = sum(weights)
    return [weight / total for weight in weights]


def _top3_probabilities(weights: Sequence[float]) -> list[float]:
    runner_count = len(weights)
    if runner_count <= 3:
        return [1.0] * runner_count

    total_weight = sum(weights)
    probabilities = [0.0] * runner_count
    for first in range(runner_count):
        first_probability = weights[first] / total_weight
        remaining_after_first = total_weight - weights[first]
        for second in range(runner_count):
            if second == first:
                continue
            second_probability = weights[second] / remaining_after_first
            remaining_after_second = remaining_after_first - weights[second]
            for third in range(runner_count):
                if third in (first, second):
                    continue
                probability = (
                    first_probability
                    * second_probability
                    * weights[third]
                    / remaining_after_second
                )
                probabilities[first] += probability
                probabilities[second] += probability
                probabilities[third] += probability
    return [min(max(value, 0.0), 1.0) for value in probabilities]


@dataclass(frozen=True)
class ConditionalLogitModel:
    coefficients: tuple[float, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    trained_through: datetime
    model_version: str = "conditional-logit-v1"

    @property
    def feature_names(self) -> tuple[str, ...]:
        return CONDITIONAL_LOGIT_FEATURE_NAMES

    def predict(self, rows: Sequence[FeatureRow]) -> tuple[PredictionRecord, ...]:
        if not rows:
            raise ValueError("at least one feature row is required")
        if len({row.race_id for row in rows}) != 1:
            raise ValueError("feature rows must belong to one race")
        if len({row.observed_at for row in rows}) != 1:
            raise ValueError("feature rows must share observed_at")
        if len({row.horse_id for row in rows}) != len(rows):
            raise ValueError("horse_id must be unique within a race")
        if len({row.post_position for row in rows}) != len(rows):
            raise ValueError("post_position must be unique within a race")
        if rows[0].observed_at <= self.trained_through:
            raise ValueError("prediction must be later than all training rows")

        vectors = [_standardize(_raw_features(row), self.means, self.scales) for row in rows]
        scores = [sum(weight * value for weight, value in zip(self.coefficients, vector)) for vector in vectors]
        win_probabilities = _softmax(scores)
        largest = max(scores)
        strengths = [exp(score - largest) for score in scores]
        top3_probabilities = _top3_probabilities(strengths)
        ranked = sorted(
            range(len(rows)),
            key=lambda index: (-win_probabilities[index], rows[index].post_position),
        )
        ranks = {index: rank for rank, index in enumerate(ranked, start=1)}
        predictions = tuple(
            PredictionRecord(
                race_id=row.race_id,
                horse_id=row.horse_id,
                predicted_at=row.observed_at,
                model_version=self.model_version,
                win_probability=win_probabilities[index],
                top3_probability=top3_probabilities[index],
                predicted_rank=ranks[index],
            )
            for index, row in enumerate(rows)
        )
        validate_race_predictions(predictions, tolerance=1e-8)
        return predictions


def _standardize(
    values: Sequence[float], means: Sequence[float], scales: Sequence[float]
) -> tuple[float, ...]:
    return tuple(
        (value - mean) / scale
        for value, mean, scale in zip(values, means, scales)
    )


def fit_conditional_logit(
    rows: Sequence[TrainingRow],
    *,
    epochs: int = 500,
    learning_rate: float = 0.1,
    l2_strength: float = 0.01,
) -> ConditionalLogitModel:
    """Fit a race-conditional winner model with deterministic batch descent."""
    if not rows:
        raise ValueError("at least one training row is required")
    if epochs < 1:
        raise ValueError("epochs must be positive")
    if learning_rate <= 0.0:
        raise ValueError("learning_rate must be positive")
    if l2_strength < 0.0:
        raise ValueError("l2_strength must not be negative")

    races: dict[str, list[TrainingRow]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for row in rows:
        _raw_features(row.features)
        key = (row.features.race_id, row.features.horse_id)
        if key in seen:
            raise ValueError("training rows contain duplicate race and horse")
        seen.add(key)
        races[row.features.race_id].append(row)

    for race_id, race_rows in races.items():
        if len(race_rows) < 2:
            raise ValueError(f"training race {race_id} requires at least two runners")
        if not any(row.finish_position == 1 for row in race_rows):
            raise ValueError(f"training race {race_id} has no winner")
        if len({row.features.observed_at for row in race_rows}) != 1:
            raise ValueError("training race rows must share observed_at")

    raw_vectors = [_raw_features(row.features) for row in rows]
    feature_count = len(CONDITIONAL_LOGIT_FEATURE_NAMES)
    means = tuple(
        sum(vector[index] for vector in raw_vectors) / len(raw_vectors)
        for index in range(feature_count)
    )
    scales = tuple(
        max(
            sqrt(
                sum((vector[index] - means[index]) ** 2 for vector in raw_vectors)
                / len(raw_vectors)
            ),
            1.0,
        )
        for index in range(feature_count)
    )
    vectors = {
        (row.features.race_id, row.features.horse_id): _standardize(
            _raw_features(row.features), means, scales
        )
        for row in rows
    }

    coefficients = [0.0] * feature_count
    ordered_races = [races[race_id] for race_id in sorted(races)]
    for _ in range(epochs):
        gradient = [l2_strength * value for value in coefficients]
        for race_rows in ordered_races:
            race_vectors = [
                vectors[(row.features.race_id, row.features.horse_id)]
                for row in race_rows
            ]
            scores = [
                sum(weight * value for weight, value in zip(coefficients, vector))
                for vector in race_vectors
            ]
            probabilities = _softmax(scores)
            winners = [index for index, row in enumerate(race_rows) if row.finish_position == 1]
            winner_credit = 1.0 / len(winners)
            for index, vector in enumerate(race_vectors):
                target = winner_credit if index in winners else 0.0
                for feature_index, value in enumerate(vector):
                    gradient[feature_index] += (probabilities[index] - target) * value
        step = learning_rate / len(ordered_races)
        coefficients = [
            value - step * change
            for value, change in zip(coefficients, gradient)
        ]

    return ConditionalLogitModel(
        coefficients=tuple(coefficients),
        means=means,
        scales=scales,
        trained_through=max(row.features.observed_at for row in rows),
    )
