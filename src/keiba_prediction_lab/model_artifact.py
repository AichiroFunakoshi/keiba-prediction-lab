"""Integrity-protected conditional-logit model artifacts."""

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from pathlib import Path
from typing import Any

from .calibration import (
    CalibrationRow,
    TemperatureCalibratedModel,
    fit_temperature_scaling,
)
from .local_adapter import build_time_safe_training_bundle
from .model import (
    CONDITIONAL_LOGIT_FEATURE_NAMES,
    TRACK_CONDITION_V2_FEATURE_NAMES,
    EVIDENCE_NEUTRAL_V3_FEATURE_NAMES,
    RECENT_FORM_V4_FEATURE_NAMES,
    ConditionalLogitModel,
    TrainingRow,
    fit_conditional_logit,
)


MODEL_ARTIFACT_SCHEMA_VERSION = "1.4"
SUPPORTED_MODEL_ARTIFACT_SCHEMA_VERSIONS = frozenset({"1.0", "1.1", "1.2", "1.3", "1.4"})


@dataclass(frozen=True)
class ModelTrainingParameters:
    prior_strength: float = 10.0
    epochs: int = 500
    learning_rate: float = 0.1
    l2_strength: float = 0.01
    calibration_races: int = 0
    track_condition_v2: bool = False
    evidence_neutral_v3: bool = False
    recent_form_v4: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.prior_strength) not in (int, float)
            or not isfinite(self.prior_strength)
            or self.prior_strength <= 0.0
        ):
            raise ValueError("prior_strength must be positive and finite")
        if type(self.epochs) is not int or self.epochs < 1:
            raise ValueError("epochs must be a positive integer")
        if (
            type(self.learning_rate) not in (int, float)
            or not isfinite(self.learning_rate)
            or self.learning_rate <= 0.0
        ):
            raise ValueError("learning_rate must be positive and finite")
        if (
            type(self.l2_strength) not in (int, float)
            or not isfinite(self.l2_strength)
            or self.l2_strength < 0.0
        ):
            raise ValueError("l2_strength must be non-negative and finite")
        if type(self.calibration_races) is not int or self.calibration_races < 0:
            raise ValueError("calibration_races must be a non-negative integer")
        if type(self.track_condition_v2) is not bool:
            raise ValueError("track_condition_v2 must be a boolean")
        if type(self.evidence_neutral_v3) is not bool:
            raise ValueError("evidence_neutral_v3 must be a boolean")
        if type(self.recent_form_v4) is not bool:
            raise ValueError("recent_form_v4 must be a boolean")
        if sum((self.track_condition_v2, self.evidence_neutral_v3, self.recent_form_v4)) > 1:
            raise ValueError("choose one feature schema")


