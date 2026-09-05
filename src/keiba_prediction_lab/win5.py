"""Five-race winner forecasts built from audited pre-race bundles."""

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .bundle_audit import load_audited_prediction_bundle
from .market_blend import load_market_blend_forecast


WIN5_SCHEMA_VERSION = "1.0"
WIN5_GENERATOR_VERSION = "win5-independent-winners-v1"
WIN5_MARKET_BLEND_GENERATOR_VERSION = "win5-market-blend-winners-v1"
WIN5_GENERATOR_VERSIONS = frozenset({
    WIN5_GENERATOR_VERSION,
    WIN5_MARKET_BLEND_GENERATOR_VERSION,
})


@dataclass(frozen=True)
class Win5Runner:
    horse_id: str
    win_probability: float

    def __post_init__(self) -> None:
        if not self.horse_id.strip():
            raise ValueError("WIN5 horse_id must not be empty")
        if not math.isfinite(self.win_probability) or not 0.0 <= self.win_probability <= 1.0:
            raise ValueError("WIN5 win_probability must be finite and between 0 and 1")


@dataclass(frozen=True)
class Win5Leg:
    race_id: str
    scheduled_at: datetime
    model_version: str
    input_data_version: str
    runners: tuple[Win5Runner, ...]

    def __post_init__(self) -> None:
        if (
            not self.race_id.strip()
            or not self.model_version.strip()
            or not self.input_data_version.strip()
        ):
            raise ValueError("WIN5 race_id, model_version, and input version are required")
        if self.scheduled_at.tzinfo is None:
            raise ValueError("WIN5 scheduled_at must include a timezone")
        if len(self.runners) < 2:
            raise ValueError("each WIN5 leg requires at least two runners")
        if len({row.horse_id for row in self.runners}) != len(self.runners):
            raise ValueError("WIN5 runner identifiers must be unique within a race")
        if abs(sum(row.win_probability for row in self.runners) - 1.0) > 1e-8:
            raise ValueError("WIN5 win probabilities must sum to 1 within each race")
        expected = tuple(sorted(
            self.runners, key=lambda row: (-row.win_probability, row.horse_id)
        ))
        if self.runners != expected:
            raise ValueError("WIN5 runners must be ordered by win probability")


@dataclass(frozen=True)
class Win5Forecast:
    frozen_at: datetime
    legs: tuple[Win5Leg, ...]
    selection: tuple[str, str, str, str, str]
    joint_probability: float
    generator_version: str = WIN5_GENERATOR_VERSION
    stake_yen: int = 0

    def __post_init__(self) -> None:
        if self.frozen_at.tzinfo is None:
            raise ValueError("WIN5 frozen_at must include a timezone")
        if self.generator_version not in WIN5_GENERATOR_VERSIONS:
            raise ValueError("unsupported WIN5 generator_version")
        if len(self.legs) != 5 or len({row.race_id for row in self.legs}) != 5:
            raise ValueError("WIN5 requires exactly five distinct races")
        if len({row.scheduled_at for row in self.legs}) != 5:
            raise ValueError("WIN5 races must have distinct scheduled times")
        if tuple(sorted(self.legs, key=lambda row: row.scheduled_at)) != self.legs:
            raise ValueError("WIN5 legs must be ordered by scheduled_at")
        local_dates = {row.scheduled_at.date() for row in self.legs}
        if len(local_dates) != 1:
            raise ValueError("WIN5 races must share one scheduled date")
        if any(self.frozen_at >= row.scheduled_at for row in self.legs):
            raise ValueError("WIN5 forecast must be frozen before every race")
        expected_selection = tuple(row.runners[0].horse_id for row in self.legs)
        if self.selection != expected_selection:
            raise ValueError("WIN5 selection must use each leg's top win candidate")
        expected_probability = math.prod(
            row.runners[0].win_probability for row in self.legs
        )
        if (
            not math.isfinite(self.joint_probability)
            or abs(self.joint_probability - expected_probability) > 1e-12
        ):
            raise ValueError("WIN5 joint_probability is inconsistent with its legs")
        if self.stake_yen != 0:
            raise ValueError("WIN5 forecast is research-only and must have zero stake")

    def to_dict(self) -> dict[str, object]:
        return {
            "generator_version": self.generator_version,
            "frozen_at": self.frozen_at.isoformat(),
            "purchase_status": "shadow_only",
            "stake_yen": self.stake_yen,
            "selection": list(self.selection),
            "joint_probability": self.joint_probability,
            "independence_assumption": (
                "leg winner probabilities are multiplied across five races"
            ),
            "legs": [
                {
                    "race_id": leg.race_id,
                    "scheduled_at": leg.scheduled_at.isoformat(),
                    "model_version": leg.model_version,
                    "input_data_version": leg.input_data_version,
                    "selected_horse_id": leg.runners[0].horse_id,
                    "selected_win_probability": leg.runners[0].win_probability,
                    "runners": [
                        {
                            "horse_id": runner.horse_id,
                            "win_probability": runner.win_probability,
                        }
                        for runner in leg.runners
                    ],
                }
                for leg in self.legs
            ],
        }


