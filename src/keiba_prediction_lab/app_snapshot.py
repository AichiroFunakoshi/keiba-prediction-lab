"""Read-only, UI-facing snapshots built only from audited artifacts."""

from dataclasses import dataclass
from pathlib import Path

from .bundle_audit import load_audited_prediction_bundle
from .evaluation import BET_TYPE_LABELS_JA
from .pipeline import PIPELINE_POLICY_VERSION
from .walk_forward_report import load_walk_forward_artifact
from .win5 import load_win5_forecast


@dataclass(frozen=True)
class RunnerSnapshot:
    predicted_rank: int
    horse_id: str
    win_probability: float
    top3_probability: float


@dataclass(frozen=True)
class ShadowPortfolioSnapshot:
    generator: str
    strategy: str
    ticket_count: int
    cumulative_probability: float
    stake_yen: int = 0


@dataclass(frozen=True)
class BetTypeCandidateSnapshot:
    bet_type: str
    label_ja: str
    selection: tuple[str, ...]
    probability: float
    stake_yen: int = 0


@dataclass(frozen=True)
class PredictionAppSnapshot:
    race_id: str
    scheduled_at: str
    frozen_at: str
    model_version: str
    input_data_version: str
    runners: tuple[RunnerSnapshot, ...]
    actual_selection: tuple[str, str, str]
    actual_stake_yen: int
    shadow_portfolios: tuple[ShadowPortfolioSnapshot, ...]
    bet_type_candidates: tuple[BetTypeCandidateSnapshot, ...]


@dataclass(frozen=True)
class WalkForwardAppSnapshot:
    fold_count: int
    evaluation_race_count: int
    evaluation_runner_count: int
    model_top1_accuracy: float
    uniform_top1_accuracy: float
    model_win_brier_score: float
    uniform_win_brier_score: float
    model_win_log_loss: float
    uniform_win_log_loss: float
    expected_calibration_error: float
    training_sha256: str
    windows_sha256: str


@dataclass(frozen=True)
class Win5LegAppSnapshot:
    race_id: str
    scheduled_at: str
    selected_horse_id: str
    selected_win_probability: float


@dataclass(frozen=True)
class Win5AppSnapshot:
    frozen_at: str
    selection: tuple[str, str, str, str, str]
    joint_probability: float
    independence_assumption: str
    stake_yen: int
    legs: tuple[Win5LegAppSnapshot, ...]


@dataclass(frozen=True)
class ReadOnlyAppSnapshot:
    policy_version: str
    actual_purchase_policy: str
    prediction: PredictionAppSnapshot | None
    walk_forward: WalkForwardAppSnapshot | None
    win5: Win5AppSnapshot | None = None

    def __post_init__(self) -> None:
        if (
            self.prediction is None
            and self.walk_forward is None
            and self.win5 is None
        ):
            raise ValueError("at least one audited artifact is required")

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "actual_purchase_policy": self.actual_purchase_policy,
            "prediction": _prediction_dict(self.prediction),
            "walk_forward": _walk_forward_dict(self.walk_forward),
            "win5": _win5_dict(self.win5),
        }


def _prediction_snapshot(directory: str | Path) -> PredictionAppSnapshot:
    audited = load_audited_prediction_bundle(directory)
    bundle = audited.bundle
    actual = bundle.actual_prediction
    runners = tuple(
        RunnerSnapshot(
            row.predicted_rank,
            row.horse_id,
            row.win_probability,
            row.top3_probability,
        )
        for row in sorted(actual.predictions, key=lambda item: item.predicted_rank)
    )
    shadow_portfolios = tuple(
        ShadowPortfolioSnapshot(
            generator=generator,
            strategy=portfolio.strategy.value,
            ticket_count=portfolio.ticket_count,
            cumulative_probability=portfolio.cumulative_probability,
        )
        for generator, frozen in (
            ("baseline", bundle.baseline_shadow),
            ("pace", bundle.pace_shadow),
        )
        for portfolio in frozen.forecast.shadow_portfolios
    )
    candidates = tuple(
        BetTypeCandidateSnapshot(
            candidate.bet_type.value,
            BET_TYPE_LABELS_JA[candidate.bet_type],
            candidate.selection,
            candidate.probability,
        )
        for candidate in bundle.bet_type_shadow.forecast.candidates
    )
    ticket = actual.trifecta_tickets[0]
    return PredictionAppSnapshot(
        race_id=actual.race_id,
        scheduled_at=actual.scheduled_at.isoformat(),
        frozen_at=actual.frozen_at.isoformat(),
        model_version=actual.model_version,
        input_data_version=actual.input_data_version,
        runners=runners,
        actual_selection=ticket.selection,
        actual_stake_yen=ticket.stake_yen,
        shadow_portfolios=shadow_portfolios,
        bet_type_candidates=candidates,
    )


