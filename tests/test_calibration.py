import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from keiba_prediction_lab.calibration import (
    CalibrationRow,
    fit_temperature_scaling,
    temperature_scale_predictions,
)
from keiba_prediction_lab.domain import PredictionRecord, validate_race_predictions
from keiba_prediction_lab.features import FeatureRow
from keiba_prediction_lab.model import ConditionalLogitModel


UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)
FEATURE_COUNT = 17


def feature(race_id: str, horse: int, observed_at: datetime, strength: float) -> FeatureRow:
    return FeatureRow(
        race_id, f"{race_id}-horse-{horse}", observed_at, "mile", horse, 56.0,
        480, 21, 10, strength, min(1.0, strength + 0.1), 5, strength,
        8, strength, 4, strength, 6, strength, 100, strength, 100, strength,
    )


def base_model() -> ConditionalLogitModel:
    coefficients = [0.0] * FEATURE_COUNT
    coefficients[7] = 10.0
    return ConditionalLogitModel(
        tuple(coefficients), (0.0,) * FEATURE_COUNT, (1.0,) * FEATURE_COUNT, START
    )


def calibration_rows() -> tuple[CalibrationRow, ...]:
    rows = []
    for race_number in range(6):
        observed_at = START + timedelta(days=race_number + 1)
        race_id = f"cal-{race_number}"
        winner = 1 if race_number % 2 == 0 else 2
        for horse, strength in enumerate((0.9, 0.6, 0.3, 0.1), start=1):
            finish = 1 if horse == winner else horse + 1
            rows.append(CalibrationRow(feature(race_id, horse, observed_at, strength), finish))
    return tuple(rows)


class TemperatureScalingTest(unittest.TestCase):
    def test_selects_softer_probabilities_for_overconfident_model(self) -> None:
        calibrated = fit_temperature_scaling(base_model(), calibration_rows())
        self.assertGreater(calibrated.temperature, 1.0)
        self.assertEqual(calibrated.calibrated_through, START + timedelta(days=6))

    def test_calibrated_prediction_preserves_probability_contract(self) -> None:
        model = fit_temperature_scaling(base_model(), calibration_rows())
        target_time = START + timedelta(days=7)
        rows = tuple(
            feature("target", horse, target_time, strength)
            for horse, strength in enumerate((0.8, 0.5, 0.2, 0.1), start=1)
        )
        predictions = model.predict(rows)

        validate_race_predictions(predictions)
        self.assertAlmostEqual(sum(row.win_probability for row in predictions), 1.0)
        self.assertAlmostEqual(sum(row.top3_probability for row in predictions), 3.0)
        self.assertEqual(predictions[0].predicted_rank, 1)

    def test_final_evaluation_must_follow_calibration_period(self) -> None:
        model = fit_temperature_scaling(base_model(), calibration_rows())
        same_period = tuple(row.features for row in calibration_rows()[:4])
        with self.assertRaisesRegex(ValueError, "later than all calibration"):
            model.predict(same_period)

    def test_calibration_must_follow_training_period(self) -> None:
        old = tuple(
            replace(row, features=replace(row.features, observed_at=START))
            for row in calibration_rows()[:4]
        )
        with self.assertRaisesRegex(ValueError, "later than all training"):
            fit_temperature_scaling(base_model(), old)

    def test_temperature_scaling_does_not_change_rank(self) -> None:
        predicted_at = START + timedelta(days=1)
        predictions = tuple(
            PredictionRecord("race", f"horse-{index}", predicted_at, "raw", probability,
                             1.0, index)
            for index, probability in enumerate((0.6, 0.25, 0.1, 0.05), start=1)
        )
        scaled = temperature_scale_predictions(
            predictions, 2.0, model_version="scaled"
        )
        self.assertEqual([row.predicted_rank for row in scaled], [1, 2, 3, 4])
        self.assertLess(scaled[0].win_probability, predictions[0].win_probability)


if __name__ == "__main__":
    unittest.main()
