import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from keiba_prediction_lab.domain import PredictionRecord
from keiba_prediction_lab.frozen import (
    FrozenPrediction,
    FrozenRaceResult,
    FrozenTrifectaTicket,
    PredictionPhase,
    TrifectaPayout,
    evaluate_frozen_predictions,
    load_frozen_prediction,
    save_frozen_prediction,
)


UTC = timezone.utc
FROZEN_AT = datetime(2026, 8, 20, 5, 0, tzinfo=UTC)
SCHEDULED_AT = FROZEN_AT + timedelta(hours=2)


def snapshot(race_id: str = "race-1", phase: PredictionPhase = PredictionPhase.PRE_ODDS) -> FrozenPrediction:
    probabilities = ((0.5, 0.9), (0.25, 0.8), (0.15, 0.7), (0.1, 0.6))
    predictions = tuple(
        PredictionRecord(
            race_id, f"horse-{index}", FROZEN_AT, "model-v1", win, top3, index
        )
        for index, (win, top3) in enumerate(probabilities, start=1)
    )
    return FrozenPrediction(
        race_id=race_id,
        scheduled_at=SCHEDULED_AT,
        frozen_at=FROZEN_AT,
        phase=phase,
        input_data_version="sha256:input-v1",
        predictions=predictions,
        trifecta_tickets=(
            FrozenTrifectaTicket(("horse-1", "horse-2", "horse-3")),
        ),
    )


class FrozenPredictionTest(unittest.TestCase):
    def test_save_and_load_round_trip_with_integrity_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "race-1.json"
            digest = save_frozen_prediction(snapshot(), path)
            loaded = load_frozen_prediction(path)

            self.assertEqual(loaded, snapshot())
            self.assertEqual(len(digest), 64)

    def test_existing_snapshot_cannot_be_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "race-1.json"
            save_frozen_prediction(snapshot(), path)
            with self.assertRaises(FileExistsError):
                save_frozen_prediction(snapshot(), path)

    def test_modified_snapshot_fails_integrity_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "race-1.json"
            save_frozen_prediction(snapshot(), path)
            envelope = json.loads(path.read_text(encoding="utf-8"))
            envelope["payload"]["input_data_version"] = "tampered"
            path.write_text(json.dumps(envelope), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "integrity check failed"):
                load_frozen_prediction(path)

    def test_snapshot_must_be_frozen_before_start(self) -> None:
        with self.assertRaisesRegex(ValueError, "before scheduled_at"):
            replace(snapshot(), frozen_at=SCHEDULED_AT)

    def test_trifecta_must_use_predicted_winner_as_anchor(self) -> None:
        invalid = (FrozenTrifectaTicket(("horse-2", "horse-1", "horse-3")),)
        with self.assertRaisesRegex(ValueError, "predicted winner"):
            replace(snapshot(), trifecta_tickets=invalid)

    def test_actual_purchase_candidate_is_limited_to_one_ticket(self) -> None:
        tickets = (
            FrozenTrifectaTicket(("horse-1", "horse-2", "horse-3")),
            FrozenTrifectaTicket(("horse-1", "horse-3", "horse-2")),
        )
        with self.assertRaisesRegex(ValueError, "limited to one ticket"):
            replace(snapshot(), trifecta_tickets=tickets)

    def test_report_is_reproducible_and_separates_phase(self) -> None:
        snapshots = (
            snapshot("race-1", PredictionPhase.PRE_ODDS),
            snapshot("race-2", PredictionPhase.POST_ODDS),
        )
        results = (
            FrozenRaceResult(
                "race-1",
                (("horse-1", 1), ("horse-2", 2), ("horse-3", 3), ("horse-4", 4)),
                (TrifectaPayout(("horse-1", "horse-2", "horse-3"), 1000),),
            ),
            FrozenRaceResult(
                "race-2",
                (("horse-2", 1), ("horse-1", 2), ("horse-3", 3), ("horse-4", 4)),
                (TrifectaPayout(("horse-2", "horse-1", "horse-3"), 5000),),
            ),
        )

        report = evaluate_frozen_predictions(snapshots, results)

        self.assertEqual(report.top1_hits, 1)
        self.assertEqual(report.trifecta_hits, 1)
        self.assertEqual(report.total_stake_yen, 200)
        self.assertEqual(report.total_payout_yen, 1000)
        self.assertEqual(report.pre_odds_race_count, 1)
        self.assertEqual(report.post_odds_race_count, 1)
        self.assertEqual(report.to_markdown(), report.to_markdown())
        self.assertIn("三連単的中: 1/2 (50.0%)", report.to_markdown())


if __name__ == "__main__":
    unittest.main()
