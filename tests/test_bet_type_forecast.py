import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from keiba_prediction_lab.bet_type_forecast import (
    BetTypeForecast,
    BetTypeProbability,
    build_bet_type_forecast,
    build_bet_type_forecast_from_combinations,
    freeze_bet_type_forecast,
    load_frozen_bet_type_forecast,
    save_frozen_bet_type_forecast,
)
from keiba_prediction_lab.domain import BetType, PredictionRecord
from keiba_prediction_lab.frozen import PredictionPhase
from keiba_prediction_lab.trifecta import rank_trifecta_combinations


UTC = timezone.utc
PREDICTED_AT = datetime(2026, 8, 22, 4, 50, tzinfo=UTC)
FROZEN_AT = PREDICTED_AT + timedelta(minutes=10)
SCHEDULED_AT = FROZEN_AT + timedelta(hours=2)


def predictions() -> tuple[PredictionRecord, ...]:
    win = (0.40, 0.35, 0.15, 0.07, 0.03)
    top3 = (0.90, 0.80, 0.60, 0.45, 0.25)
    return tuple(
        PredictionRecord(
            "race-1",
            f"horse-{index}",
            PREDICTED_AT,
            "model-v1",
            win_probability,
            top3_probability,
            index,
        )
        for index, (win_probability, top3_probability) in enumerate(
            zip(win, top3), start=1
        )
    )


def snapshot():
    return freeze_bet_type_forecast(
        predictions(),
        scheduled_at=SCHEDULED_AT,
        frozen_at=FROZEN_AT,
        phase=PredictionPhase.PRE_ODDS,
        input_data_version="sha256:input-v1",
    )


