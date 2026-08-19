import unittest
from datetime import datetime, timezone

from keiba_prediction_lab.diagnostics import (
    confidence_bucket,
    diagnose_segments,
    field_size_bucket,
)
from keiba_prediction_lab.domain import PredictionRecord, ResultRecord
from keiba_prediction_lab.features import FeatureRow


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def feature(race_id: str, horse: int, venue: str, distance: str) -> FeatureRow:
    return FeatureRow(
        race_id, f"{race_id}-horse-{horse}", NOW, distance, horse, 56.0,
        480, 21, 10, 0.2, 0.5, 5, 0.2, 8, 0.2, 4, 0.2, 6, 0.2,
        100, 0.2, 100, 0.2, venue,
    )


def race_rows(
    race_id: str, venue: str, distance: str, probabilities: tuple[float, ...]
) -> tuple[list[PredictionRecord], list[PredictionRecord], list[ResultRecord], list[FeatureRow]]:
    model = []
    uniform = []
    results = []
    features = []
    runner_count = len(probabilities)
    for index, probability in enumerate(probabilities, start=1):
        horse_id = f"{race_id}-horse-{index}"
        model.append(PredictionRecord(
            race_id, horse_id, NOW, "model", probability,
            min(1.0, 3 / runner_count), index,
        ))
        uniform.append(PredictionRecord(
            race_id, horse_id, NOW, "uniform", 1 / runner_count,
            min(1.0, 3 / runner_count), index,
        ))
        results.append(ResultRecord(race_id, horse_id, index))
        features.append(feature(race_id, index, venue, distance))
    return model, uniform, results, features


class SegmentDiagnosticsTest(unittest.TestCase):
    def test_groups_races_by_all_supported_dimensions(self) -> None:
        first = race_rows("race-1", "Tokyo", "mile", (0.6, 0.2, 0.15, 0.05))
        second = race_rows("race-2", "Kyoto", "middle", (0.3, 0.25, 0.2, 0.15, 0.1))
        combined = [first[index] + second[index] for index in range(4)]

        report = diagnose_segments(*combined)

        self.assertEqual(
            {item.dimension for item in report.segments},
            {"venue", "distance_band", "field_size", "confidence"},
        )
        self.assertEqual(
            {item.value for item in report.for_dimension("venue")},
            {"Tokyo", "Kyoto"},
        )
        self.assertTrue(
            all(item.model_score.race_count == 1 for item in report.for_dimension("venue"))
        )

    def test_requires_identical_runners(self) -> None:
        model, uniform, results, features = race_rows(
            "race", "Tokyo", "mile", (0.5, 0.3, 0.2)
        )
        with self.assertRaisesRegex(ValueError, "identical runners"):
            diagnose_segments(model, uniform, results[:-1], features)

    def test_fixed_bucket_boundaries(self) -> None:
        self.assertEqual(field_size_bucket(8), "small-1-8")
        self.assertEqual(field_size_bucket(9), "medium-9-12")
        self.assertEqual(field_size_bucket(13), "large-13-plus")
        self.assertEqual(confidence_bucket(0.39), "low-below-0.4")
        self.assertEqual(confidence_bucket(0.4), "medium-0.4-0.7")
        self.assertEqual(confidence_bucket(0.7), "high-0.7-plus")


if __name__ == "__main__":
    unittest.main()
