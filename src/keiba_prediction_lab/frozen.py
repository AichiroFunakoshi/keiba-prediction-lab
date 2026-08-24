"""Immutable pre-race prediction snapshots and reproducible evaluation reports."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from .domain import PredictionRecord, validate_race_predictions


class PredictionPhase(str, Enum):
    PRE_ODDS = "pre_odds"
    POST_ODDS = "post_odds"


@dataclass(frozen=True)
class FrozenTrifectaTicket:
    selection: tuple[str, str, str]
    stake_yen: int = 100

    def __post_init__(self) -> None:
        if len(set(self.selection)) != 3 or any(not item.strip() for item in self.selection):
            raise ValueError("trifecta selection requires three unique horse identifiers")
        if self.stake_yen != 100:
            raise ValueError("trifecta stake must be fixed at 100 yen")


@dataclass(frozen=True)
class FrozenPrediction:
    race_id: str
    scheduled_at: datetime
    frozen_at: datetime
    phase: PredictionPhase
    input_data_version: str
    predictions: tuple[PredictionRecord, ...]
    trifecta_tickets: tuple[FrozenTrifectaTicket, ...] = ()

    def __post_init__(self) -> None:
        if not self.race_id.strip() or not self.input_data_version.strip():
            raise ValueError("race_id and input_data_version must not be empty")
        for field_name in ("scheduled_at", "frozen_at"):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware")
        if self.frozen_at >= self.scheduled_at:
            raise ValueError("frozen_at must be before scheduled_at")
        validate_race_predictions(self.predictions, tolerance=1e-8)
        if any(row.race_id != self.race_id for row in self.predictions):
            raise ValueError("prediction race_id must match snapshot race_id")
        if len({row.model_version for row in self.predictions}) != 1:
            raise ValueError("snapshot predictions must share model_version")
        if any(row.predicted_at > self.frozen_at for row in self.predictions):
            raise ValueError("predicted_at must not be later than frozen_at")
        selections = [ticket.selection for ticket in self.trifecta_tickets]
        if len(set(selections)) != len(selections):
            raise ValueError("trifecta tickets must be unique")
        if len(self.trifecta_tickets) > 1:
            raise ValueError("actual trifecta purchase candidate must be limited to one ticket")
        if self.trifecta_tickets:
            predicted_winner = min(
                self.predictions, key=lambda row: row.predicted_rank
            ).horse_id
            if any(ticket.selection[0] != predicted_winner for ticket in self.trifecta_tickets):
                raise ValueError("all trifecta tickets must use the predicted winner as first anchor")
            runners = {row.horse_id for row in self.predictions}
            if any(set(ticket.selection) - runners for ticket in self.trifecta_tickets):
                raise ValueError("trifecta tickets must reference snapshot runners")

    @property
    def model_version(self) -> str:
        return self.predictions[0].model_version


def _payload(snapshot: FrozenPrediction) -> dict[str, object]:
    return {
        "race_id": snapshot.race_id,
        "scheduled_at": snapshot.scheduled_at.isoformat(),
        "frozen_at": snapshot.frozen_at.isoformat(),
        "phase": snapshot.phase.value,
        "input_data_version": snapshot.input_data_version,
        "predictions": [
            {
                "race_id": row.race_id,
                "horse_id": row.horse_id,
                "predicted_at": row.predicted_at.isoformat(),
                "model_version": row.model_version,
                "win_probability": row.win_probability,
                "top3_probability": row.top3_probability,
                "predicted_rank": row.predicted_rank,
            }
            for row in sorted(snapshot.predictions, key=lambda item: item.predicted_rank)
        ],
        "trifecta_tickets": [
            {"selection": list(ticket.selection), "stake_yen": ticket.stake_yen}
            for ticket in snapshot.trifecta_tickets
        ],
    }


def _canonical(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def save_frozen_prediction(snapshot: FrozenPrediction, path: str | Path) -> str:
    """Create a new snapshot file without ever overwriting an existing record."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _payload(snapshot)
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    envelope = {"schema_version": "1.0", "sha256": digest, "payload": payload}
    with target.open("x", encoding="utf-8") as handle:
        json.dump(envelope, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    return digest


def load_frozen_prediction_bytes(content: bytes) -> FrozenPrediction:
    """Load one immutable byte snapshot of a frozen prediction."""
    envelope = json.loads(content.decode("utf-8"))
    if envelope.get("schema_version") != "1.0":
        raise ValueError("unsupported frozen prediction schema_version")
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("frozen prediction payload must be an object")
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    if digest != envelope.get("sha256"):
        raise ValueError("frozen prediction integrity check failed")
    predictions = tuple(
        PredictionRecord(
            race_id=row["race_id"],
            horse_id=row["horse_id"],
            predicted_at=datetime.fromisoformat(row["predicted_at"]),
            model_version=row["model_version"],
            win_probability=row["win_probability"],
            top3_probability=row["top3_probability"],
            predicted_rank=row["predicted_rank"],
        )
        for row in payload["predictions"]
    )
    tickets = tuple(
        FrozenTrifectaTicket(tuple(row["selection"]), row["stake_yen"])
        for row in payload["trifecta_tickets"]
    )
    return FrozenPrediction(
        race_id=payload["race_id"],
        scheduled_at=datetime.fromisoformat(payload["scheduled_at"]),
        frozen_at=datetime.fromisoformat(payload["frozen_at"]),
        phase=PredictionPhase(payload["phase"]),
        input_data_version=payload["input_data_version"],
        predictions=predictions,
        trifecta_tickets=tickets,
    )


def load_frozen_prediction(path: str | Path) -> FrozenPrediction:
    return load_frozen_prediction_bytes(Path(path).read_bytes())


@dataclass(frozen=True)
class TrifectaPayout:
    selection: tuple[str, str, str]
    payout_yen: int

    def __post_init__(self) -> None:
        if len(set(self.selection)) != 3:
            raise ValueError("winning trifecta requires three unique horses")
        if self.payout_yen < 0:
            raise ValueError("payout_yen must not be negative")


@dataclass(frozen=True)
class FrozenRaceResult:
    race_id: str
    finish_positions: tuple[tuple[str, int], ...]
    trifecta_payouts: tuple[TrifectaPayout, ...]

    def __post_init__(self) -> None:
        if not self.race_id.strip() or not self.finish_positions:
            raise ValueError("race result requires race_id and finish positions")
        horse_ids = [horse_id for horse_id, _ in self.finish_positions]
        if len(set(horse_ids)) != len(horse_ids):
            raise ValueError("finish positions contain duplicate horses")
        if any(position < 1 for _, position in self.finish_positions):
            raise ValueError("finish positions must be positive")
        if not any(position == 1 for _, position in self.finish_positions):
            raise ValueError("race result requires at least one winner")


@dataclass(frozen=True)
class FrozenRaceEvaluation:
    race_id: str
    top1_hit: bool
    trifecta_hit: bool
    ticket_count: int
    stake_yen: int
    payout_yen: int


@dataclass(frozen=True)
class FrozenEvaluationReport:
    races: tuple[FrozenRaceEvaluation, ...]
    pre_odds_race_count: int
    post_odds_race_count: int

    @property
    def race_count(self) -> int:
        return len(self.races)

    @property
    def top1_hits(self) -> int:
        return sum(row.top1_hit for row in self.races)

    @property
    def trifecta_hits(self) -> int:
        return sum(row.trifecta_hit for row in self.races)

    @property
    def total_stake_yen(self) -> int:
        return sum(row.stake_yen for row in self.races)

    @property
    def total_payout_yen(self) -> int:
        return sum(row.payout_yen for row in self.races)

    @property
    def return_rate(self) -> float:
        return self.total_payout_yen / self.total_stake_yen if self.total_stake_yen else 0.0

    @property
    def highest_payout_concentration(self) -> float:
        if not self.total_payout_yen:
            return 0.0
        return max(row.payout_yen for row in self.races) / self.total_payout_yen

    def to_markdown(self) -> str:
        top1_rate = self.top1_hits / self.race_count if self.race_count else 0.0
        trifecta_rate = self.trifecta_hits / self.race_count if self.race_count else 0.0
        lines = [
            "# 固定予想評価レポート",
            "",
            f"- 対象レース数: {self.race_count}",
            f"- オッズ確認前: {self.pre_odds_race_count}",
            f"- オッズ確認後: {self.post_odds_race_count}",
            f"- 1着的中: {self.top1_hits}/{self.race_count} ({top1_rate:.1%})",
            f"- 三連単的中: {self.trifecta_hits}/{self.race_count} ({trifecta_rate:.1%})",
            f"- 購入額: {self.total_stake_yen:,}円",
            f"- 払戻額: {self.total_payout_yen:,}円",
            f"- 回収率: {self.return_rate:.1%}",
            f"- 最高払戻集中度: {self.highest_payout_concentration:.1%}",
            "",
            "| race_id | 1着 | 三連単 | 点数 | 購入額 | 払戻額 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for row in self.races:
            lines.append(
                f"| {row.race_id} | {'的中' if row.top1_hit else '不的中'} | "
                f"{'的中' if row.trifecta_hit else '不的中'} | {row.ticket_count} | "
                f"{row.stake_yen:,}円 | {row.payout_yen:,}円 |"
            )
        return "\n".join(lines) + "\n"


def evaluate_frozen_predictions(
    snapshots: tuple[FrozenPrediction, ...],
    results: tuple[FrozenRaceResult, ...],
) -> FrozenEvaluationReport:
    if not snapshots:
        raise ValueError("at least one frozen prediction is required")
    snapshot_by_race = {row.race_id: row for row in snapshots}
    result_by_race = {row.race_id: row for row in results}
    if len(snapshot_by_race) != len(snapshots) or len(result_by_race) != len(results):
        raise ValueError("snapshots and results must have unique race_id")
    if snapshot_by_race.keys() != result_by_race.keys():
        raise ValueError("snapshots and results must contain identical races")

    evaluations = []
    for race_id in sorted(snapshot_by_race):
        snapshot = snapshot_by_race[race_id]
        result = result_by_race[race_id]
        winners = {
            horse_id for horse_id, position in result.finish_positions if position == 1
        }
        predicted_winner = min(
            snapshot.predictions, key=lambda row: row.predicted_rank
        ).horse_id
        payouts = {row.selection: row.payout_yen for row in result.trifecta_payouts}
        selected_payouts = [
            payouts[ticket.selection]
            for ticket in snapshot.trifecta_tickets
            if ticket.selection in payouts
        ]
        evaluations.append(FrozenRaceEvaluation(
            race_id=race_id,
            top1_hit=predicted_winner in winners,
            trifecta_hit=bool(selected_payouts),
            ticket_count=len(snapshot.trifecta_tickets),
            stake_yen=sum(ticket.stake_yen for ticket in snapshot.trifecta_tickets),
            payout_yen=sum(selected_payouts),
        ))
    return FrozenEvaluationReport(
        races=tuple(evaluations),
        pre_odds_race_count=sum(row.phase is PredictionPhase.PRE_ODDS for row in snapshots),
        post_odds_race_count=sum(row.phase is PredictionPhase.POST_ODDS for row in snapshots),
    )
