"""Settle frozen shadow candidates against explicit per-100-yen payouts."""

from collections.abc import Sequence
from dataclasses import dataclass

from .bet_type_forecast import FrozenBetTypeForecast
from .domain import BetType, TicketResult
from .evaluation import (
    BetTypeEvaluationReport,
    evaluate_ticket_results_by_bet_type,
)


_UNORDERED_BET_TYPES = frozenset((BetType.QUINELLA, BetType.TRIO))


@dataclass(frozen=True)
class BetTypePayout:
    """One positive official payout amount for a 100-yen winning selection."""

    race_id: str
    bet_type: BetType
    selection: tuple[str, ...]
    payout_yen: int

    def __post_init__(self) -> None:
        if not self.race_id.strip():
            raise ValueError("race_id must not be empty")
        if not isinstance(self.bet_type, BetType):
            raise ValueError("bet_type must be a BetType value")
        if len(self.selection) != self.bet_type.selection_size:
            raise ValueError(
                f"{self.bet_type.value} requires "
                f"{self.bet_type.selection_size} selections"
            )
        if any(not horse_id.strip() for horse_id in self.selection):
            raise ValueError("selection identifiers must not be empty")
        if len(set(self.selection)) != len(self.selection):
            raise ValueError("selection identifiers must be unique")
        if (
            self.bet_type in _UNORDERED_BET_TYPES
            and self.selection != tuple(sorted(self.selection))
        ):
            raise ValueError("unordered payout selections must use canonical order")
        if type(self.payout_yen) is not int or self.payout_yen <= 0:
            raise ValueError("payout_yen must be a positive integer")


@dataclass(frozen=True)
class BetTypeRacePayouts:
    """Complete winning payout table for every supported bet type in one race."""

    race_id: str
    payouts: tuple[BetTypePayout, ...]

    def __post_init__(self) -> None:
        if not self.race_id.strip():
            raise ValueError("race_id must not be empty")
        if not self.payouts:
            raise ValueError("payouts must not be empty")
        if any(row.race_id != self.race_id for row in self.payouts):
            raise ValueError("payout race_id must match the payout table")
        identities = [(row.bet_type, row.selection) for row in self.payouts]
        if len(set(identities)) != len(identities):
            raise ValueError("payout selections must be unique within each bet type")
        if {row.bet_type for row in self.payouts} != set(BetType):
            raise ValueError("payout table must contain every supported bet type")


def _validate_payouts_against_forecast(
    snapshot: FrozenBetTypeForecast,
    result: BetTypeRacePayouts,
) -> None:
    forecast = snapshot.forecast
    if result.race_id != forecast.race_id:
        raise ValueError("forecast and payout table must have the same race_id")
    allowed = {
        (row.bet_type, row.selection) for row in forecast.probabilities
    }
    if any(
        (row.bet_type, row.selection) not in allowed for row in result.payouts
    ):
        raise ValueError("payout selections must exist in the frozen probability tables")
    place_payout_count = sum(
        row.bet_type is BetType.PLACE for row in result.payouts
    )
    if place_payout_count < forecast.place_payout_slots:
        raise ValueError(
            "place payouts must cover every payout slot recorded at sales start"
        )


def settle_frozen_bet_type_candidates(
    snapshot: FrozenBetTypeForecast,
    result: BetTypeRacePayouts,
) -> tuple[TicketResult, ...]:
    """Settle all six frozen top candidates as counterfactual 100-yen tickets."""
    _validate_payouts_against_forecast(snapshot, result)
    payouts = {
        (row.bet_type, row.selection): row.payout_yen for row in result.payouts
    }
    tickets = []
    for bet_type in BetType:
        candidate = snapshot.forecast.candidate_for(bet_type)
        tickets.append(TicketResult(
            race_id=snapshot.forecast.race_id,
            bet_type=bet_type,
            selection=candidate.selection,
            payout_yen=payouts.get((bet_type, candidate.selection), 0),
        ))
    return tuple(tickets)


def evaluate_frozen_bet_type_candidates(
    snapshots: Sequence[FrozenBetTypeForecast],
    results: Sequence[BetTypeRacePayouts],
) -> BetTypeEvaluationReport:
    """Evaluate one frozen shadow candidate per bet type and race."""
    if not snapshots:
        raise ValueError("at least one frozen bet type forecast is required")
    snapshot_by_race = {row.forecast.race_id: row for row in snapshots}
    result_by_race = {row.race_id: row for row in results}
    if len(snapshot_by_race) != len(snapshots):
        raise ValueError("frozen bet type forecasts must have unique race_id")
    if len(result_by_race) != len(results):
        raise ValueError("payout tables must have unique race_id")
    if snapshot_by_race.keys() != result_by_race.keys():
        raise ValueError("forecasts and payout tables must contain identical races")

    tickets = tuple(
        ticket
        for race_id in sorted(snapshot_by_race)
        for ticket in settle_frozen_bet_type_candidates(
            snapshot_by_race[race_id], result_by_race[race_id]
        )
    )
    return evaluate_ticket_results_by_bet_type(tickets)
