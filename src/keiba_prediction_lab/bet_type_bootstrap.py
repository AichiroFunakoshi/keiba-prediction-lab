"""Paired race-level bootstrap intervals for bet-type report comparisons."""

import math
import random
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .bet_type_report import (
    BetTypeEvaluationArtifact,
    load_bet_type_evaluation_artifact,
)
from .bet_type_report_comparison import (
    compare_bet_type_evaluation_artifacts,
)
from .domain import BetType
from .evaluation import BET_TYPE_LABELS_JA


MINIMUM_RECOMMENDED_RACES = 300
DEFAULT_BOOTSTRAP_SAMPLES = 10_000
DEFAULT_BOOTSTRAP_SEED = 0
DEFAULT_CONFIDENCE_LEVEL = 0.95


class BootstrapResamplingUnit(str, Enum):
    """Dependence unit resampled by the paired bootstrap."""

    RACE = "race"
    RACE_DATE = "race-date"


@dataclass(frozen=True)
class PairedBootstrapInterval:
    """Point difference and percentile interval for candidate minus baseline."""

    point_estimate: float
    lower: float
    upper: float
    probability_candidate_better: float

    def __post_init__(self) -> None:
        values = (self.point_estimate, self.lower, self.upper)
        if any(
            type(value) is not float or not math.isfinite(value)
            for value in values
        ):
            raise ValueError("bootstrap interval values must be finite floats")
        if self.lower > self.upper:
            raise ValueError("bootstrap lower bound must not exceed upper bound")
        if (
            type(self.probability_candidate_better) is not float
            or not math.isfinite(self.probability_candidate_better)
            or not 0.0 <= self.probability_candidate_better <= 1.0
        ):
            raise ValueError(
                "bootstrap improvement probability must be between 0 and 1"
            )


@dataclass(frozen=True)
class BetTypeBootstrapSummary:
    bet_type: BetType
    hit_rate: PairedBootstrapInterval
    return_rate: PairedBootstrapInterval
    return_rate_without_largest_hit: PairedBootstrapInterval
    largest_hit_share: PairedBootstrapInterval

    def __post_init__(self) -> None:
        if not isinstance(self.bet_type, BetType):
            raise ValueError("bet_type must be a BetType value")


