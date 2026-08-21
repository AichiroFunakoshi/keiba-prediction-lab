import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date
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
            race_date=date(2026, 8, 22 if race_id == "race-1" else 23),
        )
        for race_id in race_ids
    )
    return BetTypeEvaluationArtifact(
        inputs,
        evaluate_ticket_results_by_bet_type(tickets),
        tickets,
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
        self.assertEqual(len(loaded.tickets), 12)
        self.assertEqual(len(digest), 64)

    def test_loads_legacy_1_0_report_without_ticket_ledger(self) -> None:
        original = artifact()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bet-types-evaluation.json"
            save_bet_type_evaluation_artifact(original, path)
            envelope = json.loads(path.read_text(encoding="utf-8"))
            envelope["schema_version"] = "1.0"
            del envelope["payload"]["tickets"]
            canonical = json.dumps(
                envelope["payload"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            envelope["sha256"] = hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest()
            path.write_text(json.dumps(envelope), encoding="utf-8")

            loaded = load_bet_type_evaluation_artifact(path)

        self.assertEqual(
            tuple(
                (
                    row.race_id,
                    row.forecast_file_sha256,
                    row.payout_file_sha256,
                )
                for row in loaded.inputs
            ),
            tuple(
                (
                    row.race_id,
                    row.forecast_file_sha256,
                    row.payout_file_sha256,
                )
                for row in original.inputs
            ),
        )
        self.assertEqual(loaded.report, original.report)
        self.assertEqual(loaded.tickets, ())
        self.assertTrue(all(row.race_date is None for row in loaded.inputs))

    def test_loads_legacy_1_1_report_without_race_dates(self) -> None:
        original = artifact()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bet-types-evaluation.json"
            save_bet_type_evaluation_artifact(original, path)
            envelope = json.loads(path.read_text(encoding="utf-8"))
            envelope["schema_version"] = "1.1"
            for row in envelope["payload"]["inputs"]:
                del row["race_date"]
            canonical = json.dumps(
                envelope["payload"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            envelope["sha256"] = hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest()
            path.write_text(json.dumps(envelope), encoding="utf-8")

            loaded = load_bet_type_evaluation_artifact(path)

        self.assertEqual(loaded.report, original.report)
        self.assertEqual(loaded.tickets, original.tickets)
        self.assertTrue(all(row.race_date is None for row in loaded.inputs))

    def test_ticket_ledger_must_reproduce_summaries(self) -> None:
        original = artifact()
        changed_ticket = replace(original.tickets[0], payout_yen=0)
        with self.assertRaisesRegex(ValueError, "reproduce summaries"):
            BetTypeEvaluationArtifact(
                original.inputs,
                original.report,
                (changed_ticket,) + original.tickets[1:],
            )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.json"
            with self.assertRaisesRegex(ValueError, "must contain a ticket ledger"):
                save_bet_type_evaluation_artifact(
                    replace(original, tickets=()), path
                )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing-date.json"
            inputs = (
                replace(original.inputs[0], race_date=None),
            ) + original.inputs[1:]
            with self.assertRaisesRegex(ValueError, "every race_date"):
                save_bet_type_evaluation_artifact(
                    replace(original, inputs=inputs), path
                )

    def test_ticket_ledger_requires_canonical_unordered_selections(self) -> None:
        original = artifact()
        index = next(
            index
            for index, ticket in enumerate(original.tickets)
            if ticket.bet_type is BetType.QUINELLA
        )
        ticket = original.tickets[index]
        reversed_ticket = replace(
            ticket, selection=tuple(reversed(ticket.selection))
        )
        tickets = (
            original.tickets[:index]
            + (reversed_ticket,)
            + original.tickets[index + 1:]
        )

        with self.assertRaisesRegex(ValueError, "canonical order"):
            BetTypeEvaluationArtifact(original.inputs, original.report, tickets)

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
