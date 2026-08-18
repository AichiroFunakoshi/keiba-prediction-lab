"""Dependency-free probability baselines for future model comparisons."""

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from .domain import PredictionRecord, ResultRecord, validate_race_predictions
from .metrics import binary_brier_score, binary_log_loss


@dataclass(frozen=True)
class BaselineRunner:
    race_id: str
    scheduled_at: datetime
    horse_id: str
    post_position: int
    finish_position: int | None = None

    def __post_init__(self) -> None:
        if not self.race_id.strip() or not self.horse_id.strip():
            raise ValueError("race_id and horse_id must not be empty")
        if self.scheduled_at.tzinfo is None or self.scheduled_at.utcoffset() is None:
            raise ValueError("scheduled_at must be timezone-aware")
        if self.post_position < 1:
            raise ValueError("post_position must be positive")
        if self.finish_position is not None and self.finish_position < 1:
            raise ValueError("finish_position must be positive or None")


@dataclass(frozen=True)
class BaselineScore:
    model_version: str
    race_count: int
    runner_count: int
    top1_accuracy: float
    win_brier_score: float
    win_log_loss: float


def _validate_target_race(
    runners: Sequence[BaselineRunner], predicted_at: datetime
) -> None:
    if not runners:
        raise ValueError("at least one target runner is required")
    if predicted_at.tzinfo is None or predicted_at.utcoffset() is None:
        raise ValueError("predicted_at must be timezone-aware")
    if any(runner.finish_position is not None for runner in runners):
        raise ValueError("target runners must not contain finish_position")
    if len({runner.race_id for runner in runners}) != 1:
        raise ValueError("target runners must belong to one race")
    if len({runner.scheduled_at for runner in runners}) != 1:
        raise ValueError("target runners must share scheduled_at")
    if len({runner.horse_id for runner in runners}) != len(runners):
        raise ValueError("horse_id must be unique within a target race")
    if len({runner.post_position for runner in runners}) != len(runners):
        raise ValueError("post_position must be unique within a target race")
    if predicted_at >= runners[0].scheduled_at:
        raise ValueError("predicted_at must be before scheduled_at")


def _top3_probabilities(weights: Sequence[float]) -> list[float]:
    """Return Plackett-Luce top-three inclusion probabilities."""
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
                order_probability = (
                    first_probability
                    * second_probability
                    * weights[third]
                    / remaining_after_second
                )
                probabilities[first] += order_probability
                probabilities[second] += order_probability
                probabilities[third] += order_probability
    return probabilities


def _build_predictions(
    runners: Sequence[BaselineRunner],
    weights: Sequence[float],
    *,
    predicted_at: datetime,
    model_version: str,
) -> tuple[PredictionRecord, ...]:
    _validate_target_race(runners, predicted_at)
    if len(weights) != len(runners):
        raise ValueError("weights and runners must have equal length")
    if any(weight <= 0.0 for weight in weights):
        raise ValueError("baseline weights must be positive")

    total_weight = sum(weights)
    win_probabilities = [weight / total_weight for weight in weights]
    top3_probabilities = _top3_probabilities(weights)
    ranked_indices = sorted(
        range(len(runners)),
        key=lambda index: (-win_probabilities[index], runners[index].post_position),
    )
    rank_by_index = {
        runner_index: rank
        for rank, runner_index in enumerate(ranked_indices, start=1)
    }
    predictions = tuple(
        PredictionRecord(
            race_id=runner.race_id,
            horse_id=runner.horse_id,
            predicted_at=predicted_at,
            model_version=model_version,
            win_probability=win_probabilities[index],
            top3_probability=top3_probabilities[index],
            predicted_rank=rank_by_index[index],
        )
        for index, runner in enumerate(runners)
    )
    validate_race_predictions(predictions, tolerance=1e-8)
    return predictions


def _history_rates(
    history: Iterable[BaselineRunner],
    key: Callable[[BaselineRunner], str | int],
) -> tuple[dict[str | int, tuple[float, int]], float, datetime]:
    finished = [runner for runner in history if runner.finish_position is not None]
    if not finished:
        raise ValueError("history must contain finished runners")

    seen = set()
    winners_by_race: dict[str, list[BaselineRunner]] = defaultdict(list)
    for runner in finished:
        row_key = (runner.race_id, runner.horse_id)
        if row_key in seen:
            raise ValueError("history contains duplicate race and horse rows")
        seen.add(row_key)
        if runner.finish_position == 1:
            winners_by_race[runner.race_id].append(runner)

    race_ids = {runner.race_id for runner in finished}
    races_without_winner = race_ids - winners_by_race.keys()
    if races_without_winner:
        raise ValueError("every historical race must contain at least one winner")

    starts: dict[str | int, int] = defaultdict(int)
    win_credit: dict[str | int, float] = defaultdict(float)
    for runner in finished:
        starts[key(runner)] += 1
    for winners in winners_by_race.values():
        credit = 1.0 / len(winners)
        for winner in winners:
            win_credit[key(winner)] += credit

    stats = {
        item_key: (win_credit[item_key], item_starts)
        for item_key, item_starts in starts.items()
    }
    global_prior = len(winners_by_race) / len(finished)
    trained_through = max(runner.scheduled_at for runner in finished)
    return stats, global_prior, trained_through


