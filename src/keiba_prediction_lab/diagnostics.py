"""Segment-level diagnostics for future-race prediction evaluation."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from .baselines import BaselineScore, evaluate_baseline_predictions
from .domain import PredictionRecord, ResultRecord
from .features import FeatureRow


@dataclass(frozen=True)
class SegmentDiagnostic:
    dimension: str
    value: str
    model_score: BaselineScore
    uniform_score: BaselineScore


@dataclass(frozen=True)
class DiagnosticReport:
    segments: tuple[SegmentDiagnostic, ...]

    def for_dimension(self, dimension: str) -> tuple[SegmentDiagnostic, ...]:
        return tuple(item for item in self.segments if item.dimension == dimension)


def field_size_bucket(runner_count: int) -> str:
    if runner_count < 1:
        raise ValueError("runner_count must be positive")
    if runner_count <= 8:
        return "small-1-8"
    if runner_count <= 12:
        return "medium-9-12"
    return "large-13-plus"


def confidence_bucket(max_win_probability: float) -> str:
    if not 0.0 <= max_win_probability <= 1.0:
        raise ValueError("max_win_probability must be between 0 and 1")
    if max_win_probability < 0.4:
        return "low-below-0.4"
    if max_win_probability < 0.7:
        return "medium-0.4-0.7"
    return "high-0.7-plus"


def diagnose_segments(
    model_predictions: Sequence[PredictionRecord],
    uniform_predictions: Sequence[PredictionRecord],
    results: Sequence[ResultRecord],
    features: Sequence[FeatureRow],
) -> DiagnosticReport:
    """Compare model and uniform scores across fixed race-level segments."""
    if not model_predictions:
        raise ValueError("at least one prediction is required")

    def keyed(rows: Sequence[object]) -> dict[tuple[str, str], object]:
        values = {
            (getattr(row, "race_id"), getattr(row, "horse_id")): row
            for row in rows
        }
        if len(values) != len(rows):
            raise ValueError("diagnostic inputs contain duplicate race and horse")
        return values

    model_by_key = keyed(model_predictions)
    uniform_by_key = keyed(uniform_predictions)
    result_by_key = keyed(results)
    feature_by_key = keyed(features)
    if not (
        model_by_key.keys()
        == uniform_by_key.keys()
        == result_by_key.keys()
        == feature_by_key.keys()
    ):
        raise ValueError("diagnostic inputs must contain identical runners")

    race_keys: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for key in model_by_key:
        race_keys[key[0]].append(key)
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for race_id, keys in race_keys.items():
        race_features = [feature_by_key[key] for key in keys]
        venues = {row.venue for row in race_features}
        distance_bands = {row.distance_band for row in race_features}
        if len(venues) != 1 or len(distance_bands) != 1:
            raise ValueError("race features must share venue and distance_band")
        max_probability = max(
            model_by_key[key].win_probability for key in keys
        )
        labels = (
            ("venue", next(iter(venues))),
            ("distance_band", next(iter(distance_bands))),
            ("field_size", field_size_bucket(len(keys))),
            ("confidence", confidence_bucket(max_probability)),
        )
        for label in labels:
            groups[label].append(race_id)

    segments = []
    for (dimension, value), included_races in sorted(groups.items()):
        included = set(included_races)
        keys = [key for key in model_by_key if key[0] in included]
        segment_model = [model_by_key[key] for key in keys]
        segment_uniform = [uniform_by_key[key] for key in keys]
        segment_results = [result_by_key[key] for key in keys]
        segments.append(SegmentDiagnostic(
            dimension=dimension,
            value=value,
            model_score=evaluate_baseline_predictions(
                segment_model, segment_results
            ),
            uniform_score=evaluate_baseline_predictions(
                segment_uniform, segment_results
            ),
        ))
    return DiagnosticReport(tuple(segments))
