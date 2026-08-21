"""Reproducible artifacts for batch evaluation of frozen bet-type forecasts."""

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bet_type_forecast import load_frozen_bet_type_forecast
from .bet_type_settlement import (
    evaluate_frozen_bet_type_candidates,
    load_bet_type_race_payouts,
)
from .data_audit import sha256_file
from .domain import BetType
from .evaluation import (
    BetTypeEvaluationReport,
    BetTypeSummary,
    FixedStakeSummary,
)


BET_TYPE_EVALUATION_ARTIFACT_SCHEMA_VERSION = "1.0"
_SHA256_LENGTH = 64


def _is_sha256(value: str) -> bool:
    return len(value) == _SHA256_LENGTH and all(
        character in "0123456789abcdef" for character in value
    )


@dataclass(frozen=True)
class BetTypeEvaluationInput:
    """Exact input-file identities for one evaluated race."""

    race_id: str
    forecast_file_sha256: str
    payout_file_sha256: str

    def __post_init__(self) -> None:
        if not self.race_id.strip():
            raise ValueError("race_id must not be empty")
        if not _is_sha256(self.forecast_file_sha256):
            raise ValueError("forecast_file_sha256 must be lowercase SHA-256")
        if not _is_sha256(self.payout_file_sha256):
            raise ValueError("payout_file_sha256 must be lowercase SHA-256")


@dataclass(frozen=True)
class BetTypeEvaluationArtifact:
    """Deterministic inputs and structured six-bet-type evaluation results."""

    inputs: tuple[BetTypeEvaluationInput, ...]
    report: BetTypeEvaluationReport

    def __post_init__(self) -> None:
        if not isinstance(self.inputs, tuple) or not self.inputs:
            raise ValueError("evaluation inputs must be a non-empty tuple")
        race_ids = tuple(row.race_id for row in self.inputs)
        if race_ids != tuple(sorted(race_ids)):
            raise ValueError("evaluation inputs must use deterministic race_id order")
        if len(set(race_ids)) != len(race_ids):
            raise ValueError("evaluation inputs must have unique race_id")
        if any(
            summary.fixed_stake.tickets != len(self.inputs)
            for summary in self.report.summaries
        ):
            raise ValueError("every bet type must contain one ticket per input race")
        for summary in self.report.summaries:
            self._validate_summary(summary.fixed_stake)

    @staticmethod
    def _validate_summary(summary: FixedStakeSummary) -> None:
        integer_fields = (
            summary.tickets,
            summary.hits,
            summary.total_stake_yen,
            summary.total_return_yen,
        )
        if any(type(value) is not int for value in integer_fields):
            raise ValueError("evaluation counts and yen amounts must be integers")
        if not 0 <= summary.hits <= summary.tickets:
            raise ValueError("evaluation hits must fit within ticket count")
        if summary.total_stake_yen != summary.tickets * 100:
            raise ValueError("evaluation stake must be fixed at 100 yen per ticket")
        if summary.total_return_yen < 0:
            raise ValueError("evaluation return must not be negative")

        rate_fields = (
            summary.hit_rate,
            summary.return_rate,
            summary.return_rate_without_largest_hit,
            summary.largest_hit_share,
            summary.top3_hit_share,
            summary.top5_hit_share,
        )
        if any(
            type(value) is not float or not math.isfinite(value)
            for value in rate_fields
        ):
            raise ValueError("evaluation rates must be finite floats")
        expected_hit_rate = summary.hits / summary.tickets
        expected_return_rate = summary.total_return_yen / summary.total_stake_yen
        if not math.isclose(summary.hit_rate, expected_hit_rate):
            raise ValueError("evaluation hit_rate is inconsistent")
        if not math.isclose(summary.return_rate, expected_return_rate):
            raise ValueError("evaluation return_rate is inconsistent")
        if not 0.0 <= summary.return_rate_without_largest_hit <= summary.return_rate:
            raise ValueError("evaluation return sensitivity is inconsistent")
        if not (
            0.0
            <= summary.largest_hit_share
            <= summary.top3_hit_share
            <= summary.top5_hit_share
            <= 1.0
        ):
            raise ValueError("evaluation payout shares are inconsistent")
        if summary.total_return_yen == 0 and any(
            value != 0.0
            for value in (
                summary.largest_hit_share,
                summary.top3_hit_share,
                summary.top5_hit_share,
            )
        ):
            raise ValueError("zero-return evaluation must have zero payout shares")

    def to_markdown(self) -> str:
        return self.report.to_markdown()


