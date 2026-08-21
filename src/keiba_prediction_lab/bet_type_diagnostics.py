"""Race and race-date diagnostics for paired bet-type evaluations."""

import json
from dataclasses import dataclass
from datetime import date
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


class HitTransition(str, Enum):
    """Paired hit state for one race and bet type."""

    BOTH_MISS = "both-miss"
    BASELINE_ONLY = "baseline-only"
    CANDIDATE_ONLY = "candidate-only"
    BOTH_HIT = "both-hit"


_TRANSITION_LABELS_JA = {
    HitTransition.BOTH_MISS: "両方外れ",
    HitTransition.BASELINE_ONLY: "基準のみ的中",
    HitTransition.CANDIDATE_ONLY: "候補のみ的中",
    HitTransition.BOTH_HIT: "両方的中",
}


@dataclass(frozen=True)
class BetTypeRaceContribution:
    """Observed paired payout contribution from one race."""

    race_id: str
    race_date: date
    bet_type: BetType
    baseline_selection: tuple[str, ...]
    candidate_selection: tuple[str, ...]
    baseline_payout_yen: int
    candidate_payout_yen: int

    def __post_init__(self) -> None:
        if not self.race_id.strip():
            raise ValueError("diagnostic race_id must not be empty")
        if type(self.race_date) is not date:
            raise ValueError("diagnostic race_date must be a date")
        if not isinstance(self.bet_type, BetType):
            raise ValueError("diagnostic bet_type must be a BetType value")
        if any(
            not isinstance(selection, tuple)
            or len(selection) != self.bet_type.selection_size
            or any(
                not isinstance(horse_id, str) or not horse_id.strip()
                for horse_id in selection
            )
            for selection in (
                self.baseline_selection,
                self.candidate_selection,
            )
        ):
            raise ValueError("diagnostic selections must match the bet type")
        if any(
            type(value) is not int or value < 0
            for value in (self.baseline_payout_yen, self.candidate_payout_yen)
        ):
            raise ValueError("diagnostic payouts must be non-negative integers")

    @property
    def payout_delta_yen(self) -> int:
        return self.candidate_payout_yen - self.baseline_payout_yen

    @property
    def transition(self) -> HitTransition:
        baseline_hit = self.baseline_payout_yen > 0
        candidate_hit = self.candidate_payout_yen > 0
        if baseline_hit and candidate_hit:
            return HitTransition.BOTH_HIT
        if baseline_hit:
            return HitTransition.BASELINE_ONLY
        if candidate_hit:
            return HitTransition.CANDIDATE_ONLY
        return HitTransition.BOTH_MISS


@dataclass(frozen=True)
class BetTypeDateContribution:
    """Observed paired aggregate for one race date and bet type."""

    race_date: date
    bet_type: BetType
    race_count: int
    baseline_hits: int
    candidate_hits: int
    baseline_return_yen: int
    candidate_return_yen: int

    def __post_init__(self) -> None:
        if type(self.race_date) is not date:
            raise ValueError("diagnostic race_date must be a date")
        if not isinstance(self.bet_type, BetType):
            raise ValueError("diagnostic bet_type must be a BetType value")
        integer_fields = (
            self.race_count,
            self.baseline_hits,
            self.candidate_hits,
            self.baseline_return_yen,
            self.candidate_return_yen,
        )
        if any(type(value) is not int or value < 0 for value in integer_fields):
            raise ValueError("diagnostic date counts must be non-negative integers")
        if self.race_count < 1:
            raise ValueError("diagnostic date must contain at least one race")
        if max(self.baseline_hits, self.candidate_hits) > self.race_count:
            raise ValueError("diagnostic date hits must not exceed race count")

    @property
    def hit_delta(self) -> int:
        return self.candidate_hits - self.baseline_hits

    @property
    def return_delta_yen(self) -> int:
        return self.candidate_return_yen - self.baseline_return_yen


