"""Immutable snapshots for non-purchased trifecta shadow portfolios."""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .domain import PredictionRecord
from .frozen import PredictionPhase
from .trifecta import (
    DEFAULT_PORTFOLIO_SIZES,
    ShadowPortfolio,
    TrifectaCombination,
    TrifectaForecast,
    TrifectaStrategy,
    build_trifecta_forecast,
)


SHADOW_SNAPSHOT_SCHEMA_VERSION = "1.0"
DEFAULT_GENERATOR_VERSION = "plackett-luce-v1"


@dataclass(frozen=True)
class FrozenShadowForecast:
    scheduled_at: datetime
    frozen_at: datetime
    source_predicted_at: datetime
    phase: PredictionPhase
    input_data_version: str
    model_version: str
    generator_version: str
    forecast: TrifectaForecast

    def __post_init__(self) -> None:
        for field_name in ("scheduled_at", "frozen_at", "source_predicted_at"):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware")
        if self.frozen_at >= self.scheduled_at:
            raise ValueError("frozen_at must be before scheduled_at")
        if self.source_predicted_at > self.frozen_at:
            raise ValueError("source_predicted_at must not be later than frozen_at")
        if not all((
            self.input_data_version.strip(),
            self.model_version.strip(),
            self.generator_version.strip(),
        )):
            raise ValueError("snapshot version identifiers must not be empty")


def freeze_shadow_forecast(
    predictions: Sequence[PredictionRecord],
    *,
    scheduled_at: datetime,
    frozen_at: datetime,
    phase: PredictionPhase,
    input_data_version: str,
    generator_version: str = DEFAULT_GENERATOR_VERSION,
    portfolio_sizes: Sequence[int] = DEFAULT_PORTFOLIO_SIZES,
) -> FrozenShadowForecast:
    if not predictions:
        raise ValueError("at least one prediction is required")
    predicted_times = {row.predicted_at for row in predictions}
    model_versions = {row.model_version for row in predictions}
    if len(predicted_times) != 1 or len(model_versions) != 1:
        raise ValueError("source predictions must share predicted_at and model_version")
    return FrozenShadowForecast(
        scheduled_at=scheduled_at,
        frozen_at=frozen_at,
        source_predicted_at=next(iter(predicted_times)),
        phase=phase,
        input_data_version=input_data_version,
        model_version=next(iter(model_versions)),
        generator_version=generator_version,
        forecast=build_trifecta_forecast(
            predictions, portfolio_sizes=portfolio_sizes
        ),
    )


def _combination_payload(row: TrifectaCombination) -> dict[str, object]:
    return {"selection": list(row.selection), "probability": row.probability}


def _payload(snapshot: FrozenShadowForecast) -> dict[str, object]:
    forecast = snapshot.forecast
    return {
        "race_id": forecast.race_id,
        "scheduled_at": snapshot.scheduled_at.isoformat(),
        "frozen_at": snapshot.frozen_at.isoformat(),
        "source_predicted_at": snapshot.source_predicted_at.isoformat(),
        "phase": snapshot.phase.value,
        "input_data_version": snapshot.input_data_version,
        "model_version": snapshot.model_version,
        "generator_version": snapshot.generator_version,
        "predicted_winner": forecast.predicted_winner,
        "primary_ticket": _combination_payload(forecast.primary_ticket),
        "all_combinations": [
            _combination_payload(row) for row in forecast.all_combinations
        ],
        "shadow_portfolios": [
            {
                "strategy": portfolio.strategy.value,
                "ticket_count": portfolio.ticket_count,
                "combinations": [
                    _combination_payload(row) for row in portfolio.combinations
                ],
            }
            for portfolio in forecast.shadow_portfolios
        ],
    }


def _canonical(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def save_frozen_shadow_forecast(
    snapshot: FrozenShadowForecast, path: str | Path
) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _payload(snapshot)
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    envelope = {
        "schema_version": SHADOW_SNAPSHOT_SCHEMA_VERSION,
        "sha256": digest,
        "payload": payload,
    }
    with target.open("x", encoding="utf-8") as handle:
        json.dump(envelope, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    return digest


def _load_combination(payload: dict[str, object]) -> TrifectaCombination:
    return TrifectaCombination(
        tuple(payload["selection"]),  # type: ignore[arg-type]
        payload["probability"],  # type: ignore[arg-type]
    )


def load_frozen_shadow_forecast(path: str | Path) -> FrozenShadowForecast:
    envelope = json.loads(Path(path).read_text(encoding="utf-8"))
    if envelope.get("schema_version") != SHADOW_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("unsupported frozen shadow schema_version")
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("frozen shadow payload must be an object")
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    if digest != envelope.get("sha256"):
        raise ValueError("frozen shadow forecast integrity check failed")

    all_combinations = tuple(
        _load_combination(row) for row in payload["all_combinations"]
    )
    portfolios = tuple(
        ShadowPortfolio(
            strategy=TrifectaStrategy(row["strategy"]),
            ticket_count=row["ticket_count"],
            combinations=tuple(
                _load_combination(candidate) for candidate in row["combinations"]
            ),
        )
        for row in payload["shadow_portfolios"]
    )
    forecast = TrifectaForecast(
        race_id=payload["race_id"],
        predicted_winner=payload["predicted_winner"],
        primary_ticket=_load_combination(payload["primary_ticket"]),
        all_combinations=all_combinations,
        shadow_portfolios=portfolios,
    )
    return FrozenShadowForecast(
        scheduled_at=datetime.fromisoformat(payload["scheduled_at"]),
        frozen_at=datetime.fromisoformat(payload["frozen_at"]),
        source_predicted_at=datetime.fromisoformat(payload["source_predicted_at"]),
        phase=PredictionPhase(payload["phase"]),
        input_data_version=payload["input_data_version"],
        model_version=payload["model_version"],
        generator_version=payload["generator_version"],
        forecast=forecast,
    )