@dataclass(frozen=True)
class BetTypeBootstrapReport:
    race_ids: tuple[str, ...]
    samples: int
    seed: int
    confidence_level: float
    resampling_unit: BootstrapResamplingUnit
    cluster_count: int
    summaries: tuple[BetTypeBootstrapSummary, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.race_ids, tuple) or len(self.race_ids) < 2:
            raise ValueError("bootstrap requires at least two paired races")
        if self.race_ids != tuple(sorted(self.race_ids)):
            raise ValueError("bootstrap race_ids must use deterministic order")
        if len(set(self.race_ids)) != len(self.race_ids):
            raise ValueError("bootstrap race_ids must be unique")
        if type(self.samples) is not int or self.samples < 100:
            raise ValueError("bootstrap samples must be an integer of at least 100")
        if type(self.seed) is not int:
            raise ValueError("bootstrap seed must be an integer")
        if (
            type(self.confidence_level) is not float
            or not math.isfinite(self.confidence_level)
            or not 0.5 < self.confidence_level < 1.0
        ):
            raise ValueError("bootstrap confidence_level must be between 0.5 and 1")
        if not isinstance(self.resampling_unit, BootstrapResamplingUnit):
            raise ValueError("bootstrap resampling_unit is invalid")
        if type(self.cluster_count) is not int or self.cluster_count < 2:
            raise ValueError("bootstrap requires at least two resampling clusters")
        if self.cluster_count > len(self.race_ids):
            raise ValueError("bootstrap clusters must not exceed race count")
        if (
            self.resampling_unit is BootstrapResamplingUnit.RACE
            and self.cluster_count != len(self.race_ids)
        ):
            raise ValueError("race bootstrap must contain one cluster per race")
        if tuple(row.bet_type for row in self.summaries) != tuple(BetType):
            raise ValueError("bootstrap summaries must contain every bet type in order")

    def for_bet_type(self, bet_type: BetType) -> BetTypeBootstrapSummary:
        if not isinstance(bet_type, BetType):
            raise ValueError("bet_type must be a BetType value")
        return next(row for row in self.summaries if row.bet_type is bet_type)

    @staticmethod
    def _points(value: float) -> str:
        return f"{value * 100:+.1f}pt"

    def _interval(self, value: PairedBootstrapInterval) -> str:
        return (
            f"{self._points(value.point_estimate)} "
            f"[{self._points(value.lower)}, {self._points(value.upper)}]"
        )

    def to_markdown(self) -> str:
        confidence = self.confidence_level * 100
        if len(self.race_ids) < MINIMUM_RECOMMENDED_RACES:
            status = (
                f"標本不足：{len(self.race_ids)}レース。"
                f"最低目安{MINIMUM_RECOMMENDED_RACES}レース未満の"
                "探索的結果。"
            )
        else:
            status = f"最低目安{MINIMUM_RECOMMENDED_RACES}レースを満たす。"
        lines = [
            "# 馬券種別・対応クラスターブートストラップ",
            "",
            status,
            (
                f"再標本化{self.samples:,}回、seed={self.seed}、"
                f"{confidence:.1f}%パーセンタイル区間。"
            ),
            (
                "改善確率は正の差を1、同値を0.5として数えた"
                "参考値であり、"
                "有意差判定ではない。"
            ),
        ]
        if self.resampling_unit is BootstrapResamplingUnit.RACE_DATE:
            lines.extend((
                (
                    f"再標本化単位は開催日（{self.cluster_count}日）。"
                    "同日の全レースを一塊として抽出する。"
                ),
                "6券種を同時に見る多重比較は補正していない。",
            ))
        else:
            lines.extend((
                f"再標本化単位はレース（{self.cluster_count}レース）。",
                (
                    "開催日内の相関と、6券種を同時に見る"
                    "多重比較は補正していない。"
                ),
            ))
        lines.extend((
            "",
            (
                "| 馬券種 | 的中率差 [区間] | P(候補>基準) | "
                "回収率差 [区間] | P(候補>基準) |"
            ),
            "|---|---:|---:|---:|---:|",
        ))
        for bet_type in BetType:
            summary = self.for_bet_type(bet_type)
            lines.append(
                f"| {BET_TYPE_LABELS_JA[bet_type]} | "
                f"{self._interval(summary.hit_rate)} | "
                f"{summary.hit_rate.probability_candidate_better:.1%} | "
                f"{self._interval(summary.return_rate)} | "
                f"{summary.return_rate.probability_candidate_better:.1%} |"
            )
        lines.extend((
            "",
            (
                "| 馬券種 | 最高払戻除外後差 [区間] | "
                "最高1件依存度差 [区間] |"
            ),
            "|---|---:|---:|",
        ))
        for bet_type in BetType:
            summary = self.for_bet_type(bet_type)
            lines.append(
                f"| {BET_TYPE_LABELS_JA[bet_type]} | "
                f"{self._interval(summary.return_rate_without_largest_hit)} | "
                f"{self._interval(summary.largest_hit_share)} |"
            )
        return "\n".join(lines) + "\n"


def _metric_deltas(
    baseline: tuple[int, ...], candidate: tuple[int, ...]
) -> tuple[float, float, float, float]:
    count = len(baseline)
    stake = count * 100

    def metrics(payouts: tuple[int, ...]) -> tuple[float, float, float, float]:
        total = sum(payouts)
        largest = max(payouts, default=0)
        return (
            sum(payout > 0 for payout in payouts) / count,
            total / stake,
            (total - largest) / stake,
            largest / total if total else 0.0,
        )

    baseline_metrics = metrics(baseline)
    candidate_metrics = metrics(candidate)
    return (
        candidate_metrics[0] - baseline_metrics[0],
        candidate_metrics[1] - baseline_metrics[1],
        candidate_metrics[2] - baseline_metrics[2],
        candidate_metrics[3] - baseline_metrics[3],
    )


def _percentile(sorted_values: list[float], quantile: float) -> float:
    position = (len(sorted_values) - 1) * quantile
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return sorted_values[lower_index]
    fraction = position - lower_index
    return (
        sorted_values[lower_index] * (1.0 - fraction)
        + sorted_values[upper_index] * fraction
    )


def _interval(
    point_estimate: float,
    values: list[float],
    confidence_level: float,
) -> PairedBootstrapInterval:
    values.sort()
    tail = (1.0 - confidence_level) / 2.0
    positive = sum(value > 0.0 for value in values)
    ties = sum(value == 0.0 for value in values)
    return PairedBootstrapInterval(
        point_estimate=float(point_estimate),
        lower=float(_percentile(values, tail)),
        upper=float(_percentile(values, 1.0 - tail)),
        probability_candidate_better=(positive + ties * 0.5) / len(values),
    )


