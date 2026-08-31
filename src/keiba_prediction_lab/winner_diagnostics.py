"""Winner-focused diagnostics from audited pre-race bundles and official payouts."""

from dataclasses import dataclass
from pathlib import Path
from statistics import mean

from .bet_type_settlement import load_bet_type_race_payouts
from .bundle_audit import load_audited_prediction_bundle
from .domain import BetType


HIGH_CONFIDENCE_THRESHOLD = 0.4


@dataclass(frozen=True)
class WinnerMissRace:
    race_id: str
    predicted_winner: str
    actual_winners: tuple[str, ...]
    hit: bool
    actual_winner_best_rank: int
    actual_winner_probability: float
    predicted_winner_probability: float
    probability_margin: float
    miss_type: str
    high_confidence_miss: bool


@dataclass(frozen=True)
class WinnerMissReport:
    races: tuple[WinnerMissRace, ...]

    @property
    def race_count(self) -> int:
        return len(self.races)

    @property
    def hits(self) -> int:
        return sum(row.hit for row in self.races)

    @property
    def top1_accuracy(self) -> float:
        return self.hits / self.race_count

    @property
    def top2_coverage(self) -> float:
        return (
            sum(row.actual_winner_best_rank <= 2 for row in self.races)
            / self.race_count
        )

    @property
    def top3_coverage(self) -> float:
        return (
            sum(row.actual_winner_best_rank <= 3 for row in self.races)
            / self.race_count
        )

    @property
    def high_confidence_misses(self) -> int:
        return sum(row.high_confidence_miss for row in self.races)

    @property
    def mean_actual_winner_rank(self) -> float:
        return mean(row.actual_winner_best_rank for row in self.races)

    def to_dict(self) -> dict[str, object]:
        return {
            "race_count": self.race_count,
            "hits": self.hits,
            "top1_accuracy": self.top1_accuracy,
            "top2_coverage": self.top2_coverage,
            "top3_coverage": self.top3_coverage,
            "high_confidence_misses": self.high_confidence_misses,
            "mean_actual_winner_rank": self.mean_actual_winner_rank,
            "races": [
                {
                    "race_id": row.race_id,
                    "predicted_winner": row.predicted_winner,
                    "actual_winners": list(row.actual_winners),
                    "hit": row.hit,
                    "actual_winner_best_rank": row.actual_winner_best_rank,
                    "actual_winner_probability": row.actual_winner_probability,
                    "predicted_winner_probability": row.predicted_winner_probability,
                    "probability_margin": row.probability_margin,
                    "miss_type": row.miss_type,
                    "high_confidence_miss": row.high_confidence_miss,
                }
                for row in self.races
            ],
        }

    def to_markdown(self) -> str:
        lines = [
            "# 1着予測の外れ方診断",
            "",
            f"- 評価レース数: {self.race_count}",
            f"- 1着的中率: {self.top1_accuracy:.1%} ({self.hits}/{self.race_count})",
            f"- 実勝馬の上位2頭カバー率: {self.top2_coverage:.1%}",
            f"- 実勝馬の上位3頭カバー率: {self.top3_coverage:.1%}",
            f"- 実勝馬の平均予測順位: {self.mean_actual_winner_rank:.2f}",
            f"- 高信頼の外れ: {self.high_confidence_misses}",
            "",
            "| レース | 予測1位 | 実勝馬 | 的中 | 実勝馬順位 | 予測1位確率 | 1位差 | 分類 |",
            "|---|---|---|---:|---:|---:|---:|---|",
        ]
        for row in self.races:
            lines.append(
                f"| {row.race_id} | {row.predicted_winner} | "
                f"{', '.join(row.actual_winners)} | {'○' if row.hit else '×'} | "
                f"{row.actual_winner_best_rank} | {row.predicted_winner_probability:.1%} | "
                f"{row.probability_margin:.1%} | {row.miss_type} |"
            )
        lines.extend([
            "",
            "> 高信頼の外れは、予測1位の勝率が40%以上だった不的中です。少数レースだけでモデル更新の根拠とは扱いません。",
        ])
        return "\n".join(lines) + "\n"


def _miss_type(hit: bool, winner_rank: int) -> str:
    if hit:
        return "hit"
    if winner_rank == 2:
        return "near_miss_rank_2"
    if winner_rank == 3:
        return "top3_miss"
    return "deep_rank_miss"


def diagnose_winner_misses(
    race_directories: tuple[str | Path, ...],
) -> WinnerMissReport:
    """Diagnose top-one errors without modifying frozen prediction artifacts."""
    if not race_directories:
        raise ValueError("at least one race directory is required")
    rows = []
    seen_races: set[str] = set()
    for directory in race_directories:
        root = Path(directory)
        audited = load_audited_prediction_bundle(root)
        prediction = audited.bundle.actual_prediction
        payouts = load_bet_type_race_payouts(root / "bet-types-payouts.json")
        if payouts.race_id != prediction.race_id:
            raise ValueError("prediction bundle and payout race_id must match")
        if prediction.race_id in seen_races:
            raise ValueError("race directories must contain unique race_id values")
        predictions = {row.horse_id: row for row in prediction.predictions}
        actual_winners = tuple(sorted({
            payout.selection[0]
            for payout in payouts.payouts
            if payout.bet_type is BetType.WIN
        }))
        if not actual_winners or any(
            winner not in predictions for winner in actual_winners
        ):
            raise ValueError("win payouts must identify predicted runners")
        ranked = sorted(prediction.predictions, key=lambda row: row.predicted_rank)
        if len(ranked) < 2:
            raise ValueError("winner diagnostics require at least two runners")
        predicted_winner = ranked[0]
        runner_up_probability = ranked[1].win_probability
        winner_rank = min(predictions[winner].predicted_rank for winner in actual_winners)
        winner_probability = sum(
            predictions[winner].win_probability for winner in actual_winners
        ) / len(actual_winners)
        hit = predicted_winner.horse_id in actual_winners
        rows.append(WinnerMissRace(
            race_id=prediction.race_id,
            predicted_winner=predicted_winner.horse_id,
            actual_winners=actual_winners,
            hit=hit,
            actual_winner_best_rank=winner_rank,
            actual_winner_probability=winner_probability,
            predicted_winner_probability=predicted_winner.win_probability,
            probability_margin=(
                predicted_winner.win_probability - runner_up_probability
            ),
            miss_type=_miss_type(hit, winner_rank),
            high_confidence_miss=(
                not hit
                and predicted_winner.win_probability >= HIGH_CONFIDENCE_THRESHOLD
            ),
        ))
        seen_races.add(prediction.race_id)
    return WinnerMissReport(tuple(sorted(rows, key=lambda row: row.race_id)))
