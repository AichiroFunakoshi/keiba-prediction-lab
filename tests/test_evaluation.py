import unittest

from keiba_prediction_lab.evaluation import evaluate_fixed_stake


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


if __name__ == "__main__":
    unittest.main()
