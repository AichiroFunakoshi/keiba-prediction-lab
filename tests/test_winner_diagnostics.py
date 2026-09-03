import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from keiba_prediction_lab.bet_type_settlement import save_bet_type_race_payouts
from keiba_prediction_lab.bundle_audit import load_audited_prediction_bundle
from keiba_prediction_lab.cli import main
from keiba_prediction_lab.domain import BetType
from keiba_prediction_lab.winner_diagnostics import diagnose_winner_misses
from tests.test_bet_type_settlement import payout_table
from tests.test_bundle_audit import _saved_bundle


class WinnerDiagnosticsTest(unittest.TestCase):
    def _miss_directory(self, root: Path) -> Path:
        directory = _saved_bundle(root)
        snapshot = load_audited_prediction_bundle(directory).bundle.bet_type_shadow
        save_bet_type_race_payouts(
            payout_table(snapshot), directory / "bet-types-payouts.json"
        )
        return directory

    def test_diagnoses_actual_winner_rank_and_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            race = self._miss_directory(Path(directory))
            report = diagnose_winner_misses((race,))

        self.assertEqual(report.race_count, 1)
        self.assertEqual(report.hits, 0)
        self.assertEqual(report.top1_accuracy, 0.0)
        self.assertEqual(report.top2_coverage, 1.0)
        self.assertEqual(report.races[0].actual_winner_best_rank, 2)
        self.assertEqual(report.races[0].miss_type, "near_miss_rank_2")

    def test_records_top_one_hit_without_calling_it_a_miss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            race = _saved_bundle(root)
            snapshot = load_audited_prediction_bundle(race).bundle.bet_type_shadow
            save_bet_type_race_payouts(
                payout_table(snapshot, hit_types=frozenset((BetType.WIN,))),
                race / "bet-types-payouts.json",
            )
            report = diagnose_winner_misses((race,))

        self.assertEqual(report.hits, 1)
        self.assertEqual(report.top1_accuracy, 1.0)
        self.assertEqual(report.races[0].miss_type, "hit")
        self.assertFalse(report.races[0].high_confidence_miss)

    def test_cli_outputs_machine_readable_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            race = self._miss_directory(Path(directory))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "diagnose-winner-misses", str(race), "--format", "json",
                ])
            payload = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["race_count"], 1)
        self.assertEqual(payload["races"][0]["miss_type"], "near_miss_rank_2")

    def test_rejects_mismatched_result_race(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            race = self._miss_directory(Path(directory))
            path = race / "bet-types-payouts.json"
            envelope = json.loads(path.read_text(encoding="utf-8"))
            envelope["payload"]["race_id"] = "other-race"
            canonical = json.dumps(
                envelope["payload"], ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            )
            envelope["sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
            path.write_text(json.dumps(envelope), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "race_id must match"):
                diagnose_winner_misses((race,))


if __name__ == "__main__":
    unittest.main()
