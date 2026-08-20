"""Conditional trifecta probabilities and non-purchased shadow portfolios."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from .domain import PredictionRecord, validate_race_predictions


DEFAULT_PORTFOLIO_SIZES = (1, 3, 5, 10)


class TrifectaStrategy(str, Enum):
    SINGLE_WINNER_ANCHOR = "single_winner_anchor"
    MULTI_WINNER_SCENARIO = "multi_winner_scenario"


@dataclass(frozen=True)
class TrifectaCombination:
    selection: tuple[str, str, str]
    probability: float

    def __post_init__(self) -> None:
        if len(set(self.selection)) != 3 or any(not value.strip() for value in self.selection):
            raise ValueError("trifecta selection requires three unique horse identifiers")
        if not 0.0 <= self.probability <= 1.0:
            raise ValueError("trifecta probability must be between 0 and 1")


@dataclass(frozen=True)
class ShadowPortfolio:
    strategy: TrifectaStrategy
    ticket_count: int
    combinations: tuple[TrifectaCombination, ...]

    def __post_init__(self) -> None:
        if self.ticket_count < 1 or len(self.combinations) != self.ticket_count:
            raise ValueError("ticket_count must match the number of combinations")
        selections = [row.selection for row in self.combinations]
        if len(set(selections)) != len(selections):
            raise ValueError("portfolio combinations must be unique")

    @property
    def cumulative_probability(self) -> float:
        return sum(row.probability for row in self.combinations)


@dataclass(frozen=True)
class TrifectaForecast:
    race_id: str
    predicted_winner: str
    primary_ticket: TrifectaCombination
    all_combinations: tuple[TrifectaCombination, ...]
    shadow_portfolios: tuple[ShadowPortfolio, ...]

    def __post_init__(self) -> None:
        if not self.race_id.strip() or not self.predicted_winner.strip():
            raise ValueError("race_id and predicted_winner must not be empty")
        if self.primary_ticket.selection[0] != self.predicted_winner:
            raise ValueError("primary ticket must use the predicted winner as first anchor")


@dataclass(frozen=True)
class TrifectaRaceResult:
    race_id: str
    winning_selections: tuple[tuple[str, str, str], ...]

    def __post_init__(self) -> None:
        if not self.race_id.strip() or not self.winning_selections:
            raise ValueError("trifecta result requires race_id and a winning selection")
        if any(len(set(selection)) != 3 for selection in self.winning_selections):
            raise ValueError("winning trifecta requires three unique horses")


@dataclass(frozen=True)
class PortfolioEvaluation:
    strategy: TrifectaStrategy
    ticket_count: int
    race_count: int
    hits: int
    added_ticket_rescues: int
    alternate_winner_rescues: int

    @property
    def hit_rate(self) -> float:
        return self.hits / self.race_count if self.race_count else 0.0


@dataclass(frozen=True)
class ShadowPortfolioReport:
    rows: tuple[PortfolioEvaluation, ...]

    def to_markdown(self) -> str:
        lines = [
            "# 三連単・影の評価ポートフォリオ",
            "",
            "実購入候補は単一1着固定の1点だけとし、3・5・10点は購入しない反実仮想評価である。",
            "",
            "| 戦略 | 点数 | 的中 | 的中率 | 前段階からの救済 | 別1着での救済 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for row in self.rows:
            lines.append(
                f"| {row.strategy.value} | {row.ticket_count} | "
                f"{row.hits}/{row.race_count} | {row.hit_rate:.1%} | "
                f"{row.added_ticket_rescues} | {row.alternate_winner_rescues} |"
            )
        return "\n".join(lines) + "\n"


def rank_trifecta_combinations(
    predictions: Sequence[PredictionRecord],
) -> tuple[TrifectaCombination, ...]:
    """Rank every ordered top-three outcome with Plackett-Luce probabilities."""
    if len(predictions) < 3:
        raise ValueError("at least three runners are required")
    validate_race_predictions(predictions, tolerance=1e-8)
    if any(row.win_probability <= 0.0 for row in predictions):
        raise ValueError("trifecta ranking requires positive win probabilities")
    ordered = sorted(predictions, key=lambda row: row.horse_id)
    combinations = []
    for first in ordered:
        remaining_after_first = 1.0 - first.win_probability
        for second in ordered:
            if second is first:
                continue
            remaining_after_second = remaining_after_first - second.win_probability
            for third in ordered:
                if third is first or third is second:
                    continue
                probability = (
                    first.win_probability
                    * second.win_probability
                    / remaining_after_first
                    * third.win_probability
                    / remaining_after_second
                )
                combinations.append(TrifectaCombination(
                    (first.horse_id, second.horse_id, third.horse_id), probability
                ))
    return tuple(sorted(combinations, key=lambda row: (-row.probability, row.selection)))


def build_trifecta_forecast(
    predictions: Sequence[PredictionRecord],
    *,
    portfolio_sizes: Sequence[int] = DEFAULT_PORTFOLIO_SIZES,
) -> TrifectaForecast:
    combinations = rank_trifecta_combinations(predictions)
    sizes = tuple(portfolio_sizes)
    if not sizes or any(size < 1 for size in sizes):
        raise ValueError("portfolio sizes must be positive")
    if tuple(sorted(set(sizes))) != sizes:
        raise ValueError("portfolio sizes must be unique and increasing")
    predicted_winner = min(predictions, key=lambda row: row.predicted_rank).horse_id
    anchored = tuple(
        row for row in combinations if row.selection[0] == predicted_winner
    )
    if sizes[-1] > len(anchored):
        raise ValueError("largest portfolio exceeds available anchored combinations")

    portfolios = []
    for strategy, candidates in (
        (TrifectaStrategy.SINGLE_WINNER_ANCHOR, anchored),
        (TrifectaStrategy.MULTI_WINNER_SCENARIO, combinations),
    ):
        for size in sizes:
            portfolios.append(ShadowPortfolio(strategy, size, candidates[:size]))
    return TrifectaForecast(
        race_id=predictions[0].race_id,
        predicted_winner=predicted_winner,
        primary_ticket=anchored[0],
        all_combinations=combinations,
        shadow_portfolios=tuple(portfolios),
    )


def evaluate_shadow_portfolios(
    forecasts: Sequence[TrifectaForecast],
    results: Sequence[TrifectaRaceResult],
) -> ShadowPortfolioReport:
    if not forecasts:
        raise ValueError("at least one forecast is required")
    forecast_by_race = {row.race_id: row for row in forecasts}
    result_by_race = {row.race_id: row for row in results}
    if len(forecast_by_race) != len(forecasts) or len(result_by_race) != len(results):
        raise ValueError("forecasts and results must have unique race_id")
    if forecast_by_race.keys() != result_by_race.keys():
        raise ValueError("forecasts and results must contain identical races")

    keys = {
        (portfolio.strategy, portfolio.ticket_count)
        for forecast in forecasts
        for portfolio in forecast.shadow_portfolios
    }
    rows = []
    for strategy in TrifectaStrategy:
        sizes = sorted(size for candidate, size in keys if candidate is strategy)
        previous_selections_by_race: dict[str, set[tuple[str, str, str]]] = {}
        for size in sizes:
            hits = 0
            rescues = 0
            alternate_rescues = 0
            current_by_race: dict[str, set[tuple[str, str, str]]] = {}
            for race_id, forecast in forecast_by_race.items():
                portfolio = next(
                    row for row in forecast.shadow_portfolios
                    if row.strategy is strategy and row.ticket_count == size
                )
                selections = {row.selection for row in portfolio.combinations}
                winners = set(result_by_race[race_id].winning_selections)
                current_by_race[race_id] = selections
                hit = bool(selections & winners)
                hits += hit
                previous = previous_selections_by_race.get(race_id, set())
                rescued = hit and not bool(previous & winners)
                rescues += rescued
                if rescued and any(
                    selection[0] != forecast.predicted_winner
                    for selection in selections & winners
                ):
                    alternate_rescues += 1
            rows.append(PortfolioEvaluation(
                strategy=strategy,
                ticket_count=size,
                race_count=len(forecasts),
                hits=hits,
                added_ticket_rescues=rescues if previous_selections_by_race else 0,
                alternate_winner_rescues=alternate_rescues,
            ))
            previous_selections_by_race = current_by_race
    return ShadowPortfolioReport(tuple(rows))
