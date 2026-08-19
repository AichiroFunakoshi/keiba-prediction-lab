import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from keiba_prediction_lab.baselines import (
    BaselineRunner,
    UniformBaseline,
    evaluate_baseline_predictions,
)
from keiba_prediction_lab.domain import ResultRecord, validate_race_predictions
from keiba_prediction_lab.features import FeatureRow
from keiba_prediction_lab.model import TrainingRow, fit_conditional_logit


UTC = timezone.utc
START = datetime(2026, 1, 1, 6, 0, tzinfo=UTC)


def feature(race_id: str, horse: int, observed_at: datetime, strength: float) -> FeatureRow:
    return FeatureRow(
        race_id=race_id,
        horse_id=f"{race_id}-horse-{horse}",
        observed_at=observed_at,
        distance_band="mile",
        post_position=horse,
        carried_weight_kg=56.0,
        body_weight_kg=480,
        days_since_last_run=21,
        horse_starts=10,
        horse_win_rate=strength,
        horse_top3_rate=min(1.0, strength + 0.2),
        horse_venue_starts=5,
        horse_venue_win_rate=strength,
        horse_surface_starts=8,
        horse_surface_win_rate=strength,
        horse_track_condition_starts=4,
        horse_track_condition_win_rate=strength,
        horse_distance_band_starts=6,
        horse_distance_band_win_rate=strength,
        jockey_starts=100,
        jockey_win_rate=strength,
        trainer_starts=100,
        trainer_win_rate=strength,
    )


def training_rows() -> tuple[TrainingRow, ...]:
    rows = []
    for race_number in range(12):
        observed_at = START + timedelta(days=race_number)
        race_id = f"train-{race_number:02d}"
        for horse, strength in enumerate((0.8, 0.3, 0.2, 0.1), start=1):
            rows.append(TrainingRow(
                feature(race_id, horse, observed_at, strength),
                finish_position=horse,
            ))
    return tuple(rows)


def target_rows() -> tuple[FeatureRow, ...]:
    observed_at = START + timedelta(days=20)
    return tuple(
        feature("target", horse, observed_at, strength)
        for horse, strength in enumerate((0.75, 0.35, 0.2, 0.1), start=1)
    )


class ConditionalLogitModelTest(unittest.TestCase):
    def test_learns_race_probabilities_and_ranking(self) -> None:
        model = fit_conditional_logit(training_rows())
        predictions = model.predict(target_rows())

        validate_race_predictions(predictions)
        self.assertEqual(predictions[0].predicted_rank, 1)
        self.assertGreater(predictions[0].win_probability, predictions[1].win_probability)
        self.assertAlmostEqual(sum(row.win_probability for row in predictions), 1.0)
        self.assertAlmostEqual(sum(row.top3_probability for row in predictions), 3.0)

    def test_fit_and_prediction_are_deterministic(self) -> None:
        first = fit_conditional_logit(training_rows())
        second = fit_conditional_logit(training_rows())

        self.assertEqual(first, second)
        self.assertEqual(first.predict(target_rows()), second.predict(target_rows()))

    def test_predictions_use_shared_evaluation_contract(self) -> None:
        predictions = fit_conditional_logit(training_rows()).predict(target_rows())
        results = tuple(
            ResultRecord("target", row.horse_id, position)
            for position, row in enumerate(target_rows(), start=1)
        )

        score = evaluate_baseline_predictions(predictions, results)
        self.assertEqual(score.model_version, "conditional-logit-v1")
        self.assertEqual(score.top1_accuracy, 1.0)

    def test_learned_signal_beats_uniform_baseline_on_synthetic_data(self) -> None:
        rows = target_rows()
        results = tuple(
            ResultRecord("target", row.horse_id, position)
            for position, row in enumerate(rows, start=1)
        )
        model_score = evaluate_baseline_predictions(
            fit_conditional_logit(training_rows()).predict(rows), results
        )
        scheduled_at = rows[0].observed_at + timedelta(hours=2)
        baseline_runners = tuple(
            BaselineRunner(
                row.race_id, scheduled_at, row.horse_id, row.post_position
            )
            for row in rows
        )
        uniform_score = evaluate_baseline_predictions(
            UniformBaseline().predict(
                baseline_runners, predicted_at=rows[0].observed_at
            ),
            results,
        )

        self.assertLess(model_score.win_brier_score, uniform_score.win_brier_score)
        self.assertLess(model_score.win_log_loss, uniform_score.win_log_loss)

    def test_rejects_prediction_from_training_period(self) -> None:
        model = fit_conditional_logit(training_rows())
        old_rows = tuple(
            replace(row, observed_at=START + timedelta(days=11))
            for row in target_rows()
        )
        with self.assertRaisesRegex(ValueError, "later than all training"):
            model.predict(old_rows)

    def test_training_race_requires_winner(self) -> None:
        rows = tuple(
            TrainingRow(feature("no-winner", horse, START, 0.2), horse + 1)
            for horse in range(1, 3)
        )
        with self.assertRaisesRegex(ValueError, "has no winner"):
            fit_conditional_logit(rows)

    def test_rejects_naive_feature_timestamp(self) -> None:
        invalid = replace(training_rows()[0].features, observed_at=datetime(2026, 1, 1))
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            fit_conditional_logit((TrainingRow(invalid, 1), training_rows()[1]))

    def test_dead_heat_winners_share_training_credit(self) -> None:
        rows = (
            TrainingRow(feature("tie", 1, START, 0.8), 1),
            TrainingRow(feature("tie", 2, START, 0.8), 1),
            TrainingRow(feature("tie", 3, START, 0.1), 3),
            TrainingRow(feature("tie", 4, START, 0.1), 4),
        )
        model = fit_conditional_logit(rows)
        predictions = model.predict(target_rows())
        self.assertGreater(predictions[0].win_probability, predictions[3].win_probability)


if __name__ == "__main__":
    unittest.main()
