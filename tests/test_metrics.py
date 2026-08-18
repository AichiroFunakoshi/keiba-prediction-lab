import math
import unittest

from keiba_prediction_lab.metrics import (
    binary_brier_score,
    binary_log_loss,
    top1_accuracy,
    top3_unordered_accuracy,
)


class PredictionMetricsTest(unittest.TestCase):
    def test_brier_score(self) -> None:
        self.assertAlmostEqual(binary_brier_score([0.8, 0.3], [1, 0]), 0.065)

    def test_log_loss(self) -> None:
        expected = -(math.log(0.8) + math.log(0.7)) / 2
        self.assertAlmostEqual(binary_log_loss([0.8, 0.3], [1, 0]), expected)

    def test_binary_metrics_reject_mismatched_lengths(self) -> None:
        with self.assertRaisesRegex(ValueError, "equal length"):
            binary_brier_score([0.8], [1, 0])

    def test_top1_accuracy(self) -> None:
        self.assertAlmostEqual(
            top1_accuracy(["horse-1", "horse-4"], ["horse-1", "horse-3"]), 0.5
        )

    def test_top3_unordered_accuracy(self) -> None:
        predicted = [("a", "b", "c"), ("d", "e", "f")]
        actual = [("c", "a", "b"), ("d", "f", "g")]
        self.assertAlmostEqual(top3_unordered_accuracy(predicted, actual), 0.5)

    def test_top3_requires_three_unique_horses(self) -> None:
        with self.assertRaisesRegex(ValueError, "unique"):
            top3_unordered_accuracy([("a", "a", "b")], [("a", "b", "c")])
