"""Atomic local race-day prediction from an explicit, result-free plan."""

import ctypes
import errno
import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .bundle_audit import load_audited_prediction_bundle
from .frozen import PredictionPhase
from .local_pipeline import (
    LocalPipelineRun,
    build_local_race_prediction,
    save_local_pipeline_run,
)


RACE_DAY_PLAN_SCHEMA_VERSION = "1.0"
RACE_DAY_PROVENANCE_SCHEMA_VERSION = "1.0"
_PLAN_KEYS = frozenset({"schema_version", "race_date", "races"})
_RACE_KEYS = frozenset({
    "venue", "race_number", "targets", "pace_profiles", "pace_scenario",
})


def _publish_directory_no_replace(source: Path, target: Path) -> None:
    """Atomically publish a same-filesystem directory without replacing target."""
    if os.name == "nt":
        source.rename(target)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    target_bytes = os.fsencode(target)
    if sys.platform == "darwin":
        rename = libc.renamex_np
        rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        rename.restype = ctypes.c_int
        result = rename(source_bytes, target_bytes, 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        rename = libc.renameat2
        rename.argtypes = (
            ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename.restype = ctypes.c_int
        result = rename(
            -100, source_bytes, -100, target_bytes, 0x00000001
        )  # AT_FDCWD, RENAME_NOREPLACE
    else:
        raise OSError(
            errno.ENOTSUP,
            "atomic no-replace directory publish is unsupported",
            str(target),
        )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), str(target))


@dataclass(frozen=True)
class LocalRaceDayEntry:
    venue: str
    race_number: int
    targets: Path
    pace_profiles: Path
    pace_scenario: Path


@dataclass(frozen=True)
class LocalRaceDayPlan:
    path: Path
    sha256: str
    race_date: date
    races: tuple[LocalRaceDayEntry, ...]


@dataclass(frozen=True)
class LocalRaceDayOutput:
    output_directory: Path
    race_day_manifest: Path
    provenance: Path
    race_count: int
    venue_count: int


@dataclass(frozen=True)
class LocalRaceDayAudit:
    race_date: date
    frozen_at: datetime
    phase: PredictionPhase
    race_count: int
    venue_count: int
    model_sha256: str
    history_sha256: str


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"race-day plan contains duplicate key: {key}")
        result[key] = value
    return result


def _exact_keys(value: dict[str, Any], expected: frozenset[str], label: str) -> None:
    if value.keys() != expected:
        raise ValueError(
            f"invalid {label} keys: missing={sorted(expected - value.keys())}, "
            f"unexpected={sorted(value.keys() - expected)}"
        )


