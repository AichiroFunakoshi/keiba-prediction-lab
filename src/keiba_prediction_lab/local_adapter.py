"""Strict local CSV adapter for time-safe feature generation."""

import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .data_audit import sha256_file
from .features import (
    FeatureRow,
    RacePerformance,
    Surface,
    TargetRunner,
    generate_features,
)


HISTORY_COLUMNS = frozenset({
    "race_id", "scheduled_at", "result_known_at", "horse_id", "jockey_id",
    "trainer_id", "venue", "surface", "track_condition", "distance_m",
    "post_position", "carried_weight_kg", "body_weight_kg", "finish_position",
})

TARGET_COLUMNS = frozenset({
    "race_id", "scheduled_at", "observed_at", "horse_id", "jockey_id",
    "trainer_id", "venue", "surface", "track_condition", "distance_m",
    "post_position", "carried_weight_kg", "body_weight_kg",
})


@dataclass(frozen=True)
class LocalFeatureBundle:
    history_sha256: str
    targets_sha256: str
    input_data_version: str
    features: tuple[FeatureRow, ...]


def _rows(path: str | Path, expected: frozenset[str]) -> tuple[dict[str, str], ...]:
    source = Path(path)
    with source.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        actual = set(reader.fieldnames or ())
        missing = expected - actual
        unexpected = actual - expected
        if missing or unexpected:
            raise ValueError(
                f"invalid columns for {source.name}: "
                f"missing={sorted(missing)}, unexpected={sorted(unexpected)}"
            )
        return tuple(dict(row) for row in reader)


def _required(row: dict[str, str], name: str) -> str:
    value = (row.get(name) or "").strip()
    if not value:
        raise ValueError(f"{name} must not be empty")
    return value


def _optional_int(row: dict[str, str], name: str) -> int | None:
    value = (row.get(name) or "").strip()
    return int(value) if value else None


def load_history_csv(path: str | Path) -> tuple[RacePerformance, ...]:
    """Load complete historical race rows with result-availability timestamps."""
    return tuple(
        RacePerformance(
            race_id=_required(row, "race_id"),
            scheduled_at=datetime.fromisoformat(_required(row, "scheduled_at")),
            result_known_at=datetime.fromisoformat(_required(row, "result_known_at")),
            horse_id=_required(row, "horse_id"),
            jockey_id=_required(row, "jockey_id"),
            trainer_id=_required(row, "trainer_id"),
            venue=_required(row, "venue"),
            surface=Surface(_required(row, "surface")),
            track_condition=_required(row, "track_condition"),
            distance_m=int(_required(row, "distance_m")),
            post_position=int(_required(row, "post_position")),
            carried_weight_kg=float(_required(row, "carried_weight_kg")),
            body_weight_kg=_optional_int(row, "body_weight_kg"),
            finish_position=int(_required(row, "finish_position")),
        )
        for row in _rows(path, HISTORY_COLUMNS)
    )


def load_targets_csv(path: str | Path) -> tuple[TargetRunner, ...]:
    """Load result-free target rows; any additional column is rejected."""
    return tuple(
        TargetRunner(
            race_id=_required(row, "race_id"),
            scheduled_at=datetime.fromisoformat(_required(row, "scheduled_at")),
            observed_at=datetime.fromisoformat(_required(row, "observed_at")),
            horse_id=_required(row, "horse_id"),
            jockey_id=_required(row, "jockey_id"),
            trainer_id=_required(row, "trainer_id"),
            venue=_required(row, "venue"),
            surface=Surface(_required(row, "surface")),
            track_condition=_required(row, "track_condition"),
            distance_m=int(_required(row, "distance_m")),
            post_position=int(_required(row, "post_position")),
            carried_weight_kg=float(_required(row, "carried_weight_kg")),
            body_weight_kg=_optional_int(row, "body_weight_kg"),
        )
        for row in _rows(path, TARGET_COLUMNS)
    )


def build_local_feature_bundle(
    history_path: str | Path,
    targets_path: str | Path,
    *,
    prior_strength: float = 10.0,
) -> LocalFeatureBundle:
    """Convert two local files into versioned, leakage-checked model features."""
    history_hash = sha256_file(history_path)
    targets_hash = sha256_file(targets_path)
    version_hash = hashlib.sha256(
        f"history:{history_hash}\ntargets:{targets_hash}".encode("utf-8")
    ).hexdigest()
    features = generate_features(
        load_history_csv(history_path),
        load_targets_csv(targets_path),
        prior_strength=prior_strength,
    )
    return LocalFeatureBundle(
        history_sha256=history_hash,
        targets_sha256=targets_hash,
        input_data_version=f"sha256:{version_hash}",
        features=features,
    )


def save_local_feature_bundle(
    bundle: LocalFeatureBundle, path: str | Path
) -> None:
    """Write a new JSON feature artifact without overwriting existing output."""
    payload = {
        "schema_version": "1.0",
        "history_sha256": bundle.history_sha256,
        "targets_sha256": bundle.targets_sha256,
        "input_data_version": bundle.input_data_version,
        "features": [
            {
                **asdict(row),
                "observed_at": row.observed_at.isoformat(),
            }
            for row in bundle.features
        ],
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
