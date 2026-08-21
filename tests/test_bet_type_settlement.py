import unittest
from datetime import datetime, timedelta, timezone

from keiba_prediction_lab.bet_type_forecast import freeze_bet_type_forecast
from keiba_prediction_lab.bet_type_settlement import (
    BetTypePayout,
    BetTypeRacePayouts,
    evaluate_frozen_bet_type_candidates,
    settle_frozen_bet_type_candidates,
)
from keiba_prediction_lab.domain import BetType, PredictionRecord
from keiba_prediction_lab.frozen import PredictionPhase


UTC = timezone.utc
PREDICTED_AT = datetime(2026, 8, 22, 5, 0, tzinfo=UTC)
FROZEN_AT = PREDICTED_AT + timedelta(minutes=10)
SCHEDULED_AT = FROZEN_AT + timedelta(hours=2)


def snapshot(race_id: str):
    win = (0.40, 0.35, 0.15, 0.07, 0.03)
    top3 = (0.90, 0.80, 0.60, 0.45, 0.25)
    predictions = tuple(
        PredictionRecord(
            race_id,
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
    return freeze_bet_type_forecast(
        predictions,
        scheduled_at=SCHEDULED_AT,
        frozen_at=FROZEN_AT,
        phase=PredictionPhase.PRE_ODDS,
        input_data_version=f"sha256:{race_id}",
    )


def payout_table(
    frozen,
    *,
    hit_types: frozenset[BetType] = frozenset(),
) -> BetTypeRacePayouts:
    rows = []
    payout_amounts = {
        BetType.WIN: 250,
        BetType.PLACE: 140,
        BetType.QUINELLA: 780,
        BetType.EXACTA: 1320,
        BetType.TRIO: 910,
        BetType.TRIFECTA: 4650,
    }
    for bet_type in BetType:
        table = frozen.forecast.for_bet_type(bet_type)
        candidate = frozen.forecast.candidate_for(bet_type)
        if bet_type in hit_types:
            winning = candidate
        else:
            winning = next(row for row in table if row.selection != candidate.selection)
        rows.append(BetTypePayout(
            frozen.forecast.race_id,
            bet_type,
            winning.selection,
            payout_amounts[bet_type],
        ))
        if bet_type is BetType.PLACE:
            second_place = next(
                row for row in table
                if row.selection not in {winning.selection, candidate.selection}
            )
            rows.append(BetTypePayout(
                frozen.forecast.race_id,
                bet_type,
                second_place.selection,
                180,
            ))
    return BetTypeRacePayouts(frozen.forecast.race_id, tuple(rows))


class BetTypeSettlementTest(unittest.TestCase):
    def test_settles_one_candidate_per_bet_type_at_fixed_100_yen(self) -> None:
        frozen = snapshot("race-1")
        results = payout_table(
            frozen,
            hit_types=frozenset((
                BetType.WIN,
                BetType.PLACE,
                BetType.QUINELLA,
                BetType.TRIO,
            )),
        )

        tickets = settle_frozen_bet_type_candidates(frozen, results)

        self.assertEqual(tuple(row.bet_type for row in tickets), tuple(BetType))
        self.assertTrue(all(row.stake_yen == 100 for row in tickets))
        self.assertEqual(tickets[0].payout_yen, 250)
        self.assertEqual(tickets[1].payout_yen, 140)
        self.assertEqual(tickets[2].payout_yen, 780)
        self.assertEqual(tickets[3].payout_yen, 0)
        self.assertEqual(tickets[4].payout_yen, 910)
        self.assertEqual(tickets[5].payout_yen, 0)

    def test_batch_evaluation_keeps_each_bet_type_separate(self) -> None:
        first = snapshot("race-1")
        second = snapshot("race-2")

        report = evaluate_frozen_bet_type_candidates(
            (first, second),
            (
                payout_table(first, hit_types=frozenset((BetType.WIN,))),
                payout_table(second, hit_types=frozenset((BetType.EXACTA,))),
            ),
        )

        win = report.for_bet_type(BetType.WIN)
        exacta = report.for_bet_type(BetType.EXACTA)
        trifecta = report.for_bet_type(BetType.TRIFECTA)
        self.assertEqual(win.tickets, 2)
        self.assertEqual(win.hits, 1)
        self.assertEqual(win.total_stake_yen, 200)
        self.assertEqual(exacta.hits, 1)
        self.assertEqual(trifecta.hits, 0)
        self.assertIn("馬券種をまたいだ回収率は算出しない", report.to_markdown())

    def test_allows_multiple_winning_payouts_for_dead_heats(self) -> None:
        frozen = snapshot("race-1")
        result = payout_table(frozen, hit_types=frozenset((BetType.WIN,)))
        extra_win = next(
            row for row in frozen.forecast.for_bet_type(BetType.WIN)
            if row.selection != frozen.forecast.candidate_for(BetType.WIN).selection
        )
        result = BetTypeRacePayouts(
            result.race_id,
            result.payouts + (
                BetTypePayout(
                    result.race_id,
                    BetType.WIN,
                    extra_win.selection,
                    330,
                ),
            ),
        )

        tickets = settle_frozen_bet_type_candidates(frozen, result)

        self.assertEqual(tickets[0].payout_yen, 250)

    def test_rejects_noncanonical_and_duplicate_payouts(self) -> None:
        with self.assertRaisesRegex(ValueError, "selection must be a tuple"):
            BetTypePayout(
                "race-1",
                BetType.WIN,
                ["horse-1"],  # type: ignore[arg-type]
                250,
            )
        with self.assertRaisesRegex(ValueError, "canonical order"):
            BetTypePayout(
                "race-1",
                BetType.QUINELLA,
                ("horse-2", "horse-1"),
                500,
            )
        row = BetTypePayout("race-1", BetType.WIN, ("horse-1",), 250)
        with self.assertRaisesRegex(ValueError, "must be unique"):
            BetTypeRacePayouts("race-1", (row, row))

    def test_rejects_mutable_payout_container(self) -> None:
        complete = payout_table(snapshot("race-1"))

        with self.assertRaisesRegex(ValueError, "payouts must be a tuple"):
            BetTypeRacePayouts(
                complete.race_id,
                list(complete.payouts),  # type: ignore[arg-type]
            )

    def test_rejects_incomplete_or_invalid_payout_tables(self) -> None:
        frozen = snapshot("race-1")
        complete = payout_table(frozen)
        without_trifecta = tuple(
            row for row in complete.payouts
            if row.bet_type is not BetType.TRIFECTA
        )

        with self.assertRaisesRegex(ValueError, "every supported bet type"):
            BetTypeRacePayouts(complete.race_id, without_trifecta)

        one_place = BetTypeRacePayouts(
            complete.race_id,
            tuple(
                row for row in complete.payouts
                if row.bet_type is not BetType.PLACE
            ) + (next(
                row for row in complete.payouts
                if row.bet_type is BetType.PLACE
            ),),
        )
        with self.assertRaisesRegex(ValueError, "every payout slot"):
            settle_frozen_bet_type_candidates(frozen, one_place)

    def test_rejects_unknown_selection_and_mismatched_race(self) -> None:
        frozen = snapshot("race-1")
        complete = payout_table(frozen)
        unknown = BetTypePayout(
            "race-1", BetType.WIN, ("not-in-forecast",), 250
        )
        tampered = BetTypeRacePayouts(
            "race-1",
            (unknown,) + tuple(
                row for row in complete.payouts if row.bet_type is not BetType.WIN
            ),
        )

        with self.assertRaisesRegex(ValueError, "frozen probability tables"):
            settle_frozen_bet_type_candidates(frozen, tampered)
        with self.assertRaisesRegex(ValueError, "same race_id"):
            settle_frozen_bet_type_candidates(
                snapshot("race-2"), complete
            )

    def test_batch_requires_unique_identical_races(self) -> None:
        frozen = snapshot("race-1")
        result = payout_table(frozen)

        with self.assertRaisesRegex(ValueError, "unique race_id"):
            evaluate_frozen_bet_type_candidates((frozen, frozen), (result,))
        with self.assertRaisesRegex(ValueError, "identical races"):
            evaluate_frozen_bet_type_candidates(
                (frozen,), (payout_table(snapshot("race-2")),)
            )


if __name__ == "__main__":
    unittest.main()
