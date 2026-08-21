import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from keiba_prediction_lab.bet_type_report import (
    BetTypeEvaluationArtifact,
    BetTypeEvaluationInput,
    load_bet_type_evaluation_artifact,
    save_bet_type_evaluation_artifact,
)
from keiba_prediction_lab.domain import BetType, TicketResult
from keiba_prediction_lab.evaluation import evaluate_ticket_results_by_bet_type


def artifact() -> BetTypeEvaluationArtifact:
    race_ids = ("race-1", "race-2")
    tickets = tuple(
        TicketResult(
            race_id,
            bet_type,
            tuple(f"horse-{index}" for index in range(bet_type.selection_size)),
            payout_yen=(250 if race_id == "race-1" and bet_type is BetType.WIN else 0),
        )
        for race_id in race_ids
        for bet_type in BetType
    )
    inputs = tuple(
        BetTypeEvaluationInput(
            race_id,
            forecast_file_sha256=("a" if race_id == "race-1" else "b") * 64,
            payout_file_sha256=("c" if race_id == "race-1" else "d") * 64,
        )
        for race_id in race_ids
    )
    return BetTypeEvaluationArtifact(
        inputs,
        evaluate_ticket_results_by_bet_type(tickets),
    )


class BetTypeReportTest(unittest.TestCase):
    def test_round_trip_preserves_inputs_results_and_markdown(self) -> None:
        original = artifact()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bet-types-evaluation.json"

            digest = save_bet_type_evaluation_artifact(original, path)
            loaded = load_bet_type_evaluation_artifact(path)

        self.assertEqual(loaded, original)
        self.assertEqual(loaded.to_markdown(), original.to_markdown())
        self.assertEqual(len(digest), 64)

    def test_save_does_not_overwrite_and_load_rejects_tampering(self) -> None:
        original = artifact()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bet-types-evaluation.json"
            save_bet_type_evaluation_artifact(original, path)

            with self.assertRaises(FileExistsError):
                save_bet_type_evaluation_artifact(original, path)

            envelope = json.loads(path.read_text(encoding="utf-8"))
            envelope["payload"]["summaries"][0]["hits"] = 2
            path.write_text(json.dumps(envelope), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "integrity check failed"):
                load_bet_type_evaluation_artifact(path)

    def test_requires_deterministic_unique_input_order(self) -> None:
        original = artifact()
        with self.assertRaisesRegex(ValueError, "deterministic race_id order"):
            BetTypeEvaluationArtifact(
                tuple(reversed(original.inputs)), original.report
            )
        with self.assertRaisesRegex(ValueError, "unique race_id"):
            BetTypeEvaluationArtifact(
                (original.inputs[0], original.inputs[0]), original.report
            )

    def test_rejects_internally_inconsistent_summary(self) -> None:
        original = artifact()
        first = original.report.summaries[0]
        invalid = replace(
            original.report,
            summaries=(
                replace(
                    first,
                    fixed_stake=replace(first.fixed_stake, total_stake_yen=201),
                ),
            ) + original.report.summaries[1:],
        )

        with self.assertRaisesRegex(ValueError, "fixed at 100 yen"):
            BetTypeEvaluationArtifact(original.inputs, invalid)


if __name__ == "__main__":
    unittest.main()
