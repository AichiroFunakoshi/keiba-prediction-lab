"""Strict local-file connection to the one-race prediction pipeline."""

import csv
import hashlib
import io
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .frozen import PredictionPhase
from .frame import jra_frame_number
from .local_adapter import build_local_feature_bundle
from .model_artifact import load_trained_model_artifact_bytes
from .pace import (
    ExpectedPace,
    PaceRunnerProfile,
    RacePaceScenario,
    RunningStyle,
)
from .pipeline import (
    RacePredictionBundle,
    run_race_prediction_pipeline,
    save_race_prediction_bundle,
)


PACE_PROFILE_COLUMNS = frozenset({
    "race_id", "horse_id", "observed_at", "running_style",
    "early_speed", "late_speed", "pace_resilience",
})
PACE_SCENARIO_KEYS = frozenset({
    "race_id", "observed_at", "expected_pace", "confidence",
})
LOCAL_INPUT_HASH_KEYS = frozenset({
    "model", "history", "targets", "pace_profiles", "pace_scenario",
})


@dataclass(frozen=True)
class LocalRunnerDisplay:
    horse_id: str
    horse_number: int
    horse_name: str
    frame_number: int


@dataclass(frozen=True)
class LocalPipelineRun:
    prediction: RacePredictionBundle
    input_data_version: str
    model_sha256: str
    history_sha256: str
    targets_sha256: str
    pace_profiles_sha256: str
    pace_scenario_sha256: str
    runner_display: tuple[LocalRunnerDisplay, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.prediction, RacePredictionBundle):
            raise ValueError("prediction must be a RacePredictionBundle")
        component_hashes = {
            "model": self.model_sha256,
            "history": self.history_sha256,
            "targets": self.targets_sha256,
            "pace_profiles": self.pace_profiles_sha256,
            "pace_scenario": self.pace_scenario_sha256,
        }
        expected_version = build_local_input_data_version(component_hashes)
        if self.input_data_version != expected_version:
            raise ValueError("input_data_version must match local input hashes")
        if (
            self.prediction.actual_prediction.input_data_version
            != self.input_data_version
        ):
            raise ValueError("prediction must use the local input_data_version")
        predicted_ids = {
            row.horse_id for row in self.prediction.actual_prediction.predictions
        }
        display_ids = {row.horse_id for row in self.runner_display}
        horse_numbers = {row.horse_number for row in self.runner_display}
        if display_ids != predicted_ids:
            raise ValueError("runner display must identify every predicted runner")
        if len(horse_numbers) != len(self.runner_display):
            raise ValueError("runner display horse numbers must be unique")
        if any(not 1 <= row.frame_number <= 8 for row in self.runner_display):
            raise ValueError("runner display frame numbers must be from 1 to 8")


def _horse_display_name(horse_id: str) -> str:
    prefix = "horse:name:"
    return horse_id.removeprefix(prefix) if horse_id.startswith(prefix) else horse_id


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def build_local_input_data_version(component_hashes: dict[str, str]) -> str:
    """Combine the five exact local-input hashes into one stable version."""
    if component_hashes.keys() != LOCAL_INPUT_HASH_KEYS:
        raise ValueError("local input hashes must contain exactly five components")
    if any(
        len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in component_hashes.values()
    ):
        raise ValueError("local pipeline input hashes must be lowercase SHA-256")
    version_source = "\n".join(
        f"{key}:{component_hashes[key]}" for key in sorted(component_hashes)
    )
    return f"sha256:{_sha256(version_source.encode('utf-8'))}"


