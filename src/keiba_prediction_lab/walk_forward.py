"""Expanding-window evaluation with strict train, calibration, and test periods."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from .baselines import (
    BaselineRunner,
    BaselineScore,
    UniformBaseline,
    evaluate_baseline_predictions,
)
from .calibration import CalibrationRow, fit_temperature_scaling
from .domain import PredictionRecord, ResultRecord
from .metrics import CalibrationSummary, calibration_summary
from .model import TrainingRow, fit_conditional_logit


@dataclass(frozen=True)
class WalkForwardWindow:
    train_end: datetime
    calibration_end: datetime
    evaluation_end: datetime

    def __post_init__(self) -> None:
        for field_name in ("train_end", "calibration_end", "evaluation_end"):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware")
        if not self.train_end < self.calibration_end < self.evaluation_end:
            raise ValueError("window boundaries must satisfy train < calibration < evaluation")


@dataclass(frozen=True)
class WalkForwardFoldResult:
    window: WalkForwardWindow
    training_race_count: int
    calibration_race_count: int
    evaluation_race_count: int
    temperature: float
    model_score: BaselineScore
    uniform_score: BaselineScore


@dataclass(frozen=True)
class WalkForwardResult:
    folds: tuple[WalkForwardFoldResult, ...]
    aggregate_model_score: BaselineScore
    aggregate_uniform_score: BaselineScore
    calibration: CalibrationSummary


def _group_rows(rows: Sequence[TrainingRow]) -> dict[str, list[TrainingRow]]:
    races: dict[str, list[TrainingRow]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row.features.race_id, row.features.horse_id)
        if key in seen:
            raise ValueError("rows contain duplicate race and horse")
        seen.add(key)
        races[row.features.race_id].append(row)
    for race_id, race_rows in races.items():
        if len({row.features.observed_at for row in race_rows}) != 1:
            raise ValueError(f"race {race_id} must share observed_at")
        if not any(row.finish_position == 1 for row in race_rows):
            raise ValueError(f"race {race_id} has no winner")
    return races


def _race_time(rows: Sequence[TrainingRow]) -> datetime:
    return rows[0].features.observed_at


def _flatten(races: Sequence[Sequence[TrainingRow]]) -> tuple[TrainingRow, ...]:
    return tuple(row for race in races for row in race)


def run_walk_forward(
    rows: Sequence[TrainingRow],
    windows: Sequence[WalkForwardWindow],
) -> WalkForwardResult:
    """Refit, recalibrate, and evaluate once for every chronological window."""
    if not rows:
        raise ValueError("at least one labeled row is required")
    if not windows:
        raise ValueError("at least one walk-forward window is required")
    for previous, current in zip(windows, windows[1:]):
        if current.train_end < previous.evaluation_end:
            raise ValueError("walk-forward evaluation periods must not overlap")
        if current.evaluation_end <= previous.evaluation_end:
            raise ValueError("walk-forward windows must move forward")

    grouped = _group_rows(rows)
    ordered_races = sorted(
        grouped.values(), key=lambda race: (_race_time(race), race[0].features.race_id)
    )
    fold_results = []
    all_model_predictions: list[PredictionRecord] = []
    all_uniform_predictions: list[PredictionRecord] = []
    all_results: list[ResultRecord] = []

    for window in windows:
        training = [race for race in ordered_races if _race_time(race) <= window.train_end]
        calibration = [
            race for race in ordered_races
            if window.train_end < _race_time(race) <= window.calibration_end
        ]
        evaluation = [
            race for race in ordered_races
            if window.calibration_end < _race_time(race) <= window.evaluation_end
        ]
        if not training or not calibration or not evaluation:
            raise ValueError("every window requires training, calibration, and evaluation races")

        base_model = fit_conditional_logit(_flatten(training))
        calibrated_model = fit_temperature_scaling(
            base_model,
            tuple(
                CalibrationRow(row.features, row.finish_position)
                for row in _flatten(calibration)
            ),
        )
        fold_model_predictions: list[PredictionRecord] = []
        fold_uniform_predictions: list[PredictionRecord] = []
        fold_results_rows: list[ResultRecord] = []
        for race in evaluation:
            race = sorted(race, key=lambda row: row.features.post_position)
            features = [row.features for row in race]
            model_predictions = calibrated_model.predict(features)
            scheduled_at = features[0].observed_at + timedelta(seconds=1)
            uniform_predictions = UniformBaseline().predict(
                tuple(
                    BaselineRunner(
                        row.features.race_id,
                        scheduled_at,
                        row.features.horse_id,
                        row.features.post_position,
                    )
                    for row in race
                ),
                predicted_at=features[0].observed_at,
            )
            results = tuple(
                ResultRecord(
                    row.features.race_id,
                    row.features.horse_id,
                    row.finish_position,
                )
                for row in race
            )
            fold_model_predictions.extend(model_predictions)
            fold_uniform_predictions.extend(uniform_predictions)
            fold_results_rows.extend(results)

        model_score = evaluate_baseline_predictions(
            fold_model_predictions, fold_results_rows
        )
        uniform_score = evaluate_baseline_predictions(
            fold_uniform_predictions, fold_results_rows
        )
        fold_results.append(WalkForwardFoldResult(
            window=window,
            training_race_count=len(training),
            calibration_race_count=len(calibration),
            evaluation_race_count=len(evaluation),
            temperature=calibrated_model.temperature,
            model_score=model_score,
            uniform_score=uniform_score,
        ))
        all_model_predictions.extend(fold_model_predictions)
        all_uniform_predictions.extend(fold_uniform_predictions)
        all_results.extend(fold_results_rows)

    outcomes = [int(result.finish_position == 1) for result in all_results]
    return WalkForwardResult(
        folds=tuple(fold_results),
        aggregate_model_score=evaluate_baseline_predictions(
            all_model_predictions, all_results
        ),
        aggregate_uniform_score=evaluate_baseline_predictions(
            all_uniform_predictions, all_results
        ),
        calibration=calibration_summary(
            [prediction.win_probability for prediction in all_model_predictions],
            outcomes,
        ),
    )