def bootstrap_bet_type_evaluation_artifacts(
    baseline: BetTypeEvaluationArtifact,
    candidate: BetTypeEvaluationArtifact,
    *,
    samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
    resampling_unit: BootstrapResamplingUnit = BootstrapResamplingUnit.RACE,
) -> BetTypeBootstrapReport:
    """Estimate paired race- or race-date-level intervals for every bet type."""
    comparison = compare_bet_type_evaluation_artifacts(baseline, candidate)
    if not baseline.tickets or not candidate.tickets:
        raise ValueError("bootstrap requires reports with ticket ledgers")
    if len(comparison.race_ids) < 2:
        raise ValueError("bootstrap requires at least two paired races")
    if type(samples) is not int or samples < 100:
        raise ValueError("bootstrap samples must be an integer of at least 100")
    if type(seed) is not int:
        raise ValueError("bootstrap seed must be an integer")
    if (
        type(confidence_level) is not float
        or not math.isfinite(confidence_level)
        or not 0.5 < confidence_level < 1.0
    ):
        raise ValueError("bootstrap confidence_level must be between 0.5 and 1")
    if not isinstance(resampling_unit, BootstrapResamplingUnit):
        raise ValueError("bootstrap resampling_unit is invalid")

    baseline_payouts = {
        (ticket.race_id, ticket.bet_type): ticket.payout_yen
        for ticket in baseline.tickets
    }
    candidate_payouts = {
        (ticket.race_id, ticket.bet_type): ticket.payout_yen
        for ticket in candidate.tickets
    }
    arrays = {
        bet_type: (
            tuple(
                baseline_payouts[(race_id, bet_type)]
                for race_id in comparison.race_ids
            ),
            tuple(
                candidate_payouts[(race_id, bet_type)]
                for race_id in comparison.race_ids
            ),
        )
        for bet_type in BetType
    }
    point_estimates = {
        bet_type: _metric_deltas(*arrays[bet_type]) for bet_type in BetType
    }
    if resampling_unit is BootstrapResamplingUnit.RACE_DATE:
        baseline_dates = {
            row.race_id: row.race_date for row in baseline.inputs
        }
        candidate_dates = {
            row.race_id: row.race_date for row in candidate.inputs
        }
        if any(
            baseline_dates[race_id] is None
            or candidate_dates[race_id] is None
            for race_id in comparison.race_ids
        ):
            raise ValueError(
                "race-date bootstrap requires schema 1.2 reports with race_date"
            )
        if any(
            baseline_dates[race_id] != candidate_dates[race_id]
            for race_id in comparison.race_ids
        ):
            raise ValueError(
                "race-date bootstrap requires identical paired race_date values"
            )
        dates = tuple(sorted({
            baseline_dates[race_id] for race_id in comparison.race_ids
        }))
        clusters = tuple(
            tuple(
                index
                for index, race_id in enumerate(comparison.race_ids)
                if baseline_dates[race_id] == race_date
            )
            for race_date in dates
        )
    else:
        clusters = tuple((index,) for index in range(len(comparison.race_ids)))
    if len(clusters) < 2:
        raise ValueError("bootstrap requires at least two resampling clusters")

    distributions = {
        bet_type: ([], [], [], []) for bet_type in BetType
    }
    rng = random.Random(seed)
    for _ in range(samples):
        indices = tuple(
            race_index
            for _ in range(len(clusters))
            for race_index in clusters[rng.randrange(len(clusters))]
        )
        for bet_type in BetType:
            baseline_values, candidate_values = arrays[bet_type]
            deltas = _metric_deltas(
                tuple(baseline_values[index] for index in indices),
                tuple(candidate_values[index] for index in indices),
            )
            for metric_index, value in enumerate(deltas):
                distributions[bet_type][metric_index].append(value)

    summaries = tuple(
        BetTypeBootstrapSummary(
            bet_type,
            *(
                _interval(
                    point_estimates[bet_type][metric_index],
                    distributions[bet_type][metric_index],
                    confidence_level,
                )
                for metric_index in range(4)
            ),
        )
        for bet_type in BetType
    )
    return BetTypeBootstrapReport(
        race_ids=comparison.race_ids,
        samples=samples,
        seed=seed,
        confidence_level=confidence_level,
        resampling_unit=resampling_unit,
        cluster_count=len(clusters),
        summaries=summaries,
    )


def bootstrap_bet_type_evaluation_report_files(
    baseline_path: str | Path,
    candidate_path: str | Path,
    *,
    samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    resampling_unit: BootstrapResamplingUnit = BootstrapResamplingUnit.RACE,
) -> BetTypeBootstrapReport:
    """Load two reports and run a reproducible paired bootstrap."""
    return bootstrap_bet_type_evaluation_artifacts(
        load_bet_type_evaluation_artifact(baseline_path),
        load_bet_type_evaluation_artifact(candidate_path),
        samples=samples,
        seed=seed,
        resampling_unit=resampling_unit,
    )
