import unittest
from dataclasses import replace

from keiba_prediction_lab.bet_type_report import (
    BetTypeEvaluationArtifact,
    BetTypeEvaluationInput,
)
from keiba_prediction_lab.bet_type_report_comparison import (
    compare_bet_type_evaluation_artifacts,
)
from keiba_prediction_lab.domain import BetType, TicketResult
from keiba_prediction_lab.evaluation import evaluate_ticket_results_by_bet_type


RACE_IDS = ("race-1", "race-2")


def evaluation_artifact(
    forecast_hash_character: str,
    hits: frozenset[tuple[str, BetType]],
) -> BetTypeEvaluationArtifact:
    inputs = tuple(
        BetTypeEvaluationInput(
            race_id,
            forecast_file_sha256=forecast_hash_character * 64,
            payout_file_sha256=("c" if race_id == "race-1" else "d") * 64,
        )
        for race_id in RACE_IDS
    )
    tickets = tuple(
        TicketResult(
            race_id,
            bet_type,
            tuple(f"horse-{index}" for index in range(bet_type.selection_size)),
            payout_yen=(
                500 + tuple(BetType).index(bet_type) * 100
                if (race_id, bet_type) in hits
                else 0
            ),
        )
        for race_id in RACE_IDS
        for bet_type in BetType
    )
    return BetTypeEvaluationArtifact(
        inputs,
        evaluate_ticket_results_by_bet_type(tickets),
        tickets,
    )


class BetTypeReportComparisonTest(unittest.TestCase):
    def test_compares_candidate_minus_baseline_by_bet_type(self) -> None:
        baseline = evaluation_artifact(
            "a", frozenset((("race-1", BetType.WIN),))
        )
        candidate = evaluation_artifact(
            "b",
            frozenset((
                ("race-1", BetType.WIN),
                ("race-2", BetType.WIN),
                ("race-2", BetType.EXACTA),
            )),
        )

        comparison = compare_bet_type_evaluation_artifacts(
            baseline, candidate
        )

        win = comparison.for_bet_type(BetType.WIN)
        exacta = comparison.for_bet_type(BetType.EXACTA)
        self.assertEqual(comparison.race_ids, RACE_IDS)
        self.assertEqual(win.hit_rate_delta, 0.5)
        self.assertEqual(win.return_rate_delta, 2.5)
        self.assertEqual(exacta.hit_rate_delta, 0.5)
        self.assertIn("候補−基準", comparison.to_markdown())
        self.assertIn(
            "| 単勝 | 1/2 → 2/2 | +50.0pt | +250.0pt |",
            comparison.to_markdown(),
        )

    def test_rejects_different_races_or_payout_files(self) -> None:
        baseline = evaluation_artifact("a", frozenset())
        candidate = evaluation_artifact("b", frozenset())
        different_races = replace(
            candidate,
            inputs=(
                replace(candidate.inputs[0], race_id="race-0"),
                candidate.inputs[1],
            ),
            tickets=(),
        )
        with self.assertRaisesRegex(ValueError, "identical race_ids"):
            compare_bet_type_evaluation_artifacts(baseline, different_races)

        different_payout = replace(
            candidate,
            inputs=(
                replace(candidate.inputs[0], payout_file_sha256="e" * 64),
                candidate.inputs[1],
            ),
        )
        with self.assertRaisesRegex(ValueError, "identical payout files"):
            compare_bet_type_evaluation_artifacts(baseline, different_payout)


if __name__ == "__main__":
    unittest.main()