def build_win5_forecast(
    prediction_directories: Sequence[str | Path],
    *,
    frozen_at: datetime,
) -> Win5Forecast:
    """Build a zero-stake WIN5 forecast from five audited race bundles."""
    if len(prediction_directories) != 5:
        raise ValueError("WIN5 requires exactly five prediction bundles")
    audited = [load_audited_prediction_bundle(path) for path in prediction_directories]
    if any(frozen_at < row.audit.frozen_at for row in audited):
        raise ValueError("WIN5 frozen_at cannot precede a source prediction freeze")
    legs = tuple(sorted((
        Win5Leg(
            race_id=row.audit.race_id,
            scheduled_at=row.audit.scheduled_at,
            model_version=row.audit.model_version,
            input_data_version=row.audit.input_data_version,
            runners=tuple(sorted(
                (
                    Win5Runner(prediction.horse_id, prediction.win_probability)
                    for prediction in row.bundle.actual_prediction.predictions
                ),
                key=lambda runner: (-runner.win_probability, runner.horse_id),
            )),
        )
        for row in audited
    ), key=lambda leg: leg.scheduled_at))
    return build_win5_forecast_from_legs(legs, frozen_at=frozen_at)


def build_win5_forecast_from_legs(
    legs: Sequence[Win5Leg], *, frozen_at: datetime,
    generator_version: str = WIN5_GENERATOR_VERSION,
) -> Win5Forecast:
    """Build a WIN5 forecast from five already validated race legs."""
    ordered = tuple(sorted(legs, key=lambda leg: leg.scheduled_at))
    selection = tuple(leg.runners[0].horse_id for leg in ordered)
    return Win5Forecast(
        frozen_at=frozen_at,
        legs=ordered,
        selection=selection,  # type: ignore[arg-type]
        joint_probability=math.prod(
            leg.runners[0].win_probability for leg in ordered
        ),
        generator_version=generator_version,
    )


def build_market_blend_win5_forecast(
    market_blend_path: str | Path,
    race_ids: Sequence[str],
) -> Win5Forecast:
    """Build a zero-stake WIN5 forecast from one audited market-blend snapshot."""
    if len(race_ids) != 5 or len(set(race_ids)) != 5:
        raise ValueError("market-blend WIN5 requires five distinct race_ids")
    blend = load_market_blend_forecast(market_blend_path)
    by_id = {race.race_id: race for race in blend.races}
    missing = set(race_ids) - set(by_id)
    if missing:
        raise ValueError(f"market blend is missing WIN5 races: {sorted(missing)}")
    legs = tuple(
        Win5Leg(
            race_id=race.race_id,
            scheduled_at=race.scheduled_at,
            model_version=race.model_version,
            input_data_version=race.input_data_version,
            runners=tuple(
                Win5Runner(row.horse_id, row.blended_probability)
                for row in race.runners
            ),
        )
        for race in (by_id[race_id] for race_id in race_ids)
    )
    return build_win5_forecast_from_legs(
        legs,
        frozen_at=blend.observed_at,
        generator_version=WIN5_MARKET_BLEND_GENERATOR_VERSION,
    )


