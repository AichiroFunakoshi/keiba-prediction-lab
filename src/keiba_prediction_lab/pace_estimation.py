"""Time-safe automatic running-style and expected-pace estimation.

The estimator consumes a separate, result-only pace-history contract.  Keeping
corner positions outside the pre-race model CSV prevents accidental leakage
while still allowing them to describe a runner's *past* races.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .local_adapter import load_targets_csv
from .pace import ExpectedPace, PaceRunnerProfile, RacePaceScenario, RunningStyle


PACE_ESTIMATOR_VERSION = "historical-corners-v1"
PACE_HISTORY_COLUMNS = frozenset({
    "race_id", "scheduled_at", "result_known_at", "horse_id", "field_size",
    "first_corner_position", "final_corner_position", "finish_position",
    "last_3f_rank",
})
PACE_PROFILE_FIELD_ORDER = (
    "race_id", "horse_id", "observed_at", "running_style", "early_speed",
    "late_speed", "pace_resilience",
)


@dataclass(frozen=True)
class PaceHistoryRow:
    race_id: str
    scheduled_at: datetime
    result_known_at: datetime
    horse_id: str
    field_size: int
    first_corner_position: int
    final_corner_position: int
    finish_position: int
    last_3f_rank: int | None

    def __post_init__(self) -> None:
        if not self.race_id.strip() or not self.horse_id.strip():
            raise ValueError("pace history identities must not be empty")
        for value, name in (
            (self.scheduled_at, "scheduled_at"),
            (self.result_known_at, "result_known_at"),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.result_known_at <= self.scheduled_at:
            raise ValueError("result_known_at must be after scheduled_at")
        if self.field_size < 2:
            raise ValueError("field_size must be at least two")
        positions = (
            self.first_corner_position,
            self.final_corner_position,
            self.finish_position,
        )
        if any(not 1 <= value <= self.field_size for value in positions):
            raise ValueError("pace history positions must be within field_size")
        if self.last_3f_rank is not None and not 1 <= self.last_3f_rank <= self.field_size:
            raise ValueError("last_3f_rank must be within field_size")


@dataclass(frozen=True)
class AutomaticPaceInputs:
    profiles: tuple[PaceRunnerProfile, ...]
    scenario: RacePaceScenario
    history_sha256: str
    targets_sha256: str
    history_rows_used: int
    runners_with_history: int


def _strict_rows(content: bytes, source_name: str) -> tuple[dict[str, str], ...]:
    with io.StringIO(content.decode("utf-8"), newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        duplicates = sorted({name for name in fieldnames if fieldnames.count(name) > 1})
        missing = PACE_HISTORY_COLUMNS - set(fieldnames)
        unexpected = set(fieldnames) - PACE_HISTORY_COLUMNS
        if missing or unexpected or duplicates:
            raise ValueError(
                f"invalid columns for {source_name}: missing={sorted(missing)}, "
                f"unexpected={sorted(unexpected)}, duplicates={duplicates}"
            )
        return tuple(dict(row) for row in reader)


def _required(row: dict[str, str], name: str) -> str:
    value = (row.get(name) or "").strip()
    if not value:
        raise ValueError(f"{name} must not be empty")
    return value


def _load_history_bytes(content: bytes, source_name: str) -> tuple[PaceHistoryRow, ...]:
    result = []
    seen: set[tuple[str, str]] = set()
    for raw in _strict_rows(content, source_name):
        key = (_required(raw, "race_id"), _required(raw, "horse_id"))
        if key in seen:
            raise ValueError("pace history contains duplicate race_id and horse_id")
        seen.add(key)
        last_rank = (raw.get("last_3f_rank") or "").strip()
        result.append(PaceHistoryRow(
            race_id=key[0],
            scheduled_at=datetime.fromisoformat(_required(raw, "scheduled_at")),
            result_known_at=datetime.fromisoformat(_required(raw, "result_known_at")),
            horse_id=key[1],
            field_size=int(_required(raw, "field_size")),
            first_corner_position=int(_required(raw, "first_corner_position")),
            final_corner_position=int(_required(raw, "final_corner_position")),
            finish_position=int(_required(raw, "finish_position")),
            last_3f_rank=int(last_rank) if last_rank else None,
        ))
    return tuple(result)


def load_pace_history_csv(path: str | Path) -> tuple[PaceHistoryRow, ...]:
    source = Path(path)
    return _load_history_bytes(source.read_bytes(), source.name)


def _position_score(position: int, field_size: int) -> float:
    return 1.0 - (position - 1) / (field_size - 1)


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def _weighted_mean(values: list[float]) -> float:
    weights = range(1, len(values) + 1)
    return sum(value * weight for value, weight in zip(values, weights)) / sum(weights)


def _profile_values(rows: tuple[PaceHistoryRow, ...]) -> tuple[float, float, float]:
    if not rows:
        return 0.5, 0.5, 0.5
    ordered = sorted(rows, key=lambda row: (row.scheduled_at, row.race_id))[-5:]
    early_values = [_position_score(row.first_corner_position, row.field_size) for row in ordered]
    late_values = []
    resilience_values = []
    for row in ordered:
        denominator = 2 * (row.field_size - 1)
        gain = _clamp(0.5 + (row.final_corner_position - row.finish_position) / denominator)
        last_3f = (
            _position_score(row.last_3f_rank, row.field_size)
            if row.last_3f_rank is not None else 0.5
        )
        late_values.append(0.65 * last_3f + 0.35 * gain)
        no_fade = _clamp(0.5 + (row.first_corner_position - row.finish_position) / denominator)
        resilience_values.append(
            0.5 * _position_score(row.finish_position, row.field_size) + 0.5 * no_fade
        )
    evidence = len(ordered) / (len(ordered) + 3.0)
    shrink = lambda value: 0.5 + evidence * (value - 0.5)
    return tuple(_clamp(shrink(_weighted_mean(values))) for values in (
        early_values, late_values, resilience_values,
    ))


def _running_style(early_speed: float) -> RunningStyle:
    if early_speed >= 0.72:
        return RunningStyle.LEADER
    if early_speed >= 0.56:
        return RunningStyle.PRESSER
    if early_speed >= 0.38:
        return RunningStyle.STALKER
    return RunningStyle.CLOSER


def build_automatic_pace_inputs(
    pace_history_path: str | Path,
    targets_path: str | Path,
) -> AutomaticPaceInputs:
    history_source = Path(pace_history_path)
    targets_source = Path(targets_path)
    history_content = history_source.read_bytes()
    targets_content = targets_source.read_bytes()
    history = _load_history_bytes(history_content, history_source.name)
    targets = load_targets_csv(targets_source)
    race_ids = {row.race_id for row in targets}
    observed_times = {row.observed_at for row in targets}
    if len(race_ids) != 1 or len(observed_times) != 1:
        raise ValueError("targets must describe exactly one race and observation time")
    race_id = next(iter(race_ids))
    observed_at = next(iter(observed_times))
    usable = tuple(row for row in history if row.result_known_at <= observed_at)
    by_horse: dict[str, tuple[PaceHistoryRow, ...]] = {}
    for target in targets:
        by_horse[target.horse_id] = tuple(
            row for row in usable if row.horse_id == target.horse_id
        )
    profiles = []
    for target in sorted(targets, key=lambda row: row.post_position):
        early, late, resilience = _profile_values(by_horse[target.horse_id])
        profiles.append(PaceRunnerProfile(
            race_id=race_id,
            horse_id=target.horse_id,
            observed_at=observed_at,
            running_style=_running_style(early),
            early_speed=early,
            late_speed=late,
            pace_resilience=resilience,
        ))
    covered = sum(bool(by_horse[row.horse_id]) for row in targets)
    if covered == 0:
        expected = ExpectedPace.AVERAGE
        confidence = 0.0
    else:
        pressure_by_style = {
            RunningStyle.LEADER: 1.0,
            RunningStyle.PRESSER: 0.65,
            RunningStyle.STALKER: 0.25,
            RunningStyle.CLOSER: 0.0,
        }
        pressure = sum(pressure_by_style[row.running_style] for row in profiles) / len(profiles)
        if pressure >= 0.45:
            expected = ExpectedPace.FAST
        elif pressure <= 0.25:
            expected = ExpectedPace.SLOW
        else:
            expected = ExpectedPace.AVERAGE
        coverage = covered / len(targets)
        boundary_distance = min(abs(pressure - 0.25), abs(pressure - 0.45))
        confidence = min(0.95, 0.15 + 0.6 * coverage + boundary_distance)
    scenario = RacePaceScenario(
        race_id=race_id,
        observed_at=observed_at,
        expected_pace=expected,
        confidence=confidence,
    )
    used_keys = {
        (row.race_id, row.horse_id)
        for rows in by_horse.values()
        for row in sorted(rows, key=lambda item: (item.scheduled_at, item.race_id))[-5:]
    }
    return AutomaticPaceInputs(
        profiles=tuple(profiles),
        scenario=scenario,
        history_sha256=hashlib.sha256(history_content).hexdigest(),
        targets_sha256=hashlib.sha256(targets_content).hexdigest(),
        history_rows_used=len(used_keys),
        runners_with_history=covered,
    )


def save_automatic_pace_inputs(inputs: AutomaticPaceInputs, directory: str | Path) -> tuple[Path, Path, Path]:
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=False)
    profiles_path = target / "pace-profiles.csv"
    scenario_path = target / "pace-scenario.json"
    manifest_path = target / "pace-generation-manifest.json"
    try:
        with profiles_path.open("x", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=PACE_PROFILE_FIELD_ORDER, lineterminator="\n")
            writer.writeheader()
            for row in inputs.profiles:
                writer.writerow({
                    "race_id": row.race_id,
                    "horse_id": row.horse_id,
                    "observed_at": row.observed_at.isoformat(),
                    "running_style": row.running_style.value,
                    "early_speed": format(row.early_speed, ".12g"),
                    "late_speed": format(row.late_speed, ".12g"),
                    "pace_resilience": format(row.pace_resilience, ".12g"),
                })
        scenario_path.write_text(json.dumps({
            "race_id": inputs.scenario.race_id,
            "observed_at": inputs.scenario.observed_at.isoformat(),
            "expected_pace": inputs.scenario.expected_pace.value,
            "confidence": inputs.scenario.confidence,
        }, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        profile_hash = hashlib.sha256(profiles_path.read_bytes()).hexdigest()
        scenario_hash = hashlib.sha256(scenario_path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps({
            "schema_version": "1.0",
            "generator_version": PACE_ESTIMATOR_VERSION,
            "race_id": inputs.scenario.race_id,
            "observed_at": inputs.scenario.observed_at.isoformat(),
            "pace_history_sha256": inputs.history_sha256,
            "targets_sha256": inputs.targets_sha256,
            "pace_profiles_sha256": profile_hash,
            "pace_scenario_sha256": scenario_hash,
            "history_rows_used": inputs.history_rows_used,
            "runner_count": len(inputs.profiles),
            "runners_with_history": inputs.runners_with_history,
        }, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    except Exception:
        for path in (manifest_path, scenario_path, profiles_path):
            path.unlink(missing_ok=True)
        target.rmdir()
        raise
    return profiles_path, scenario_path, manifest_path
