"""Evaluation helpers that keep every betting selection at a fixed 100 yen."""

from dataclasses import dataclass
from typing import Iterable


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
    )
