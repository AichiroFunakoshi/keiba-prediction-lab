"""Offline conversion of legacy local race snapshots into strict CSV inputs.

This module never downloads data.  It only converts JSON snapshots that the
user has already obtained and is allowed to keep locally.  The resulting CSV
files remain subject to the source's terms and are ignored by Git by default.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .local_adapter import HISTORY_COLUMNS, TARGET_COLUMNS, TRAINING_COLUMNS


SNAPSHOT_ADAPTER_SCHEMA_VERSION = "1.0"
IDENTITY_SCHEME = "normalized-name-v2"
JST = ZoneInfo("Asia/Tokyo")

HISTORY_FIELD_ORDER = (
    "race_id", "scheduled_at", "result_known_at", "horse_id", "jockey_id",
    "trainer_id", "venue", "surface", "track_condition", "distance_m",
    "post_position", "carried_weight_kg", "body_weight_kg", "finish_position",
)
TARGET_FIELD_ORDER = (
    "race_id", "scheduled_at", "observed_at", "horse_id", "jockey_id",
    "trainer_id", "venue", "surface", "track_condition", "distance_m",
    "post_position", "carried_weight_kg", "body_weight_kg",
)
TRAINING_FIELD_ORDER = HISTORY_FIELD_ORDER[:2] + ("observed_at",) + HISTORY_FIELD_ORDER[2:]

if frozenset(HISTORY_FIELD_ORDER) != HISTORY_COLUMNS:
    raise RuntimeError("history field order does not match the local CSV contract")
if frozenset(TARGET_FIELD_ORDER) != TARGET_COLUMNS:
    raise RuntimeError("target field order does not match the local CSV contract")
if frozenset(TRAINING_FIELD_ORDER) != TRAINING_COLUMNS:
    raise RuntimeError("training field order does not match the local CSV contract")


@dataclass(frozen=True)
class SnapshotConversionResult:
    output_directory: Path
    manifest_path: Path
    output_paths: tuple[Path, ...]
    race_count: int
    runner_count: int
    source_sha256: str


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"snapshot contains duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_snapshot(path: str | Path) -> tuple[bytes, list[dict[str, Any]]]:
    source = Path(path)
    content = source.read_bytes()
    payload = json.loads(content.decode("utf-8"), object_pairs_hook=_strict_object)
    if not isinstance(payload, list) or not payload:
        raise ValueError("snapshot must be a non-empty JSON array")
    if any(not isinstance(item, dict) for item in payload):
        raise ValueError("every snapshot race must be a JSON object")
    return content, payload


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")
    return " ".join(value.split())


def _integer(value: Any, field_name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{field_name} must be an integer")
    return value


def _positive_integer(value: Any, field_name: str) -> int:
    result = _integer(value, field_name)
    if result < 1:
        raise ValueError(f"{field_name} must be positive")
    return result


def _optional_integer(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    return _positive_integer(value, field_name)


def _normalized_name(kind: str, value: Any) -> str:
    text = unicodedata.normalize("NFKC", _text(value, f"{kind} name"))
    if kind == "jockey":
        text = re.sub(r"^[▲△☆★◇]+\s*", "", text)
    text = " ".join(text.split()).casefold()
    return f"{kind}:name:{text}"


def _surface(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _text(value, "surface")).casefold()
    if text == "jump" or "障害" in text:
        return "jump"
    if text == "dirt" or "ダート" in text:
        return "dirt"
    if text == "turf" or "芝" in text:
        return "turf"
    raise ValueError(f"unsupported surface: {value}")


def _scheduled_at(date_value: Any, start_value: Any) -> datetime:
    date_text = _text(date_value, "date")
    start_text = _text(start_value, "start")
    try:
        return datetime.strptime(
            f"{date_text} {start_text}", "%Y%m%d %H:%M"
        ).replace(tzinfo=JST)
    except ValueError as error:
        raise ValueError("date and start must use YYYYMMDD and HH:MM") from error


def _carried_weight(value: Any) -> float:
    if type(value) in (int, float):
        result = float(value)
    elif isinstance(value, str):
        match = re.search(r"\d+(?:\.\d+)?", value)
        if match is None:
            raise ValueError("carried weight must contain a number")
        result = float(match.group())
    else:
        raise ValueError("carried weight must be numeric or text containing a number")
    if result <= 0:
        raise ValueError("carried weight must be positive")
    return result


def _validate_race_rows(rows: list[dict[str, Any]], race_id: str) -> None:
    if len(rows) < 2:
        raise ValueError(f"snapshot race {race_id} must contain at least two runners")
    horse_ids = [row["horse_id"] for row in rows]
    posts = [row["post_position"] for row in rows]
    if len(set(horse_ids)) != len(horse_ids):
        raise ValueError(f"snapshot race {race_id} contains duplicate horse identity")
    if len(set(posts)) != len(posts):
        raise ValueError(f"snapshot race {race_id} contains duplicate post position")


def _csv_bytes(fieldnames: tuple[str, ...], rows: list[dict[str, Any]]) -> bytes:
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return handle.getvalue().encode("utf-8")


def _write_outputs(
    output_directory: Path,
    outputs: dict[str, bytes],
    manifest: dict[str, Any],
) -> tuple[Path, tuple[Path, ...]]:
    manifest["outputs"] = {
        name: {"sha256": _sha256(content), "size_bytes": len(content)}
        for name, content in sorted(outputs.items())
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    output_directory.mkdir(parents=True, exist_ok=False)
    paths: list[Path] = []
    for relative_name, content in sorted(outputs.items()):
        path = output_directory / relative_name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(content)
        paths.append(path)
    manifest_path = output_directory / "snapshot-manifest.json"
    with manifest_path.open("xb") as handle:
        handle.write(manifest_bytes)
    return manifest_path, tuple(paths)


def convert_history_snapshot(
    source_path: str | Path,
    output_directory: str | Path,
    *,
    source_id: str,
    acquired_at: datetime,
    observation_offset_minutes: int = 5,
    result_delay_minutes: int = 20,
) -> SnapshotConversionResult:
    """Convert a local historical JSON snapshot to history/training CSV files."""
    source_id = _text(source_id, "source_id")
    if acquired_at.tzinfo is None or acquired_at.utcoffset() is None:
        raise ValueError("acquired_at must be timezone-aware")
    if observation_offset_minutes < 1:
        raise ValueError("observation_offset_minutes must be positive")
    if result_delay_minutes < 1:
        raise ValueError("result_delay_minutes must be positive")

    content, races = _load_snapshot(source_path)
    history_rows: list[dict[str, Any]] = []
    training_rows: list[dict[str, Any]] = []
    seen_races: set[str] = set()
    for race in races:
        race_id = _text(race.get("race_id"), "race_id")
        if race_id in seen_races:
            raise ValueError(f"snapshot contains duplicate race_id: {race_id}")
        seen_races.add(race_id)
        scheduled = _scheduled_at(race.get("date"), race.get("start"))
        observed = scheduled - timedelta(minutes=observation_offset_minutes)
        result_known = scheduled + timedelta(minutes=result_delay_minutes)
        venue = _text(race.get("venue"), "venue")
        surface = _surface(race.get("surface"))
        condition = _text(race.get("track_condition"), "track_condition")
        distance = _positive_integer(race.get("distance"), "distance")
        runners = race.get("runners")
        if not isinstance(runners, list):
            raise ValueError(f"snapshot race {race_id} runners must be an array")
        current: list[dict[str, Any]] = []
        for runner in runners:
            if not isinstance(runner, dict):
                raise ValueError(f"snapshot race {race_id} runner must be an object")
            finish = _positive_integer(runner.get("finish"), "finish")
            body_weight = _optional_integer(
                runner.get("body_weight_kg"), "body_weight_kg"
            )
            row = {
                "race_id": race_id,
                "scheduled_at": scheduled.isoformat(),
                "result_known_at": result_known.isoformat(),
                "horse_id": _normalized_name("horse", runner.get("name")),
                "jockey_id": _normalized_name("jockey", runner.get("jockey")),
                "trainer_id": _normalized_name("trainer", runner.get("trainer")),
                "venue": venue,
                "surface": surface,
                "track_condition": condition,
                "distance_m": distance,
                "post_position": _positive_integer(runner.get("number"), "number"),
                "carried_weight_kg": _carried_weight(runner.get("carried_weight_kg")),
                "body_weight_kg": body_weight if body_weight is not None else "",
                "finish_position": finish,
            }
            current.append(row)
        _validate_race_rows(current, race_id)
        if not any(row["finish_position"] == 1 for row in current):
            raise ValueError(f"snapshot race {race_id} has no winner")
        for row in current:
            history_rows.append(row)
            training_rows.append({
                "race_id": row["race_id"],
                "scheduled_at": row["scheduled_at"],
                "observed_at": observed.isoformat(),
                **{
                    key: value
                    for key, value in row.items()
                    if key not in {"race_id", "scheduled_at"}
                },
            })

    history_rows.sort(key=lambda row: (row["scheduled_at"], row["race_id"], row["post_position"]))
    training_rows.sort(key=lambda row: (row["scheduled_at"], row["race_id"], row["post_position"]))
    outputs = {
        "history.csv": _csv_bytes(HISTORY_FIELD_ORDER, history_rows),
        "training.csv": _csv_bytes(TRAINING_FIELD_ORDER, training_rows),
    }
    manifest = {
        "schema_version": SNAPSHOT_ADAPTER_SCHEMA_VERSION,
        "adapter": "local-history-snapshot-v1",
        "source_id": source_id,
        "source_file": Path(source_path).name,
        "source_sha256": _sha256(content),
        "acquired_at": acquired_at.isoformat(),
        "identity_scheme": IDENTITY_SCHEME,
        "race_count": len(seen_races),
        "runner_count": len(history_rows),
        "network_access_performed": False,
        "assumptions": {
            "observed_at": f"scheduled_at minus {observation_offset_minutes} minutes (simulated)",
            "result_known_at": (
                f"scheduled_at plus {result_delay_minutes} minutes "
                "(conservative proxy)"
            ),
        },
    }
    output = Path(output_directory)
    manifest_path, output_paths = _write_outputs(output, outputs, manifest)
    return SnapshotConversionResult(
        output, manifest_path, output_paths, len(seen_races), len(history_rows), _sha256(content)
    )


def _track_condition(
    conditions: dict[str, Any], race_id: str, venue: str, surface: str
) -> str:
    for key in (race_id, f"{venue}:{surface}", venue, "default"):
        if key in conditions:
            return _text(conditions[key], f"track condition {key}")
    raise ValueError(f"track condition is missing for {race_id}")


def convert_target_snapshot(
    source_path: str | Path,
    track_conditions_path: str | Path,
    output_directory: str | Path,
    *,
    source_id: str,
    acquired_at: datetime,
    race_date: date,
    observed_at: datetime,
) -> SnapshotConversionResult:
    """Convert result-free local card snapshots into one target CSV per race."""
    source_id = _text(source_id, "source_id")
    for value, field_name in ((acquired_at, "acquired_at"), (observed_at, "observed_at")):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware")
    content, races = _load_snapshot(source_path)
    condition_content = Path(track_conditions_path).read_bytes()
    conditions = json.loads(
        condition_content.decode("utf-8"), object_pairs_hook=_strict_object
    )
    if not isinstance(conditions, dict) or not conditions:
        raise ValueError("track conditions must be a non-empty JSON object")

    outputs: dict[str, bytes] = {}
    runner_count = 0
    seen_races: set[str] = set()
    for race in races:
        venue = _text(race.get("venue"), "venue")
        race_number = _positive_integer(race.get("race"), "race")
        race_id = f"{race_date:%Y%m%d}-{venue}-{race_number:02d}"
        if race_id in seen_races:
            raise ValueError(f"snapshot contains duplicate race_id: {race_id}")
        seen_races.add(race_id)
        scheduled = datetime.combine(
            race_date,
            datetime.strptime(_text(race.get("start"), "start"), "%H:%M").time(),
            tzinfo=JST,
        )
        if observed_at >= scheduled:
            raise ValueError(f"observed_at must be before scheduled_at for {race_id}")
        surface = _surface(race.get("surface"))
        condition = _track_condition(conditions, race_id, venue, surface)
        distance = _positive_integer(race.get("distance"), "distance")
        horses = race.get("horses")
        if not isinstance(horses, list):
            raise ValueError(f"snapshot race {race_id} horses must be an array")
        rows: list[dict[str, Any]] = []
        for horse in horses:
            if not isinstance(horse, dict):
                raise ValueError(f"snapshot race {race_id} horse must be an object")
            rows.append({
                "race_id": race_id,
                "scheduled_at": scheduled.isoformat(),
                "observed_at": observed_at.isoformat(),
                "horse_id": _normalized_name("horse", horse.get("name")),
                "jockey_id": _normalized_name("jockey", horse.get("jockey")),
                "trainer_id": _normalized_name("trainer", horse.get("trainer")),
                "venue": venue,
                "surface": surface,
                "track_condition": condition,
                "distance_m": distance,
                "post_position": _positive_integer(horse.get("number"), "number"),
                "carried_weight_kg": _carried_weight(horse.get("weight")),
                "body_weight_kg": "",
            })
        _validate_race_rows(rows, race_id)
        rows.sort(key=lambda row: row["post_position"])
        outputs[f"targets/{race_id}.csv"] = _csv_bytes(TARGET_FIELD_ORDER, rows)
        runner_count += len(rows)

    manifest = {
        "schema_version": SNAPSHOT_ADAPTER_SCHEMA_VERSION,
        "adapter": "local-target-snapshot-v1",
        "source_id": source_id,
        "source_file": Path(source_path).name,
        "source_sha256": _sha256(content),
        "track_conditions_file": Path(track_conditions_path).name,
        "track_conditions_sha256": _sha256(condition_content),
        "acquired_at": acquired_at.isoformat(),
        "observed_at": observed_at.isoformat(),
        "race_date": race_date.isoformat(),
        "identity_scheme": IDENTITY_SCHEME,
        "race_count": len(seen_races),
        "runner_count": runner_count,
        "network_access_performed": False,
        "body_weight_policy": "missing until explicitly present in a later snapshot contract",
    }
    output = Path(output_directory)
    manifest_path, output_paths = _write_outputs(output, outputs, manifest)
    return SnapshotConversionResult(
        output, manifest_path, output_paths, len(seen_races), runner_count, _sha256(content)
    )