def evaluate_bet_type_race_directories(
    race_directories: tuple[Path, ...],
) -> BetTypeEvaluationArtifact:
    """Load, hash, match, and evaluate complete race directories."""
    if not isinstance(race_directories, tuple) or not race_directories:
        raise ValueError("race_directories must be a non-empty tuple")

    loaded = []
    for directory in race_directories:
        forecast_path = directory / "bet-types-shadow.json"
        payout_path = directory / "bet-types-payouts.json"
        forecast_hash = sha256_file(forecast_path)
        payout_hash = sha256_file(payout_path)
        snapshot = load_frozen_bet_type_forecast(forecast_path)
        payouts = load_bet_type_race_payouts(payout_path)
        if (
            forecast_hash != sha256_file(forecast_path)
            or payout_hash != sha256_file(payout_path)
        ):
            raise ValueError("evaluation input changed while it was being loaded")
        if snapshot.forecast.race_id != payouts.race_id:
            raise ValueError("forecast and payout table must have the same race_id")
        loaded.append((
            snapshot.forecast.race_id,
            snapshot,
            payouts,
            forecast_hash,
            payout_hash,
        ))

    loaded.sort(key=lambda row: row[0])
    inputs = tuple(
        BetTypeEvaluationInput(race_id, forecast_hash, payout_hash)
        for race_id, _, _, forecast_hash, payout_hash in loaded
    )
    report = evaluate_frozen_bet_type_candidates(
        tuple(row[1] for row in loaded),
        tuple(row[2] for row in loaded),
    )
    return BetTypeEvaluationArtifact(inputs, report)


def _summary_payload(summary: BetTypeSummary) -> dict[str, object]:
    fixed = summary.fixed_stake
    return {
        "bet_type": summary.bet_type.value,
        "tickets": fixed.tickets,
        "hits": fixed.hits,
        "total_stake_yen": fixed.total_stake_yen,
        "total_return_yen": fixed.total_return_yen,
        "hit_rate": fixed.hit_rate,
        "return_rate": fixed.return_rate,
        "return_rate_without_largest_hit": fixed.return_rate_without_largest_hit,
        "largest_hit_share": fixed.largest_hit_share,
        "top3_hit_share": fixed.top3_hit_share,
        "top5_hit_share": fixed.top5_hit_share,
    }


def _payload(artifact: BetTypeEvaluationArtifact) -> dict[str, object]:
    summaries = {row.bet_type: row for row in artifact.report.summaries}
    return {
        "inputs": [
            {
                "race_id": row.race_id,
                "forecast_file_sha256": row.forecast_file_sha256,
                "payout_file_sha256": row.payout_file_sha256,
            }
            for row in artifact.inputs
        ],
        "summaries": [
            _summary_payload(summaries[bet_type])
            for bet_type in BetType
        ],
    }


def _canonical(payload: dict[str, object]) -> str:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def save_bet_type_evaluation_artifact(
    artifact: BetTypeEvaluationArtifact, path: str | Path
) -> str:
    """Save one integrity-protected evaluation artifact without overwriting."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _payload(artifact)
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    envelope = {
        "schema_version": BET_TYPE_EVALUATION_ARTIFACT_SCHEMA_VERSION,
        "sha256": digest,
        "payload": payload,
    }
    with target.open("x", encoding="utf-8") as handle:
        json.dump(envelope, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    return digest


def _required(payload: dict[str, Any], key: str, expected_type: type) -> Any:
    value = payload.get(key)
    if type(value) is not expected_type:
        raise ValueError(f"evaluation {key} has an invalid type")
    return value


def _load_summary(payload: dict[str, Any]) -> BetTypeSummary:
    return BetTypeSummary(
        bet_type=BetType(_required(payload, "bet_type", str)),
        fixed_stake=FixedStakeSummary(
            tickets=_required(payload, "tickets", int),
            hits=_required(payload, "hits", int),
            total_stake_yen=_required(payload, "total_stake_yen", int),
            total_return_yen=_required(payload, "total_return_yen", int),
            hit_rate=_required(payload, "hit_rate", float),
            return_rate=_required(payload, "return_rate", float),
            return_rate_without_largest_hit=_required(
                payload, "return_rate_without_largest_hit", float
            ),
            largest_hit_share=_required(payload, "largest_hit_share", float),
            top3_hit_share=_required(payload, "top3_hit_share", float),
            top5_hit_share=_required(payload, "top5_hit_share", float),
        ),
    )


def load_bet_type_evaluation_artifact(
    path: str | Path,
) -> BetTypeEvaluationArtifact:
    """Load an evaluation artifact after schema and integrity verification."""
    envelope = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(envelope, dict):
        raise ValueError("bet type evaluation envelope must be an object")
    if (
        envelope.get("schema_version")
        != BET_TYPE_EVALUATION_ARTIFACT_SCHEMA_VERSION
    ):
        raise ValueError("unsupported bet type evaluation schema_version")
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("bet type evaluation payload must be an object")
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    if digest != envelope.get("sha256"):
        raise ValueError("bet type evaluation integrity check failed")

    input_payloads = payload.get("inputs")
    summary_payloads = payload.get("summaries")
    if not isinstance(input_payloads, list):
        raise ValueError("evaluation inputs must be an array")
    if not isinstance(summary_payloads, list):
        raise ValueError("evaluation summaries must be an array")
    if any(not isinstance(row, dict) for row in input_payloads):
        raise ValueError("each evaluation input must be an object")
    if any(not isinstance(row, dict) for row in summary_payloads):
        raise ValueError("each evaluation summary must be an object")

    inputs = tuple(
        BetTypeEvaluationInput(
            race_id=_required(row, "race_id", str),
            forecast_file_sha256=_required(
                row, "forecast_file_sha256", str
            ),
            payout_file_sha256=_required(row, "payout_file_sha256", str),
        )
        for row in input_payloads
    )
    report = BetTypeEvaluationReport(tuple(
        _load_summary(row) for row in summary_payloads
    ))
    return BetTypeEvaluationArtifact(inputs, report)