class BetTypeForecastTest(unittest.TestCase):
    def test_builds_complete_probability_table_for_every_bet_type(self) -> None:
        forecast = build_bet_type_forecast(predictions())
        expected_counts = {
            BetType.WIN: 5,
            BetType.PLACE: 5,
            BetType.QUINELLA: 10,
            BetType.EXACTA: 20,
            BetType.TRIO: 10,
            BetType.TRIFECTA: 60,
        }

        self.assertEqual(tuple(row.bet_type for row in forecast.candidates), tuple(BetType))
        self.assertEqual(forecast.place_payout_slots, 2)
        for bet_type, expected_count in expected_counts.items():
            rows = forecast.for_bet_type(bet_type)
            expected_total = 2.0 if bet_type is BetType.PLACE else 1.0
            self.assertEqual(len(rows), expected_count)
            self.assertAlmostEqual(sum(row.probability for row in rows), expected_total)
            self.assertEqual(forecast.candidate_for(bet_type), rows[0])

    def test_ordered_distribution_is_marginalized_for_unordered_bets(self) -> None:
        forecast = build_bet_type_forecast(predictions())
        trifectas = forecast.for_bet_type(BetType.TRIFECTA)
        exacta = next(
            row for row in forecast.for_bet_type(BetType.EXACTA)
            if row.selection == ("horse-1", "horse-2")
        )
        quinella = next(
            row for row in forecast.for_bet_type(BetType.QUINELLA)
            if row.selection == ("horse-1", "horse-2")
        )
        trio = next(
            row for row in forecast.for_bet_type(BetType.TRIO)
            if row.selection == ("horse-1", "horse-2", "horse-3")
        )

        self.assertAlmostEqual(
            exacta.probability,
            sum(
                row.probability
                for row in trifectas
                if row.selection[:2] == exacta.selection
            ),
        )
        self.assertAlmostEqual(
            quinella.probability,
            sum(
                row.probability
                for row in trifectas
                if set(row.selection[:2]) == set(quinella.selection)
            ),
        )
        self.assertAlmostEqual(
            trio.probability,
            sum(
                row.probability
                for row in trifectas
                if set(row.selection) == set(trio.selection)
            ),
        )

    def test_build_is_deterministic_for_reordered_input(self) -> None:
        original = build_bet_type_forecast(predictions())
        reordered = build_bet_type_forecast(tuple(reversed(predictions())))

        self.assertEqual(reordered, original)

    def test_place_slots_can_preserve_the_sales_start_rule(self) -> None:
        three_place_forecast = build_bet_type_forecast(
            predictions(), place_payout_slots=3
        )
        eight_runners = tuple(
            PredictionRecord(
                "race-8",
                f"runner-{index}",
                PREDICTED_AT,
                "model-v1",
                1.0 / 8,
                3.0 / 8,
                index,
            )
            for index in range(1, 9)
        )
        inferred_three_place_forecast = build_bet_type_forecast(eight_runners)

        self.assertEqual(three_place_forecast.place_payout_slots, 3)
        self.assertEqual(inferred_three_place_forecast.place_payout_slots, 3)
        self.assertAlmostEqual(
            sum(
                row.probability
                for row in three_place_forecast.for_bet_type(BetType.PLACE)
            ),
            3.0,
        )
        with self.assertRaisesRegex(ValueError, "must be 2 or 3"):
            build_bet_type_forecast(predictions(), place_payout_slots=1)

    def test_rejects_incomplete_joint_distribution(self) -> None:
        combinations = rank_trifecta_combinations(predictions())

        with self.assertRaisesRegex(ValueError, "cover every ordered outcome"):
            build_bet_type_forecast_from_combinations(
                predictions(), combinations[:-1]
            )

    def test_rejects_raw_bet_type_and_noncanonical_unordered_selection(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be a BetType value"):
            BetTypeProbability("win", ("horse-1",), 0.4)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "canonical order"):
            BetTypeProbability(
                BetType.QUINELLA, ("horse-2", "horse-1"), 0.4
            )

    def test_rejects_candidate_that_is_not_highest_ranked(self) -> None:
        forecast = build_bet_type_forecast(predictions())
        candidates = list(forecast.candidates)
        candidates[0] = forecast.for_bet_type(BetType.WIN)[1]

        with self.assertRaisesRegex(ValueError, "highest-ranked"):
            BetTypeForecast(
                race_id=forecast.race_id,
                place_payout_slots=forecast.place_payout_slots,
                probabilities=forecast.probabilities,
                candidates=tuple(candidates),
            )

    def test_snapshot_round_trip_is_integrity_protected_and_has_no_stake(self) -> None:
        original = snapshot()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bet-types-shadow.json"
            digest = save_frozen_bet_type_forecast(original, path)
            contents = path.read_text(encoding="utf-8")
            loaded = load_frozen_bet_type_forecast(path)

        self.assertEqual(len(digest), 64)
        self.assertEqual(loaded, original)
        self.assertNotIn("stake_yen", contents)

    def test_snapshot_cannot_be_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bet-types-shadow.json"
            save_frozen_bet_type_forecast(snapshot(), path)
            with self.assertRaises(FileExistsError):
                save_frozen_bet_type_forecast(snapshot(), path)

    def test_modified_snapshot_fails_integrity_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bet-types-shadow.json"
            save_frozen_bet_type_forecast(snapshot(), path)
            envelope = json.loads(path.read_text(encoding="utf-8"))
            envelope["payload"]["candidates"][0]["selection"] = ["tampered"]
            path.write_text(json.dumps(envelope), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "integrity check failed"):
                load_frozen_bet_type_forecast(path)

    def test_source_prediction_must_exist_before_freeze(self) -> None:
        future = tuple(
            PredictionRecord(
                row.race_id,
                row.horse_id,
                FROZEN_AT + timedelta(seconds=1),
                row.model_version,
                row.win_probability,
                row.top3_probability,
                row.predicted_rank,
            )
            for row in predictions()
        )

        with self.assertRaisesRegex(ValueError, "later than frozen_at"):
            freeze_bet_type_forecast(
                future,
                scheduled_at=SCHEDULED_AT,
                frozen_at=FROZEN_AT,
                phase=PredictionPhase.PRE_ODDS,
                input_data_version="sha256:input-v1",
            )


if __name__ == "__main__":
    unittest.main()
