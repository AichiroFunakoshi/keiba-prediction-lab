"""Versioned JV-Data RA/SE/WE/WH/AV adapter for the local CSV contracts."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .local_adapter import HISTORY_COLUMNS, TARGET_COLUMNS, TRAINING_COLUMNS
from .pace_estimation import (
    PACE_HISTORY_COLUMNS,
    build_automatic_pace_inputs,
    save_automatic_pace_inputs,
)


JV_ADAPTER_VERSION = "jv-data-4.9-ra-se-we-wh-av-v1"
JST = ZoneInfo("Asia/Tokyo")
VENUES = {
    "01": "Sapporo", "02": "Hakodate", "03": "Fukushima",
    "04": "Niigata", "05": "Tokyo", "06": "Nakayama",
    "07": "Chukyo", "08": "Kyoto", "09": "Hanshin", "10": "Kokura",
}
TRACK_CONDITIONS = {
    "1": "good", "2": "slightly_heavy", "3": "heavy", "4": "bad",
}
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
PACE_HISTORY_FIELD_ORDER = (
    "race_id", "scheduled_at", "result_known_at", "horse_id", "field_size",
    "first_corner_position", "final_corner_position", "finish_position",
    "last_3f_rank",
)
if frozenset(HISTORY_FIELD_ORDER) != HISTORY_COLUMNS:
    raise RuntimeError("JV history order does not match local contract")
if frozenset(TARGET_FIELD_ORDER) != TARGET_COLUMNS:
    raise RuntimeError("JV target order does not match local contract")
if frozenset(PACE_HISTORY_FIELD_ORDER) != PACE_HISTORY_COLUMNS:
    raise RuntimeError("JV pace history order does not match pace contract")


def _field(raw: bytes, position: int, length: int) -> str:
    return raw[position - 1:position - 1 + length].decode("cp932").strip()


def _positive_int(text: str, name: str) -> int:
    if not text.isdigit() or int(text) < 1:
        raise ValueError(f"invalid JV-Data {name}")
    return int(text)


def _load_snapshot(directory: str | Path) -> tuple[tuple[bytes, ...], str, datetime]:
    root = Path(directory)
    manifest_content = (root / "jv-fetch-manifest.json").read_bytes()
    manifest = json.loads(manifest_content.decode("utf-8"))
    if manifest.get("source_id") != "jra-van-data-lab":
        raise ValueError("snapshot source_id must be jra-van-data-lab")
    records_path = root / manifest.get("records_file", "")
    content = records_path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != manifest.get("records_sha256"):
        raise ValueError("JV-Data records SHA-256 mismatch")
    records = []
    for line in content.splitlines():
        payload = json.loads(line.decode("utf-8"))
        if set(payload) != {"record_type", "raw", "source_filename", "download_timestamp"}:
            raise ValueError("unexpected JV-Data JSONL keys")
        raw_text = payload["raw"]
        if not isinstance(raw_text, str) or payload["record_type"] != raw_text[:2]:
            raise ValueError("invalid JV-Data raw record")
        records.append(raw_text.encode("cp932"))
    if len(records) != manifest.get("record_count"):
        raise ValueError("JV-Data record count mismatch")
    return tuple(records), digest, datetime.fromisoformat(manifest["acquired_at"])


def _race_key(raw: bytes) -> str:
    return _field(raw, 12, 16)


def _surface(track_code: str) -> str:
    if track_code.isdigit():
        value = int(track_code)
        if 10 <= value <= 22:
            return "turf"
        if 23 <= value <= 29:
            return "dirt"
        if 51 <= value <= 59:
            return "jump"
    raise ValueError(f"unsupported JV track code: {track_code}")


def _condition_surface(track_code: str) -> str:
    """Return which announced going applies, including mixed jump course 52."""
    value = int(track_code)
    return "dirt" if 23 <= value <= 29 or value == 52 else "turf"


def _scheduled(raw: bytes) -> datetime:
    day = _field(raw, 12, 8)
    start = _field(raw, 874, 4)
    return datetime.strptime(day + start, "%Y%m%d%H%M").replace(tzinfo=JST)


def _condition(code: str) -> str:
    if code not in TRACK_CONDITIONS:
        raise ValueError("track condition is not available in JV-Data")
    return TRACK_CONDITIONS[code]


def _body_weight(text: str) -> str:
    if not text.isdigit() or int(text) in (0, 999):
        return ""
    return str(int(text))


def _latest_records(records: tuple[bytes, ...], kind: str, divisions: set[str]) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for raw in records:
        if _field(raw, 1, 2) != kind:
            continue
        division = _field(raw, 3, 1)
        key = _race_key(raw) + (_field(raw, 29, 2) if kind == "SE" else "")
        if division == "0":
            result.pop(key, None)
        elif division in divisions:
            result[key] = raw
    return result


def _weather(records: tuple[bytes, ...], race_date: date) -> dict[str, dict[str, str]]:
    states: dict[str, dict[str, str]] = {}
    day = race_date.strftime("%Y%m%d")
    for raw in records:
        if _field(raw, 1, 2) != "WE" or _field(raw, 12, 8) != day:
            continue
        venue = _field(raw, 20, 2)
        state = states.setdefault(venue, {})
        turf, dirt = _field(raw, 36, 1), _field(raw, 37, 1)
        if turf in TRACK_CONDITIONS:
            state["turf"] = TRACK_CONDITIONS[turf]
        if dirt in TRACK_CONDITIONS:
            state["dirt"] = TRACK_CONDITIONS[dirt]
    return states


def _body_weights(records: tuple[bytes, ...], race_date: date) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    day = race_date.strftime("%Y%m%d")
    for raw in records:
        if _field(raw, 1, 2) != "WH" or _field(raw, 12, 8) != day:
            continue
        race_key = _race_key(raw)
        for index in range(18):
            offset = 36 + index * 45
            post = _field(raw, offset, 2)
            if post and post != "00":
                result[(race_key, post)] = _body_weight(_field(raw, offset + 38, 3))
    return result


def _withdrawals(records: tuple[bytes, ...], race_date: date) -> set[tuple[str, str]]:
    day = race_date.strftime("%Y%m%d")
    return {
        (_race_key(raw), _field(raw, 36, 2))
        for raw in records
        if _field(raw, 1, 2) == "AV" and _field(raw, 12, 8) == day
    }


def _historical_rows(records: tuple[bytes, ...]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    races = _latest_records(records, "RA", {"6", "7"})
    runners = _latest_records(records, "SE", {"6", "7"})
    history: list[dict[str, Any]] = []
    pace: list[dict[str, Any]] = []
    grouped: dict[str, list[bytes]] = {}
    for raw in runners.values():
        grouped.setdefault(_race_key(raw), []).append(raw)
    for key, race in sorted(races.items()):
        entries = grouped.get(key, [])
        if len(entries) < 2:
            continue
        scheduled = _scheduled(race)
        result_known = datetime.combine(
            scheduled.date() + timedelta(days=1), time.min, JST
        )
        venue_code = _field(race, 20, 2)
        if venue_code not in VENUES:
            continue
        track_code = _field(race, 706, 2)
        surface = _surface(track_code)
        condition_code = _field(
            race, 889 if _condition_surface(track_code) == "turf" else 890, 1
        )
        condition = _condition(condition_code)
        field_size_text = _field(race, 884, 2)
        field_size = int(field_size_text) if field_size_text.isdigit() and int(field_size_text) >= 2 else len(entries)
        finishers = []
        for runner in entries:
            finish = _field(runner, 335, 2)
            if finish.isdigit() and int(finish) >= 1:
                finishers.append(runner)
        last_times = sorted({
            int(value) for runner in finishers
            if (value := _field(runner, 391, 3)).isdigit() and int(value) not in (0, 999)
        })
        for runner in finishers:
            horse_id = f"horse:jv:{_field(runner, 31, 10)}"
            finish = int(_field(runner, 335, 2))
            common = {
                "race_id": f"race:jv:{key}", "scheduled_at": scheduled.isoformat(),
                "result_known_at": result_known.isoformat(), "horse_id": horse_id,
                "jockey_id": f"jockey:jv:{_field(runner, 297, 5)}",
                "trainer_id": f"trainer:jv:{_field(runner, 86, 5)}",
                "venue": VENUES[venue_code], "surface": surface,
                "track_condition": condition, "distance_m": int(_field(race, 698, 4)),
                "post_position": int(_field(runner, 29, 2)),
                "carried_weight_kg": int(_field(runner, 289, 3)) / 10,
                "body_weight_kg": _body_weight(_field(runner, 325, 3)),
                "finish_position": finish,
            }
            history.append(common)
            corners = [
                int(value) for position in (352, 354, 356, 358)
                if (value := _field(runner, position, 2)).isdigit() and int(value) >= 1
            ]
            if corners:
                last_value = _field(runner, 391, 3)
                last_rank = (
                    last_times.index(int(last_value)) + 1
                    if last_value.isdigit() and int(last_value) in last_times else ""
                )
                pace.append({
                    "race_id": common["race_id"], "scheduled_at": common["scheduled_at"],
                    "result_known_at": common["result_known_at"], "horse_id": horse_id,
                    "field_size": field_size, "first_corner_position": corners[0],
                    "final_corner_position": corners[-1], "finish_position": finish,
                    "last_3f_rank": last_rank,
                })
    return history, pace


def _target_races(
    race_records: tuple[bytes, ...], realtime_records: tuple[bytes, ...],
    race_date: date, observed_at: datetime,
) -> list[tuple[str, str, int, list[dict[str, Any]]]]:
    day = race_date.strftime("%Y%m%d")
    races = {
        key: raw for key, raw in _latest_records(race_records, "RA", {"1", "2"}).items()
        if key.startswith(day)
    }
    runners = _latest_records(race_records, "SE", {"1", "2"})
    weather = _weather(realtime_records, race_date)
    weights = _body_weights(realtime_records, race_date)
    withdrawn = _withdrawals(realtime_records, race_date)
    result = []
    for key, race in sorted(races.items()):
        scheduled = _scheduled(race)
        if not observed_at < scheduled:
            raise ValueError(f"observation is not before race {key}")
        venue_code = _field(race, 20, 2)
        if venue_code not in VENUES:
            continue
        track_code = _field(race, 706, 2)
        surface = _surface(track_code)
        condition = weather.get(venue_code, {}).get(
            _condition_surface(track_code)
        )
        if condition is None:
            raise ValueError(f"real-time track condition missing for venue {venue_code} {surface}")
        rows = []
        for runner_key, runner in sorted(runners.items()):
            if not runner_key.startswith(key):
                continue
            post_text = _field(runner, 29, 2)
            if (key, post_text) in withdrawn:
                continue
            rows.append({
                "race_id": f"race:jv:{key}", "scheduled_at": scheduled.isoformat(),
                "observed_at": observed_at.isoformat(),
                "horse_id": f"horse:jv:{_field(runner, 31, 10)}",
                "jockey_id": f"jockey:jv:{_field(runner, 297, 5)}",
                "trainer_id": f"trainer:jv:{_field(runner, 86, 5)}",
                "venue": VENUES[venue_code], "surface": surface,
                "track_condition": condition, "distance_m": int(_field(race, 698, 4)),
                "post_position": int(post_text),
                "carried_weight_kg": int(_field(runner, 289, 3)) / 10,
                "body_weight_kg": weights.get((key, post_text), _body_weight(_field(runner, 325, 3))),
            })
        if len(rows) < 3:
            raise ValueError(f"target race {key} has fewer than three active runners")
        result.append((key, VENUES[venue_code], int(_field(race, 26, 2)), rows))
    if not result:
        raise ValueError("no pre-race JV-Data races found for race_date")
    return result


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def prepare_jra_van_race_day(
    history_snapshot: str | Path,
    race_snapshot: str | Path,
    realtime_snapshot: str | Path,
    output_directory: str | Path,
    *,
    race_date: date,
    observed_at: datetime,
) -> Path:
    """Create prediction-ready local CSV, automatic pace inputs, and day plan."""
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    history_records, history_hash, history_acquired = _load_snapshot(history_snapshot)
    race_records, race_hash, race_acquired = _load_snapshot(race_snapshot)
    realtime_records, realtime_hash, realtime_acquired = _load_snapshot(realtime_snapshot)
    if any(acquired > observed_at for acquired in (history_acquired, race_acquired, realtime_acquired)):
        raise ValueError("snapshot acquired_at must not be after observed_at")
    history_rows, pace_rows = _historical_rows(history_records)
    if not history_rows or not pace_rows:
        raise ValueError("JV history snapshot has no usable finalized race history")
    target_races = _target_races(race_records, realtime_records, race_date, observed_at)
    target = Path(output_directory)
    target.mkdir(parents=True, exist_ok=False)
    try:
        history_path = target / "history.csv"
        training_path = target / "training.csv"
        pace_history_path = target / "pace-history.csv"
        _write_csv(history_path, HISTORY_FIELD_ORDER, history_rows)
        training_rows = []
        for row in history_rows:
            training = dict(row)
            training["observed_at"] = (
                datetime.fromisoformat(row["scheduled_at"]) - timedelta(minutes=5)
            ).isoformat()
            training_rows.append(training)
        training_order = HISTORY_FIELD_ORDER[:2] + ("observed_at",) + HISTORY_FIELD_ORDER[2:]
        if frozenset(training_order) != TRAINING_COLUMNS:
            raise RuntimeError("JV training order does not match local contract")
        _write_csv(training_path, training_order, training_rows)
        _write_csv(pace_history_path, PACE_HISTORY_FIELD_ORDER, pace_rows)
        plan_races = []
        for key, venue, race_number, rows in target_races:
            race_dir = target / "races" / key
            race_dir.mkdir(parents=True)
            targets_path = race_dir / "targets.csv"
            _write_csv(targets_path, TARGET_FIELD_ORDER, rows)
            pace_dir = race_dir / "pace"
            auto = build_automatic_pace_inputs(pace_history_path, targets_path)
            profiles, scenario, _ = save_automatic_pace_inputs(auto, pace_dir)
            plan_races.append({
                "venue": venue, "race_number": race_number,
                "targets": str(targets_path.relative_to(target)),
                "pace_profiles": str(profiles.relative_to(target)),
                "pace_scenario": str(scenario.relative_to(target)),
            })
        plan = {"schema_version": "1.0", "race_date": race_date.isoformat(), "races": plan_races}
        plan_path = target / "race-day-plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        files = [path for path in target.rglob("*") if path.is_file()]
        manifest = {
            "schema_version": "1.0", "adapter_version": JV_ADAPTER_VERSION,
            "race_date": race_date.isoformat(), "observed_at": observed_at.isoformat(),
            "source_hashes": {"history": history_hash, "race": race_hash, "realtime": realtime_hash},
            "history_row_count": len(history_rows), "pace_history_row_count": len(pace_rows),
            "race_count": len(target_races),
            "outputs": {str(path.relative_to(target)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(files)},
        }
        (target / "jra-van-adapter-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        return plan_path
    except Exception:
        shutil.rmtree(target)
        raise