@dataclass(frozen=True)
class BetTypeContributionReport:
    """Deterministic paired contributions without causal interpretation."""

    race_ids: tuple[str, ...]
    race_rows: tuple[BetTypeRaceContribution, ...]
    date_rows: tuple[BetTypeDateContribution, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.race_ids, tuple) or not self.race_ids:
            raise ValueError("diagnostic race_ids must be a non-empty tuple")
        if self.race_ids != tuple(sorted(self.race_ids)):
            raise ValueError("diagnostic race_ids must use deterministic order")
        if len(set(self.race_ids)) != len(self.race_ids):
            raise ValueError("diagnostic race_ids must be unique")
        if (
            not isinstance(self.race_rows, tuple)
            or any(
                not isinstance(row, BetTypeRaceContribution)
                for row in self.race_rows
            )
        ):
            raise ValueError("diagnostic race_rows must be a typed tuple")
        if (
            not isinstance(self.date_rows, tuple)
            or any(
                not isinstance(row, BetTypeDateContribution)
                for row in self.date_rows
            )
        ):
            raise ValueError("diagnostic date_rows must be a typed tuple")
        expected_races = tuple(
            (race_id, bet_type)
            for race_id in self.race_ids
            for bet_type in BetType
        )
        actual_races = tuple(
            (row.race_id, row.bet_type) for row in self.race_rows
        )
        if actual_races != expected_races:
            raise ValueError(
                "diagnostic rows must contain every race and bet type in order"
            )
        expected_dates = tuple(
            (race_date, bet_type)
            for race_date in sorted({row.race_date for row in self.race_rows})
            for bet_type in BetType
        )
        actual_dates = tuple(
            (row.race_date, row.bet_type) for row in self.date_rows
        )
        if actual_dates != expected_dates:
            raise ValueError(
                "diagnostic date rows must contain every date and bet type in order"
            )
        for date_row in self.date_rows:
            matching = tuple(
                row
                for row in self.race_rows
                if row.race_date == date_row.race_date
                and row.bet_type is date_row.bet_type
            )
            reproduced = (
                len(matching),
                sum(row.baseline_payout_yen > 0 for row in matching),
                sum(row.candidate_payout_yen > 0 for row in matching),
                sum(row.baseline_payout_yen for row in matching),
                sum(row.candidate_payout_yen for row in matching),
            )
            stored = (
                date_row.race_count,
                date_row.baseline_hits,
                date_row.candidate_hits,
                date_row.baseline_return_yen,
                date_row.candidate_return_yen,
            )
            if reproduced != stored:
                raise ValueError("diagnostic date row must reproduce race rows")

    def _extreme_rows(
        self, bet_type: BetType, *, improving: bool, limit: int
    ) -> tuple[BetTypeRaceContribution, ...]:
        rows = tuple(
            row
            for row in self.race_rows
            if row.bet_type is bet_type
            and (
                row.payout_delta_yen > 0
                if improving
                else row.payout_delta_yen < 0
            )
        )
        return tuple(sorted(
            rows,
            key=lambda row: (
                -row.payout_delta_yen if improving else row.payout_delta_yen,
                row.race_id,
            ),
        )[:limit])

    def to_dict(self) -> dict[str, object]:
        return {
            "race_count": len(self.race_ids),
            "race_rows": [
                {
                    "race_id": row.race_id,
                    "race_date": row.race_date.isoformat(),
                    "bet_type": row.bet_type.value,
                    "baseline_selection": list(row.baseline_selection),
                    "candidate_selection": list(row.candidate_selection),
                    "baseline_payout_yen": row.baseline_payout_yen,
                    "candidate_payout_yen": row.candidate_payout_yen,
                    "payout_delta_yen": row.payout_delta_yen,
                    "transition": row.transition.value,
                }
                for row in self.race_rows
            ],
            "date_rows": [
                {
                    "race_date": row.race_date.isoformat(),
                    "bet_type": row.bet_type.value,
                    "race_count": row.race_count,
                    "baseline_hits": row.baseline_hits,
                    "candidate_hits": row.candidate_hits,
                    "hit_delta": row.hit_delta,
                    "baseline_return_yen": row.baseline_return_yen,
                    "candidate_return_yen": row.candidate_return_yen,
                    "return_delta_yen": row.return_delta_yen,
                }
                for row in self.date_rows
            ],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2
        ) + "\n"

    def to_markdown(self, *, top_races: int = 5) -> str:
        if type(top_races) is not int or top_races < 1:
            raise ValueError("top_races must be an integer of at least one")
        lines = [
            "# 馬券種別・対応寄与診断",
            "",
            (
                f"同一の{len(self.race_ids)}レースを"
                "開催日・券種別に分解する。"
            ),
            (
                "払戻差は観測結果の所在を示すだけで、"
                "原因、統計的有意差、"
                "モデル採用可否を示さない。"
            ),
            "",
            (
                "| 開催日 | 馬券種 | レース数 | "
                "的中（基準→候補） | "
                "純的中差 | 払戻（基準→候補） | 払戻差 |"
            ),
            "|---|---|---:|---:|---:|---:|---:|",
        ]
        for row in self.date_rows:
            lines.append(
                f"| {row.race_date.isoformat()} | "
                f"{BET_TYPE_LABELS_JA[row.bet_type]} | {row.race_count} | "
                f"{row.baseline_hits}→{row.candidate_hits} | "
                f"{row.hit_delta:+d} | "
                f"{row.baseline_return_yen:,}円→"
                f"{row.candidate_return_yen:,}円 | "
                f"{row.return_delta_yen:+,}円 |"
            )
        lines.extend((
            "",
            (
                "## レース別払戻差"
                f"（各券種・方向ごとに最大{top_races}件）"
            ),
            "",
            (
                "| 馬券種 | 方向 | レース | 開催日 | 状態 | "
                "買い目（基準→候補） | 払戻差 |"
            ),
            "|---|---|---|---|---|---|---:|",
        ))
        found = False
        for bet_type in BetType:
            for improving, direction in ((True, "候補側"), (False, "基準側")):
                for row in self._extreme_rows(
                    bet_type, improving=improving, limit=top_races
                ):
                    found = True
                    lines.append(
                        f"| {BET_TYPE_LABELS_JA[bet_type]} | {direction} | "
                        f"{row.race_id} | {row.race_date.isoformat()} | "
                        f"{_TRANSITION_LABELS_JA[row.transition]} | "
                        f"{'/'.join(row.baseline_selection)}→"
                        f"{'/'.join(row.candidate_selection)} | "
                        f"{row.payout_delta_yen:+,}円 |"
                    )
        if not found:
            lines.append("| - | - | - | - | 払戻差なし | - | 0円 |")
        return "\n".join(lines) + "\n"


