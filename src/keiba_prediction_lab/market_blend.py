"""Versioned post-odds forecasts that combine model and market evidence."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .baselines import _top3_probabilities
from .bundle_audit import load_audited_prediction_bundle
from .domain import PredictionRecord, validate_race_predictions
from .jra_web_fetch import SOURCE_ID
from .market_guard import _load_cards
from .trifecta import rank_trifecta_combinations


MARKET_BLEND_SCHEMA_VERSION = "1.0"
MARKET_BLEND_POLICY_VERSION = "market-log-opinion-pool-v1"
DEFAULT_MARKET_WEIGHT = 0.35


@dataclass(frozen=True)
class MarketBlendRunner:
    horse_id: str
    model_probability: float
    market_odds: float
    market_probability: float
    blended_probability: float
    top3_probability: float
    predicted_rank: int


@dataclass(frozen=True)
class MarketBlendRace:
    race_id: str
    scheduled_at: datetime
    source_model_version: str
    model_version: str
    input_data_version: str
    runners: tuple[MarketBlendRunner, ...]
    trifecta_selection: tuple[str, str, str]
    shadow_portfolios: tuple[tuple[str, int, float], ...]


@dataclass(frozen=True)
class MarketBlendForecast:
    observed_at: datetime
    model_weight: float
    market_weight: float
    cards_sha256: str
    race_day_manifest_sha256: str
    races: tuple[MarketBlendRace, ...]


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def blend_probabilities(
    model_probabilities: dict[str, float],
    odds_by_horse: dict[str, float | None],
    *,
    market_weight: float = DEFAULT_MARKET_WEIGHT,
) -> tuple[dict[str, float], dict[str, float]]:
    """Blend independent and normalized implied probabilities on log scale."""
    if not 0.0 < market_weight < 1.0:
        raise ValueError("market_weight must be strictly between 0 and 1")
    if not model_probabilities or model_probabilities.keys() != odds_by_horse.keys():
        raise ValueError("model and market must cover the same non-empty runners")
    if any(not math.isfinite(value) or value <= 0.0 for value in model_probabilities.values()):
        raise ValueError("model probabilities must be finite and positive")
    if abs(sum(model_probabilities.values()) - 1.0) > 1e-8:
        raise ValueError("model probabilities must sum to one")
    if any(value is None or not math.isfinite(float(value)) or float(value) <= 0.0 for value in odds_by_horse.values()):
        raise ValueError("complete finite positive odds are required")
    implied = {horse_id: 1.0 / float(odds_by_horse[horse_id]) for horse_id in model_probabilities}
    implied_total = sum(implied.values())
    market = {horse_id: value / implied_total for horse_id, value in implied.items()}
    model_weight = 1.0 - market_weight
    raw = {
        horse_id: math.exp(
            model_weight * math.log(model_probabilities[horse_id])
            + market_weight * math.log(market[horse_id])
        )
        for horse_id in model_probabilities
    }
    total = sum(raw.values())
    return market, {horse_id: value / total for horse_id, value in raw.items()}


def _race_payload(race: MarketBlendRace) -> dict[str, Any]:
    return {
        "race_id": race.race_id,
        "scheduled_at": race.scheduled_at.isoformat(),
        "source_model_version": race.source_model_version,
        "model_version": race.model_version,
        "input_data_version": race.input_data_version,
        "runners": [vars(row) for row in race.runners],
        "trifecta_selection": list(race.trifecta_selection),
        "stake_yen": 100,
        "shadow_portfolios": [
            {"strategy": strategy, "ticket_count": size, "cumulative_probability": probability, "stake_yen": 0}
            for strategy, size, probability in race.shadow_portfolios
        ],
    }


def _payload(forecast: MarketBlendForecast) -> dict[str, Any]:
    return {
        "policy_version": MARKET_BLEND_POLICY_VERSION,
        "status": "post_odds_revised_prediction",
        "observed_at": forecast.observed_at.isoformat(),
        "model_weight": forecast.model_weight,
        "market_weight": forecast.market_weight,
        "cards_sha256": forecast.cards_sha256,
        "race_day_manifest_sha256": forecast.race_day_manifest_sha256,
        "race_count": len(forecast.races),
        "races": [_race_payload(race) for race in forecast.races],
    }


def build_market_blend_forecast_from_snapshot(
    race_day_directory: str | Path,
    snapshot_directory: str | Path,
    *,
    market_weight: float = DEFAULT_MARKET_WEIGHT,
) -> MarketBlendForecast:
    """Build revised forecasts only for races still unfired at market observation."""
    root = Path(race_day_directory)
    snapshot = Path(snapshot_directory)
    acquisition = json.loads((snapshot / "acquisition-manifest.json").read_text(encoding="utf-8"))
    if acquisition.get("source_id") != SOURCE_ID or acquisition.get("private_use_only") is not True:
        raise ValueError("not a private-use JRA web snapshot")
    observed_at = datetime.fromisoformat(acquisition["acquired_at"])
    cards_content = (snapshot / "cards.json").read_bytes()
    cards_sha256 = _sha256(cards_content)
    if acquisition.get("outputs", {}).get("cards.json", {}).get("sha256") != cards_sha256:
        raise ValueError("JRA cards snapshot hash mismatch")
    cards = _load_cards(cards_content)
    manifest_content = (root / "race-day.json").read_bytes()
    manifest = json.loads(manifest_content.decode("utf-8"))
    races: list[MarketBlendRace] = []
    for venue in manifest["venues"]:
        for row in venue["races"]:
            audited = load_audited_prediction_bundle(root / row["prediction_bundle"])
            actual = audited.bundle.actual_prediction
            if observed_at >= actual.scheduled_at:
                continue
            odds = cards.get(actual.race_id)
            if odds is None:
                raise ValueError(f"market cards are missing race_id: {actual.race_id}")
            model = {item.horse_id: item.win_probability for item in actual.predictions}
            market, blended = blend_probabilities(model, odds, market_weight=market_weight)
            ordered_ids = sorted(blended, key=lambda horse_id: (-blended[horse_id], horse_id))
            rank = {horse_id: index for index, horse_id in enumerate(ordered_ids, start=1)}
            top3_values = _top3_probabilities([blended[horse_id] for horse_id in ordered_ids])
            top3 = dict(zip(ordered_ids, top3_values, strict=True))
            model_version = f"{actual.model_version}-market-log-pool-v1-w{round(market_weight * 100):02d}"
            predictions = tuple(PredictionRecord(
                race_id=actual.race_id,
                horse_id=horse_id,
                predicted_at=observed_at,
                model_version=model_version,
                win_probability=blended[horse_id],
                top3_probability=top3[horse_id],
                predicted_rank=rank[horse_id],
            ) for horse_id in ordered_ids)
            validate_race_predictions(predictions, tolerance=1e-8)
            combinations = rank_trifecta_combinations(predictions)
            anchor = ordered_ids[0]
            anchored = tuple(item for item in combinations if item.selection[0] == anchor)
            portfolios = []
            for strategy, pool in (("single_winner_anchor", anchored), ("multi_winner_scenario", combinations)):
                for size in (3, 5, 10):
                    portfolios.append((strategy, size, sum(item.probability for item in pool[:size])))
            input_digest = _sha256((actual.input_data_version + "\n" + cards_sha256 + f"\n{market_weight:.12g}").encode())
            races.append(MarketBlendRace(
                actual.race_id,
                actual.scheduled_at,
                actual.model_version,
                model_version,
                f"sha256:{input_digest}",
                tuple(MarketBlendRunner(
                    horse_id,
                    model[horse_id],
                    float(odds[horse_id]),
                    market[horse_id],
                    blended[horse_id],
                    top3[horse_id],
                    rank[horse_id],
                ) for horse_id in ordered_ids),
                anchored[0].selection,
                tuple(portfolios),
            ))
    if not races:
        raise ValueError("market snapshot is not before any race")
    return MarketBlendForecast(
        observed_at, 1.0 - market_weight, market_weight, cards_sha256,
        _sha256(manifest_content), tuple(sorted(races, key=lambda race: race.race_id)),
    )


def save_market_blend_forecast(forecast: MarketBlendForecast, path: str | Path) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _payload(forecast)
    digest = _sha256(_canonical(payload))
    with target.open("x", encoding="utf-8") as handle:
        json.dump({"schema_version": MARKET_BLEND_SCHEMA_VERSION, "sha256": digest, "payload": payload}, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    return digest


def load_market_blend_forecast(path: str | Path) -> MarketBlendForecast:
    envelope = json.loads(Path(path).read_text(encoding="utf-8"))
    if envelope.get("schema_version") != MARKET_BLEND_SCHEMA_VERSION or not isinstance(envelope.get("payload"), dict):
        raise ValueError("unsupported market blend artifact")
    payload = envelope["payload"]
    if _sha256(_canonical(payload)) != envelope.get("sha256"):
        raise ValueError("market blend integrity check failed")
    if payload.get("policy_version") != MARKET_BLEND_POLICY_VERSION or payload.get("status") != "post_odds_revised_prediction":
        raise ValueError("unsupported market blend policy")
    races = []
    for race in payload["races"]:
        runners = tuple(MarketBlendRunner(**row) for row in race["runners"])
        if race.get("stake_yen") != 100 or any(row.get("stake_yen") != 0 for row in race["shadow_portfolios"]):
            raise ValueError("market blend stake policy is invalid")
        races.append(MarketBlendRace(
            race["race_id"], datetime.fromisoformat(race["scheduled_at"]),
            race["source_model_version"], race["model_version"], race["input_data_version"],
            runners, tuple(race["trifecta_selection"]),
            tuple((row["strategy"], row["ticket_count"], row["cumulative_probability"]) for row in race["shadow_portfolios"]),
        ))
    forecast = MarketBlendForecast(
        datetime.fromisoformat(payload["observed_at"]), payload["model_weight"], payload["market_weight"],
        payload["cards_sha256"], payload["race_day_manifest_sha256"], tuple(races),
    )
    if (
        not 0.0 < forecast.market_weight < 1.0
        or abs(forecast.model_weight + forecast.market_weight - 1.0) > 1e-12
        or len({race.race_id for race in forecast.races}) != len(forecast.races)
        or not forecast.races
    ):
        raise ValueError("market blend weights or race identities are invalid")
    for race in forecast.races:
        if tuple(sorted(race.runners, key=lambda row: row.predicted_rank)) != race.runners:
            raise ValueError("market blend runners must be ordered by rank")
        if any(
            not all(math.isfinite(value) and value > 0.0 for value in (
                row.model_probability, row.market_odds,
                row.market_probability, row.blended_probability,
                row.top3_probability,
            ))
            for row in race.runners
        ):
            raise ValueError("market blend runner values must be finite and positive")
        if (
            abs(sum(row.model_probability for row in race.runners) - 1.0) > 1e-8
            or abs(sum(row.market_probability for row in race.runners) - 1.0) > 1e-8
        ):
            raise ValueError("market blend component probabilities must sum to one")
        predictions = tuple(PredictionRecord(
            race.race_id, row.horse_id, forecast.observed_at,
            race.model_version, row.blended_probability, row.top3_probability,
            row.predicted_rank,
        ) for row in race.runners)
        validate_race_predictions(predictions, tolerance=1e-8)
        combinations = rank_trifecta_combinations(predictions)
        anchor = race.runners[0].horse_id
        anchored = tuple(item for item in combinations if item.selection[0] == anchor)
        expected_portfolios = tuple(
            (strategy, size, sum(item.probability for item in pool[:size]))
            for strategy, pool in (
                ("single_winner_anchor", anchored),
                ("multi_winner_scenario", combinations),
            )
            for size in (3, 5, 10)
        )
        if (
            len(set(race.trifecta_selection)) != 3
            or set(race.trifecta_selection) - {row.horse_id for row in race.runners}
            or race.trifecta_selection != anchored[0].selection
            or len(race.shadow_portfolios) != len(expected_portfolios)
            or any(
                actual[:2] != expected[:2] or abs(actual[2] - expected[2]) > 1e-12
                for actual, expected in zip(
                    race.shadow_portfolios, expected_portfolios, strict=True
                )
            )
        ):
            raise ValueError("market blend trifecta or shadow portfolios are invalid")
    if any(forecast.observed_at >= race.scheduled_at for race in forecast.races):
        raise ValueError("market blend contains a race already started at observation")
    return forecast
