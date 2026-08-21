import unittest
from dataclasses import replace
from datetime import date

from keiba_prediction_lab.bet_type_bootstrap import (
    BootstrapResamplingUnit,
    bootstrap_bet_type_evaluation_artifacts,
)
from keiba_prediction_lab.bet_type_report import (
    BetTypeEvaluationArtifact,
    BetTypeEvaluationInput,
)
from keiba_prediction_lab.domain import BetType, TicketResult
from keiba_prediction_lab.evaluation import evaluate_ticket_results_by_bet_type


RACE_IDS = ("race-1", "race-2")


def artifact(
    forecast_hash: str,
    hits: frozenset[tuple[str, BetType]],
    *,
    race_ids: tuple[str, ...] = RACE_IDS,
    race_dates: tuple[date, ...] | None = None,
) -> BetTypeEvaluationArtifact:
    dates = race_dates or tuple(
        date(2026, 8, 22 + index) for index in range(len(race_ids))
    )
    inputs = tuple(
        BetTypeEvaluationInput(
            race_id,
            forecast_file_sha256=forecast_hash * 64,
            payout_file_sha256=f"{index + 1:x}" * 64,
            race_date=race_date,
        )
        for index, (race_id, race_date) in enumerate(zip(race_ids, dates))
    )
    tickets = tuple(
        TicketResult(
            race_id,
            bet_type,
            tuple(f"horse-{index}" for index in range(bet_type.selection_size)),
            payout_yen=(500 if (race_id, bet_type) in hits else 0),
        )
        for race_id in race_ids
        for bet_type in BetType
    )
    return BetTypeEvaluationArtifact(
        inputs,
        evaluate_ticket_results_by_bet_type(tickets),
        tickets,
    )


class BetTypeBootstrapTest(unittest.TestCase):
    def test_all_race_improvement_has_exact_positive_interval(self) -> None:
        baseline = artifact("a", frozenset())
        candidate = artifact(
            "b",
            frozenset(
                (race_id, bet_type)
                for race_id in RACE_IDS
                for bet_type in BetType
            ),
        )

        report = bootstrap_bet_type_evaluation_artifacts(
            baseline, candidate, samples=200, seed=11
        )

        win = report.for_bet_type(BetType.WIN)
        self.assertEqual(win.hit_rate.point_estimate, 1.0)
        self.assertEqual(win.hit_rate.lower, 1.0)
        self.assertEqual(win.hit_rate.upper, 1.0)
        self.assertEqual(win.hit_rate.probability_candidate_better, 1.0)
        self.assertEqual(win.return_rate.point_estimate, 5.0)
        self.assertIn("標本不足", report.to_markdown())
        self.assertIn("有意差判定ではない", report.to_markdown())
        self.assertIn("多重比較は補正していない", report.to_markdown())

    def test_race_date_clusters_resample_same_day_races_together(self) -> None:
        race_ids = ("race-1", "race-2", "race-3", "race-4")
        first_day = date(2026, 8, 22)
        second_day = date(2026, 8, 23)
        race_dates = (first_day, first_day, second_day, second_day)
        baseline = artifact(
            "a",
            frozenset((race_id, BetType.WIN) for race_id in race_ids[:2]),
            race_ids=race_ids,
            race_dates=race_dates,
        )
        candidate = artifact(
            "b",
            frozenset((race_id, BetType.WIN) for race_id in race_ids[2:]),
            race_ids=race_ids,
            race_dates=race_dates,
        )

        report = bootstrap_bet_type_evaluation_artifacts(
            baseline,
            candidate,
            samples=1_000,
            seed=7,
            resampling_unit=BootstrapResamplingUnit.RACE_DATE,
        )

        win = report.for_bet_type(BetType.WIN)
        self.assertEqual(report.cluster_count, 2)
        self.assertEqual(win.hit_rate.point_estimate, 0.0)
        self.assertEqual(win.hit_rate.lower, -1.0)
        self.assertEqual(win.hit_rate.upper, 1.0)
        self.assertIn("同日の全レースを一塊", report.to_markdown())

    def test_fixed_seed_is_reproducible_for_mixed_race_effects(self) -> None:
        baseline = artifact(
            "a", frozenset((("race-1", BetType.WIN),))
        )
        candidate = artifact(
            "b", frozenset((("race-2", BetType.WIN),))
        )

        first = bootstrap_bet_type_evaluation_artifacts(
            baseline, candidate, samples=500, seed=23
        )
        second = bootstrap_bet_type_evaluation_artifacts(
            baseline, candidate, samples=500, seed=23
        )

        self.assertEqual(first, second)
        win = first.for_bet_type(BetType.WIN)
        self.assertEqual(win.hit_rate.point_estimate, 0.0)
        self.assertEqual(win.hit_rate.lower, -1.0)
        self.assertEqual(win.hit_rate.upper, 1.0)

    def test_requires_ticket_ledgers_and_enough_resamples(self) -> None:
        baseline = artifact("a", frozenset())
        candidate = artifact("b", frozenset())

        with self.assertRaisesRegex(ValueError, "ticket ledgers"):
            bootstrap_bet_type_evaluation_artifacts(
                replace(baseline, tickets=()), candidate, samples=100
            )
        with self.assertRaisesRegex(ValueError, "at least 100"):
            bootstrap_bet_type_evaluation_artifacts(
                baseline, candidate, samples=99
            )
        without_date = replace(
            baseline,
            inputs=tuple(replace(row, race_date=None) for row in baseline.inputs),
        )
        with self.assertRaisesRegex(ValueError, "race_date"):
            bootstrap_bet_type_evaluation_artifacts(
                without_date,
                candidate,
                samples=100,
                resampling_unit=BootstrapResamplingUnit.RACE_DATE,
            )
        same_day = replace(
            baseline,
            inputs=tuple(
                replace(row, race_date=date(2026, 8, 22))
                for row in baseline.inputs
            ),
        )
        same_day_candidate = replace(
            candidate,
            inputs=tuple(
                replace(row, race_date=date(2026, 8, 22))
                for row in candidate.inputs
            ),
        )
        with self.assertRaisesRegex(ValueError, "two resampling clusters"):
            bootstrap_bet_type_evaluation_artifacts(
                same_day,
                same_day_candidate,
                samples=100,
                resampling_unit=BootstrapResamplingUnit.RACE_DATE,
            )
        with self.assertRaisesRegex(ValueError, "resampling_unit is invalid"):
            bootstrap_bet_type_evaluation_artifacts(
                baseline,
                candidate,
                samples=100,
                resampling_unit="race-date",  # type: ignore[arg-type]
            )

        baseline_tickets = baseline.tickets[:len(BetType)]
        candidate_tickets = candidate.tickets[:len(BetType)]
        one_race_baseline = BetTypeEvaluationArtifact(
            baseline.inputs[:1],
            evaluate_ticket_results_by_bet_type(baseline_tickets),
            baseline_tickets,
        )
        one_race_candidate = BetTypeEvaluationArtifact(
            candidate.inputs[:1],
            evaluate_ticket_results_by_bet_type(candidate_tickets),
            candidate_tickets,
        )
        with self.assertRaisesRegex(ValueError, "at least two paired races"):
            bootstrap_bet_type_evaluation_artifacts(
                one_race_baseline, one_race_candidate, samples=100
            )


if __name__ == "__main__":
    unittest.main()