def _walk_forward_snapshot(path: str | Path) -> WalkForwardAppSnapshot:
    artifact = load_walk_forward_artifact(path)
    result = artifact.result
    model = result.aggregate_model_score
    uniform = result.aggregate_uniform_score
    return WalkForwardAppSnapshot(
        fold_count=len(result.folds),
        evaluation_race_count=model.race_count,
        evaluation_runner_count=model.runner_count,
        model_top1_accuracy=model.top1_accuracy,
        uniform_top1_accuracy=uniform.top1_accuracy,
        model_win_brier_score=model.win_brier_score,
        uniform_win_brier_score=uniform.win_brier_score,
        model_win_log_loss=model.win_log_loss,
        uniform_win_log_loss=uniform.win_log_loss,
        expected_calibration_error=result.calibration.expected_calibration_error,
        training_sha256=artifact.training_sha256,
        windows_sha256=artifact.windows_sha256,
    )


def _win5_snapshot(path: str | Path) -> Win5AppSnapshot:
    forecast = load_win5_forecast(path)
    return Win5AppSnapshot(
        frozen_at=forecast.frozen_at.isoformat(),
        selection=forecast.selection,
        joint_probability=forecast.joint_probability,
        independence_assumption=(
            "5レース間を独立と仮定し、各選択馬の1着確率を掛け合わせる"
        ),
        stake_yen=forecast.stake_yen,
        legs=tuple(
            Win5LegAppSnapshot(
                race_id=leg.race_id,
                scheduled_at=leg.scheduled_at.isoformat(),
                selected_horse_id=leg.runners[0].horse_id,
                selected_win_probability=leg.runners[0].win_probability,
            )
            for leg in forecast.legs
        ),
    )


def build_read_only_app_snapshot(
    *,
    prediction_directory: str | Path | None = None,
    walk_forward_report: str | Path | None = None,
    win5_forecast: str | Path | None = None,
) -> ReadOnlyAppSnapshot:
    """Build UI data from explicitly selected and fully audited artifacts."""
    if (
        prediction_directory is None
        and walk_forward_report is None
        and win5_forecast is None
    ):
        raise ValueError("at least one artifact path is required")
    return ReadOnlyAppSnapshot(
        policy_version=PIPELINE_POLICY_VERSION,
        actual_purchase_policy="三連単1点100円。影予測は購入額0円。",
        prediction=(
            _prediction_snapshot(prediction_directory)
            if prediction_directory is not None else None
        ),
        walk_forward=(
            _walk_forward_snapshot(walk_forward_report)
            if walk_forward_report is not None else None
        ),
        win5=(
            _win5_snapshot(win5_forecast)
            if win5_forecast is not None else None
        ),
    )


def _prediction_dict(value: PredictionAppSnapshot | None) -> object:
    if value is None:
        return None
    return {
        "race_id": value.race_id,
        "scheduled_at": value.scheduled_at,
        "frozen_at": value.frozen_at,
        "model_version": value.model_version,
        "input_data_version": value.input_data_version,
        "runners": [
            {
                "predicted_rank": row.predicted_rank,
                "horse_id": row.horse_id,
                "win_probability": row.win_probability,
                "top3_probability": row.top3_probability,
            }
            for row in value.runners
        ],
        "actual": {
            "bet_type": "trifecta",
            "selection": list(value.actual_selection),
            "stake_yen": value.actual_stake_yen,
        },
        "shadow_portfolios": [
            {
                "generator": row.generator,
                "strategy": row.strategy,
                "ticket_count": row.ticket_count,
                "cumulative_probability": row.cumulative_probability,
                "stake_yen": row.stake_yen,
            }
            for row in value.shadow_portfolios
        ],
        "bet_type_candidates": [
            {
                "bet_type": row.bet_type,
                "label_ja": row.label_ja,
                "selection": list(row.selection),
                "probability": row.probability,
                "stake_yen": row.stake_yen,
            }
            for row in value.bet_type_candidates
        ],
    }


def _walk_forward_dict(value: WalkForwardAppSnapshot | None) -> object:
    if value is None:
        return None
    return {
        "fold_count": value.fold_count,
        "evaluation_race_count": value.evaluation_race_count,
        "evaluation_runner_count": value.evaluation_runner_count,
        "model": {
            "top1_accuracy": value.model_top1_accuracy,
            "win_brier_score": value.model_win_brier_score,
            "win_log_loss": value.model_win_log_loss,
        },
        "uniform": {
            "top1_accuracy": value.uniform_top1_accuracy,
            "win_brier_score": value.uniform_win_brier_score,
            "win_log_loss": value.uniform_win_log_loss,
        },
        "expected_calibration_error": value.expected_calibration_error,
        "training_sha256": value.training_sha256,
        "windows_sha256": value.windows_sha256,
    }


def _win5_dict(value: Win5AppSnapshot | None) -> object:
    if value is None:
        return None
    return {
        "frozen_at": value.frozen_at,
        "purchase_status": "shadow_only",
        "stake_yen": value.stake_yen,
        "selection": list(value.selection),
        "joint_probability": value.joint_probability,
        "independence_assumption": value.independence_assumption,
        "legs": [
            {
                "race_id": leg.race_id,
                "scheduled_at": leg.scheduled_at,
                "selected_horse_id": leg.selected_horse_id,
                "selected_win_probability": leg.selected_win_probability,
            }
            for leg in value.legs
        ],
    }
