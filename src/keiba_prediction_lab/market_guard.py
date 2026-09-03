"""Post-odds market-disagreement guard for independent predictions.

The guard never changes model probabilities or horse rankings.  It freezes a
separate, explicitly post-odds recommendation that can abstain when the model's
top horse is far outside the market leaders.  This preserves the pre-odds
prediction while allowing a conservative purchase policy to be evaluated.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .bundle_audit import load_audited_prediction_bundle
from .jra_web_fetch import SOURCE_ID
from .snapshot_adapter import _normalized_name


MARKET_GUARD_POLICY_VERSION = "market-disagreement-shadow-v1"
MARKET_GUARD_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class MarketGuardPolicy:
    max_market_rank: int = 3

    def __post_init__(self) -> None:
        if type(self.max_market_rank) is not int or self.max_market_rank < 1:
            raise ValueError("max_market_rank must be a positive integer")


@dataclass(frozen=True)
class MarketGuardRow:
    race_id: str
    predicted_horse_id: str
    model_win_probability: float
    market_odds: float | None
    market_rank: int | None
    eligible: bool
    reason: str


@dataclass(frozen=True)
class MarketGuardReport:
    observed_at: datetime
    policy: MarketGuardPolicy
    cards_sha256: str
    race_day_manifest_sha256: str
    rows: tuple[MarketGuardRow, ...]

    @property
    def eligible_race_count(self) -> int:
        return sum(row.eligible for row in self.rows)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def assess_market_guard(
    *,
    race_id: str,
    predicted_horse_id: str,
    model_win_probability: float,
    odds_by_horse: dict[str, float | None],
    policy: MarketGuardPolicy = MarketGuardPolicy(),
) -> MarketGuardRow:
    """Assess one frozen top pick without modifying the prediction itself."""
    if not race_id.strip() or not predicted_horse_id.strip():
        raise ValueError("race_id and predicted_horse_id must not be empty")
    if not 0.0 <= model_win_probability <= 1.0:
        raise ValueError("model_win_probability must be between 0 and 1")
    if predicted_horse_id not in odds_by_horse:
        raise ValueError("predicted horse is missing from the market snapshot")
    known_odds = {
        horse_id: float(value)
        for horse_id, value in odds_by_horse.items()
        if value is not None and float(value) > 0.0
    }
    predicted_odds = known_odds.get(predicted_horse_id)
    if predicted_odds is None:
        return MarketGuardRow(
            race_id, predicted_horse_id, model_win_probability,
            None, None, False, "missing-market-odds",
        )
    market_rank = 1 + sum(value < predicted_odds for value in known_odds.values())
    eligible = market_rank <= policy.max_market_rank
    return MarketGuardRow(
        race_id,
        predicted_horse_id,
        model_win_probability,
        predicted_odds,
        market_rank,
        eligible,
        "within-market-rank-limit" if eligible else "market-rank-above-limit",
    )


def _load_cards(content: bytes) -> dict[str, dict[str, float | None]]:
    payload = json.loads(content.decode("utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("JRA cards snapshot must be a non-empty array")
    result: dict[str, dict[str, float | None]] = {}
    for card in payload:
        if not isinstance(card, dict) or not isinstance(card.get("race_id"), str):
            raise ValueError("JRA card has no race_id")
        race_id = card["race_id"]
        if race_id in result:
            raise ValueError("JRA cards snapshot contains duplicate race_id")
        horses = card.get("horses")
        if not isinstance(horses, list) or len(horses) < 2:
            raise ValueError(f"JRA card has too few horses: {race_id}")
        odds_by_horse: dict[str, float | None] = {}
        for horse in horses:
            if not isinstance(horse, dict):
                raise ValueError(f"JRA card horse is invalid: {race_id}")
            horse_id = _normalized_name("horse", horse.get("name"))
            if horse_id in odds_by_horse:
                raise ValueError(f"JRA card contains duplicate horse: {race_id}")
            odds = horse.get("odds")
            if odds is not None and (type(odds) not in (int, float) or odds <= 0):
                raise ValueError(f"JRA card odds are invalid: {race_id}")
            odds_by_horse[horse_id] = float(odds) if odds is not None else None
        result[race_id] = odds_by_horse
    return result


def build_market_guard_report(
    race_day_directory: str | Path,
    cards_path: str | Path,
    *,
    observed_at: datetime,
    policy: MarketGuardPolicy = MarketGuardPolicy(),
) -> MarketGuardReport:
    """Build a post-odds guard from an audited race day and exact card bytes."""
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    root = Path(race_day_directory)
    manifest_content = (root / "race-day.json").read_bytes()
    manifest = json.loads(manifest_content.decode("utf-8"))
    cards_content = Path(cards_path).read_bytes()
    cards = _load_cards(cards_content)
    rows: list[MarketGuardRow] = []
    observed_race_ids: set[str] = set()
    for venue in manifest.get("venues", []):
        for race in venue.get("races", []):
            bundle_path = root / race["prediction_bundle"]
            audited = load_audited_prediction_bundle(bundle_path)
            actual = audited.bundle.actual_prediction
            if not actual.frozen_at <= observed_at < actual.scheduled_at:
                raise ValueError(
                    "market observed_at must be at or after prediction freeze and before every race"
                )
            if actual.race_id not in cards:
                raise ValueError(f"market cards are missing race_id: {actual.race_id}")
            top = next(row for row in actual.predictions if row.predicted_rank == 1)
            rows.append(assess_market_guard(
                race_id=actual.race_id,
                predicted_horse_id=top.horse_id,
                model_win_probability=top.win_probability,
                odds_by_horse=cards[actual.race_id],
                policy=policy,
            ))
            observed_race_ids.add(actual.race_id)
    if not rows:
        raise ValueError("race-day manifest contains no prediction races")
    extra_cards = cards.keys() - observed_race_ids
    if extra_cards:
        raise ValueError(f"market cards contain unmatched races: {sorted(extra_cards)}")
    rows.sort(key=lambda row: row.race_id)
    return MarketGuardReport(
        observed_at,
        policy,
        _sha256(cards_content),
        _sha256(manifest_content),
        tuple(rows),
    )


def build_market_guard_report_from_snapshot(
    race_day_directory: str | Path,
    snapshot_directory: str | Path,
    *,
    policy: MarketGuardPolicy = MarketGuardPolicy(),
) -> MarketGuardReport:
    """Derive market observation time and card hash from an audited snapshot."""
    snapshot = Path(snapshot_directory)
    manifest = json.loads(
        (snapshot / "acquisition-manifest.json").read_text(encoding="utf-8")
    )
    if not isinstance(manifest, dict) or manifest.get("source_id") != SOURCE_ID:
        raise ValueError("not a supported JRA public-web snapshot")
    if manifest.get("private_use_only") is not True:
        raise ValueError("JRA public-web snapshot is missing private-use restriction")
    cards_path = snapshot / "cards.json"
    cards_content = cards_path.read_bytes()
    expected = manifest.get("outputs", {}).get("cards.json", {}).get("sha256")
    if not isinstance(expected, str) or expected != _sha256(cards_content):
        raise ValueError("JRA public-web snapshot hash mismatch: cards.json")
    try:
        observed_at = datetime.fromisoformat(manifest["acquired_at"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("JRA public-web acquired_at is invalid") from error
    return build_market_guard_report(
        race_day_directory,
        cards_path,
        observed_at=observed_at,
        policy=policy,
    )


def _payload(report: MarketGuardReport) -> dict[str, Any]:
    return {
        "policy_version": MARKET_GUARD_POLICY_VERSION,
        "status": "research-shadow",
        "observed_at": report.observed_at.isoformat(),
        "max_market_rank": report.policy.max_market_rank,
        "cards_sha256": report.cards_sha256,
        "race_day_manifest_sha256": report.race_day_manifest_sha256,
        "race_count": len(report.rows),
        "eligible_race_count": report.eligible_race_count,
        "rows": [
            {
                "race_id": row.race_id,
                "predicted_horse_id": row.predicted_horse_id,
                "model_win_probability": row.model_win_probability,
                "market_odds": row.market_odds,
                "market_rank": row.market_rank,
                "eligible": row.eligible,
                "reason": row.reason,
            }
            for row in report.rows
        ],
    }


def save_market_guard_report(
    report: MarketGuardReport, path: str | Path
) -> str:
    """Save an integrity-protected guard without overwriting prior evidence."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _payload(report)
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    digest = _sha256(canonical)
    envelope = {
        "schema_version": MARKET_GUARD_SCHEMA_VERSION,
        "sha256": digest,
        "payload": payload,
    }
    with target.open("x", encoding="utf-8") as handle:
        json.dump(envelope, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    return digest


def load_market_guard_report(path: str | Path) -> MarketGuardReport:
    """Load and verify a saved market guard artifact."""
    envelope = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(envelope, dict) or envelope.get("schema_version") != MARKET_GUARD_SCHEMA_VERSION:
        raise ValueError("unsupported market guard schema_version")
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("market guard payload must be an object")
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if envelope.get("sha256") != _sha256(canonical):
        raise ValueError("market guard integrity check failed")
    if payload.get("policy_version") != MARKET_GUARD_POLICY_VERSION:
        raise ValueError("unsupported market guard policy_version")
    policy = MarketGuardPolicy(int(payload["max_market_rank"]))
    rows = tuple(MarketGuardRow(
        race_id=row["race_id"],
        predicted_horse_id=row["predicted_horse_id"],
        model_win_probability=float(row["model_win_probability"]),
        market_odds=float(row["market_odds"]) if row["market_odds"] is not None else None,
        market_rank=int(row["market_rank"]) if row["market_rank"] is not None else None,
        eligible=bool(row["eligible"]),
        reason=row["reason"],
    ) for row in payload["rows"])
    report = MarketGuardReport(
        datetime.fromisoformat(payload["observed_at"]),
        policy,
        payload["cards_sha256"],
        payload["race_day_manifest_sha256"],
        rows,
    )
    if payload.get("race_count") != len(rows) or (
        payload.get("eligible_race_count") != report.eligible_race_count
    ):
        raise ValueError("market guard counts are inconsistent")
    return report
