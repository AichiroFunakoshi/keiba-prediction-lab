"""Strict local CSV adapter for time-safe feature generation."""

import csv
import hashlib
import io
import json
import re
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
from .model import TrainingRow


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

TRAINING_COLUMNS = HISTORY_COLUMNS | {"observed_at"}


@dataclass(frozen=True)
class LocalFeatureBundle:
    history_sha256: str
    targets_sha256: str
    input_data_version: str
    race_id: str
    scheduled_at: datetime
    observed_at: datetime
    history_row_count: int
    horse_history_coverage_count: int
    jockey_history_coverage_count: int
    trainer_history_coverage_count: int
    features: tuple[FeatureRow, ...]


@dataclass(frozen=True)
class HistoricalTrainingRunner:
    performance: RacePerformance
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.observed_at >= self.performance.scheduled_at:
            raise ValueError("observed_at must be before scheduled_at")


@dataclass(frozen=True)
class LocalTrainingBundle:
    training_sha256: str
    input_data_version: str
    rows: tuple[TrainingRow, ...]


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


def _performance_from_row(row: dict[str, str]) -> RacePerformance:
    return RacePerformance(
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


def _load_history_bytes(
    content: bytes, source_name: str
) -> tuple[RacePerformance, ...]:
    return tuple(
        _performance_from_row(row)
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


def load_targets_csv_bytes(
    content: bytes, source_name: str,
) -> tuple[TargetRunner, ...]:
    """Load one already-hashed target snapshot without reading it a second time."""
    return _load_targets_bytes(content, source_name)


def _identity_namespace(value: str) -> str | None:
    parts = value.split(":", 2)
    return ":".join(parts[:2]) if len(parts) == 3 else None


def _validate_identity_compatibility(
    history: tuple[RacePerformance, ...],
    targets: tuple[TargetRunner, ...],
) -> None:
    """Reject obvious history/target identifier-domain mismatches.

    A previous local run used horse names in history and post-position numbers
    in targets.  That silently reset every horse-history feature to its prior.
    Debut runners are valid, so zero overlap alone is not rejected; conflicting
    explicit namespaces or numeric-vs-text domains are rejected instead.
    """
    if not history or not targets:
        return
    for field_name in ("horse_id", "jockey_id", "trainer_id"):
        historical = {getattr(row, field_name) for row in history}
        current = {getattr(row, field_name) for row in targets}
        history_namespaces = {
            namespace
            for value in historical
            if (namespace := _identity_namespace(value)) is not None
        }
        target_namespaces = {
            namespace
            for value in current
            if (namespace := _identity_namespace(value)) is not None
        }
        history_has_raw = any(_identity_namespace(value) is None for value in historical)
        target_has_raw = any(_identity_namespace(value) is None for value in current)
        if (history_namespaces and history_has_raw) or (target_namespaces and target_has_raw):
            raise ValueError(f"{field_name} mixes namespaced and raw identifiers")
        if bool(history_namespaces) != bool(target_namespaces):
            raise ValueError(
                f"{field_name} namespace usage differs between history and targets"
            )
        if (
            history_namespaces
            and target_namespaces
            and history_namespaces.isdisjoint(target_namespaces)
        ):
            raise ValueError(
                f"{field_name} namespace differs between history and targets"
            )
        historical_numeric = all(re.fullmatch(r"\d+", value) for value in historical)
        current_numeric = all(re.fullmatch(r"\d+", value) for value in current)
        if historical_numeric != current_numeric:
            raise ValueError(
                f"{field_name} identifier domain differs between history and targets"
            )


def _load_training_bytes(
    content: bytes, source_name: str
) -> tuple[HistoricalTrainingRunner, ...]:
    rows = _rows(content, source_name, TRAINING_COLUMNS)
    return tuple(
        HistoricalTrainingRunner(
            performance=_performance_from_row(row),
            observed_at=datetime.fromisoformat(_required(row, "observed_at")),
        )
        for row in rows
    )


def load_training_csv(path: str | Path) -> tuple[HistoricalTrainingRunner, ...]:
    """Load historical runners with their explicit pre-race observation times."""
    source = Path(path)
    return _load_training_bytes(source.read_bytes(), source.name)


def _validated_training_races(
    runners: tuple[HistoricalTrainingRunner, ...],
) -> tuple[tuple[HistoricalTrainingRunner, ...], ...]:
    if not runners:
        raise ValueError("training must contain at least one race")
    grouped: dict[str, list[HistoricalTrainingRunner]] = {}
    seen: set[tuple[str, str]] = set()
    for runner in runners:
        item = runner.performance
        key = (item.race_id, item.horse_id)
        if key in seen:
            raise ValueError("training contains duplicate race_id and horse_id")
        seen.add(key)
        grouped.setdefault(item.race_id, []).append(runner)

    races: list[tuple[HistoricalTrainingRunner, ...]] = []
    for race_id, entries in grouped.items():
        scheduled = {entry.performance.scheduled_at for entry in entries}
        observed = {entry.observed_at for entry in entries}
        result_known = {entry.performance.result_known_at for entry in entries}
        posts = {entry.performance.post_position for entry in entries}
        race_contexts = {
            (
                entry.performance.venue,
                entry.performance.surface,
                entry.performance.track_condition,
                entry.performance.distance_m,
            )
            for entry in entries
        }
        if len(entries) < 2:
            raise ValueError(f"training race {race_id} must contain at least two runners")
        if len(scheduled) != 1 or len(observed) != 1 or len(result_known) != 1:
            raise ValueError(
                f"training race {race_id} must share scheduled, observed, and result-known times"
            )
        if len(posts) != len(entries):
            raise ValueError(f"training race {race_id} contains duplicate post_position")
        if len(race_contexts) != 1:
            raise ValueError(f"training race {race_id} must share race context")
        if not any(entry.performance.finish_position == 1 for entry in entries):
            raise ValueError(f"training race {race_id} has no winner")
        races.append(tuple(sorted(entries, key=lambda entry: entry.performance.horse_id)))
    return tuple(sorted(
        races,
        key=lambda race: (
            race[0].observed_at,
            race[0].performance.scheduled_at,
            race[0].performance.race_id,
        ),
    ))


def build_time_safe_training_bundle(
    training_path: str | Path,
    *,
    prior_strength: float = 10.0,
) -> LocalTrainingBundle:
    """Generate training rows using only whole races known at each observation."""
    source = Path(training_path)
    content = source.read_bytes()
    training_hash = hashlib.sha256(content).hexdigest()
    races = _validated_training_races(_load_training_bytes(content, source.name))
    rows: list[TrainingRow] = []
    for race in races:
        observed_at = race[0].observed_at
        race_id = race[0].performance.race_id
        available_history = tuple(
            entry.performance
            for historical_race in races
            if historical_race[0].performance.race_id != race_id
            and historical_race[0].performance.result_known_at <= observed_at
            for entry in historical_race
        )
        targets = tuple(
            TargetRunner(
                race_id=entry.performance.race_id,
                scheduled_at=entry.performance.scheduled_at,
                observed_at=entry.observed_at,
                horse_id=entry.performance.horse_id,
                jockey_id=entry.performance.jockey_id,
                trainer_id=entry.performance.trainer_id,
                venue=entry.performance.venue,
                surface=entry.performance.surface,
                track_condition=entry.performance.track_condition,
                distance_m=entry.performance.distance_m,
                post_position=entry.performance.post_position,
                carried_weight_kg=entry.performance.carried_weight_kg,
                body_weight_kg=entry.performance.body_weight_kg,
            )
            for entry in race
        )
        features_by_horse = {
            feature.horse_id: feature
            for feature in generate_features(
                available_history, targets, prior_strength=prior_strength
            )
        }
        rows.extend(
            TrainingRow(
                features=features_by_horse[entry.performance.horse_id],
                finish_position=entry.performance.finish_position,
            )
            for entry in race
        )
    return LocalTrainingBundle(
        training_sha256=training_hash,
        input_data_version=f"sha256:{training_hash}",
        rows=tuple(rows),
    )


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
    history = _load_history_bytes(history_content, history_source.name)
    targets = _load_targets_bytes(targets_content, targets_source.name)
    _validate_identity_compatibility(history, targets)
    features = generate_features(
        history,
        targets,
        prior_strength=prior_strength,
    )
    return LocalFeatureBundle(
        history_sha256=history_hash,
        targets_sha256=targets_hash,
        input_data_version=f"sha256:{version_hash}",
        race_id=targets[0].race_id,
        scheduled_at=targets[0].scheduled_at,
        observed_at=targets[0].observed_at,
        history_row_count=len(history),
        horse_history_coverage_count=sum(row.horse_starts > 0 for row in features),
        jockey_history_coverage_count=sum(row.jockey_starts > 0 for row in features),
        trainer_history_coverage_count=sum(row.trainer_starts > 0 for row in features),
        features=features,
    )


def save_local_feature_bundle(
    bundle: LocalFeatureBundle, path: str | Path
) -> None:
    """Write a new JSON feature artifact without overwriting existing output."""
    payload = {
        "schema_version": "1.2",
        "history_sha256": bundle.history_sha256,
        "targets_sha256": bundle.targets_sha256,
        "input_data_version": bundle.input_data_version,
        "race_id": bundle.race_id,
        "scheduled_at": bundle.scheduled_at.isoformat(),
        "observed_at": bundle.observed_at.isoformat(),
        "history_row_count": bundle.history_row_count,
        "feature_history_coverage": {
            "horse": bundle.horse_history_coverage_count,
            "jockey": bundle.jockey_history_coverage_count,
            "trainer": bundle.trainer_history_coverage_count,
            "runner_count": len(bundle.features),
        },
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


def save_local_training_bundle(
    bundle: LocalTrainingBundle, path: str | Path
) -> None:
    """Write a new JSON training artifact without overwriting existing output."""
    payload = {
        "schema_version": "1.0",
        "training_sha256": bundle.training_sha256,
        "input_data_version": bundle.input_data_version,
        "rows": [
            {
                "finish_position": row.finish_position,
                "features": {
                    **asdict(row.features),
                    "observed_at": row.features.observed_at.isoformat(),
                },
            }
            for row in bundle.rows
        ],
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
