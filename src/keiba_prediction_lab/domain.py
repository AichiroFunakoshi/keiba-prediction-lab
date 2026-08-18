"""Core records shared by data ingestion, prediction, and evaluation."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class BetType(str, Enum):
    WIN = "win"
    PLACE = "place"
    QUINELLA = "quinella"
    EXACTA = "exacta"
    TRIO = "trio"
    TRIFECTA = "trifecta"

    @property
    def selection_size(self) -> int:
        return {
            BetType.WIN: 1,
            BetType.PLACE: 1,
            BetType.QUINELLA: 2,
            BetType.EXACTA: 2,
            BetType.TRIO: 3,
            BetType.TRIFECTA: 3,
        }[self]


def _require_identifier(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


@dataclass(frozen=True)
class PredictionRecord:
    race_id: str
    horse_id: str
    predicted_at: datetime
    model_version: str
    win_probability: float
    top3_probability: float
    predicted_rank: int

    def __post_init__(self) -> None:
        _require_identifier(self.race_id, "race_id")
        _require_identifier(self.horse_id, "horse_id")
        _require_identifier(self.model_version, "model_version")
        if self.predicted_at.tzinfo is None or self.predicted_at.utcoffset() is None:
            raise ValueError("predicted_at must be timezone-aware")
        if not 0.0 <= self.win_probability <= 1.0:
            raise ValueError("win_probability must be between 0 and 1")
        if not 0.0 <= self.top3_probability <= 1.0:
            raise ValueError("top3_probability must be between 0 and 1")
        if self.top3_probability < self.win_probability:
            raise ValueError("top3_probability must not be below win_probability")
        if self.predicted_rank < 1:
            raise ValueError("predicted_rank must be positive")


@dataclass(frozen=True)
class ResultRecord:
    race_id: str
    horse_id: str
    finish_position: int | None
    result_status: str = "finished"

    def __post_init__(self) -> None:
        _require_identifier(self.race_id, "race_id")
        _require_identifier(self.horse_id, "horse_id")
        _require_identifier(self.result_status, "result_status")
        if self.finish_position is not None and self.finish_position < 1:
            raise ValueError("finish_position must be positive or None")
        if self.result_status == "finished" and self.finish_position is None:
            raise ValueError("finished horses require finish_position")


@dataclass(frozen=True)
class TicketResult:
    race_id: str
    bet_type: BetType
    selection: tuple[str, ...]
    payout_yen: int
    stake_yen: int = 100

    def __post_init__(self) -> None:
        _require_identifier(self.race_id, "race_id")
        if not self.selection or any(not item.strip() for item in self.selection):
            raise ValueError("selection must contain non-empty identifiers")
        if len(self.selection) != self.bet_type.selection_size:
            raise ValueError(
                f"{self.bet_type.value} requires "
                f"{self.bet_type.selection_size} selections"
            )
        if len(set(self.selection)) != len(self.selection):
            raise ValueError("selection identifiers must be unique")
        if self.payout_yen < 0:
            raise ValueError("payout_yen must not be negative")
        if self.stake_yen != 100:
            raise ValueError("stake_yen must be fixed at 100 yen")


def validate_race_predictions(
    predictions: tuple[PredictionRecord, ...], *, tolerance: float = 1e-9
) -> None:
    """Validate probability and ranking invariants within one race."""
    if not predictions:
        raise ValueError("at least one prediction is required")
    if tolerance <= 0.0:
        raise ValueError("tolerance must be positive")

    race_ids = {prediction.race_id for prediction in predictions}
    horse_ids = {prediction.horse_id for prediction in predictions}
    ranks = {prediction.predicted_rank for prediction in predictions}
    expected_ranks = set(range(1, len(predictions) + 1))

    if len(race_ids) != 1:
        raise ValueError("predictions must belong to one race")
    if len(horse_ids) != len(predictions):
        raise ValueError("horse_id must be unique within a race")
    if ranks != expected_ranks:
        raise ValueError("predicted_rank must be unique and consecutive")

    win_probability_sum = sum(
        prediction.win_probability for prediction in predictions
    )
    expected_top3_sum = min(3, len(predictions))
    top3_probability_sum = sum(
        prediction.top3_probability for prediction in predictions
    )
    if abs(win_probability_sum - 1.0) > tolerance:
        raise ValueError("win probabilities must sum to 1 within a race")
    if abs(top3_probability_sum - expected_top3_sum) > tolerance:
        raise ValueError(
            "top3 probabilities must sum to the available top-three places"
        )
