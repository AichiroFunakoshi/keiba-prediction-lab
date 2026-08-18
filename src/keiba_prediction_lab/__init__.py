"""Evaluation tools for reproducible horse-racing prediction research."""

from .domain import (
    BetType,
    PredictionRecord,
    ResultRecord,
    TicketResult,
    validate_race_predictions,
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
    "FixedStakeSummary",
    "PredictionRecord",
    "ResultRecord",
    "TicketResult",
    "binary_brier_score",
    "binary_log_loss",
    "evaluate_fixed_stake",
    "top1_accuracy",
    "top3_unordered_accuracy",
    "validate_race_predictions",
]
