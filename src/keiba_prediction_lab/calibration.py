"""Time-separated temperature scaling for race probabilities."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from math import log

from .domain import PredictionRecord, validate_race_predictions
from .features import FeatureRow
from .model import ConditionalLogitModel, _top3_probabilities


@dataclass(frozen=True)
class CalibrationRow:
    features: FeatureRow
    finish_position: int

    def __post_init__(self) -> None:
        if self.finish_position < 1:
            raise ValueError("finish_position must be positive")


def temperature_scale_predictions(
    predictions: Sequence[PredictionRecord],
    temperature: float,
    *,
    model_version: str,
) -> tuple[PredictionRecord, ...]:
    """Adjust confidence without changing the ordering of a race."""
    if not predictions:
        raise ValueError("at least one prediction is required")
    if temperature <= 0.0:
        raise ValueError("temperature must be positive")
    if not model_version.strip():
        raise ValueError("model_version must not be empty")
    if len({row.race_id for row in predictions}) != 1:
        raise ValueError("predictions must belong to one race")

    strengths = [row.win_probability ** (1.0 / temperature) for row in predictions]
    total = sum(strengths)
    win_probabilities = [value / total for value in strengths]
    top3_probabilities = _top3_probabilities(strengths)
    scaled = tuple(
        PredictionRecord(
            race_id=row.race_id,
            horse_id=row.horse_id,
            predicted_at=row.predicted_at,
            model_version=model_version,
            win_probability=win_probabilities[index],
            top3_probability=top3_probabilities[index],
            predicted_rank=row.predicted_rank,
        )
        for index, row in enumerate(predictions)
    )
    validate_race_predictions(scaled, tolerance=1e-8)
    return scaled


@dataclass(frozen=True)
class TemperatureCalibratedModel:
    base_model: ConditionalLogitModel
    temperature: float
    calibrated_through: datetime

    @property
    def model_version(self) -> str:
        return f"{self.base_model.model_version}-temperature-v1"

    def predict(self, rows: Sequence[FeatureRow]) -> tuple[PredictionRecord, ...]:
        if not rows:
            raise ValueError("at least one feature row is required")
        if any(row.observed_at <= self.calibrated_through for row in rows):
            raise ValueError("prediction must be later than all calibration rows")
        return temperature_scale_predictions(
            self.base_model.predict(rows),
            self.temperature,
            model_version=self.model_version,
        )


def fit_temperature_scaling(
    base_model: ConditionalLogitModel,
    rows: Sequence[CalibrationRow],
    *,
    temperatures: Sequence[float] | None = None,
) -> TemperatureCalibratedModel:
    """Select temperature on data strictly later than model training data."""
    if not rows:
        raise ValueError("at least one calibration row is required")
    candidates = tuple(temperatures or (index / 20 for index in range(10, 101)))
    if not candidates or any(value <= 0.0 for value in candidates):
        raise ValueError("temperatures must contain positive values")

    races: dict[str, list[CalibrationRow]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row.features.race_id, row.features.horse_id)
        if key in seen:
            raise ValueError("calibration rows contain duplicate race and horse")
        seen.add(key)
        races[row.features.race_id].append(row)

    race_data: list[tuple[tuple[PredictionRecord, ...], tuple[int, ...]]] = []
    for race_id in sorted(races):
        race_rows = races[race_id]
        if len(race_rows) < 2:
            raise ValueError(f"calibration race {race_id} requires at least two runners")
        winners = tuple(
            index for index, row in enumerate(race_rows) if row.finish_position == 1
        )
        if not winners:
            raise ValueError(f"calibration race {race_id} has no winner")
        predictions = base_model.predict([row.features for row in race_rows])
        race_data.append((predictions, winners))

    def loss(temperature: float) -> float:
        total = 0.0
        for predictions, winners in race_data:
            scaled = temperature_scale_predictions(
                predictions, temperature, model_version="calibration-candidate"
            )
            credit = 1.0 / len(winners)
            total -= sum(
                credit * log(max(scaled[index].win_probability, 1e-15))
                for index in winners
            )
        return total / len(race_data)

    temperature = min(candidates, key=lambda value: (loss(value), abs(value - 1.0)))
    return TemperatureCalibratedModel(
        base_model=base_model,
        temperature=temperature,
        calibrated_through=max(row.features.observed_at for row in rows),
    )
