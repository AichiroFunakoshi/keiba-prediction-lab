"""Paired comparisons of reproducible bet-type evaluation artifacts."""

from dataclasses import dataclass
from pathlib import Path

from .bet_type_report import (
    BetTypeEvaluationArtifact,
    load_bet_type_evaluation_artifact,
)
from .domain import BetType
from .evaluation import BET_TYPE_LABELS_JA, FixedStakeSummary


@dataclass(frozen=True)
class BetTypeEvaluationDelta:
    """Baseline and candidate summaries for one paired bet type."""

    bet_type: BetType
    baseline: FixedStakeSummary
    candidate: FixedStakeSummary

    def __post_init__(self) -> None:
        if not isinstance(self.bet_type, BetType):
            raise ValueError("bet_type must be a BetType value")
        if self.baseline.tickets != self.candidate.tickets:
            raise ValueError("paired summaries must have identical ticket counts")

    @property
    def hit_rate_delta(self) -> float:
        return self.candidate.hit_rate - self.baseline.hit_rate

    @property
    def return_rate_delta(self) -> float:
        return self.candidate.return_rate - self.baseline.return_rate

    @property
    def return_rate_without_largest_hit_delta(self) -> float:
        return (
            self.candidate.return_rate_without_largest_hit
            - self.baseline.return_rate_without_largest_hit
        )

    @property
    def largest_hit_share_delta(self) -> float:
        return (
            self.candidate.largest_hit_share
            - self.baseline.largest_hit_share
        )


@dataclass(frozen=True)
class BetTypeEvaluationComparison:
    """Fair paired comparison over identical races and official payouts."""

    race_ids: tuple[str, ...]
    deltas: tuple[BetTypeEvaluationDelta, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.race_ids, tuple) or not self.race_ids:
            raise ValueError("comparison race_ids must be a non-empty tuple")
        if self.race_ids != tuple(sorted(self.race_ids)):
            raise ValueError("comparison race_ids must use deterministic order")
        if len(set(self.race_ids)) != len(self.race_ids):
            raise ValueError("comparison race_ids must be unique")
        if tuple(row.bet_type for row in self.deltas) != tuple(BetType):
            raise ValueError("comparison must contain every bet type in order")
        if any(row.baseline.tickets != len(self.race_ids) for row in self.deltas):
            raise ValueError("comparison must contain one ticket per race and bet type")

    def for_bet_type(self, bet_type: BetType) -> BetTypeEvaluationDelta:
        if not isinstance(bet_type, BetType):
            raise ValueError("bet_type must be a BetType value")
        return next(row for row in self.deltas if row.bet_type is bet_type)

    @staticmethod
    def _points(value: float) -> str:
        return f"{value * 100:+.1f}pt"

    def to_markdown(self) -> str:
        lines = [
            "# 馬券種別・対応比較",
            "",
            (
                f"同一の{len(self.race_ids)}レース・同一払戻で、"
                "候補−基準を比較する。"
            ),
            "",
            (
                "| 馬券種 | 的中（基準→候補） | 的中率差 | "
                "回収率差 | "
                "最高払戻除外後差 | 最高1件依存度差 |"
            ),
            "|---|---:|---:|---:|---:|---:|",
        ]
        for bet_type in BetType:
            delta = self.for_bet_type(bet_type)
            lines.append(
                f"| {BET_TYPE_LABELS_JA[bet_type]} | "
                f"{delta.baseline.hits}/{delta.baseline.tickets} → "
                f"{delta.candidate.hits}/{delta.candidate.tickets} | "
                f"{self._points(delta.hit_rate_delta)} | "
                f"{self._points(delta.return_rate_delta)} | "
                f"{self._points(delta.return_rate_without_largest_hit_delta)} | "
                f"{self._points(delta.largest_hit_share_delta)} |"
            )
        return "\n".join(lines) + "\n"


def compare_bet_type_evaluation_artifacts(
    baseline: BetTypeEvaluationArtifact,
    candidate: BetTypeEvaluationArtifact,
) -> BetTypeEvaluationComparison:
    """Compare artifacts only when race identities and payouts are paired."""
    baseline_inputs = {row.race_id: row for row in baseline.inputs}
    candidate_inputs = {row.race_id: row for row in candidate.inputs}
    if baseline_inputs.keys() != candidate_inputs.keys():
        raise ValueError("paired artifacts must contain identical race_ids")
    for race_id in baseline_inputs:
        if (
            baseline_inputs[race_id].payout_file_sha256
            != candidate_inputs[race_id].payout_file_sha256
        ):
            raise ValueError(
                "paired artifacts must use identical payout files for every race"
            )
        baseline_date = baseline_inputs[race_id].race_date
        candidate_date = candidate_inputs[race_id].race_date
        if (
            baseline_date is not None
            and candidate_date is not None
            and baseline_date != candidate_date
        ):
            raise ValueError(
                "paired artifacts must use identical race_date for every race"
            )

    race_ids = tuple(sorted(baseline_inputs))
    deltas = tuple(
        BetTypeEvaluationDelta(
            bet_type,
            baseline.report.for_bet_type(bet_type),
            candidate.report.for_bet_type(bet_type),
        )
        for bet_type in BetType
    )
    return BetTypeEvaluationComparison(race_ids, deltas)


def compare_bet_type_evaluation_report_files(
    baseline_path: str | Path,
    candidate_path: str | Path,
) -> BetTypeEvaluationComparison:
    """Load two integrity-checked report files and compare them fairly."""
    return compare_bet_type_evaluation_artifacts(
        load_bet_type_evaluation_artifact(baseline_path),
        load_bet_type_evaluation_artifact(candidate_path),
    )