@dataclass(frozen=True)
class TrainedModelArtifact:
    training_sha256: str
    input_data_version: str
    training_row_count: int
    training_race_count: int
    parameters: ModelTrainingParameters
    model: ConditionalLogitModel | TemperatureCalibratedModel

    def __post_init__(self) -> None:
        if not isinstance(self.parameters, ModelTrainingParameters):
            raise ValueError("parameters must be ModelTrainingParameters")
        if not isinstance(
            self.model, (ConditionalLogitModel, TemperatureCalibratedModel)
        ):
            raise ValueError("model must be a supported conditional-logit model")
        if len(self.training_sha256) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.training_sha256
        ):
            raise ValueError("training_sha256 must be a lowercase SHA-256")
        if self.input_data_version != f"sha256:{self.training_sha256}":
            raise ValueError("input_data_version must match training_sha256")
        if type(self.training_row_count) is not int or self.training_row_count < 2:
            raise ValueError("training_row_count must be at least two")
        if type(self.training_race_count) is not int or self.training_race_count < 1:
            raise ValueError("training_race_count must be positive")
        if self.training_row_count < 2 * self.training_race_count:
            raise ValueError("each training race must contain at least two rows")
        expected = len(self.model.feature_names)
        vectors = (self.model.coefficients, self.model.means, self.model.scales)
        if any(len(vector) != expected for vector in vectors):
            raise ValueError("model vectors do not match the feature schema")
        if any(
            type(value) not in (int, float) or not isfinite(value)
            for vector in vectors
            for value in vector
        ):
            raise ValueError("model vectors must contain only finite values")
        if any(value <= 0.0 for value in self.model.scales):
            raise ValueError("model scales must be positive")
        if not isinstance(self.model.trained_through, datetime) or (
            self.model.trained_through.tzinfo is None
            or self.model.trained_through.utcoffset() is None
        ):
            raise ValueError("model trained_through must be timezone-aware")
        if not isinstance(self.model.model_version, str) or not self.model.model_version.strip():
            raise ValueError("model_version must not be empty")
        if self.model.model_version not in (
            "conditional-logit-v1",
            "conditional-logit-v1-temperature-v1",
            "conditional-logit-track-condition-v2",
            "conditional-logit-track-condition-v2-temperature-v1",
            "conditional-logit-evidence-neutral-v3",
            "conditional-logit-recent-form-v4",
            "conditional-logit-evidence-neutral-v3-temperature-v1",
            "conditional-logit-recent-form-v4-temperature-v1",
        ):
            raise ValueError("unsupported model_version")
        if self.parameters.track_condition_v2 != self.model.model_version.startswith(
            "conditional-logit-track-condition-v2"
        ):
            raise ValueError("track_condition_v2 does not match model_version")
        if self.parameters.recent_form_v4 != self.model.model_version.startswith(
            "conditional-logit-recent-form-v4"
        ):
            raise ValueError("recent_form_v4 does not match model_version")
        if self.parameters.evidence_neutral_v3 != self.model.model_version.startswith(
            "conditional-logit-evidence-neutral-v3"
        ):
            raise ValueError("evidence_neutral_v3 does not match model_version")
        if isinstance(self.model, TemperatureCalibratedModel):
            if self.parameters.calibration_races < 1:
                raise ValueError("calibrated model requires calibration_races")
            if self.model.calibrated_through <= self.model.trained_through:
                raise ValueError("calibration period must follow model training")
        elif self.parameters.calibration_races != 0:
            raise ValueError("uncalibrated model requires calibration_races=0")


def train_local_model_artifact(
    training_path: str | Path,
    *,
    parameters: ModelTrainingParameters | None = None,
) -> TrainedModelArtifact:
    """Build time-safe rows and fit a reproducible conditional-logit model."""
    selected = parameters or ModelTrainingParameters()
    if not isinstance(selected, ModelTrainingParameters):
        raise ValueError("parameters must be ModelTrainingParameters")
    bundle = build_time_safe_training_bundle(
        training_path, prior_strength=selected.prior_strength
    )
    races: dict[str, list[TrainingRow]] = defaultdict(list)
    for row in bundle.rows:
        races[row.features.race_id].append(row)
    ordered_races = sorted(
        races.values(),
        key=lambda race: (race[0].features.observed_at, race[0].features.race_id),
    )
    if selected.calibration_races >= len(ordered_races):
        raise ValueError("calibration_races must leave at least one training race")
    calibration = (
        ordered_races[-selected.calibration_races:]
        if selected.calibration_races
        else []
    )
    training = (
        ordered_races[:-selected.calibration_races]
        if selected.calibration_races
        else ordered_races
    )
    base_model = fit_conditional_logit(
        tuple(row for race in training for row in race),
        epochs=selected.epochs,
        learning_rate=selected.learning_rate,
        l2_strength=selected.l2_strength,
        feature_names=(
            RECENT_FORM_V4_FEATURE_NAMES
            if selected.recent_form_v4 else EVIDENCE_NEUTRAL_V3_FEATURE_NAMES
            if selected.evidence_neutral_v3 else TRACK_CONDITION_V2_FEATURE_NAMES
            if selected.track_condition_v2
            else CONDITIONAL_LOGIT_FEATURE_NAMES
        ),
        model_version=(
            "conditional-logit-recent-form-v4"
            if selected.recent_form_v4 else "conditional-logit-evidence-neutral-v3"
            if selected.evidence_neutral_v3 else "conditional-logit-track-condition-v2"
            if selected.track_condition_v2
            else "conditional-logit-v1"
        ),
    )
    model: ConditionalLogitModel | TemperatureCalibratedModel = base_model
    if calibration:
        model = fit_temperature_scaling(
            base_model,
            tuple(
                CalibrationRow(row.features, row.finish_position)
                for race in calibration
                for row in race
            ),
        )
    return TrainedModelArtifact(
        training_sha256=bundle.training_sha256,
        input_data_version=bundle.input_data_version,
        training_row_count=len(bundle.rows),
        training_race_count=len({row.features.race_id for row in bundle.rows}),
        parameters=selected,
        model=model,
    )


