"""Integrity-protected pre-race context for segment diagnostics."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .diagnostics import field_size_bucket
from .features import Surface, distance_band


RACE_CONTEXT_SCHEMA_VERSION = "1.0"


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"race context {field_name} must not be empty")


@dataclass(frozen=True)
class RaceContext:
    """Race-level facts that are common to every compared model."""

    race_id: str
    observed_at: datetime
    venue: str
    surface: Surface
    track_condition: str
    distance_m: int
    race_class: str
    field_size: int

    def __post_init__(self) -> None:
        for field_name in (
            "race_id",
            "venue",
            "track_condition",
            "race_class",
        ):
            _require_text(getattr(self, field_name), field_name)
        if (
            not isinstance(self.observed_at, datetime)
            or self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() is None
        ):
            raise ValueError("race context observed_at must be timezone-aware")
        if not isinstance(self.surface, Surface):
            raise ValueError("race context surface must be a Surface value")
        if type(self.distance_m) is not int:
            raise ValueError("race context distance_m must be an integer")
        distance_band(self.distance_m)
        if type(self.field_size) is not int:
            raise ValueError("race context field_size must be an integer")
        if self.field_size < 1:
            raise ValueError("race context field_size must be positive")
        field_size_bucket(self.field_size)

    @property
    def distance_band(self) -> str:
        return distance_band(self.distance_m)

    @property
    def field_size_bucket(self) -> str:
        return field_size_bucket(self.field_size)

    def to_dict(self) -> dict[str, object]:
        return _payload(self)


def _payload(context: RaceContext) -> dict[str, object]:
    return {
        "race_id": context.race_id,
        "observed_at": context.observed_at.isoformat(),
        "venue": context.venue,
        "surface": context.surface.value,
        "track_condition": context.track_condition,
        "distance_m": context.distance_m,
        "race_class": context.race_class,
        "field_size": context.field_size,
    }


def _canonical(payload: dict[str, object]) -> str:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def save_race_context(context: RaceContext, path: str | Path) -> str:
    """Save context without overwriting and return its payload digest."""
    if not isinstance(context, RaceContext):
        raise ValueError("context must be a RaceContext value")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _payload(context)
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    envelope = {
        "schema_version": RACE_CONTEXT_SCHEMA_VERSION,
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
        raise ValueError(f"race context {key} has an invalid type")
    return value


def load_race_context(path: str | Path) -> RaceContext:
    """Load context after schema and integrity verification."""
    envelope = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(envelope, dict):
        raise ValueError("race context envelope must be an object")
    if envelope.get("schema_version") != RACE_CONTEXT_SCHEMA_VERSION:
        raise ValueError("unsupported race context schema_version")
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("race context payload must be an object")
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    if digest != envelope.get("sha256"):
        raise ValueError("race context integrity check failed")
    return RaceContext(
        race_id=_required(payload, "race_id", str),
        observed_at=datetime.fromisoformat(
            _required(payload, "observed_at", str)
        ),
        venue=_required(payload, "venue", str),
        surface=Surface(_required(payload, "surface", str)),
        track_condition=_required(payload, "track_condition", str),
        distance_m=_required(payload, "distance_m", int),
        race_class=_required(payload, "race_class", str),
        field_size=_required(payload, "field_size", int),
    )
