"""Strict local CSV adapter for time-safe feature generation."""

import csv
import hashlib
import io
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

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


def _rows(
    content: bytes, source_name: str, expected: frozenset[str]
) -> tuple[dict[str, str], ...]:
    with io.StringIO(content.decode("utf-8"), newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        duplicates = sorted({
            name for name in fieldnames if fieldnames.count(name) > 1
        })
        actual = set(fieldnames)
        missing = expected - actual
        unexpected = actual - expected
        if missing or unexpected or duplicates:
            raise ValueError(
                f"invalid columns for {source_name}: "
                f"missing={sorted(missing)}, unexpected={sorted(unexpected)}, "
                f"duplicates={duplicates}"
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


def _load_history_bytes(
    content: bytes, source_name: str
) -> tuple[RacePerformance, ...]:
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
        for row in _rows(content, source_name, HISTORY_COLUMNS)
    )


def load_history_csv(path: str | Path) -> tuple[RacePerformance, ...]:
    """Load complete historical race rows with result-availability timestamps."""
    source = Path(path)
    return _load_history_bytes(source.read_bytes(), source.name)


def _load_targets_bytes(
    content: bytes, source_name: str
) -> tuple[TargetRunner, ...]:
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
        for row in _rows(content, source_name, TARGET_COLUMNS)
    )


def load_targets_csv(path: str | Path) -> tuple[TargetRunner, ...]:
    """Load result-free target rows; any additional column is rejected."""
    source = Path(path)
    return _load_targets_bytes(source.read_bytes(), source.name)


def build_local_feature_bundle(
    history_path: str | Path,
    targets_path: str | Path,
    *,
    prior_strength: float = 10.0,
) -> LocalFeatureBundle:
    """Convert two local files into versioned, leakage-checked model features."""
    history_source = Path(history_path)
    targets_source = Path(targets_path)
    history_content = history_source.read_bytes()
    targets_content = targets_source.read_bytes()
    history_hash = hashlib.sha256(history_content).hexdigest()
    targets_hash = hashlib.sha256(targets_content).hexdigest()
    version_hash = hashlib.sha256(
        f"history:{history_hash}\ntargets:{targets_hash}".encode("utf-8")
    ).hexdigest()
    features = generate_features(
        _load_history_bytes(history_content, history_source.name),
        _load_targets_bytes(targets_content, targets_source.name),
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
