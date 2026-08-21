"""Paired comparison of two frozen-compatible trifecta generators."""

from collections.abc import Sequence
from dataclasses import dataclass
from math import comb, log

from .shadow_snapshot import FrozenShadowForecast
from .trifecta import TrifectaForecast, TrifectaRaceResult, TrifectaStrategy


@dataclass(frozen=True)
class PairedPortfolioComparison:
    strategy: TrifectaStrategy
    ticket_count: int
    race_count: int
    both_hit: int
    baseline_only_hit: int
    candidate_only_hit: int
    neither_hit: int
    discordant_exact_p_value: float

    @property
    def baseline_hits(self) -> int:
        return self.both_hit + self.baseline_only_hit

    @property
    def candidate_hits(self) -> int:
        return self.both_hit + self.candidate_only_hit

    @property
    def net_candidate_hits(self) -> int:
        return self.candidate_only_hit - self.baseline_only_hit


@dataclass(frozen=True)
class TrifectaGeneratorComparison:
    baseline_label: str
    candidate_label: str
    race_count: int
    baseline_mean_log_loss: float
    candidate_mean_log_loss: float
    rows: tuple[PairedPortfolioComparison, ...]

    @property
    def log_loss_improvement(self) -> float:
        return self.baseline_mean_log_loss - self.candidate_mean_log_loss

    def to_markdown(self) -> str:
        lines = [
            "# 三連単生成モデルの対応比較",
            "",
            f"- 基準モデル: {self.baseline_label}",
            f"- 候補モデル: {self.candidate_label}",
            f"- 対象レース数: {self.race_count}",
            f"- 基準モデル平均Log loss: {self.baseline_mean_log_loss:.6f}",
            f"- 候補モデル平均Log loss: {self.candidate_mean_log_loss:.6f}",
            f"- Log loss改善量: {self.log_loss_improvement:+.6f}",
            "",
            "| 戦略 | 点数 | 基準的中 | 候補的中 | 候補のみ | 基準のみ | 純増 | 対応p値 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for row in self.rows:
            lines.append(
                f"| {row.strategy.value} | {row.ticket_count} | "
                f"{row.baseline_hits}/{row.race_count} | "
                f"{row.candidate_hits}/{row.race_count} | "
                f"{row.candidate_only_hit} | {row.baseline_only_hit} | "
                f"{row.net_candidate_hits:+d} | {row.discordant_exact_p_value:.4f} |"
            )
        lines.extend((
            "",
            "この結果は固定期間の比較材料であり、モデルや係数を自動更新しない。",
        ))
        return "\n".join(lines) + "\n"


def _two_sided_discordant_p_value(candidate_only: int, baseline_only: int) -> float:
    discordant = candidate_only + baseline_only
    if discordant == 0:
        return 1.0
    smaller = min(candidate_only, baseline_only)
    lower_tail = sum(comb(discordant, value) for value in range(smaller + 1))
    return min(1.0, 2.0 * lower_tail / (2 ** discordant))


def _portfolio_selections(
    forecast: TrifectaForecast,
) -> dict[tuple[TrifectaStrategy, int], set[tuple[str, str, str]]]:
    return {
        (portfolio.strategy, portfolio.ticket_count): {
            row.selection for row in portfolio.combinations
        }
        for portfolio in forecast.shadow_portfolios
    }


def _result_probability(
    forecast: TrifectaForecast,
    winning_selections: set[tuple[str, str, str]],
) -> float:
    return sum(
        row.probability for row in forecast.all_combinations
        if row.selection in winning_selections
    )


def compare_trifecta_generators(
    baseline_label: str,
    baseline_forecasts: Sequence[TrifectaForecast],
    candidate_label: str,
    candidate_forecasts: Sequence[TrifectaForecast],
    results: Sequence[TrifectaRaceResult],
) -> TrifectaGeneratorComparison:
    if not baseline_label.strip() or not candidate_label.strip():
        raise ValueError("generator labels must not be empty")
    if not baseline_forecasts:
        raise ValueError("at least one forecast is required")
    baseline_by_race = {row.race_id: row for row in baseline_forecasts}
    candidate_by_race = {row.race_id: row for row in candidate_forecasts}
    result_by_race = {row.race_id: row for row in results}
    if (
        len(baseline_by_race) != len(baseline_forecasts)
        or len(candidate_by_race) != len(candidate_forecasts)
        or len(result_by_race) != len(results)
    ):
        raise ValueError("forecasts and results must have unique race_id")
    if not (
        baseline_by_race.keys() == candidate_by_race.keys() == result_by_race.keys()
    ):
        raise ValueError("both generators and results must contain identical races")

    first_race = next(iter(baseline_by_race))
    expected_keys = set(_portfolio_selections(baseline_by_race[first_race]))
    if not expected_keys:
        raise ValueError("forecasts must contain shadow portfolios")
    for race_id in baseline_by_race:
        if set(_portfolio_selections(baseline_by_race[race_id])) != expected_keys:
            raise ValueError("baseline portfolio contracts must be identical")
        if set(_portfolio_selections(candidate_by_race[race_id])) != expected_keys:
            raise ValueError("candidate portfolio contract must match baseline")

    counts = {
        key: {"both": 0, "baseline": 0, "candidate": 0, "neither": 0}
        for key in expected_keys
    }
    baseline_losses = []
    candidate_losses = []
    for race_id in sorted(baseline_by_race):
        winners = set(result_by_race[race_id].winning_selections)
        baseline = baseline_by_race[race_id]
        candidate = candidate_by_race[race_id]
        baseline_probability = _result_probability(baseline, winners)
        candidate_probability = _result_probability(candidate, winners)
        if baseline_probability <= 0.0 or candidate_probability <= 0.0:
            raise ValueError("winning selection must have positive probability")
        baseline_losses.append(-log(baseline_probability))
        candidate_losses.append(-log(candidate_probability))

        baseline_portfolios = _portfolio_selections(baseline)
        candidate_portfolios = _portfolio_selections(candidate)
        for key in expected_keys:
            baseline_hit = bool(baseline_portfolios[key] & winners)
            candidate_hit = bool(candidate_portfolios[key] & winners)
            if baseline_hit and candidate_hit:
                counts[key]["both"] += 1
            elif baseline_hit:
                counts[key]["baseline"] += 1
            elif candidate_hit:
                counts[key]["candidate"] += 1
            else:
                counts[key]["neither"] += 1

    rows = []
    for strategy, ticket_count in sorted(
        expected_keys, key=lambda key: (key[0].value, key[1])
    ):
        row = counts[(strategy, ticket_count)]
        rows.append(PairedPortfolioComparison(
            strategy=strategy,
            ticket_count=ticket_count,
            race_count=len(baseline_by_race),
            both_hit=row["both"],
            baseline_only_hit=row["baseline"],
            candidate_only_hit=row["candidate"],
            neither_hit=row["neither"],
            discordant_exact_p_value=_two_sided_discordant_p_value(
                row["candidate"], row["baseline"]
            ),
        ))
    return TrifectaGeneratorComparison(
        baseline_label=baseline_label,
        candidate_label=candidate_label,
        race_count=len(baseline_by_race),
        baseline_mean_log_loss=sum(baseline_losses) / len(baseline_losses),
        candidate_mean_log_loss=sum(candidate_losses) / len(candidate_losses),
        rows=tuple(rows),
    )


def compare_frozen_trifecta_generators(
    baseline_snapshots: Sequence[FrozenShadowForecast],
    candidate_snapshots: Sequence[FrozenShadowForecast],
    results: Sequence[TrifectaRaceResult],
) -> TrifectaGeneratorComparison:
    """Compare generators only when all non-generator inputs are identical."""
    if not baseline_snapshots or not candidate_snapshots:
        raise ValueError("both frozen generator sets are required")
    baseline_by_race = {
        row.forecast.race_id: row for row in baseline_snapshots
    }
    candidate_by_race = {
        row.forecast.race_id: row for row in candidate_snapshots
    }
    if (
        len(baseline_by_race) != len(baseline_snapshots)
        or len(candidate_by_race) != len(candidate_snapshots)
    ):
        raise ValueError("frozen forecasts must have unique race_id")
    if baseline_by_race.keys() != candidate_by_race.keys():
        raise ValueError("frozen generator sets must contain identical races")
    baseline_versions = {row.generator_version for row in baseline_snapshots}
    candidate_versions = {row.generator_version for row in candidate_snapshots}
    if len(baseline_versions) != 1 or len(candidate_versions) != 1:
        raise ValueError("each frozen generator set must use one generator version")

    for race_id, baseline in baseline_by_race.items():
        candidate = candidate_by_race[race_id]
        baseline_contract = (
            baseline.scheduled_at,
            baseline.source_predicted_at,
            baseline.phase,
            baseline.input_data_version,
            baseline.model_version,
        )
        candidate_contract = (
            candidate.scheduled_at,
            candidate.source_predicted_at,
            candidate.phase,
            candidate.input_data_version,
            candidate.model_version,
        )
        if baseline_contract != candidate_contract:
            raise ValueError(
                "frozen comparisons require identical timing, phase, data, and winner model"
            )

    return compare_trifecta_generators(
        next(iter(baseline_versions)),
        tuple(row.forecast for row in baseline_snapshots),
        next(iter(candidate_versions)),
        tuple(row.forecast for row in candidate_snapshots),
        results,
    )
