"""Prediction metrics with explicit, dependency-free definitions."""

import math
from collections.abc import Iterable, Sequence


def _validated_binary_inputs(
    probabilities: Iterable[float], outcomes: Iterable[int]
) -> tuple[list[float], list[int]]:
    probability_values = list(probabilities)
    outcome_values = list(outcomes)
    if len(probability_values) != len(outcome_values):
        raise ValueError("probabilities and outcomes must have equal length")
    if not probability_values:
        raise ValueError("at least one observation is required")
    if any(not 0.0 <= value <= 1.0 for value in probability_values):
        raise ValueError("probabilities must be between 0 and 1")
    if any(value not in (0, 1) for value in outcome_values):
        raise ValueError("outcomes must be binary")
    return probability_values, outcome_values


def binary_brier_score(
    probabilities: Iterable[float], outcomes: Iterable[int]
) -> float:
    """Return the mean squared error of binary probabilities."""
    probability_values, outcome_values = _validated_binary_inputs(
        probabilities, outcomes
    )
    return sum(
        (probability - outcome) ** 2
        for probability, outcome in zip(probability_values, outcome_values)
    ) / len(probability_values)


def binary_log_loss(
    probabilities: Iterable[float], outcomes: Iterable[int], *, epsilon: float = 1e-15
) -> float:
    """Return binary logarithmic loss after numerical clipping."""
    if not 0.0 < epsilon < 0.5:
        raise ValueError("epsilon must be between 0 and 0.5")
    probability_values, outcome_values = _validated_binary_inputs(
        probabilities, outcomes
    )
    losses = []
    for probability, outcome in zip(probability_values, outcome_values):
        clipped = min(max(probability, epsilon), 1.0 - epsilon)
        losses.append(
            -(outcome * math.log(clipped) + (1 - outcome) * math.log(1 - clipped))
        )
    return sum(losses) / len(losses)


def top1_accuracy(
    predicted_winners: Sequence[str], actual_winners: Sequence[str]
) -> float:
    """Return race-level exact winner accuracy."""
    if len(predicted_winners) != len(actual_winners):
        raise ValueError("predicted and actual winners must have equal length")
    if not predicted_winners:
        return 0.0
    return sum(
        predicted == actual
        for predicted, actual in zip(predicted_winners, actual_winners)
    ) / len(predicted_winners)


def top3_unordered_accuracy(
    predicted_top3: Sequence[Sequence[str]], actual_top3: Sequence[Sequence[str]]
) -> float:
    """Return the share of races whose top three horses match in any order."""
    if len(predicted_top3) != len(actual_top3):
        raise ValueError("predicted and actual races must have equal length")
    if not predicted_top3:
        return 0.0

    matches = 0
    for predicted, actual in zip(predicted_top3, actual_top3):
        if len(predicted) != 3 or len(actual) != 3:
            raise ValueError("each top-three result must contain exactly three horses")
        if len(set(predicted)) != 3 or len(set(actual)) != 3:
            raise ValueError("top-three horse identifiers must be unique")
        matches += set(predicted) == set(actual)
    return matches / len(predicted_top3)