def _csv_rows(
    content: bytes, source_name: str, expected: frozenset[str]
) -> tuple[dict[str, str], ...]:
    with io.StringIO(content.decode("utf-8"), newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        duplicates = sorted({
            name for name in fieldnames if fieldnames.count(name) > 1
        })
        missing = expected - set(fieldnames)
        unexpected = set(fieldnames) - expected
        if missing or unexpected or duplicates:
            raise ValueError(
                f"invalid columns for {source_name}: "
                f"missing={sorted(missing)}, unexpected={sorted(unexpected)}, "
                f"duplicates={duplicates}"
            )
        return tuple(dict(row) for row in reader)


def _required(row: dict[str, str], key: str) -> str:
    value = (row.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} must not be empty")
    return value


def _load_pace_profiles_bytes(
    content: bytes, source_name: str
) -> tuple[PaceRunnerProfile, ...]:
    return tuple(
        PaceRunnerProfile(
            race_id=_required(row, "race_id"),
            horse_id=_required(row, "horse_id"),
            observed_at=datetime.fromisoformat(_required(row, "observed_at")),
            running_style=RunningStyle(_required(row, "running_style")),
            early_speed=float(_required(row, "early_speed")),
            late_speed=float(_required(row, "late_speed")),
            pace_resilience=float(_required(row, "pace_resilience")),
        )
        for row in _csv_rows(content, source_name, PACE_PROFILE_COLUMNS)
    )


def load_local_pace_profiles(
    path: str | Path,
) -> tuple[PaceRunnerProfile, ...]:
    source = Path(path)
    return _load_pace_profiles_bytes(source.read_bytes(), source.name)


def _load_pace_scenario_bytes(content: bytes) -> RacePaceScenario:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        payload: dict[str, object] = {}
        for key, value in pairs:
            if key in payload:
                raise ValueError(f"pace scenario contains duplicate key: {key}")
            payload[key] = value
        return payload

    payload = json.loads(content.decode("utf-8"), object_pairs_hook=unique_object)
    if not isinstance(payload, dict):
        raise ValueError("pace scenario must be an object")
    missing = PACE_SCENARIO_KEYS - payload.keys()
    unexpected = payload.keys() - PACE_SCENARIO_KEYS
    if missing or unexpected:
        raise ValueError(
            "invalid pace scenario keys: "
            f"missing={sorted(missing)}, unexpected={sorted(unexpected)}"
        )
    if any(not isinstance(payload[key], str) for key in (
        "race_id", "observed_at", "expected_pace",
    )):
        raise ValueError("pace scenario text fields must be strings")
    confidence = payload["confidence"]
    if type(confidence) not in (int, float):
        raise ValueError("pace scenario confidence must be numeric")
    return RacePaceScenario(
        race_id=payload["race_id"],
        observed_at=datetime.fromisoformat(payload["observed_at"]),
        expected_pace=ExpectedPace(payload["expected_pace"]),
        confidence=float(confidence),
    )


def load_local_pace_scenario(path: str | Path) -> RacePaceScenario:
    return _load_pace_scenario_bytes(Path(path).read_bytes())


def build_local_race_prediction(
    model_path: str | Path,
    history_path: str | Path,
    targets_path: str | Path,
    pace_profiles_path: str | Path,
    pace_scenario_path: str | Path,
    *,
    frozen_at: datetime,
    phase: PredictionPhase = PredictionPhase.PRE_ODDS,
    place_payout_slots: int | None = None,
    require_complete_body_weight: bool = False,
) -> LocalPipelineRun:
    """Build one formal prediction from immutable snapshots of all local inputs."""
    if frozen_at.tzinfo is None or frozen_at.utcoffset() is None:
        raise ValueError("frozen_at must be timezone-aware")
    if not isinstance(phase, PredictionPhase):
        raise ValueError("phase must be a PredictionPhase")
    model_content = Path(model_path).read_bytes()
    profile_source = Path(pace_profiles_path)
    profile_content = profile_source.read_bytes()
    scenario_content = Path(pace_scenario_path).read_bytes()
    artifact = load_trained_model_artifact_bytes(model_content)
    features = build_local_feature_bundle(
        history_path,
        targets_path,
        prior_strength=artifact.parameters.prior_strength,
    )
    if require_complete_body_weight:
        missing_body_weight = tuple(
            row.horse_id
            for row in features.features
            if row.body_weight_kg is None
        )
        if missing_body_weight:
            raise ValueError(
                "complete body weight is required for final prediction: "
                f"missing {len(missing_body_weight)}/{len(features.features)} runners"
            )
    if features.history_row_count < 1:
        raise ValueError("formal prediction requires at least one history row")
    profiles = _load_pace_profiles_bytes(profile_content, profile_source.name)
    scenario = _load_pace_scenario_bytes(scenario_content)
    if not features.observed_at <= frozen_at < features.scheduled_at:
        raise ValueError(
            "frozen_at must be at or after observed_at and before scheduled_at"
        )
    if any(row.observed_at != features.observed_at for row in profiles):
        raise ValueError("pace profiles must share the target observed_at")
    if scenario.observed_at != features.observed_at:
        raise ValueError("pace scenario must share the target observed_at")

    component_hashes = {
        "model": _sha256(model_content),
        "history": features.history_sha256,
        "targets": features.targets_sha256,
        "pace_profiles": _sha256(profile_content),
        "pace_scenario": _sha256(scenario_content),
    }
    input_data_version = build_local_input_data_version(component_hashes)
    prediction = run_race_prediction_pipeline(
        artifact.model,
        features.features,
        profiles,
        scenario,
        scheduled_at=features.scheduled_at,
        frozen_at=frozen_at,
        phase=phase,
        input_data_version=input_data_version,
        place_payout_slots=place_payout_slots,
    )
    return LocalPipelineRun(
        prediction=prediction,
        input_data_version=input_data_version,
        model_sha256=component_hashes["model"],
        history_sha256=component_hashes["history"],
        targets_sha256=component_hashes["targets"],
        pace_profiles_sha256=component_hashes["pace_profiles"],
        pace_scenario_sha256=component_hashes["pace_scenario"],
        runner_display=tuple(
            LocalRunnerDisplay(
                horse_id=row.horse_id,
                horse_number=row.post_position,
                horse_name=_horse_display_name(row.horse_id),
                frame_number=jra_frame_number(
                    row.post_position,
                    max(item.post_position for item in features.features),
                ),
            )
            for row in features.features
        ),
    )


def save_local_pipeline_run(
    run: LocalPipelineRun, directory: str | Path
) -> Path:
    """Save the formal bundle and its verified local-input provenance."""
    if not isinstance(run, LocalPipelineRun):
        raise ValueError("run must be a LocalPipelineRun")
    target = Path(directory)
    created = False
    try:
        manifest_path = save_race_prediction_bundle(run.prediction, target)
        created = True
        payload = {
            "input_data_version": run.input_data_version,
            "model_sha256": run.model_sha256,
            "history_sha256": run.history_sha256,
            "targets_sha256": run.targets_sha256,
            "pace_profiles_sha256": run.pace_profiles_sha256,
            "pace_scenario_sha256": run.pace_scenario_sha256,
        }
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        envelope = {
            "schema_version": "1.0",
            "sha256": _sha256(canonical.encode("utf-8")),
            "payload": payload,
        }
        with (target / "input-provenance.json").open(
            "x", encoding="utf-8"
        ) as handle:
            json.dump(envelope, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
        return manifest_path
    except Exception:
        if created:
            shutil.rmtree(target)
        raise