def _payload(artifact: TrainedModelArtifact) -> dict[str, object]:
    model_payload: dict[str, object] = {
        "model_version": artifact.model.model_version,
        "trained_through": artifact.model.trained_through.isoformat(),
        "feature_names": list(artifact.model.feature_names),
        "coefficients": list(artifact.model.coefficients),
        "means": list(artifact.model.means),
        "scales": list(artifact.model.scales),
    }
    if isinstance(artifact.model, TemperatureCalibratedModel):
        model_payload.update({
            "temperature": artifact.model.temperature,
            "calibrated_through": artifact.model.calibrated_through.isoformat(),
        })
    return {
        "training_sha256": artifact.training_sha256,
        "input_data_version": artifact.input_data_version,
        "training_row_count": artifact.training_row_count,
        "training_race_count": artifact.training_race_count,
        "parameters": {
            "prior_strength": float(artifact.parameters.prior_strength),
            "epochs": artifact.parameters.epochs,
            "learning_rate": float(artifact.parameters.learning_rate),
            "l2_strength": float(artifact.parameters.l2_strength),
            "calibration_races": artifact.parameters.calibration_races,
            "track_condition_v2": artifact.parameters.track_condition_v2,
            "evidence_neutral_v3": artifact.parameters.evidence_neutral_v3,
            "recent_form_v4": artifact.parameters.recent_form_v4,
        },
        "model": model_payload,
    }


