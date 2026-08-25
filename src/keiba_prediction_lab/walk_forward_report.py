"""Local, reproducible walk-forward evaluation artifacts."""

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .baselines import BaselineScore
from .diagnostics import DiagnosticReport, SegmentDiagnostic
from .local_adapter import build_time_safe_training_bundle
from .metrics import CalibrationBin, CalibrationSummary
from .walk_forward import (
    WalkForwardFoldResult,
    WalkForwardResult,
    WalkForwardWindow,
    run_walk_forward,
)


WALK_FORWARD_ARTIFACT_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class WalkForwardArtifact:
    training_sha256: str
    windows_sha256: str
    input_data_version: str
    result: WalkForwardResult

    def to_markdown(self) -> str:
        model = self.result.aggregate_model_score
        uniform = self.result.aggregate_uniform_score
        lines = [
            "# ウォークフォワード検証",
            "",
            "> 過去で学習し、その後の校正期間を経て、さらに未来の評価期間だけを採点した結果です。",
            "",
            f"- 評価窓数: {len(self.result.folds)}",
            f"- 評価レース数: {model.race_count}",
            f"- 評価出走馬数: {model.runner_count}",
            f"- 学習CSV SHA-256: `{self.training_sha256}`",
            f"- 窓定義 SHA-256: `{self.windows_sha256}`",
            "",
            "## 全期間集計",
            "",
            "| モデル | 1着的中率 | Brier score | Log loss |",
            "|---|---:|---:|---:|",
            _score_row("条件付きロジット", model),
            _score_row("全馬同確率", uniform),
            "",
            f"- ECE: {self.result.calibration.expected_calibration_error:.4f}",
            "",
            "## 窓別結果",
            "",
            "| 窓 | 学習終了 | 校正終了 | 評価終了 | 学習/校正/評価レース | 温度 | モデル1着 | 一様1着 |",
            "|---:|---|---|---|---:|---:|---:|---:|",
        ]
        for index, fold in enumerate(self.result.folds, start=1):
            lines.append(
                f"| {index} | {fold.window.train_end.isoformat()} | "
                f"{fold.window.calibration_end.isoformat()} | "
                f"{fold.window.evaluation_end.isoformat()} | "
                f"{fold.training_race_count}/{fold.calibration_race_count}/"
                f"{fold.evaluation_race_count} | {fold.temperature:.4f} | "
                f"{fold.model_score.top1_accuracy:.2%} | "
                f"{fold.uniform_score.top1_accuracy:.2%} |"
            )
        lines.extend([
            "",
            "## 条件別診断",
            "",
            "| 軸 | 区分 | レース数 | モデル1着 | 一様1着 | Brier差 |",
            "|---|---|---:|---:|---:|---:|",
        ])
        for row in self.result.diagnostics.segments:
            lines.append(
                f"| {row.dimension} | {row.value} | {row.model_score.race_count} | "
                f"{row.model_score.top1_accuracy:.2%} | "
                f"{row.uniform_score.top1_accuracy:.2%} | "
                f"{row.model_score.win_brier_score - row.uniform_score.win_brier_score:+.4f} |"
            )
        lines.extend([
            "",
            "---",
            "",
            "この結果はモデル更新の材料であり、個別レース後に係数を自動更新するものではありません。少数レースの差を改善の証拠とは扱いません。",
        ])
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class WalkForwardArtifactAudit:
    schema_version: str
    sha256: str
    fold_count: int
    evaluation_race_count: int
    evaluation_runner_count: int
    training_sha256: str
    windows_sha256: str


