"""Convert a private JRA public-web snapshot into formal race-day inputs."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .jra_web_fetch import JST, SOURCE_ID
from .pace_estimation import build_automatic_pace_inputs, save_automatic_pace_inputs
from .snapshot_adapter import (
    _normalized_name,
    convert_history_snapshot,
    convert_target_snapshot,
)


PACE_HISTORY_FIELDS = (
    "race_id", "scheduled_at", "result_known_at", "horse_id", "field_size",
    "first_corner_position", "final_corner_position", "finish_position",
    "last_3f_rank",
)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JRA web snapshot contains duplicate key: {key}")
        result[key] = value
    return result


def _load_json(path: Path):
    return json.loads(
        path.read_text(encoding="utf-8", errors="strict"),
        object_pairs_hook=_strict_object,
    )


def _validate_acquisition(directory: Path) -> tuple[dict, date, datetime]:
    manifest_path = directory / "acquisition-manifest.json"
    manifest = _load_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("source_id") != SOURCE_ID:
        raise ValueError("not a supported JRA public-web snapshot")
    if manifest.get("private_use_only") is not True:
        raise ValueError("JRA public-web snapshot is missing private-use restriction")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError("JRA public-web manifest has no outputs")
    for name in ("cards.json", "history.json", "track-conditions.json"):
        metadata = outputs.get(name)
        if not isinstance(metadata, dict) or not isinstance(metadata.get("sha256"), str):
            raise ValueError(f"JRA public-web manifest is missing {name}")
        content = (directory / name).read_bytes()
        if _sha256(content) != metadata["sha256"]:
            raise ValueError(f"JRA public-web snapshot hash mismatch: {name}")
    race_date = date.fromisoformat(manifest["race_date"])
    acquired_at = datetime.fromisoformat(manifest["acquired_at"])
    if acquired_at.tzinfo is None or acquired_at.utcoffset() is None:
        raise ValueError("JRA public-web acquired_at must be timezone-aware")
    return manifest, race_date, acquired_at


def _scheduled(race: dict) -> datetime:
    race_day = datetime.strptime(str(race["date"]), "%Y%m%d").date()
    start = datetime.strptime(str(race["start"]), "%H:%M").time()
    return datetime.combine(race_day, start, tzinfo=JST)


def _write_pace_history(history_path: Path, output_path: Path) -> int:
    races = _load_json(history_path)
    if not isinstance(races, list) or not races:
        raise ValueError("JRA public-web history must be a non-empty array")
    rows: list[dict[str, object]] = []
    for race in races:
        scheduled = _scheduled(race)
        result_known = scheduled + timedelta(minutes=20)
        runners = race.get("runners")
        if not isinstance(runners, list):
            raise ValueError("JRA public-web history runners must be an array")
        numeric_positions = [
            int(value)
            for runner in runners
            for value in (
                runner.get("finish"), runner.get("first_corner_position"),
                runner.get("final_corner_position"),
            )
            if isinstance(value, int) and value > 0
        ]
        field_size = max([len(runners), *numeric_positions])
        for runner in runners:
            first = runner.get("first_corner_position")
            final = runner.get("final_corner_position")
            if not isinstance(first, int) or not isinstance(final, int):
                continue
            rows.append({
                "race_id": race["race_id"],
                "scheduled_at": scheduled.isoformat(),
                "result_known_at": result_known.isoformat(),
                "horse_id": _normalized_name("horse", runner.get("name")),
                "field_size": field_size,
                "first_corner_position": first,
                "final_corner_position": final,
                "finish_position": runner["finish"],
                "last_3f_rank": runner.get("last_3f_rank") or "",
            })
    with output_path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PACE_HISTORY_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def prepare_jra_web_race_day(
    snapshot_directory: str | Path,
    output_directory: str | Path,
) -> dict[str, object]:
    """Atomically create history, targets, pace inputs, and a day plan."""
    snapshot = Path(snapshot_directory)
    manifest, race_date, acquired_at = _validate_acquisition(snapshot)
    destination = Path(output_directory)
    if destination.exists():
        raise FileExistsError(f"output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        history_result = convert_history_snapshot(
            snapshot / "history.json",
            work / "history",
            source_id=SOURCE_ID,
            acquired_at=acquired_at,
        )
        target_result = convert_target_snapshot(
            snapshot / "cards.json",
            snapshot / "track-conditions.json",
            work / "targets",
            source_id=SOURCE_ID,
            acquired_at=acquired_at,
            race_date=race_date,
            observed_at=acquired_at,
        )
        pace_history_path = work / "pace-history.csv"
        pace_row_count = _write_pace_history(snapshot / "history.json", pace_history_path)
        plan_races = []
        for target_path in sorted((work / "targets" / "targets").glob("*.csv")):
            parts = target_path.stem.rsplit("-", 2)
            if len(parts) != 3:
                raise ValueError(f"unexpected target filename: {target_path.name}")
            venue, race_number = parts[-2], int(parts[-1])
            pace_directory = work / "pace" / target_path.stem
            inputs = build_automatic_pace_inputs(pace_history_path, target_path)
            profiles, scenario, _ = save_automatic_pace_inputs(inputs, pace_directory)
            plan_races.append({
                "venue": venue,
                "race_number": race_number,
                "targets": str(target_path.relative_to(work)),
                "pace_profiles": str(profiles.relative_to(work)),
                "pace_scenario": str(scenario.relative_to(work)),
            })
        plan_races.sort(key=lambda item: (item["venue"], item["race_number"]))
        plan = {
            "schema_version": "1.0",
            "race_date": race_date.isoformat(),
            "races": plan_races,
        }
        plan_path = work / "race-day-plan.json"
        plan_path.write_text(
            json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        prepared = {
            "schema_version": "1.0",
            "adapter": "jra-public-web-race-day-v1",
            "source_id": SOURCE_ID,
            "source_manifest_sha256": _sha256((snapshot / "acquisition-manifest.json").read_bytes()),
            "race_date": race_date.isoformat(),
            "observed_at": acquired_at.isoformat(),
            "race_count": target_result.race_count,
            "history_race_count": history_result.race_count,
            "history_runner_count": history_result.runner_count,
            "pace_history_row_count": pace_row_count,
            "private_use_only": True,
        }
        (work / "prepared-manifest.json").write_text(
            json.dumps(prepared, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        work.rename(destination)
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return {
        **prepared,
        "output": str(destination),
        "history": str(destination / "history" / "history.csv"),
        "training": str(destination / "history" / "training.csv"),
        "pace_history": str(destination / "pace-history.csv"),
        "race_day_plan": str(destination / "race-day-plan.json"),
        "source_request_count": len(manifest.get("requests", [])),
    }