def _canonical(payload: dict[str, object]) -> str:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def save_trained_model_artifact(
    artifact: TrainedModelArtifact, path: str | Path
) -> str:
    """Save a model without overwriting and return its payload digest."""
    payload = _payload(artifact)
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    envelope = {
        "schema_version": MODEL_ARTIFACT_SCHEMA_VERSION,
        "sha256": digest,
        "payload": payload,
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as handle:
        json.dump(envelope, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    return digest


def _required(payload: dict[str, Any], key: str, expected_type: type) -> Any:
    value = payload.get(key)
    if type(value) is not expected_type:
        raise ValueError(f"model artifact {key} has an invalid type")
    return value


def _float_tuple(payload: dict[str, Any], key: str) -> tuple[float, ...]:
    values = _required(payload, key, list)
    if any(type(value) not in (int, float) for value in values):
        raise ValueError(f"model artifact {key} must contain numbers")
    return tuple(float(value) for value in values)


def load_trained_model_artifact_bytes(content: bytes) -> TrainedModelArtifact:
    """Load model bytes after verifying schema, digest, and feature contract."""
    envelope = json.loads(content.decode("utf-8"))
    if not isinstance(envelope, dict):
        raise ValueError("model artifact envelope must be an object")
    schema_version = envelope.get("schema_version")
    if schema_version not in SUPPORTED_MODEL_ARTIFACT_SCHEMA_VERSIONS:
        raise ValueError("unsupported model artifact schema_version")
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("model artifact payload must be an object")
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    if digest != envelope.get("sha256"):
        raise ValueError("model artifact integrity check failed")
    parameters = _required(payload, "parameters", dict)
    model_payload = _required(payload, "model", dict)
    feature_names = _required(model_payload, "feature_names", list)
    model_version = _required(model_payload, "model_version", str)
    base_model_version = model_version.removesuffix("-temperature-v1")
    expected_features = {
        "conditional-logit-v1": list(CONDITIONAL_LOGIT_FEATURE_NAMES),
        "conditional-logit-evidence-neutral-v3": list(EVIDENCE_NEUTRAL_V3_FEATURE_NAMES),
        "conditional-logit-recent-form-v4": list(RECENT_FORM_V4_FEATURE_NAMES),
        "conditional-logit-track-condition-v2": list(
            TRACK_CONDITION_V2_FEATURE_NAMES
        ),
    }.get(base_model_version)
    if feature_names != expected_features:
        raise ValueError("model artifact feature schema is incompatible")
    if (
        base_model_version == "conditional-logit-track-condition-v2"
        and schema_version not in ("1.2", "1.3", "1.4")
    ):
        raise ValueError("track-condition-v2 model requires artifact schema 1.2 or newer")
    if (
        base_model_version == "conditional-logit-evidence-neutral-v3"
        and schema_version not in ("1.3", "1.4")
    ):
        raise ValueError("evidence-neutral-v3 model requires artifact schema 1.3")
    if base_model_version == "conditional-logit-recent-form-v4" and schema_version != "1.4":
        raise ValueError("recent-form-v4 model requires artifact schema 1.4")
    base_model = ConditionalLogitModel(
        coefficients=_float_tuple(model_payload, "coefficients"),
        means=_float_tuple(model_payload, "means"),
        scales=_float_tuple(model_payload, "scales"),
        trained_through=datetime.fromisoformat(
            _required(model_payload, "trained_through", str)
        ),
        model_version=base_model_version,
    )
    if model_version in (
        "conditional-logit-v1",
        "conditional-logit-track-condition-v2",
        "conditional-logit-evidence-neutral-v3",
        "conditional-logit-recent-form-v4",
    ):
        model: ConditionalLogitModel | TemperatureCalibratedModel = base_model
    elif model_version in (
        "conditional-logit-v1-temperature-v1",
        "conditional-logit-track-condition-v2-temperature-v1",
        "conditional-logit-evidence-neutral-v3-temperature-v1",
        "conditional-logit-recent-form-v4-temperature-v1",
    ):
        if schema_version not in ("1.1", "1.2", "1.3", "1.4"):
            raise ValueError("calibrated model requires artifact schema 1.1 or newer")
        temperature = model_payload.get("temperature")
        calibrated_through = model_payload.get("calibrated_through")
        if (
            type(temperature) not in (int, float)
            or type(calibrated_through) is not str
        ):
            raise ValueError("calibrated model metadata is invalid")
        model = TemperatureCalibratedModel(
            base_model,
            float(temperature),
            datetime.fromisoformat(calibrated_through),
        )
    else:
        raise ValueError("unsupported model_version")
    return TrainedModelArtifact(
        training_sha256=_required(payload, "training_sha256", str),
        input_data_version=_required(payload, "input_data_version", str),
        training_row_count=_required(payload, "training_row_count", int),
        training_race_count=_required(payload, "training_race_count", int),
        parameters=ModelTrainingParameters(
            prior_strength=float(_required(parameters, "prior_strength", float)),
            epochs=_required(parameters, "epochs", int),
            learning_rate=float(_required(parameters, "learning_rate", float)),
            l2_strength=float(_required(parameters, "l2_strength", float)),
            calibration_races=(
                _required(parameters, "calibration_races", int)
                if "calibration_races" in parameters else 0
            ),
            recent_form_v4=(_required(parameters, "recent_form_v4", bool) if "recent_form_v4" in parameters else False),
            evidence_neutral_v3=(
                _required(parameters, "evidence_neutral_v3", bool)
                if "evidence_neutral_v3" in parameters else False
            ),
            track_condition_v2=(
                _required(parameters, "track_condition_v2", bool)
                if "track_condition_v2" in parameters else False
            ),
        ),
        model=model,
    )


def load_trained_model_artifact(path: str | Path) -> TrainedModelArtifact:
    """Load a model file after verifying schema, digest, and feature contract."""
    return load_trained_model_artifact_bytes(Path(path).read_bytes())
