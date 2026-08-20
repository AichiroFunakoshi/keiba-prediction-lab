import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from keiba_prediction_lab.domain import PredictionRecord
from keiba_prediction_lab.frozen import PredictionPhase
from keiba_prediction_lab.shadow_snapshot import (
    freeze_shadow_forecast,
    load_frozen_shadow_forecast,
    save_frozen_shadow_forecast,
)
from keiba_prediction_lab.trifecta import (
    TrifectaRaceResult,
    evaluate_shadow_portfolios,
)


UTC = timezone.utc
PREDICTED_AT = datetime(2026, 8, 20, 4, 50, tzinfo=UTC)
FROZEN_AT = datetime(2026, 8, 20, 5, 0, tzinfo=UTC)
SCHEDULED_AT = FROZEN_AT + timedelta(hours=2)


def predictions() -> tuple[PredictionRecord, ...]:
    win = (0.40, 0.35, 0.15, 0.07, 0.03)
    top3 = (0.90, 0.80, 0.60, 0.45, 0.25)
    return tuple(
        PredictionRecord(
            "race-1", f"horse-{index}", PREDICTED_AT, "model-v1",
            win_probability, top3_probability, index,
        )
        for index, (win_probability, top3_probability) in enumerate(
            zip(win, top3), start=1
        )
    )


def snapshot():
    return freeze_shadow_forecast(
        predictions(),
        scheduled_at=SCHEDULED_AT,
        frozen_at=FROZEN_AT,
        phase=PredictionPhase.PRE_ODDS,
        input_data_version="sha256:input-v1",
    )


class FrozenShadowForecastTest(unittest.TestCase):
    def test_round_trip_preserves_forecast_and_evaluation(self) -> None:
        original = snapshot()
        winner = original.forecast.primary_ticket.selection
        result = TrifectaRaceResult("race-1", (winner,))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "race-1-shadow.json"
            digest = save_frozen_shadow_forecast(original, path)
            loaded = load_frozen_shadow_forecast(path)

        self.assertEqual(len(digest), 64)
        self.assertEqual(loaded, original)
        self.assertEqual(
            evaluate_shadow_portfolios((loaded.forecast,), (result,)),
            evaluate_shadow_portfolios((original.forecast,), (result,)),
        )

    def test_snapshot_cannot_be_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "race-1-shadow.json"
            save_frozen_shadow_forecast(snapshot(), path)
            with self.assertRaises(FileExistsError):
                save_frozen_shadow_forecast(snapshot(), path)

    def test_modified_shadow_snapshot_fails_integrity_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "race-1-shadow.json"
            save_frozen_shadow_forecast(snapshot(), path)
            envelope = json.loads(path.read_text(encoding="utf-8"))
            envelope["payload"]["generator_version"] = "tampered"
            path.write_text(json.dumps(envelope), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "integrity check failed"):
                load_frozen_shadow_forecast(path)

    def test_freeze_must_precede_start(self) -> None:
        with self.assertRaisesRegex(ValueError, "before scheduled_at"):
            freeze_shadow_forecast(
                predictions(),
                scheduled_at=SCHEDULED_AT,
                frozen_at=SCHEDULED_AT,
                phase=PredictionPhase.PRE_ODDS,
                input_data_version="sha256:input-v1",
            )

    def test_source_prediction_must_exist_before_freeze(self) -> None:
        future_predictions = tuple(
            PredictionRecord(
                row.race_id, row.horse_id, FROZEN_AT + timedelta(seconds=1),
                row.model_version, row.win_probability, row.top3_probability,
                row.predicted_rank,
            )
            for row in predictions()
        )
        with self.assertRaisesRegex(ValueError, "later than frozen_at"):
            freeze_shadow_forecast(
                future_predictions,
                scheduled_at=SCHEDULED_AT,
                frozen_at=FROZEN_AT,
                phase=PredictionPhase.PRE_ODDS,
                input_data_version="sha256:input-v1",
            )

    def test_shadow_file_contains_no_stake(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "race-1-shadow.json"
            save_frozen_shadow_forecast(snapshot(), path)
            contents = path.read_text(encoding="utf-8")

        self.assertNotIn("stake_yen", contents)


if __name__ == "__main__":
    unittest.main()
