import unittest

from keiba_prediction_lab.domain import BetType, TicketResult
from keiba_prediction_lab.evaluation import (
    BetTypeEvaluationReport,
    BetTypeSummary,
    evaluate_fixed_stake,
    evaluate_ticket_results_by_bet_type,
)


class FixedStakeEvaluationTest(unittest.TestCase):
    def test_fixed_stake_summary_separates_accuracy_and_return(self) -> None:
        summary = evaluate_fixed_stake([0, 270, 0, 1650])

        self.assertEqual(summary.tickets, 4)
        self.assertEqual(summary.hits, 2)
        self.assertEqual(summary.total_stake_yen, 400)
        self.assertEqual(summary.total_return_yen, 1920)
        self.assertAlmostEqual(summary.hit_rate, 0.5)
        self.assertAlmostEqual(summary.return_rate, 4.8)
        self.assertAlmostEqual(summary.return_rate_without_largest_hit, 0.675)
        self.assertAlmostEqual(summary.largest_hit_share, 1650 / 1920)
        self.assertAlmostEqual(summary.top3_hit_share, 1.0)
        self.assertAlmostEqual(summary.top5_hit_share, 1.0)

    def test_stake_cannot_be_optimized(self) -> None:
        with self.assertRaisesRegex(ValueError, "fixed at 100 yen"):
            evaluate_fixed_stake([200], stake_per_ticket_yen=200)

    def test_empty_evaluation_is_well_defined(self) -> None:
        summary = evaluate_fixed_stake([])

        self.assertEqual(summary.tickets, 0)
        self.assertEqual(summary.hit_rate, 0.0)
        self.assertEqual(summary.return_rate, 0.0)
        self.assertEqual(summary.largest_hit_share, 0.0)


class BetTypeEvaluationTest(unittest.TestCase):
    def test_summarizes_every_bet_type_independently(self) -> None:
        tickets = (
            TicketResult("race-1", BetType.WIN, ("horse-1",), 250),
            TicketResult("race-2", BetType.WIN, ("horse-2",), 0),
            TicketResult("race-1", BetType.PLACE, ("horse-1",), 140),
            TicketResult("race-1", BetType.QUINELLA, ("horse-1", "horse-2"), 0),
            TicketResult("race-1", BetType.EXACTA, ("horse-1", "horse-2"), 620),
            TicketResult(
                "race-1", BetType.TRIO, ("horse-1", "horse-2", "horse-3"), 0
            ),
            TicketResult(
                "race-1", BetType.TRIFECTA,
                ("horse-1", "horse-2", "horse-3"), 3150,
            ),
        )

        report = evaluate_ticket_results_by_bet_type(tickets)

        self.assertIsInstance(report, BetTypeEvaluationReport)
        self.assertEqual(len(report.summaries), len(BetType))
        win = report.for_bet_type(BetType.WIN)
        self.assertEqual(win.tickets, 2)
        self.assertEqual(win.hits, 1)
        self.assertAlmostEqual(win.hit_rate, 0.5)
        self.assertAlmostEqual(win.return_rate, 1.25)
        self.assertEqual(report.for_bet_type(BetType.TRIFECTA).total_return_yen, 3150)

    def test_reports_largest_hit_sensitivity_within_each_bet_type(self) -> None:
        tickets = (
            TicketResult(
                "race-1", BetType.TRIFECTA,
                ("horse-1", "horse-2", "horse-3"), 1000,
            ),
            TicketResult(
                "race-2", BetType.TRIFECTA,
                ("horse-4", "horse-5", "horse-6"), 3000,
            ),
            TicketResult(
                "race-3", BetType.TRIFECTA,
                ("horse-7", "horse-8", "horse-9"), 0,
            ),
        )

        summary = evaluate_ticket_results_by_bet_type(tickets).for_bet_type(
            BetType.TRIFECTA
        )

        self.assertAlmostEqual(summary.return_rate, 4000 / 300)
        self.assertAlmostEqual(summary.return_rate_without_largest_hit, 1000 / 300)
        self.assertAlmostEqual(summary.largest_hit_share, 0.75)

    def test_rejects_reordered_duplicate_unordered_ticket(self) -> None:
        tickets = (
            TicketResult("race-1", BetType.QUINELLA, ("horse-1", "horse-2"), 0),
            TicketResult("race-1", BetType.QUINELLA, ("horse-2", "horse-1"), 0),
        )

        with self.assertRaisesRegex(ValueError, "duplicate ticket"):
            evaluate_ticket_results_by_bet_type(tickets)

    def test_preserves_order_for_ordered_ticket_types(self) -> None:
        tickets = (
            TicketResult("race-1", BetType.EXACTA, ("horse-1", "horse-2"), 0),
            TicketResult("race-1", BetType.EXACTA, ("horse-2", "horse-1"), 510),
        )

        summary = evaluate_ticket_results_by_bet_type(tickets).for_bet_type(
            BetType.EXACTA
        )

        self.assertEqual(summary.tickets, 2)
        self.assertEqual(summary.hits, 1)

    def test_empty_report_contains_zero_row_for_every_bet_type(self) -> None:
        report = evaluate_ticket_results_by_bet_type(())

        self.assertTrue(all(row.fixed_stake.tickets == 0 for row in report.summaries))
        markdown = report.to_markdown()
        for label in ("単勝", "複勝", "馬連", "馬単", "3連複", "3連単"):
            self.assertIn(f"| {label} |", markdown)
        self.assertIn("最高払戻除外後", markdown)
        self.assertIn("上位3件", markdown)
        self.assertIn("上位5件", markdown)

    def test_report_rejects_raw_string_bet_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be BetType values"):
            BetTypeEvaluationReport((
                BetTypeSummary("win", evaluate_fixed_stake(())),  # type: ignore[arg-type]
            ))


if __name__ == "__main__":
    unittest.main()
