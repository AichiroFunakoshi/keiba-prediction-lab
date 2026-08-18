import unittest
from datetime import datetime, timezone

from keiba_prediction_lab.domain import (
    BetType,
    PredictionRecord,
    ResultRecord,
    TicketResult,
    validate_race_predictions,
)


class DomainRecordTest(unittest.TestCase):
    def test_valid_prediction_record(self) -> None:
        prediction = PredictionRecord(
            race_id="202608180101",
            horse_id="horse-1",
            predicted_at=datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc),
            model_version="baseline-1",
            win_probability=0.2,
            top3_probability=0.5,
            predicted_rank=1,
        )
        self.assertEqual(prediction.predicted_rank, 1)

    def test_prediction_timestamp_must_be_timezone_aware(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            PredictionRecord(
                race_id="race-1",
                horse_id="horse-1",
                predicted_at=datetime(2026, 8, 18, 12, 0),
                model_version="baseline-1",
                win_probability=0.2,
                top3_probability=0.5,
                predicted_rank=1,
            )

    def test_top3_probability_cannot_be_below_win_probability(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not be below"):
            PredictionRecord(
                race_id="race-1",
                horse_id="horse-1",
                predicted_at=datetime.now(timezone.utc),
                model_version="baseline-1",
                win_probability=0.6,
                top3_probability=0.5,
                predicted_rank=1,
            )

    def test_finished_result_requires_position(self) -> None:
        with self.assertRaisesRegex(ValueError, "require finish_position"):
            ResultRecord("race-1", "horse-1", None)

    def test_non_finished_result_can_have_no_position(self) -> None:
        result = ResultRecord("race-1", "horse-1", None, "scratched")
        self.assertIsNone(result.finish_position)

    def test_ticket_stake_is_fixed(self) -> None:
        with self.assertRaisesRegex(ValueError, "fixed at 100 yen"):
            TicketResult("race-1", BetType.WIN, ("horse-1",), 270, 200)

    def test_ticket_selection_size_matches_bet_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "trifecta requires 3"):
            TicketResult(
                "race-1", BetType.TRIFECTA, ("horse-1", "horse-2"), 0
            )

    def test_race_prediction_probabilities_and_ranks(self) -> None:
        predicted_at = datetime.now(timezone.utc)
        predictions = tuple(
            PredictionRecord(
                race_id="race-1",
                horse_id=f"horse-{index}",
                predicted_at=predicted_at,
                model_version="baseline-1",
                win_probability=win_probability,
                top3_probability=top3_probability,
                predicted_rank=index,
            )
            for index, (win_probability, top3_probability) in enumerate(
                ((0.5, 0.9), (0.3, 0.8), (0.15, 0.7), (0.05, 0.6)),
                start=1,
            )
        )

        validate_race_predictions(predictions)

    def test_race_win_probabilities_must_sum_to_one(self) -> None:
        predicted_at = datetime.now(timezone.utc)
        predictions = (
            PredictionRecord(
                "race-1", "horse-1", predicted_at, "baseline-1", 0.4, 0.7, 1
            ),
            PredictionRecord(
                "race-1", "horse-2", predicted_at, "baseline-1", 0.4, 0.7, 2
            ),
        )

        with self.assertRaisesRegex(ValueError, "win probabilities must sum"):
            validate_race_predictions(predictions)
