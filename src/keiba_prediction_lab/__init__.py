"""Evaluation tools for reproducible horse-racing prediction research."""

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
from .metrics import (
    binary_brier_score,
    binary_log_loss,
    top1_accuracy,
    top3_unordered_accuracy,
)

__all__ = [
    "BetType",
    "ColumnAvailability",
    "CsvAuditReport",
    "DataSource",
    "FixedStakeSummary",
    "PredictionRecord",
    "RedistributionStatus",
    "ResultRecord",
    "TicketResult",
    "SourceStatus",
    "assert_pre_race_features",
    "audit_standard_csv",
    "binary_brier_score",
    "binary_log_loss",
    "evaluate_fixed_stake",
    "load_source_registry",
    "sha256_file",
    "top1_accuracy",
    "top3_unordered_accuracy",
    "validate_race_predictions",
]
