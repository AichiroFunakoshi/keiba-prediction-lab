import unittest
from datetime import datetime, timedelta, timezone

from keiba_prediction_lab.baselines import (
    BaselineRunner,
    UniformBaseline,
    evaluate_baseline_predictions,
    horse_history_baseline,
    post_position_baseline,
)
from keiba_prediction_lab.domain import ResultRecord


UTC = timezone.utc
HISTORY_TIME_1 = datetime(2026, 1, 1, 6, 0, tzinfo=UTC)
HISTORY_TIME_2 = datetime(2026, 1, 2, 6, 0, tzinfo=UTC)
TARGET_TIME = datetime(2026, 1, 3, 6, 0, tzinfo=UTC)


def history() -> tuple[BaselineRunner, ...]:
    return (
        BaselineRunner("race-1", HISTORY_TIME_1, "horse-a", 1, 1),
        BaselineRunner("race-1", HISTORY_TIME_1, "horse-b", 2, 2),
        BaselineRunner("race-1", HISTORY_TIME_1, "horse-c", 3, 3),
        BaselineRunner("race-2", HISTORY_TIME_2, "horse-a", 1, 1),
        BaselineRunner("race-2", HISTORY_TIME_2, "horse-b", 2, 3),
        BaselineRunner("race-2", HISTORY_TIME_2, "horse-d", 3, 2),
    )


def target() -> tuple[BaselineRunner, ...]:
    return (
        BaselineRunner("race-3", TARGET_TIME, "horse-a", 1),
        BaselineRunner("race-3", TARGET_TIME, "horse-b", 2),
        BaselineRunner("race-3", TARGET_TIME, "horse-c", 3),
        BaselineRunner("race-3", TARGET_TIME, "horse-new", 4),
    )


class BaselineTest(unittest.TestCase):
    def test_uniform_baseline_assigns_equal_probabilities(self) -> None:
        predictions = UniformBaseline().predict(
            target(), predicted_at=TARGET_TIME - timedelta(hours=1)
        )

        self.assertTrue(
            all(prediction.win_probability == 0.25 for prediction in predictions)
        )
        for prediction in predictions:
            self.assertAlmostEqual(prediction.top3_probability, 0.75)
        self.assertEqual(
            [prediction.predicted_rank for prediction in predictions], [1, 2, 3, 4]
        )

    def test_post_position_baseline_rewards_historical_winning_position(self) -> None:
        model = post_position_baseline(history(), prior_strength=2.0)
        predictions = model.predict(
            target(), predicted_at=TARGET_TIME - timedelta(hours=1)
        )

        by_horse = {prediction.horse_id: prediction for prediction in predictions}
        self.assertGreater(
            by_horse["horse-a"].win_probability,
            by_horse["horse-b"].win_probability,
        )

    def test_horse_history_baseline_rewards_past_winner(self) -> None:
        model = horse_history_baseline(history(), prior_strength=2.0)
        predictions = model.predict(
            target(), predicted_at=TARGET_TIME - timedelta(hours=1)
        )

        by_horse = {prediction.horse_id: prediction for prediction in predictions}
        self.assertGreater(
            by_horse["horse-a"].win_probability,
            by_horse["horse-b"].win_probability,
        )
        self.assertAlmostEqual(
            sum(prediction.win_probability for prediction in predictions), 1.0
        )
        self.assertAlmostEqual(
            sum(prediction.top3_probability for prediction in predictions), 3.0
        )

    def test_trained_baseline_rejects_past_or_same_time_target(self) -> None:
        model = horse_history_baseline(history())
        invalid_target = (
            BaselineRunner("race-old", HISTORY_TIME_2, "horse-a", 1),
            BaselineRunner("race-old", HISTORY_TIME_2, "horse-b", 2),
        )

        with self.assertRaisesRegex(ValueError, "later than all training history"):
            model.predict(
                invalid_target, predicted_at=HISTORY_TIME_2 - timedelta(hours=1)
            )

    def test_target_must_not_contain_results(self) -> None:
        leaked_target = list(target())
        leaked_target[0] = BaselineRunner("race-3", TARGET_TIME, "horse-a", 1, 1)

        with self.assertRaisesRegex(ValueError, "must not contain finish_position"):
            UniformBaseline().predict(
                leaked_target, predicted_at=TARGET_TIME - timedelta(hours=1)
            )

    def test_prediction_must_be_frozen_before_start(self) -> None:
        with self.assertRaisesRegex(ValueError, "before scheduled_at"):
            UniformBaseline().predict(target(), predicted_at=TARGET_TIME)

    def test_dead_heat_splits_win_credit(self) -> None:
        tied_history = (
            BaselineRunner("race-tie", HISTORY_TIME_1, "horse-a", 1, 1),
            BaselineRunner("race-tie", HISTORY_TIME_1, "horse-b", 2, 1),
            BaselineRunner("race-tie", HISTORY_TIME_1, "horse-c", 3, 3),
        )
        model = horse_history_baseline(tied_history, prior_strength=2.0)
        predictions = model.predict(
            target(), predicted_at=TARGET_TIME - timedelta(hours=1)
        )

        by_horse = {prediction.horse_id: prediction for prediction in predictions}
        self.assertAlmostEqual(
            by_horse["horse-a"].win_probability,
            by_horse["horse-b"].win_probability,
        )

    def test_all_baselines_share_one_evaluation_contract(self) -> None:
        predictions = UniformBaseline().predict(
            target(), predicted_at=TARGET_TIME - timedelta(hours=1)
        )
        results = (
            ResultRecord("race-3", "horse-a", 1),
            ResultRecord("race-3", "horse-b", 2),
            ResultRecord("race-3", "horse-c", 3),
            ResultRecord("race-3", "horse-new", 4),
        )

        score = evaluate_baseline_predictions(predictions, results)

        self.assertEqual(score.model_version, "uniform-v1")
        self.assertEqual(score.race_count, 1)
        self.assertEqual(score.runner_count, 4)
        self.assertEqual(score.top1_accuracy, 1.0)
        self.assertAlmostEqual(score.win_brier_score, 0.1875)
