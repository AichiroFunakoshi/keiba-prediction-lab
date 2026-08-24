"""Local, reproducible walk-forward evaluation artifacts."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .baselines import BaselineScore
from .local_adapter import build_time_safe_training_bundle
from .walk_forward import WalkForwardResult, WalkForwardWindow, run_walk_forward


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


def _score_row(label: str, score: BaselineScore) -> str:
    return (
        f"| {label} | {score.top1_accuracy:.2%} | "
        f"{score.win_brier_score:.4f} | {score.win_log_loss:.4f} |"
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"windows JSON contains duplicate key: {key}")
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