def diagnose_bet_type_evaluation_artifacts(
    baseline: BetTypeEvaluationArtifact,
    candidate: BetTypeEvaluationArtifact,
) -> BetTypeContributionReport:
    """Locate paired payout differences by race and race date."""
    comparison = compare_bet_type_evaluation_artifacts(baseline, candidate)
    if not baseline.tickets or not candidate.tickets:
        raise ValueError("diagnostics require reports with ticket ledgers")
    baseline_dates = {row.race_id: row.race_date for row in baseline.inputs}
    candidate_dates = {row.race_id: row.race_date for row in candidate.inputs}
    if any(
        baseline_dates[race_id] is None
        or candidate_dates[race_id] is None
        for race_id in comparison.race_ids
    ):
        raise ValueError(
            "diagnostics require schema 1.2 reports with race_date"
        )
    baseline_tickets = {
        (ticket.race_id, ticket.bet_type): ticket
        for ticket in baseline.tickets
    }
    candidate_tickets = {
        (ticket.race_id, ticket.bet_type): ticket
        for ticket in candidate.tickets
    }
    race_rows = tuple(
        BetTypeRaceContribution(
            race_id,
            baseline_dates[race_id],  # type: ignore[arg-type]
            bet_type,
            baseline_tickets[(race_id, bet_type)].selection,
            candidate_tickets[(race_id, bet_type)].selection,
            baseline_tickets[(race_id, bet_type)].payout_yen,
            candidate_tickets[(race_id, bet_type)].payout_yen,
        )
        for race_id in comparison.race_ids
        for bet_type in BetType
    )
    dates = tuple(sorted({row.race_date for row in race_rows}))
    date_rows = tuple(
        BetTypeDateContribution(
            race_date,
            bet_type,
            len(matching),
            sum(row.baseline_payout_yen > 0 for row in matching),
            sum(row.candidate_payout_yen > 0 for row in matching),
            sum(row.baseline_payout_yen for row in matching),
            sum(row.candidate_payout_yen for row in matching),
        )
        for race_date in dates
        for bet_type in BetType
        for matching in (tuple(
            row
            for row in race_rows
            if row.race_date == race_date and row.bet_type is bet_type
        ),)
    )
    return BetTypeContributionReport(
        comparison.race_ids, race_rows, date_rows
    )


def diagnose_bet_type_evaluation_report_files(
    baseline_path: str | Path,
    candidate_path: str | Path,
) -> BetTypeContributionReport:
    """Load two integrity-checked reports and diagnose paired contributions."""
    return diagnose_bet_type_evaluation_artifacts(
        load_bet_type_evaluation_artifact(baseline_path),
        load_bet_type_evaluation_artifact(candidate_path),
    )
