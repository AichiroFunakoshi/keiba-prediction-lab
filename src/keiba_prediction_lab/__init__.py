"""Evaluation tools for reproducible horse-racing prediction research."""

from .baselines import (
    BaselineRunner,
    BaselineScore,
    UniformBaseline,
    evaluate_baseline_predictions,
    horse_history_baseline,
    post_position_baseline,
)
from .domain import (
    BetType,
    PredictionRecord,
    ResultRecord,
    TicketResult,
    validate_race_predictions,
)
from .data_audit import (
    ColumnAvailability,
    CsvAuditReport,
    DataSource,
    RedistributionStatus,
    SourceStatus,
    assert_pre_race_features,
    audit_standard_csv,
    load_source_registry,
    sha256_file,
)
from .evaluation import FixedStakeSummary, evaluate_fixed_stake
from .features import (
    FeatureRow,
    RacePerformance,
    Surface,
    TargetRunner,
    distance_band,
    generate_features,
)
from .metrics import (
    binary_brier_score,
    binary_log_loss,
    top1_accuracy,
    top3_unordered_accuracy,
)
from .model import ConditionalLogitModel, TrainingRow, fit_conditional_logit

__all__ = [
    "BaselineRunner",
    "BaselineScore",
    "BetType",
    "ColumnAvailability",
    "ConditionalLogitModel",
    "CsvAuditReport",
    "DataSource",
    "FixedStakeSummary",
    "FeatureRow",
    "PredictionRecord",
    "RedistributionStatus",
    "ResultRecord",
    "RacePerformance",
    "Surface",
    "TargetRunner",
    "TicketResult",
    "TrainingRow",
    "UniformBaseline",
    "SourceStatus",
    "assert_pre_race_features",
    "audit_standard_csv",
    "binary_brier_score",
    "binary_log_loss",
    "evaluate_fixed_stake",
    "distance_band",
    "generate_features",
    "fit_conditional_logit",
    "evaluate_baseline_predictions",
    "horse_history_baseline",
    "load_source_registry",
    "post_position_baseline",
    "sha256_file",
    "top1_accuracy",
    "top3_unordered_accuracy",
    "validate_race_predictions",
]
