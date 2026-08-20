import unittest
from datetime import datetime, timezone

from keiba_prediction_lab.domain import PredictionRecord
from keiba_prediction_lab.trifecta import (
    TrifectaRaceResult,
    TrifectaStrategy,
    build_trifecta_forecast,
    evaluate_shadow_portfolios,
    rank_trifecta_combinations,
)


NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def predictions(race_id: str = "race-1") -> tuple[PredictionRecord, ...]:
    probabilities = (0.40, 0.35, 0.15, 0.07, 0.03)
    top3_probabilities = (0.90, 0.80, 0.60, 0.45, 0.25)
    return tuple(
        PredictionRecord(
            race_id, f"horse-{index}", NOW, "model-v1", probability,
            top3_probability, index,
        )
        for index, (probability, top3_probability) in enumerate(
            zip(probabilities, top3_probabilities), start=1
        )
    )


class TrifectaForecastTest(unittest.TestCase):
    def test_joint_probabilities_cover_all_ordered_outcomes(self) -> None:
        combinations = rank_trifecta_combinations(predictions())

        self.assertEqual(len(combinations), 5 * 4 * 3)
        self.assertAlmostEqual(sum(row.probability for row in combinations), 1.0)
        self.assertGreaterEqual(
            combinations[0].probability, combinations[-1].probability
        )

    def test_primary_ticket_keeps_predicted_winner_fixed(self) -> None:
        forecast = build_trifecta_forecast(predictions())

        self.assertEqual(forecast.predicted_winner, "horse-1")
        self.assertEqual(forecast.primary_ticket.selection[0], "horse-1")

    def test_portfolios_are_nested_for_each_strategy(self) -> None:
        forecast = build_trifecta_forecast(predictions())

        for strategy in TrifectaStrategy:
            portfolios = [
                row for row in forecast.shadow_portfolios if row.strategy is strategy
            ]
            for smaller, larger in zip(portfolios, portfolios[1:]):
                self.assertLessEqual(
                    {row.selection for row in smaller.combinations},
                    {row.selection for row in larger.combinations},
                )
                self.assertLessEqual(
                    smaller.cumulative_probability, larger.cumulative_probability
                )

    def test_multi_scenario_can_add_an_alternate_winner(self) -> None:
        forecast = build_trifecta_forecast(predictions())
        global_three = next(
            row for row in forecast.shadow_portfolios
            if row.strategy is TrifectaStrategy.MULTI_WINNER_SCENARIO
            and row.ticket_count == 3
        )

        self.assertIn("horse-2", {row.selection[0] for row in global_three.combinations})

    def test_report_counts_added_and_alternate_winner_rescue(self) -> None:
        forecast = build_trifecta_forecast(predictions())
        global_three = next(
            row for row in forecast.shadow_portfolios
            if row.strategy is TrifectaStrategy.MULTI_WINNER_SCENARIO
            and row.ticket_count == 3
        )
        alternate = next(
            row.selection for row in global_three.combinations
            if row.selection[0] != forecast.predicted_winner
        )
        report = evaluate_shadow_portfolios(
            (forecast,), (TrifectaRaceResult("race-1", (alternate,)),)
        )
        row = next(
            row for row in report.rows
            if row.strategy is TrifectaStrategy.MULTI_WINNER_SCENARIO
            and row.ticket_count == 3
        )

        self.assertEqual(row.hits, 1)
        self.assertEqual(row.added_ticket_rescues, 1)
        self.assertEqual(row.alternate_winner_rescues, 1)
        self.assertIn("反実仮想評価", report.to_markdown())

    def test_rejects_ten_point_anchor_portfolio_with_only_three_runners(self) -> None:
        three_runners = tuple(
            PredictionRecord(
                "small-race", f"horse-{index}", NOW, "model-v1",
                probability, 1.0, index,
            )
            for index, probability in enumerate((0.5, 0.3, 0.2), start=1)
        )
        with self.assertRaisesRegex(ValueError, "available anchored combinations"):
            build_trifecta_forecast(three_runners)


if __name__ == "__main__":
    unittest.main()
