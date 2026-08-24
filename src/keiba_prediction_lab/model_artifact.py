"""Integrity-protected conditional-logit model artifacts."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from pathlib import Path
from typing import Any

from .local_adapter import build_time_safe_training_bundle
from .model import (
    CONDITIONAL_LOGIT_FEATURE_NAMES,
    ConditionalLogitModel,
    fit_conditional_logit,
)


MODEL_ARTIFACT_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class ModelTrainingParameters:
    prior_strength: float = 10.0
    epochs: int = 500
    learning_rate: float = 0.1
    l2_strength: float = 0.01

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


@dataclass(frozen=True)
class TrainedModelArtifact:
    training_sha256: str
    input_data_version: str
    training_row_count: int
    training_race_count: int
    parameters: ModelTrainingParameters
    model: ConditionalLogitModel

    def __post_init__(self) -> None:
        if not isinstance(self.parameters, ModelTrainingParameters):
            raise ValueError("parameters must be ModelTrainingParameters")
        if not isinstance(self.model, ConditionalLogitModel):
            raise ValueError("model must be ConditionalLogitModel")
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
        expected = len(CONDITIONAL_LOGIT_FEATURE_NAMES)
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
        if self.model.model_version != "conditional-logit-v1":
            raise ValueError("unsupported model_version")


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
    model = fit_conditional_logit(
        bundle.rows,
        epochs=selected.epochs,
        learning_rate=selected.learning_rate,
        l2_strength=selected.l2_strength,
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
        },
        "model": {
            "model_version": artifact.model.model_version,
            "trained_through": artifact.model.trained_through.isoformat(),
            "feature_names": list(CONDITIONAL_LOGIT_FEATURE_NAMES),
            "coefficients": list(artifact.model.coefficients),
            "means": list(artifact.model.means),
            "scales": list(artifact.model.scales),
        },
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


def load_trained_model_artifact(path: str | Path) -> TrainedModelArtifact:
    """Load a model after verifying its schema, digest, and feature contract."""
    envelope = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(envelope, dict):
        raise ValueError("model artifact envelope must be an object")
    if envelope.get("schema_version") != MODEL_ARTIFACT_SCHEMA_VERSION:
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
    if feature_names != list(CONDITIONAL_LOGIT_FEATURE_NAMES):
        raise ValueError("model artifact feature schema is incompatible")
    model = ConditionalLogitModel(
        coefficients=_float_tuple(model_payload, "coefficients"),
        means=_float_tuple(model_payload, "means"),
        scales=_float_tuple(model_payload, "scales"),
        trained_through=datetime.fromisoformat(
            _required(model_payload, "trained_through", str)
        ),
        model_version=_required(model_payload, "model_version", str),
    )
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
        ),
        model=model,
    )