def _score_row(label: str, score: BaselineScore) -> str:
    return (
        f"| {label} | {score.top1_accuracy:.2%} | "
        f"{score.win_brier_score:.4f} | {score.win_log_loss:.4f} |"
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON contains duplicate key: {key}")
        result[key] = value
    return result


def load_walk_forward_windows_bytes(content: bytes) -> tuple[WalkForwardWindow, ...]:
    value = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(value, list) or not value:
        raise ValueError("windows JSON must be a non-empty array")
    windows = []
    expected = {"train_end", "calibration_end", "evaluation_end"}
    for index, row in enumerate(value):
        if not isinstance(row, dict) or set(row) != expected:
            raise ValueError(f"window {index} must contain exactly {sorted(expected)}")
        if any(not isinstance(row[key], str) for key in expected):
            raise ValueError(f"window {index} boundaries must be strings")
        windows.append(WalkForwardWindow(
            train_end=datetime.fromisoformat(row["train_end"]),
            calibration_end=datetime.fromisoformat(row["calibration_end"]),
            evaluation_end=datetime.fromisoformat(row["evaluation_end"]),
        ))
    return tuple(windows)


def evaluate_local_walk_forward(
    training_path: str | Path,
    windows_path: str | Path,
) -> WalkForwardArtifact:
    windows_content = Path(windows_path).read_bytes()
    windows_sha256 = hashlib.sha256(windows_content).hexdigest()
    bundle = build_time_safe_training_bundle(training_path)
    result = run_walk_forward(
        bundle.rows, load_walk_forward_windows_bytes(windows_content)
    )
    return WalkForwardArtifact(
        training_sha256=bundle.training_sha256,
        windows_sha256=windows_sha256,
        input_data_version=bundle.input_data_version,
        result=result,
    )


def _score_payload(score: BaselineScore) -> dict[str, object]:
    return {
        "model_version": score.model_version,
        "race_count": score.race_count,
        "runner_count": score.runner_count,
        "top1_accuracy": score.top1_accuracy,
        "win_brier_score": score.win_brier_score,
        "win_log_loss": score.win_log_loss,
    }


def _payload(artifact: WalkForwardArtifact) -> dict[str, object]:
    result = artifact.result
    return {
        "training_sha256": artifact.training_sha256,
        "windows_sha256": artifact.windows_sha256,
        "input_data_version": artifact.input_data_version,
        "folds": [
            {
                "train_end": fold.window.train_end.isoformat(),
                "calibration_end": fold.window.calibration_end.isoformat(),
                "evaluation_end": fold.window.evaluation_end.isoformat(),
                "training_race_count": fold.training_race_count,
                "calibration_race_count": fold.calibration_race_count,
                "evaluation_race_count": fold.evaluation_race_count,
                "temperature": fold.temperature,
                "model_score": _score_payload(fold.model_score),
                "uniform_score": _score_payload(fold.uniform_score),
            }
            for fold in result.folds
        ],
        "aggregate_model_score": _score_payload(result.aggregate_model_score),
        "aggregate_uniform_score": _score_payload(result.aggregate_uniform_score),
        "calibration": {
            "expected_calibration_error": result.calibration.expected_calibration_error,
            "bins": [
                {
                    "lower_bound": row.lower_bound,
                    "upper_bound": row.upper_bound,
                    "count": row.count,
                    "mean_probability": row.mean_probability,
                    "observed_rate": row.observed_rate,
                }
                for row in result.calibration.bins
            ],
        },
        "diagnostics": [
            {
                "dimension": row.dimension,
                "value": row.value,
                "model_score": _score_payload(row.model_score),
                "uniform_score": _score_payload(row.uniform_score),
            }
            for row in result.diagnostics.segments
        ],
    }


def save_walk_forward_artifact(
    artifact: WalkForwardArtifact, path: str | Path
) -> str:
    payload = _payload(artifact)
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as handle:
        json.dump(
            {"schema_version": WALK_FORWARD_ARTIFACT_SCHEMA_VERSION,
             "sha256": digest, "payload": payload},
            handle, ensure_ascii=False, sort_keys=True, indent=2,
        )
        handle.write("\n")
    return digest


def _exact_keys(payload: dict[str, Any], expected: set[str], label: str) -> None:
    if set(payload) != expected:
        raise ValueError(
            f"invalid {label} keys: missing={sorted(expected - set(payload))}, "
            f"unexpected={sorted(set(payload) - expected)}"
        )


def _required(payload: dict[str, Any], key: str, expected_type: type) -> Any:
    value = payload.get(key)
    if type(value) is not expected_type:
        raise ValueError(f"walk-forward {key} has an invalid type")
    return value


def _finite_number(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"walk-forward {key} must be a finite number")
    return float(value)


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{label} must be lowercase SHA-256")
    return value


_SCORE_KEYS = {
    "model_version", "race_count", "runner_count", "top1_accuracy",
    "win_brier_score", "win_log_loss",
}


def _load_score(payload: Any, label: str) -> BaselineScore:
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be an object")
    _exact_keys(payload, _SCORE_KEYS, label)
    model_version = _required(payload, "model_version", str)
    race_count = _required(payload, "race_count", int)
    runner_count = _required(payload, "runner_count", int)
    top1 = _finite_number(payload, "top1_accuracy")
    brier = _finite_number(payload, "win_brier_score")
    log_loss = _finite_number(payload, "win_log_loss")
    if not model_version.strip() or race_count < 1 or runner_count < race_count:
        raise ValueError(f"{label} counts and model_version are invalid")
    if not 0.0 <= top1 <= 1.0 or not 0.0 <= brier <= 1.0 or log_loss < 0.0:
        raise ValueError(f"{label} metrics are outside valid ranges")
    return BaselineScore(
        model_version, race_count, runner_count, top1, brier, log_loss
    )


def _paired_scores(payload: dict[str, Any], label: str) -> tuple[BaselineScore, BaselineScore]:
    model = _load_score(payload.get("model_score"), f"{label} model_score")
    uniform = _load_score(payload.get("uniform_score"), f"{label} uniform_score")
    if (model.race_count, model.runner_count) != (
        uniform.race_count, uniform.runner_count
    ):
        raise ValueError(f"{label} model and uniform counts must match")
    return model, uniform


def load_walk_forward_artifact_bytes(content: bytes) -> WalkForwardArtifact:
    """Load a saved evaluation after verifying integrity and all contracts."""
    envelope = json.loads(
        content.decode("utf-8"), object_pairs_hook=_unique_object
    )
    if not isinstance(envelope, dict):
        raise ValueError("walk-forward artifact envelope must be an object")
    _exact_keys(envelope, {"schema_version", "sha256", "payload"}, "envelope")
    if envelope["schema_version"] != WALK_FORWARD_ARTIFACT_SCHEMA_VERSION:
        raise ValueError("unsupported walk-forward artifact schema_version")
    payload = envelope["payload"]
    if not isinstance(payload, dict):
        raise ValueError("walk-forward artifact payload must be an object")
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if envelope["sha256"] != digest:
        raise ValueError("walk-forward artifact integrity check failed")
    _exact_keys(payload, {
        "training_sha256", "windows_sha256", "input_data_version", "folds",
        "aggregate_model_score", "aggregate_uniform_score", "calibration",
        "diagnostics",
    }, "payload")
    training_sha256 = _sha256(payload["training_sha256"], "training_sha256")
    windows_sha256 = _sha256(payload["windows_sha256"], "windows_sha256")
    input_version = _required(payload, "input_data_version", str)
    if input_version != f"sha256:{training_sha256}":
        raise ValueError("input_data_version must match training_sha256")

    folds_payload = _required(payload, "folds", list)
    if not folds_payload:
        raise ValueError("walk-forward artifact requires at least one fold")
    fold_keys = {
        "train_end", "calibration_end", "evaluation_end",
        "training_race_count", "calibration_race_count", "evaluation_race_count",
        "temperature", "model_score", "uniform_score",
    }
    folds = []
    for index, row in enumerate(folds_payload):
        if not isinstance(row, dict):
            raise ValueError(f"fold {index} must be an object")
        _exact_keys(row, fold_keys, f"fold {index}")
        window = WalkForwardWindow(
            datetime.fromisoformat(_required(row, "train_end", str)),
            datetime.fromisoformat(_required(row, "calibration_end", str)),
            datetime.fromisoformat(_required(row, "evaluation_end", str)),
        )
        counts = tuple(
            _required(row, key, int) for key in (
                "training_race_count", "calibration_race_count",
                "evaluation_race_count",
            )
        )
        if any(value < 1 for value in counts):
            raise ValueError(f"fold {index} race counts must be positive")
        temperature = _finite_number(row, "temperature")
        if temperature <= 0.0:
            raise ValueError(f"fold {index} temperature must be positive")
        model, uniform = _paired_scores(row, f"fold {index}")
        if model.race_count != counts[2]:
            raise ValueError(f"fold {index} evaluation count is inconsistent")
        folds.append(WalkForwardFoldResult(
            window, counts[0], counts[1], counts[2], temperature, model, uniform
        ))
    for previous, current in zip(folds, folds[1:]):
        if current.window.train_end < previous.window.evaluation_end:
            raise ValueError("walk-forward evaluation periods must not overlap")
        if current.window.evaluation_end <= previous.window.evaluation_end:
            raise ValueError("walk-forward windows must move forward")

    aggregate_model = _load_score(
        payload["aggregate_model_score"], "aggregate model score"
    )
    aggregate_uniform = _load_score(
        payload["aggregate_uniform_score"], "aggregate uniform score"
    )
    expected_races = sum(fold.evaluation_race_count for fold in folds)
    expected_runners = sum(fold.model_score.runner_count for fold in folds)
    for score in (aggregate_model, aggregate_uniform):
        if (score.race_count, score.runner_count) != (
            expected_races, expected_runners
        ):
            raise ValueError("aggregate score counts do not match folds")

    calibration_payload = _required(payload, "calibration", dict)
    _exact_keys(
        calibration_payload, {"expected_calibration_error", "bins"},
        "calibration",
    )
    ece = _finite_number(calibration_payload, "expected_calibration_error")
    if not 0.0 <= ece <= 1.0:
        raise ValueError("expected_calibration_error must be between 0 and 1")
    bins_payload = _required(calibration_payload, "bins", list)
    if not bins_payload:
        raise ValueError("calibration bins must not be empty")
    bins = []
    seen_bounds = set()
    for index, row in enumerate(bins_payload):
        if not isinstance(row, dict):
            raise ValueError(f"calibration bin {index} must be an object")
        _exact_keys(row, {
            "lower_bound", "upper_bound", "count", "mean_probability",
            "observed_rate",
        }, f"calibration bin {index}")
        lower = _finite_number(row, "lower_bound")
        upper = _finite_number(row, "upper_bound")
        count = _required(row, "count", int)
        mean = _finite_number(row, "mean_probability")
        observed = _finite_number(row, "observed_rate")
        if not 0.0 <= lower < upper <= 1.0 or count < 1:
            raise ValueError(f"calibration bin {index} bounds or count are invalid")
        if not lower <= mean <= upper or not 0.0 <= observed <= 1.0:
            raise ValueError(f"calibration bin {index} probabilities are invalid")
        if (lower, upper) in seen_bounds:
            raise ValueError("calibration bins must have unique bounds")
        if bins and lower < bins[-1].upper_bound:
            raise ValueError("calibration bins must be ordered and non-overlapping")
        seen_bounds.add((lower, upper))
        bins.append(CalibrationBin(lower, upper, count, mean, observed))
    if sum(row.count for row in bins) != expected_runners:
        raise ValueError("calibration counts do not match aggregate runner_count")

    diagnostics_payload = _required(payload, "diagnostics", list)
    if not diagnostics_payload:
        raise ValueError("diagnostics must not be empty")
    diagnostics = []
    identities = set()
    for index, row in enumerate(diagnostics_payload):
        if not isinstance(row, dict):
            raise ValueError(f"diagnostic {index} must be an object")
        _exact_keys(
            row, {"dimension", "value", "model_score", "uniform_score"},
            f"diagnostic {index}",
        )
        dimension = _required(row, "dimension", str)
        value = _required(row, "value", str)
        if not dimension.strip() or not value.strip():
            raise ValueError(f"diagnostic {index} identity must not be empty")
        if (dimension, value) in identities:
            raise ValueError("diagnostics must have unique dimension and value")
        identities.add((dimension, value))
        model, uniform = _paired_scores(row, f"diagnostic {index}")
        if (
            model.race_count > expected_races
            or model.runner_count > expected_runners
        ):
            raise ValueError("diagnostic counts exceed aggregate counts")
        diagnostics.append(SegmentDiagnostic(dimension, value, model, uniform))

    dimensions = {row.dimension for row in diagnostics}
    if dimensions != {"venue", "distance_band", "field_size", "confidence"}:
        raise ValueError("diagnostics must contain every supported dimension")
    for dimension in dimensions:
        rows = [row for row in diagnostics if row.dimension == dimension]
        if (
            sum(row.model_score.race_count for row in rows) != expected_races
            or sum(row.model_score.runner_count for row in rows) != expected_runners
        ):
            raise ValueError(
                f"diagnostic dimension {dimension} does not partition evaluation"
            )

    return WalkForwardArtifact(
        training_sha256=training_sha256,
        windows_sha256=windows_sha256,
        input_data_version=input_version,
        result=WalkForwardResult(
            folds=tuple(folds),
            aggregate_model_score=aggregate_model,
            aggregate_uniform_score=aggregate_uniform,
            calibration=CalibrationSummary(tuple(bins), ece),
            diagnostics=DiagnosticReport(tuple(diagnostics)),
        ),
    )


def load_walk_forward_artifact(path: str | Path) -> WalkForwardArtifact:
    return load_walk_forward_artifact_bytes(Path(path).read_bytes())


def audit_walk_forward_artifact(path: str | Path) -> WalkForwardArtifactAudit:
    content = Path(path).read_bytes()
    artifact = load_walk_forward_artifact_bytes(content)
    envelope = json.loads(content.decode("utf-8"))
    return WalkForwardArtifactAudit(
        schema_version=WALK_FORWARD_ARTIFACT_SCHEMA_VERSION,
        sha256=envelope["sha256"],
        fold_count=len(artifact.result.folds),
        evaluation_race_count=artifact.result.aggregate_model_score.race_count,
        evaluation_runner_count=artifact.result.aggregate_model_score.runner_count,
        training_sha256=artifact.training_sha256,
        windows_sha256=artifact.windows_sha256,
    )
