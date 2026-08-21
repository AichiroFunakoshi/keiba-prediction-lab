"""Paired bet-type diagnostics across fixed pre-race context segments."""

import json
import math
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
from .race_context import RaceContext


class BetTypeSegmentDimension(str, Enum):
    VENUE = "venue"
    SURFACE = "surface"
    TRACK_CONDITION = "track_condition"
    DISTANCE_BAND = "distance_band"
    RACE_CLASS = "race_class"
    FIELD_SIZE = "field_size"


def _segment_value(
    context: RaceContext, dimension: BetTypeSegmentDimension
) -> str:
    if dimension is BetTypeSegmentDimension.VENUE:
        return context.venue
    if dimension is BetTypeSegmentDimension.SURFACE:
        return context.surface.value
    if dimension is BetTypeSegmentDimension.TRACK_CONDITION:
        return context.track_condition
    if dimension is BetTypeSegmentDimension.DISTANCE_BAND:
        return context.distance_band
    if dimension is BetTypeSegmentDimension.RACE_CLASS:
        return context.race_class
    return context.field_size_bucket


@dataclass(frozen=True)
class BetTypeSegmentContribution:
    """Paired hit and return totals for one fixed segment and bet type."""

    dimension: BetTypeSegmentDimension
    value: str
    bet_type: BetType
    race_count: int
    baseline_hits: int
    candidate_hits: int
    baseline_return_yen: int
    candidate_return_yen: int

    def __post_init__(self) -> None:
        if not isinstance(self.dimension, BetTypeSegmentDimension):
            raise ValueError("segment dimension is invalid")
        if not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("segment value must not be empty")
        if not isinstance(self.bet_type, BetType):
            raise ValueError("segment bet_type must be a BetType value")
        integers = (
            self.race_count,
            self.baseline_hits,
            self.candidate_hits,
            self.baseline_return_yen,
            self.candidate_return_yen,
        )
        if any(type(value) is not int or value < 0 for value in integers):
            raise ValueError("segment counts and returns must be non-negative integers")
        if self.race_count < 1:
            raise ValueError("segment must contain at least one race")
        if max(self.baseline_hits, self.candidate_hits) > self.race_count:
            raise ValueError("segment hits must not exceed race count")

    @property
    def hit_delta(self) -> int:
        return self.candidate_hits - self.baseline_hits

    @property
    def return_delta_yen(self) -> int:
        return self.candidate_return_yen - self.baseline_return_yen

    @property
    def hit_rate_delta(self) -> float:
        return self.hit_delta / self.race_count

    @property
    def return_rate_delta(self) -> float:
        return self.return_delta_yen / (self.race_count * 100)


@dataclass(frozen=True)
class BetTypeSegmentReport:
    race_count: int
    rows: tuple[BetTypeSegmentContribution, ...]

    def __post_init__(self) -> None:
        if type(self.race_count) is not int or self.race_count < 1:
            raise ValueError("segment report race_count must be positive")
        if (
            not isinstance(self.rows, tuple)
            or not self.rows
            or any(
                not isinstance(row, BetTypeSegmentContribution)
                for row in self.rows
            )
        ):
            raise ValueError("segment report rows must be a non-empty typed tuple")
        identities = tuple(
            (row.dimension, row.value, row.bet_type) for row in self.rows
        )
        expected = tuple(
            (dimension, value, bet_type)
            for dimension in BetTypeSegmentDimension
            for value in sorted({
                row.value for row in self.rows if row.dimension is dimension
            })
            for bet_type in BetType
        )
        if identities != expected:
            raise ValueError(
                "segment rows must contain every dimension value and bet type in order"
            )
        for dimension in BetTypeSegmentDimension:
            rows = tuple(row for row in self.rows if row.dimension is dimension)
            for bet_type in BetType:
                matching = tuple(
                    row for row in rows if row.bet_type is bet_type
                )
                covered = sum(row.race_count for row in matching)
                if covered != self.race_count:
                    raise ValueError(
                        "every segment dimension must cover every input race"
                    )
                totals = (
                    sum(row.baseline_hits for row in matching),
                    sum(row.candidate_hits for row in matching),
                    sum(row.baseline_return_yen for row in matching),
                    sum(row.candidate_return_yen for row in matching),
                )
                reference = tuple(
                    row
                    for row in self.rows
                    if row.dimension is BetTypeSegmentDimension.VENUE
                    and row.bet_type is bet_type
                )
                reference_totals = (
                    sum(row.baseline_hits for row in reference),
                    sum(row.candidate_hits for row in reference),
                    sum(row.baseline_return_yen for row in reference),
                    sum(row.candidate_return_yen for row in reference),
                )
                if totals != reference_totals:
                    raise ValueError(
                        "segment dimensions must reproduce identical totals"
                    )

    @staticmethod
    def _points(value: float) -> str:
        if not math.isfinite(value):
            raise ValueError("segment rate must be finite")
        return f"{value * 100:+.1f}pt"

    def to_dict(self) -> dict[str, object]:
        return {
            "race_count": self.race_count,
            "rows": [
                {
                    "dimension": row.dimension.value,
                    "value": row.value,
                    "bet_type": row.bet_type.value,
                    "race_count": row.race_count,
                    "baseline_hits": row.baseline_hits,
                    "candidate_hits": row.candidate_hits,
                    "hit_delta": row.hit_delta,
                    "hit_rate_delta": row.hit_rate_delta,
                    "baseline_return_yen": row.baseline_return_yen,
                    "candidate_return_yen": row.candidate_return_yen,
                    "return_delta_yen": row.return_delta_yen,
                    "return_rate_delta": row.return_rate_delta,
                }
                for row in self.rows
            ],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2
        ) + "\n"

    def to_markdown(self) -> str:
        lines = [
            "# 馬券種別・レース条件診断",
            "",
            (
                f"同一の{self.race_count}レースを"
                "固定済み条件で分解する。"
            ),
            (
                "各部分集団は探索的診断であり、"
                "多重比較を補正していない。"
                "原因、統計的有意差、モデル採用可否を示さない。"
            ),
            "",
            (
                "| 条件 | 値 | 馬券種 | レース数 | "
                "的中（基準→候補） | "
                "的中率差 | 払戻差 | 回収率差 |"
            ),
            "|---|---|---|---:|---:|---:|---:|---:|",
        ]
        for row in self.rows:
            lines.append(
                f"| {row.dimension.value} | {row.value} | "
                f"{BET_TYPE_LABELS_JA[row.bet_type]} | {row.race_count} | "
                f"{row.baseline_hits}→{row.candidate_hits} | "
                f"{self._points(row.hit_rate_delta)} | "
                f"{row.return_delta_yen:+,}円 | "
                f"{self._points(row.return_rate_delta)} |"
            )
        return "\n".join(lines) + "\n"