@dataclass(frozen=True)
class UniformBaseline:
    model_version: str = "uniform-v1"

    def predict(
        self, runners: Sequence[BaselineRunner], *, predicted_at: datetime
    ) -> tuple[PredictionRecord, ...]:
        return _build_predictions(
            runners,
            [1.0] * len(runners),
            predicted_at=predicted_at,
            model_version=self.model_version,
        )


@dataclass(frozen=True)
class _HistoricalRateBaseline:
    stats: dict[str | int, tuple[float, int]]
    global_prior: float
    trained_through: datetime
    prior_strength: float
    model_version: str
    key: Callable[[BaselineRunner], str | int]

    def predict(
        self, runners: Sequence[BaselineRunner], *, predicted_at: datetime
    ) -> tuple[PredictionRecord, ...]:
        if not runners:
            raise ValueError("at least one target runner is required")
        if runners[0].scheduled_at <= self.trained_through:
            raise ValueError("target race must be later than all training history")
        weights = []
        for runner in runners:
            wins, starts = self.stats.get(self.key(runner), (0.0, 0))
            weights.append(
                (wins + self.global_prior * self.prior_strength)
                / (starts + self.prior_strength)
            )
        return _build_predictions(
            runners,
            weights,
            predicted_at=predicted_at,
            model_version=self.model_version,
        )


def post_position_baseline(
    history: Iterable[BaselineRunner], *, prior_strength: float = 10.0
) -> _HistoricalRateBaseline:
    """Fit a smoothed historical win-rate baseline by post position."""
    if prior_strength <= 0.0:
        raise ValueError("prior_strength must be positive")
    stats, global_prior, trained_through = _history_rates(
        history, lambda runner: runner.post_position
    )
    return _HistoricalRateBaseline(
        stats=stats,
        global_prior=global_prior,
        trained_through=trained_through,
        prior_strength=prior_strength,
        model_version="post-position-v1",
        key=lambda runner: runner.post_position,
    )


def horse_history_baseline(
    history: Iterable[BaselineRunner], *, prior_strength: float = 10.0
) -> _HistoricalRateBaseline:
    """Fit a smoothed historical win-rate baseline by horse."""
    if prior_strength <= 0.0:
        raise ValueError("prior_strength must be positive")
    stats, global_prior, trained_through = _history_rates(
        history, lambda runner: runner.horse_id
    )
    return _HistoricalRateBaseline(
        stats=stats,
        global_prior=global_prior,
        trained_through=trained_through,
        prior_strength=prior_strength,
        model_version="horse-history-v1",
        key=lambda runner: runner.horse_id,
    )


def evaluate_baseline_predictions(
    predictions: Sequence[PredictionRecord], results: Sequence[ResultRecord]
) -> BaselineScore:
    """Evaluate any baseline output with one shared metric definition."""
    if not predictions:
        raise ValueError("at least one prediction is required")
    if len({prediction.model_version for prediction in predictions}) != 1:
        raise ValueError("predictions must use one model_version")

    prediction_by_key = {
        (prediction.race_id, prediction.horse_id): prediction
        for prediction in predictions
    }
    result_by_key = {
        (result.race_id, result.horse_id): result for result in results
    }
    if len(prediction_by_key) != len(predictions):
        raise ValueError("predictions contain duplicate race and horse rows")
    if len(result_by_key) != len(results):
        raise ValueError("results contain duplicate race and horse rows")
    if prediction_by_key.keys() != result_by_key.keys():
        raise ValueError("predictions and results must contain identical runners")
    if any(result.finish_position is None for result in results):
        raise ValueError("baseline evaluation requires finished results")

    probabilities = []
    outcomes = []
    predictions_by_race: dict[str, list[PredictionRecord]] = defaultdict(list)
    winners_by_race: dict[str, set[str]] = defaultdict(set)
    for key, prediction in prediction_by_key.items():
        result = result_by_key[key]
        probabilities.append(prediction.win_probability)
        outcomes.append(int(result.finish_position == 1))
        predictions_by_race[prediction.race_id].append(prediction)
        if result.finish_position == 1:
            winners_by_race[result.race_id].add(result.horse_id)

    hits = 0
    for race_id, race_predictions in predictions_by_race.items():
        predicted_winner = min(
            race_predictions, key=lambda prediction: prediction.predicted_rank
        ).horse_id
        hits += predicted_winner in winners_by_race[race_id]

    return BaselineScore(
        model_version=predictions[0].model_version,
        race_count=len(predictions_by_race),
        runner_count=len(predictions),
        top1_accuracy=hits / len(predictions_by_race),
        win_brier_score=binary_brier_score(probabilities, outcomes),
        win_log_loss=binary_log_loss(probabilities, outcomes),
    )
