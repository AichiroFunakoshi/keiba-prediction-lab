import unittest
from datetime import datetime, timedelta, timezone

from keiba_prediction_lab.features import FeatureRow
from keiba_prediction_lab.model import TrainingRow
from keiba_prediction_lab.walk_forward import WalkForwardWindow, run_walk_forward


UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)


def feature(
    race_id: str, horse: int, observed_at: datetime, strength: float, venue: str
) -> FeatureRow:
    return FeatureRow(
        race_id, f"{race_id}-horse-{horse}", observed_at, "mile", horse, 56.0,
        480, 21, 10, strength, min(1.0, strength + 0.2), 5, strength,
        8, strength, 4, strength, 6, strength, 100, strength, 100, strength, venue,
    )


def labeled_rows() -> tuple[TrainingRow, ...]:
    rows = []
    for race_number in range(12):
        race_id = f"race-{race_number:02d}"
        observed_at = START + timedelta(days=race_number)
        for horse, strength in enumerate((0.8, 0.35, 0.2, 0.1), start=1):
            rows.append(TrainingRow(
                feature(
                    race_id, horse, observed_at, strength,
                    "Tokyo" if race_number % 2 == 0 else "Kyoto",
                ),
                horse,
            ))
    return tuple(rows)


def windows() -> tuple[WalkForwardWindow, ...]:
    return (
        WalkForwardWindow(
            START + timedelta(days=3),
            START + timedelta(days=5),
            START + timedelta(days=7),
        ),
        WalkForwardWindow(
            START + timedelta(days=7),
            START + timedelta(days=9),
            START + timedelta(days=11),
        ),
    )


class WalkForwardTest(unittest.TestCase):
    def test_runs_expanding_windows_and_aggregates_scores(self) -> None:
        result = run_walk_forward(labeled_rows(), windows())

        self.assertEqual(len(result.folds), 2)
        self.assertEqual(result.folds[0].training_race_count, 4)
        self.assertEqual(result.folds[1].training_race_count, 8)
        self.assertEqual(result.folds[0].calibration_race_count, 2)
        self.assertEqual(result.folds[0].evaluation_race_count, 2)
        self.assertEqual(result.aggregate_model_score.race_count, 4)
        self.assertEqual(result.aggregate_model_score.runner_count, 16)
        self.assertLess(
            result.aggregate_model_score.win_brier_score,
            result.aggregate_uniform_score.win_brier_score,
        )
        self.assertGreater(len(result.calibration.bins), 0)
        self.assertEqual(
            {item.dimension for item in result.diagnostics.segments},
            {"venue", "distance_band", "field_size", "confidence"},
        )

    def test_is_deterministic(self) -> None:
        self.assertEqual(
            run_walk_forward(labeled_rows(), windows()),
            run_walk_forward(labeled_rows(), windows()),
        )

    def test_rejects_overlapping_evaluation_periods(self) -> None:
        invalid = (
            windows()[0],
            WalkForwardWindow(
                START + timedelta(days=6),
                START + timedelta(days=8),
                START + timedelta(days=10),
            ),
        )
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            run_walk_forward(labeled_rows(), invalid)

    def test_rejects_reversed_window_boundaries(self) -> None:
        with self.assertRaisesRegex(ValueError, "train < calibration < evaluation"):
            WalkForwardWindow(START + timedelta(days=2), START, START + timedelta(days=3))

    def test_requires_data_in_every_period(self) -> None:
        empty_calibration = (
            WalkForwardWindow(
                START + timedelta(days=3, hours=1),
                START + timedelta(days=3, hours=2),
                START + timedelta(days=5),
            ),
        )
        with self.assertRaisesRegex(ValueError, "requires training, calibration"):
            run_walk_forward(labeled_rows(), empty_calibration)


if __name__ == "__main__":
    unittest.main()