def _input_path(parent: Path, value: Any, field_name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty path")
    path = Path(value)
    return path if path.is_absolute() else parent / path


def load_local_race_day_plan(path: str | Path) -> LocalRaceDayPlan:
    """Load a strict plan whose paths are explicit and relative to the plan file."""
    source = Path(path)
    content = source.read_bytes()
    payload = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(payload, dict):
        raise ValueError("race-day plan must be a JSON object")
    _exact_keys(payload, _PLAN_KEYS, "race-day plan")
    if payload["schema_version"] != RACE_DAY_PLAN_SCHEMA_VERSION:
        raise ValueError("unsupported race-day plan schema_version")
    if not isinstance(payload["race_date"], str):
        raise ValueError("race_date must be an ISO date string")
    try:
        race_date = date.fromisoformat(payload["race_date"])
    except ValueError as error:
        raise ValueError("race_date must be an ISO date string") from error
    raw_races = payload["races"]
    if not isinstance(raw_races, list) or not raw_races:
        raise ValueError("race-day plan requires at least one race")

    races: list[LocalRaceDayEntry] = []
    identities: set[tuple[str, int]] = set()
    for index, raw in enumerate(raw_races):
        if not isinstance(raw, dict):
            raise ValueError(f"race-day plan race {index} must be an object")
        _exact_keys(raw, _RACE_KEYS, f"race-day plan race {index}")
        venue = raw["venue"]
        race_number = raw["race_number"]
        if not isinstance(venue, str) or not venue.strip():
            raise ValueError("venue must be non-empty text")
        normalized_venue = venue.strip()
        if type(race_number) is not int or not 1 <= race_number <= 12:
            raise ValueError("race_number must be an integer from 1 to 12")
        identity = (normalized_venue, race_number)
        if identity in identities:
            raise ValueError("venue and race_number must be unique")
        identities.add(identity)
        races.append(LocalRaceDayEntry(
            normalized_venue,
            race_number,
            _input_path(source.parent, raw["targets"], "targets"),
            _input_path(source.parent, raw["pace_profiles"], "pace_profiles"),
            _input_path(source.parent, raw["pace_scenario"], "pace_scenario"),
        ))
    return LocalRaceDayPlan(
        source, _sha256(content), race_date, tuple(races)
    )


def _provenance_payload(
    plan: LocalRaceDayPlan,
    runs: tuple[tuple[LocalRaceDayEntry, LocalPipelineRun], ...],
    frozen_at: datetime,
    phase: PredictionPhase,
    race_day_manifest_sha256: str,
) -> dict[str, object]:
    first_run = runs[0][1]
    return {
        "plan_sha256": plan.sha256,
        "race_day_manifest_sha256": race_day_manifest_sha256,
        "race_date": plan.race_date.isoformat(),
        "frozen_at": frozen_at.isoformat(),
        "phase": phase.value,
        "race_count": len(runs),
        "model_sha256": first_run.model_sha256,
        "history_sha256": first_run.history_sha256,
        "races": [
            {
                "race_id": run.prediction.actual_prediction.race_id,
                "venue": entry.venue,
                "race_number": entry.race_number,
                "input_data_version": run.input_data_version,
            }
            for entry, run in runs
        ],
    }


def _save_provenance(path: Path, payload: dict[str, object]) -> None:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    envelope = {
        "schema_version": RACE_DAY_PROVENANCE_SCHEMA_VERSION,
        "sha256": _sha256(canonical.encode("utf-8")),
        "payload": payload,
    }
    with path.open("x", encoding="utf-8") as handle:
        json.dump(envelope, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")


def _load_envelope(path: Path) -> dict[str, Any]:
    envelope = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
    )
    if not isinstance(envelope, dict):
        raise ValueError("race-day provenance must be an object")
    _exact_keys(
        envelope,
        frozenset({"schema_version", "sha256", "payload"}),
        "race-day provenance envelope",
    )
    if envelope["schema_version"] != RACE_DAY_PROVENANCE_SCHEMA_VERSION:
        raise ValueError("unsupported race-day provenance schema_version")
    payload = envelope["payload"]
    if not isinstance(payload, dict):
        raise ValueError("race-day provenance payload must be an object")
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if envelope["sha256"] != _sha256(canonical.encode("utf-8")):
        raise ValueError("race-day provenance integrity check failed")
    _exact_keys(payload, frozenset({
        "plan_sha256", "race_day_manifest_sha256", "race_date", "frozen_at",
        "phase", "race_count", "model_sha256", "history_sha256", "races",
    }), "race-day provenance payload")
    for key in (
        "plan_sha256", "race_day_manifest_sha256", "model_sha256",
        "history_sha256",
    ):
        value = payload[key]
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError(f"race-day provenance {key} must be a SHA-256")
    if any(not isinstance(payload[key], str) for key in (
        "race_date", "frozen_at", "phase",
    )):
        raise ValueError("race-day provenance date, time, and phase must be strings")
    if type(payload["race_count"]) is not int or payload["race_count"] < 1:
        raise ValueError("race-day provenance race_count must be positive")
    return payload


def audit_local_race_day(directory: str | Path) -> LocalRaceDayAudit:
    """Re-audit a whole saved race day and every prediction bundle within it."""
    root = Path(directory)
    manifest_path = root / "race-day.json"
    provenance_path = root / "race-day-provenance.json"
    payload = _load_envelope(provenance_path)
    manifest_content = manifest_path.read_bytes()
    if payload["race_day_manifest_sha256"] != _sha256(manifest_content):
        raise ValueError("race-day manifest does not match provenance")
    try:
        race_date = date.fromisoformat(payload["race_date"])
        frozen_at = datetime.fromisoformat(payload["frozen_at"])
        phase = PredictionPhase(payload["phase"])
    except (TypeError, ValueError) as error:
        raise ValueError("race-day provenance date, time, or phase is invalid") from error
    if frozen_at.tzinfo is None or frozen_at.utcoffset() is None:
        raise ValueError("race-day provenance frozen_at must be timezone-aware")
    raw_races = payload["races"]
    if not isinstance(raw_races, list) or not raw_races:
        raise ValueError("race-day provenance races must be a non-empty array")
    expected: dict[tuple[str, int], dict[str, Any]] = {}
    for row in raw_races:
        if not isinstance(row, dict):
            raise ValueError("race-day provenance race must be an object")
        _exact_keys(row, frozenset({
            "race_id", "venue", "race_number", "input_data_version",
        }), "race-day provenance race")
        if any(
            not isinstance(row[key], str) or not row[key].strip()
            for key in ("race_id", "venue", "input_data_version")
        ):
            raise ValueError("race-day provenance race text must be non-empty")
        if type(row["race_number"]) is not int or not 1 <= row["race_number"] <= 12:
            raise ValueError("race-day provenance race_number is invalid")
        key = (row["venue"], row["race_number"])
        if key in expected:
            raise ValueError("race-day provenance contains duplicate race")
        expected[key] = row

    manifest = json.loads(
        manifest_content.decode("utf-8"), object_pairs_hook=_unique_object
    )
    if not isinstance(manifest, dict):
        raise ValueError("race-day manifest must be an object")
    _exact_keys(
        manifest,
        frozenset({"schema_version", "race_date", "venues"}),
        "race-day manifest",
    )
    if manifest["schema_version"] != "1.0":
        raise ValueError("unsupported race-day manifest schema_version")
    if manifest["race_date"] != race_date.isoformat():
        raise ValueError("race-day provenance date does not match manifest")
    if not isinstance(manifest["venues"], list) or not manifest["venues"]:
        raise ValueError("race-day manifest requires at least one venue")
    observed: set[tuple[str, int]] = set()
    seen_race_ids: set[str] = set()
    seen_venues: set[str] = set()
    model_hashes: set[str] = set()
    history_hashes: set[str] = set()
    for venue in manifest["venues"]:
        if not isinstance(venue, dict):
            raise ValueError("race-day venue must be an object")
        _exact_keys(venue, frozenset({"venue", "races"}), "race-day venue")
        venue_name = venue["venue"]
        if (
            not isinstance(venue_name, str)
            or not venue_name.strip()
            or venue_name in seen_venues
        ):
            raise ValueError("race-day venue names must be unique and non-empty")
        if not isinstance(venue["races"], list) or not venue["races"]:
            raise ValueError("race-day venue requires at least one race")
        seen_numbers: set[int] = set()
        for race in venue["races"]:
            if not isinstance(race, dict):
                raise ValueError("race-day race must be an object")
            required_race_keys = frozenset({"race_number", "prediction_bundle"})
            allowed_race_keys = required_race_keys | {"runner_display"}
            if not required_race_keys <= race.keys() or race.keys() - allowed_race_keys:
                raise ValueError("invalid race-day race keys")
            race_number = race["race_number"]
            if (
                type(race_number) is not int
                or not 1 <= race_number <= 12
                or race_number in seen_numbers
            ):
                raise ValueError("race-day race numbers must be unique from 1 to 12")
            bundle_value = race["prediction_bundle"]
            if not isinstance(bundle_value, str) or not bundle_value.strip():
                raise ValueError("prediction_bundle must be a non-empty path")
            key = (venue_name, race_number)
            if key not in expected:
                raise ValueError("race-day provenance does not match manifest races")
            bundle_path = Path(bundle_value)
            if bundle_path.is_absolute():
                raise ValueError("saved race-day bundle paths must be relative")
            bundle_path = (root / bundle_path).resolve()
            if not bundle_path.is_relative_to(root.resolve()):
                raise ValueError("saved race-day bundle path escapes output directory")
            audited = load_audited_prediction_bundle(bundle_path)
            actual = audited.bundle.actual_prediction
            raw_display = race.get("runner_display", [])
            if not isinstance(raw_display, list):
                raise ValueError("runner_display must be a list")
            if raw_display:
                predicted_ids = {runner.horse_id for runner in actual.predictions}
                display_ids: set[str] = set()
                horse_numbers: set[int] = set()
                for item in raw_display:
                    legacy_keys = {"horse_id", "horse_number", "horse_name"}
                    if (
                        not isinstance(item, dict)
                        or set(item) not in (legacy_keys, legacy_keys | {"frame_number"})
                    ):
                        raise ValueError("invalid runner_display entry")
                    horse_id = item["horse_id"]
                    horse_number = item["horse_number"]
                    horse_name = item["horse_name"]
                    frame_number = item.get("frame_number")
                    if (
                        not isinstance(horse_id, str)
                        or horse_id not in predicted_ids
                        or horse_id in display_ids
                    ):
                        raise ValueError("runner_display horse_id is invalid")
                    if (
                        type(horse_number) is not int
                        or not 1 <= horse_number <= 18
                        or horse_number in horse_numbers
                    ):
                        raise ValueError("runner_display horse_number is invalid")
                    if not isinstance(horse_name, str) or not horse_name.strip():
                        raise ValueError("runner_display horse_name is invalid")
                    if frame_number is not None and (
                        type(frame_number) is not int or not 1 <= frame_number <= 8
                    ):
                        raise ValueError("runner_display frame_number is invalid")
                    display_ids.add(horse_id)
                    horse_numbers.add(horse_number)
                if display_ids != predicted_ids:
                    raise ValueError("runner_display must cover every predicted runner")
            row = expected[key]
            if (
                row["race_id"] != actual.race_id
                or row["input_data_version"] != actual.input_data_version
                or actual.frozen_at != frozen_at
                or actual.phase is not phase
            ):
                raise ValueError("race-day provenance does not match prediction bundle")
            if actual.scheduled_at.date() != race_date:
                raise ValueError("prediction date does not match race day")
            if actual.race_id in seen_race_ids:
                raise ValueError("race-day prediction race_id values must be unique")
            model_hashes.add(audited.audit.model_sha256)
            history_hashes.add(audited.audit.history_sha256)
            observed.add(key)
            seen_numbers.add(race_number)
            seen_race_ids.add(actual.race_id)
        seen_venues.add(venue_name)
    if observed != expected.keys():
        raise ValueError("race-day provenance contains unmatched races")
    if payload["race_count"] != len(observed):
        raise ValueError("race-day provenance race_count is inconsistent")
    if model_hashes != {payload["model_sha256"]}:
        raise ValueError("race-day model snapshot is inconsistent")
    if history_hashes != {payload["history_sha256"]}:
        raise ValueError("race-day history snapshot is inconsistent")
    return LocalRaceDayAudit(
        race_date,
        frozen_at,
        phase,
        len(observed),
        len(seen_venues),
        payload["model_sha256"],
        payload["history_sha256"],
    )


def build_and_save_local_race_day(
    model_path: str | Path,
    history_path: str | Path,
    plan_path: str | Path,
    output_directory: str | Path,
    *,
    frozen_at: datetime,
    phase: PredictionPhase = PredictionPhase.PRE_ODDS,
    place_payout_slots: int | None = None,
    require_complete_body_weight: bool = False,
) -> LocalRaceDayOutput:
    """Validate every race, then atomically save a complete race-day snapshot."""
    if frozen_at.tzinfo is None or frozen_at.utcoffset() is None:
        raise ValueError("frozen_at must be timezone-aware")
    if not isinstance(phase, PredictionPhase):
        raise ValueError("phase must be a PredictionPhase")
    target = Path(output_directory)
    if target.exists():
        raise FileExistsError(f"race-day output already exists: {target}")
    plan = load_local_race_day_plan(plan_path)

    built: list[tuple[LocalRaceDayEntry, LocalPipelineRun]] = []
    seen_race_ids: set[str] = set()
    for entry in plan.races:
        run = build_local_race_prediction(
            model_path,
            history_path,
            entry.targets,
            entry.pace_profiles,
            entry.pace_scenario,
            frozen_at=frozen_at,
            phase=phase,
            place_payout_slots=place_payout_slots,
            require_complete_body_weight=require_complete_body_weight,
        )
        actual = run.prediction.actual_prediction
        if actual.scheduled_at.date() != plan.race_date:
            raise ValueError("prediction date does not match race-day plan")
        if actual.race_id in seen_race_ids:
            raise ValueError("race-day plan produced duplicate race_id values")
        seen_race_ids.add(actual.race_id)
        built.append((entry, run))
    runs = tuple(built)
    if len({run.model_sha256 for _, run in runs}) != 1:
        raise ValueError("every race must use the same model snapshot")
    if len({run.history_sha256 for _, run in runs}) != 1:
        raise ValueError("every race must use the same history snapshot")

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(
        prefix=f".{target.name}-creating-", dir=target.parent
    ))
    try:
        venues: dict[str, list[dict[str, object]]] = {}
        for index, (entry, run) in enumerate(runs, start=1):
            relative = Path("predictions") / f"race-{index:03d}"
            save_local_pipeline_run(run, temporary / relative)
            venues.setdefault(entry.venue, []).append({
                "race_number": entry.race_number,
                "prediction_bundle": relative.as_posix(),
                "runner_display": [
                    {
                        "horse_id": row.horse_id,
                        "horse_number": row.horse_number,
                        "horse_name": row.horse_name,
                        "frame_number": row.frame_number,
                    }
                    for row in run.runner_display
                ],
            })
        race_day_payload = {
            "schema_version": "1.0",
            "race_date": plan.race_date.isoformat(),
            "venues": [
                {
                    "venue": venue,
                    "races": sorted(rows, key=lambda row: row["race_number"]),
                }
                for venue, rows in venues.items()
            ],
        }
        race_day_bytes = (
            json.dumps(
                race_day_payload, ensure_ascii=False, sort_keys=True, indent=2
            ) + "\n"
        ).encode("utf-8")
        manifest = temporary / "race-day.json"
        with manifest.open("xb") as handle:
            handle.write(race_day_bytes)
        provenance = temporary / "race-day-provenance.json"
        _save_provenance(
            provenance,
            _provenance_payload(
                plan,
                runs,
                frozen_at,
                phase,
                _sha256(race_day_bytes),
            ),
        )
        audit_local_race_day(temporary)
        _publish_directory_no_replace(temporary, target)
    except Exception:
        shutil.rmtree(temporary)
        raise
    return LocalRaceDayOutput(
        target,
        target / "race-day.json",
        target / "race-day-provenance.json",
        len(runs),
        len(venues),
    )
