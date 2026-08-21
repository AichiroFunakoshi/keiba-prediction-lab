import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from keiba_prediction_lab.bet_type_forecast import (
    freeze_bet_type_forecast,
    save_frozen_bet_type_forecast,
)
from keiba_prediction_lab.bet_type_report import (
    load_bet_type_evaluation_artifact,
)
from keiba_prediction_lab.bet_type_settlement import (
    BetTypePayout,
    BetTypeRacePayouts,
    save_bet_type_race_payouts,
)
from keiba_prediction_lab.cli import main
from keiba_prediction_lab.data_audit import sha256_file
from keiba_prediction_lab.domain import BetType, PredictionRecord
from keiba_prediction_lab.frozen import PredictionPhase


ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def _write_race_bundle(
    directory: Path, race_id: str, hit_type: BetType
) -> None:
    race_day = 22 if race_id == "race-1" else 23
    predicted_at = datetime(2026, 8, race_day, 5, 0, tzinfo=UTC)
    frozen_at = predicted_at + timedelta(minutes=10)
    scheduled_at = frozen_at + timedelta(hours=2)
    predictions = tuple(
        PredictionRecord(
            race_id,
            f"horse-{index}",
            predicted_at,
            "model-v1",
            win_probability,
            top3_probability,
            index,
        )
        for index, (win_probability, top3_probability) in enumerate(
            zip(
                (0.40, 0.35, 0.15, 0.07, 0.03),
                (0.90, 0.80, 0.60, 0.45, 0.25),
            ),
            start=1,
        )
    )
    snapshot = freeze_bet_type_forecast(
        predictions,
        scheduled_at=scheduled_at,
        frozen_at=frozen_at,
        phase=PredictionPhase.PRE_ODDS,
        input_data_version=f"sha256:{race_id}",
    )
    amounts = {
        BetType.WIN: 250,
        BetType.PLACE: 140,
        BetType.QUINELLA: 780,
        BetType.EXACTA: 1320,
        BetType.TRIO: 910,
        BetType.TRIFECTA: 4650,
    }
    rows = []
    for bet_type in BetType:
        table = snapshot.forecast.for_bet_type(bet_type)
        candidate = snapshot.forecast.candidate_for(bet_type)
        winner = (
            candidate
            if bet_type is hit_type
            else next(row for row in table if row.selection != candidate.selection)
        )
        rows.append(BetTypePayout(
            race_id, bet_type, winner.selection, amounts[bet_type]
        ))
        if bet_type is BetType.PLACE:
            second = next(
                row for row in table
                if row.selection not in {winner.selection, candidate.selection}
            )
            rows.append(BetTypePayout(
                race_id, bet_type, second.selection, 180
            ))
    directory.mkdir()
    save_frozen_bet_type_forecast(
        snapshot, directory / "bet-types-shadow.json"
    )
    save_bet_type_race_payouts(
        BetTypeRacePayouts(race_id, tuple(rows)),
        directory / "bet-types-payouts.json",
    )


class CliTest(unittest.TestCase):
    def test_evaluate_bet_types_batches_race_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            first = root / "race-1"
            second = root / "race-2"
            report_path = root / "bet-types-evaluation.json"
            reordered_report_path = root / "bet-types-evaluation-reordered.json"
            _write_race_bundle(first, "race-1", BetType.WIN)
            _write_race_bundle(second, "race-2", BetType.EXACTA)
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                exit_code = main([
                    "evaluate-bet-types",
                    str(second),
                    str(first),
                    "--report",
                    str(report_path),
                ])

            artifact = load_bet_type_evaluation_artifact(report_path)
            first_forecast_hash = sha256_file(
                first / "bet-types-shadow.json"
            )
            first_payout_hash = sha256_file(
                first / "bet-types-payouts.json"
            )
            reordered_output = io.StringIO()
            with contextlib.redirect_stdout(reordered_output):
                reordered_exit_code = main([
                    "evaluate-bet-types",
                    str(first),
                    str(second),
                    "--report",
                    str(reordered_report_path),
                ])
            reports_match = (
                report_path.read_bytes() == reordered_report_path.read_bytes()
            )
            comparison_output = io.StringIO()
            with contextlib.redirect_stdout(comparison_output):
                comparison_exit_code = main([
                    "compare-bet-type-reports",
                    str(report_path),
                    str(reordered_report_path),
                ])
            bootstrap_output = io.StringIO()
            with contextlib.redirect_stdout(bootstrap_output):
                bootstrap_exit_code = main([
                    "bootstrap-bet-type-reports",
                    str(report_path),
                    str(reordered_report_path),
                    "--samples",
                    "100",
                    "--seed",
                    "7",
                    "--resampling-unit",
                    "race-date",
                ])

        self.assertEqual(exit_code, 0)
        self.assertEqual(reordered_exit_code, 0)
        self.assertEqual(comparison_exit_code, 0)
        self.assertEqual(bootstrap_exit_code, 0)
        self.assertTrue(reports_match)
        self.assertEqual(reordered_output.getvalue(), output.getvalue())
        self.assertIn("# 馬券種別・対応比較", comparison_output.getvalue())
        self.assertIn(
            "| 単勝 | 1/2 → 1/2 | +0.0pt | +0.0pt |",
            comparison_output.getvalue(),
        )
        self.assertIn(
            "# 馬券種別・対応クラスターブートストラップ",
            bootstrap_output.getvalue(),
        )
        self.assertIn(
            "再標本化単位は開催日（2日）", bootstrap_output.getvalue()
        )
        self.assertIn("50.0%", bootstrap_output.getvalue())
        markdown = output.getvalue()
        self.assertIn("# 馬券種別・固定100円評価", markdown)
        self.assertEqual(
            tuple(row.race_id for row in artifact.inputs),
            ("race-1", "race-2"),
        )
        self.assertEqual(len(artifact.tickets), 12)
        self.assertEqual(
            tuple(row.race_date.isoformat() for row in artifact.inputs),
            ("2026-08-22", "2026-08-23"),
        )
        self.assertTrue(all(
            len(row.forecast_file_sha256) == 64
            and len(row.payout_file_sha256) == 64
            for row in artifact.inputs
        ))
        self.assertEqual(
            artifact.inputs[0].forecast_file_sha256,
            first_forecast_hash,
        )
        self.assertEqual(
            artifact.inputs[0].payout_file_sha256,
            first_payout_hash,
        )
        self.assertIn("| 単勝 | 2 | 1 | 50.0% | 200円 | 250円 | 125.0% |", markdown)
        self.assertIn(
            "| 馬単 | 2 | 1 | 50.0% | 200円 | 1,320円 | 660.0% |",
            markdown,
        )
        self.assertIn("| 3連単 | 2 | 0 | 0.0% | 200円 | 0円 | 0.0% |", markdown)

    def test_list_sources_outputs_json(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(
                ["list-sources", "--registry", str(ROOT / "data" / "sources.json")]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(payload), 4)

    def test_valid_csv_audit_returns_zero(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(
                [
                    "audit-csv",
                    str(ROOT / "tests" / "fixtures" / "synthetic_race_results.csv"),
                ]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["is_valid"])
        self.assertEqual(payload["row_count"], 3)
