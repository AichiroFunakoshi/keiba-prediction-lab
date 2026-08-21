"""Evaluation helpers that keep every betting selection at a fixed 100 yen."""

from dataclasses import dataclass
from typing import Iterable

from .domain import BetType, TicketResult


BET_TYPE_LABELS_JA = {
    BetType.WIN: "単勝",
    BetType.PLACE: "複勝",
    BetType.QUINELLA: "馬連",
    BetType.EXACTA: "馬単",
    BetType.TRIO: "3連複",
    BetType.TRIFECTA: "3連単",
}

_UNORDERED_BET_TYPES = frozenset({BetType.QUINELLA, BetType.TRIO})


@dataclass(frozen=True)
class FixedStakeSummary:
    tickets: int
    hits: int
    total_stake_yen: int
    total_return_yen: int
    hit_rate: float
    return_rate: float
    return_rate_without_largest_hit: float
    largest_hit_share: float
    top3_hit_share: float
    top5_hit_share: float


@dataclass(frozen=True)
class BetTypeSummary:
    bet_type: BetType
    fixed_stake: FixedStakeSummary


@dataclass(frozen=True)
class BetTypeEvaluationReport:
    """Fixed-stake summaries kept separate for every supported bet type."""

    summaries: tuple[BetTypeSummary, ...]

    def __post_init__(self) -> None:
        bet_types = [row.bet_type for row in self.summaries]
        if len(set(bet_types)) != len(bet_types):
            raise ValueError("bet type summaries must be unique")
        if set(bet_types) != set(BetType):
            raise ValueError("report must contain every supported bet type")

    def for_bet_type(self, bet_type: BetType) -> FixedStakeSummary:
        return next(
            row.fixed_stake for row in self.summaries if row.bet_type is bet_type
        )

    def to_markdown(self) -> str:
        lines = [
            "# 馬券種別・固定100円評価",
            "",
            "各馬券種を独立して集計し、馬券種をまたいだ回収率は算出しない。",
            "",
            "| 馬券種 | 点数 | 的中 | 的中率 | 購入額 | 払戻額 | 回収率 | 最高払戻除外後 | 最高1件 | 上位3件 | 上位5件 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for bet_type in BetType:
            summary = self.for_bet_type(bet_type)
            lines.append(
                f"| {BET_TYPE_LABELS_JA[bet_type]} | {summary.tickets} | "
                f"{summary.hits} | {summary.hit_rate:.1%} | "
                f"{summary.total_stake_yen:,}円 | {summary.total_return_yen:,}円 | "
                f"{summary.return_rate:.1%} | "
                f"{summary.return_rate_without_largest_hit:.1%} | "
                f"{summary.largest_hit_share:.1%} | "
                f"{summary.top3_hit_share:.1%} | "
                f"{summary.top5_hit_share:.1%} |"
            )
        return "\n".join(lines) + "\n"


def evaluate_fixed_stake(
    payouts_yen: Iterable[int], *, stake_per_ticket_yen: int = 100
) -> FixedStakeSummary:
    """Summarize fixed-stake tickets from their per-ticket payout amounts."""
    if stake_per_ticket_yen != 100:
        raise ValueError("stake_per_ticket_yen must be fixed at 100 yen")

    payouts = list(payouts_yen)
    if any(payout < 0 for payout in payouts):
        raise ValueError("payouts must not be negative")

    tickets = len(payouts)
    hits = sum(payout > 0 for payout in payouts)
    total_stake = tickets * stake_per_ticket_yen
    total_return = sum(payouts)
    largest_hit = max(payouts, default=0)
    payouts_descending = sorted((payout for payout in payouts if payout > 0), reverse=True)

    def payout_share(count: int) -> float:
        return sum(payouts_descending[:count]) / total_return if total_return else 0.0

    return FixedStakeSummary(
        tickets=tickets,
        hits=hits,
        total_stake_yen=total_stake,
        total_return_yen=total_return,
        hit_rate=hits / tickets if tickets else 0.0,
        return_rate=total_return / total_stake if total_stake else 0.0,
        return_rate_without_largest_hit=(total_return - largest_hit) / total_stake
        if total_stake
        else 0.0,
        largest_hit_share=largest_hit / total_return if total_return else 0.0,
        top3_hit_share=payout_share(3),
        top5_hit_share=payout_share(5),
    )


def _ticket_identity(ticket: TicketResult) -> tuple[str, BetType, tuple[str, ...]]:
    selection = (
        tuple(sorted(ticket.selection))
        if ticket.bet_type in _UNORDERED_BET_TYPES
        else ticket.selection
    )
    return ticket.race_id, ticket.bet_type, selection


def evaluate_ticket_results_by_bet_type(
    tickets: Iterable[TicketResult],
) -> BetTypeEvaluationReport:
    """Evaluate all supported bet types independently at one point per 100 yen."""
    grouped: dict[BetType, list[int]] = {bet_type: [] for bet_type in BetType}
    seen: set[tuple[str, BetType, tuple[str, ...]]] = set()
    for ticket in tickets:
        identity = _ticket_identity(ticket)
        if identity in seen:
            raise ValueError("duplicate ticket within the same race and bet type")
        seen.add(identity)
        grouped[ticket.bet_type].append(ticket.payout_yen)

    return BetTypeEvaluationReport(tuple(
        BetTypeSummary(bet_type, evaluate_fixed_stake(grouped[bet_type]))
        for bet_type in BetType
    ))
