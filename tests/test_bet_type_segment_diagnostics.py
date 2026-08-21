import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone

from keiba_prediction_lab.bet_type_report import (
    BetTypeEvaluationArtifact,
    BetTypeEvaluationInput,
)
from keiba_prediction_lab.bet_type_segment_diagnostics import (
    BetTypeSegmentReport,
    BetTypeSegmentDimension,
    diagnose_bet_type_segments,
)
from keiba_prediction_lab.domain import BetType, TicketResult
from keiba_prediction_lab.evaluation import evaluate_ticket_results_by_bet_type
from keiba_prediction_lab.features import Surface
from keiba_prediction_lab.race_context import RaceContext


RACE_IDS = ("race-1", "race-2", "race-3", "race-4")
CONTEXTS = (
    RaceContext(
        "race-1", datetime(2026, 8, 22, tzinfo=timezone.utc),
        "Tokyo", Surface.TURF, "good", 1600, "G1", 8,
    ),
    RaceContext(
        "race-2", datetime(2026, 8, 22, tzinfo=timezone.utc),
        "Tokyo", Surface.DIRT, "muddy", 1400, "G2", 10,
    ),
    RaceContext(
        "race-3", datetime(2026, 8, 23, tzinfo=timezone.utc),
        "Kyoto", Surface.TURF, "good", 2000, "G1", 14,
    ),
    RaceContext(
        "race-4", datetime(2026, 8, 23, tzinfo=timezone.utc),
        "Kyoto", Surface.TURF, "soft", 2600, "G3", 16,
    ),
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
            date(2026, 8, 22 + index // 2),
            f"{index + 5:x}" * 64,
            context,
        )
        for index, (race_id, context) in enumerate(zip(RACE_IDS, CONTEXTS))
    )
    tickets = tuple(
        TicketResult(
            race_id,
            bet_type,
            tuple(f"horse-{index}" for index in range(bet_type.selection_size)),
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


class BetTypeSegmentDiagnosticsTest(unittest.TestCase):
    def test_groups_paired_results_by_every_fixed_context_dimension(self) -> None:
        baseline = artifact(
            "a",
            {
                ("race-1", BetType.WIN): 500,
                ("race-3", BetType.WIN): 300,
            },
        )
        candidate = artifact(
            "b",
            {
                ("race-2", BetType.WIN): 700,
                ("race-3", BetType.WIN): 400,
            },
        )

        report = diagnose_bet_type_segments(baseline, candidate)

        tokyo_win = next(
            row
            for row in report.rows
            if row.dimension is BetTypeSegmentDimension.VENUE
            and row.value == "Tokyo"
            and row.bet_type is BetType.WIN
        )
        self.assertEqual(tokyo_win.race_count, 2)
        self.assertEqual(tokyo_win.hit_delta, 0)
        self.assertEqual(tokyo_win.return_delta_yen, 200)
        self.assertEqual(tokyo_win.return_rate_delta, 1.0)
        self.assertEqual(
            {row.dimension for row in report.rows},
            set(BetTypeSegmentDimension),
        )
        self.assertIn("多重比較を補正していない", report.to_markdown())

        payload = json.loads(report.to_json())
        self.assertEqual(payload["race_count"], 4)
        self.assertTrue(any(
            row["dimension"] == "field_size"
            and row["value"] == "large-13-plus"
            for row in payload["rows"]
        ))

    def test_requires_context_and_rejects_mismatched_context_hashes(self) -> None:
        baseline = artifact("a", {})
        candidate = artifact("b", {})
        missing = replace(
            baseline,
            inputs=(
                replace(
                    baseline.inputs[0],
                    context_file_sha256=None,
                    context=None,
                ),
            ) + baseline.inputs[1:],
        )
        with self.assertRaisesRegex(ValueError, "schema 1.3"):
            diagnose_bet_type_segments(missing, candidate)

        mismatched = replace(
            candidate,
            inputs=(
                replace(candidate.inputs[0], context_file_sha256="f" * 64),
            ) + candidate.inputs[1:],
        )
        with self.assertRaisesRegex(ValueError, "identical race context"):
            diagnose_bet_type_segments(baseline, mismatched)

    def test_report_rejects_inconsistent_dimension_totals(self) -> None:
        report = diagnose_bet_type_segments(artifact("a", {}), artifact("b", {}))
        changed = replace(report.rows[-1], candidate_return_yen=100)
        with self.assertRaisesRegex(ValueError, "identical totals"):
            BetTypeSegmentReport(
                report.race_count,
                report.rows[:-1] + (changed,),
            )


if __name__ == "__main__":
    unittest.main()