def save_win5_forecast(forecast: Win5Forecast, path: str | Path) -> None:
    """Save an immutable, integrity-protected WIN5 forecast."""
    destination = Path(path)
    payload = forecast.to_dict()
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    envelope = {
        "schema_version": WIN5_SCHEMA_VERSION,
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "payload": payload,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as handle:
        json.dump(envelope, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def load_win5_forecast(path: str | Path) -> Win5Forecast:
    """Load and verify a saved WIN5 forecast."""
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"WIN5 forecast contains duplicate key: {key}")
            value[key] = item
        return value

    envelope = json.loads(
        Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_object
    )
    if not isinstance(envelope, dict) or set(envelope) != {
        "schema_version", "sha256", "payload"
    }:
        raise ValueError("invalid WIN5 forecast envelope")
    if envelope["schema_version"] != WIN5_SCHEMA_VERSION:
        raise ValueError("unsupported WIN5 schema_version")
    payload = envelope["payload"]
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if digest != envelope["sha256"]:
        raise ValueError("WIN5 forecast integrity check failed")
    if not isinstance(payload, dict):
        raise ValueError("WIN5 payload must be an object")
    expected_payload_keys = {
        "generator_version", "frozen_at", "purchase_status", "stake_yen",
        "selection", "joint_probability", "independence_assumption", "legs",
    }
    if set(payload) != expected_payload_keys:
        raise ValueError("invalid WIN5 payload keys")
    if payload["purchase_status"] != "shadow_only" or payload["stake_yen"] != 0:
        raise ValueError("WIN5 forecast must remain a zero-stake shadow")
    if payload["independence_assumption"] != (
        "leg winner probabilities are multiplied across five races"
    ):
        raise ValueError("unsupported WIN5 independence assumption")
    if (
        not isinstance(payload["generator_version"], str)
        or not isinstance(payload["frozen_at"], str)
        or not isinstance(payload["selection"], list)
        or len(payload["selection"]) != 5
        or any(not isinstance(value, str) for value in payload["selection"])
        or type(payload["joint_probability"]) not in (int, float)
        or not isinstance(payload["legs"], list)
    ):
        raise ValueError("invalid WIN5 payload field types")
    leg_keys = {
        "race_id", "scheduled_at", "model_version", "input_data_version",
        "selected_horse_id", "selected_win_probability", "runners",
    }
    runner_keys = {"horse_id", "win_probability"}
    if any(not isinstance(leg, dict) or set(leg) != leg_keys for leg in payload["legs"]):
        raise ValueError("invalid WIN5 leg keys")
    if any(
        not all(
            isinstance(leg[key], str)
            for key in (
                "race_id", "scheduled_at", "model_version",
                "input_data_version", "selected_horse_id",
            )
        )
        or type(leg["selected_win_probability"]) not in (int, float)
        for leg in payload["legs"]
    ):
        raise ValueError("invalid WIN5 leg field types")
    if any(
        not isinstance(leg["runners"], list)
        or any(
            not isinstance(row, dict) or set(row) != runner_keys
            for row in leg["runners"]
        )
        for leg in payload["legs"]
    ):
        raise ValueError("invalid WIN5 runner keys")
    if any(
        not isinstance(row["horse_id"], str)
        or type(row["win_probability"]) not in (int, float)
        for leg in payload["legs"]
        for row in leg["runners"]
    ):
        raise ValueError("invalid WIN5 runner field types")
    legs = tuple(
        Win5Leg(
            race_id=leg["race_id"],
            scheduled_at=datetime.fromisoformat(leg["scheduled_at"]),
            model_version=leg["model_version"],
            input_data_version=leg["input_data_version"],
            runners=tuple(
                Win5Runner(row["horse_id"], row["win_probability"])
                for row in leg["runners"]
            ),
        )
        for leg in payload["legs"]
    )
    if any(
        leg_payload["selected_horse_id"] != leg.runners[0].horse_id
        or leg_payload["selected_win_probability"] != leg.runners[0].win_probability
        for leg_payload, leg in zip(payload["legs"], legs)
    ):
        raise ValueError("WIN5 selected leg candidate is inconsistent")
    return Win5Forecast(
        frozen_at=datetime.fromisoformat(payload["frozen_at"]),
        legs=legs,
        selection=tuple(payload["selection"]),  # type: ignore[arg-type]
        joint_probability=payload["joint_probability"],
        generator_version=payload["generator_version"],
        stake_yen=payload["stake_yen"],
    )