def diagnose_bet_type_segments(
    baseline: BetTypeEvaluationArtifact,
    candidate: BetTypeEvaluationArtifact,
) -> BetTypeSegmentReport:
    """Aggregate paired ticket outcomes over common pre-race contexts."""
    comparison = compare_bet_type_evaluation_artifacts(baseline, candidate)
    if not baseline.tickets or not candidate.tickets:
        raise ValueError("segment diagnostics require reports with ticket ledgers")
    baseline_inputs = {row.race_id: row for row in baseline.inputs}
    candidate_inputs = {row.race_id: row for row in candidate.inputs}
    if any(
        baseline_inputs[race_id].context is None
        or candidate_inputs[race_id].context is None
        for race_id in comparison.race_ids
    ):
        raise ValueError(
            "segment diagnostics require schema 1.3 reports with race context"
        )
    contexts = {
        race_id: baseline_inputs[race_id].context
        for race_id in comparison.race_ids
    }
    baseline_tickets = {
        (row.race_id, row.bet_type): row for row in baseline.tickets
    }
    candidate_tickets = {
        (row.race_id, row.bet_type): row for row in candidate.tickets
    }
    rows = []
    for dimension in BetTypeSegmentDimension:
        values = sorted({
            _segment_value(context, dimension)  # type: ignore[arg-type]
            for context in contexts.values()
        })
        for value in values:
            race_ids = tuple(
                race_id
                for race_id in comparison.race_ids
                if _segment_value(
                    contexts[race_id], dimension  # type: ignore[arg-type]
                ) == value
            )
            for bet_type in BetType:
                baseline_values = tuple(
                    baseline_tickets[(race_id, bet_type)].payout_yen
                    for race_id in race_ids
                )
                candidate_values = tuple(
                    candidate_tickets[(race_id, bet_type)].payout_yen
                    for race_id in race_ids
                )
                rows.append(BetTypeSegmentContribution(
                    dimension=dimension,
                    value=value,
                    bet_type=bet_type,
                    race_count=len(race_ids),
                    baseline_hits=sum(value > 0 for value in baseline_values),
                    candidate_hits=sum(value > 0 for value in candidate_values),
                    baseline_return_yen=sum(baseline_values),
                    candidate_return_yen=sum(candidate_values),
                ))
    return BetTypeSegmentReport(len(comparison.race_ids), tuple(rows))


def diagnose_bet_type_segment_report_files(
    baseline_path: str | Path,
    candidate_path: str | Path,
) -> BetTypeSegmentReport:
    """Load two reports and diagnose common pre-race context segments."""
    return diagnose_bet_type_segments(
        load_bet_type_evaluation_artifact(baseline_path),
        load_bet_type_evaluation_artifact(candidate_path),
    )
