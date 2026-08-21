import json
import unittest
from dataclasses import replace
from datetime import date

from keiba_prediction_lab.bet_type_diagnostics import (
    BetTypeContributionReport,
    HitTransition,
    diagnose_bet_type_evaluation_artifacts,
)
from keiba_prediction_lab.bet_type_report import (
    BetTypeEvaluationArtifact,
    BetTypeEvaluationInput,
)
from keiba_prediction_lab.domain import BetType, TicketResult
from keiba_prediction_lab.evaluation import evaluate_ticket_results_by_bet_type


RACE_IDS = ("race-1", "race-2", "race-3", "race-4")
RACE_DATES = (
    date(2026, 8, 22),
    date(2026, 8, 22),
    date(2026, 8, 23),
    date(2026, 8, 23),
)


def artifact(
    forecast_hash: str,
    payouts: dict[tuple[str, BetType], int],
) -> BetTypeEvaluationArtifact:
    inputs = tuple(
        BetTypeEvaluationInput(
            race_id,
            forecast_hash * 64,
            f"{index + 1:x}" * 64,
            race_date,
        )
        for index, (race_id, race_date) in enumerate(
            zip(RACE_IDS, RACE_DATES)
        )
    )
    tickets = tuple(
        TicketResult(
            race_id,
            bet_type,
            tuple(
                f"{forecast_hash}-horse-{index}"
                for index in range(bet_type.selection_size)
            ),
            payouts.get((race_id, bet_type), 0),
        )
        for race_id in RACE_IDS
        for bet_type in BetType
    )
    return BetTypeEvaluationArtifact(
        inputs,
        evaluate_ticket_results_by_bet_type(tickets),
        tickets,
    )


class BetTypeDiagnosticsTest(unittest.TestCase):
    def test_locates_race_and_date_contributions_by_bet_type(self) -> None:
        baseline = artifact(
            "a",
            {
                ("race-1", BetType.WIN): 500,
                ("race-3", BetType.WIN): 300,
                ("race-3", BetType.EXACTA): 800,
            },
        )
        candidate = artifact(
            "b",
            {
                ("race-2", BetType.WIN): 700,
                ("race-3", BetType.WIN): 400,
                ("race-4", BetType.EXACTA): 1_200,
            },
        )

        report = diagnose_bet_type_evaluation_artifacts(baseline, candidate)

        self.assertEqual(len(report.race_rows), len(RACE_IDS) * len(BetType))
        self.assertEqual(len(report.date_rows), 2 * len(BetType))
        win_rows = tuple(
            row for row in report.race_rows if row.bet_type is BetType.WIN
        )
        self.assertEqual(
            tuple(row.transition for row in win_rows),
            (
                HitTransition.BASELINE_ONLY,
                HitTransition.CANDIDATE_ONLY,
                HitTransition.BOTH_HIT,
                HitTransition.BOTH_MISS,
            ),
        )
        first_day_win = next(
            row
            for row in report.date_rows
            if row.race_date == RACE_DATES[0]
            and row.bet_type is BetType.WIN
        )
        self.assertEqual(first_day_win.hit_delta, 0)
        self.assertEqual(first_day_win.return_delta_yen, 200)
        markdown = report.to_markdown(top_races=1)
        self.assertIn("原因、統計的有意差", markdown)
        self.assertIn("| 単勝 | 候補側 | race-2 |", markdown)
        self.assertIn("| 単勝 | 基準側 | race-1 |", markdown)

        payload = json.loads(report.to_json())
        self.assertEqual(payload["race_count"], 4)
        self.assertEqual(payload["race_rows"][0]["transition"], "baseline-only")
        self.assertEqual(
            payload["race_rows"][0]["baseline_selection"], ["a-horse-0"]
        )
        self.assertEqual(
            payload["race_rows"][0]["candidate_selection"], ["b-horse-0"]
        )

    def test_rejects_missing_ledgers_dates_and_invalid_display_limit(self) -> None:
        baseline = artifact("a", {})
        candidate = artifact("b", {})

        with self.assertRaisesRegex(ValueError, "ticket ledgers"):
            diagnose_bet_type_evaluation_artifacts(
                replace(baseline, tickets=()), candidate
            )
        without_dates = replace(
            baseline,
            inputs=tuple(replace(row, race_date=None) for row in baseline.inputs),
        )
        with self.assertRaisesRegex(ValueError, "race_date"):
            diagnose_bet_type_evaluation_artifacts(without_dates, candidate)

        report = diagnose_bet_type_evaluation_artifacts(baseline, candidate)
        with self.assertRaisesRegex(ValueError, "at least one"):
            report.to_markdown(top_races=0)

    def test_report_rejects_date_aggregate_that_does_not_reproduce_races(self) -> None:
        report = diagnose_bet_type_evaluation_artifacts(
            artifact("a", {}), artifact("b", {})
        )
        invalid_date = replace(report.date_rows[0], candidate_return_yen=100)
        with self.assertRaisesRegex(ValueError, "reproduce race rows"):
            BetTypeContributionReport(
                report.race_ids,
                report.race_rows,
                (invalid_date,) + report.date_rows[1:],
            )


if __name__ == "__main__":
    unittest.main()
