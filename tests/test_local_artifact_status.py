import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from keiba_prediction_lab.bet_type_settlement import save_bet_type_race_payouts
from keiba_prediction_lab.bundle_audit import load_audited_prediction_bundle
from keiba_prediction_lab.cli import main
from keiba_prediction_lab.local_artifact_status import (
    default_local_artifact_roots,
    inspect_local_artifacts,
)
from tests.test_bet_type_settlement import payout_table
from tests.test_bundle_audit import _saved_bundle


class LocalArtifactStatusTest(unittest.TestCase):
    def test_finds_valid_prediction_and_matching_result_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle_parent = root / "keiba-copy"
            bundle_parent.mkdir()
            bundle = _saved_bundle(bundle_parent)
            audited = load_audited_prediction_bundle(bundle)
            save_bet_type_race_payouts(
                payout_table(audited.bundle.bet_type_shadow),
                bundle / "bet-types-payouts.json",
            )
            invalid = root / "old-partial-output"
            invalid.mkdir()
            (invalid / "manifest.json").write_text("{}\n", encoding="utf-8")
            before = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }

            report = inspect_local_artifacts((root,))

            after = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }

        self.assertEqual(report.status, "ready_for_evaluation")
        self.assertEqual(len(report.candidates), 2)
        self.assertEqual(
            sum(row.bundle_status == "valid" for row in report.candidates), 1
        )
        ready = next(
            row for row in report.candidates if row.result_status == "valid"
        )
        self.assertEqual(ready.race_id, "target-1")
        self.assertEqual(before, after)

    def test_prediction_without_result_is_reported_separately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _saved_bundle(root)
            report = inspect_local_artifacts((root,))

        self.assertEqual(report.status, "predictions_found")
        self.assertEqual(report.candidates[0].result_status, "missing")

    def test_missing_root_and_no_candidate_are_not_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing"
            report = inspect_local_artifacts((root, missing))

        self.assertEqual(report.status, "no_candidates")
        self.assertEqual(report.missing_roots, (missing.resolve(),))
        self.assertFalse(report.search_truncated)

    def test_default_roots_do_not_scan_repository_parent_wholesale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "keiba-prediction-lab"
            roots = default_local_artifact_roots(repository)

        self.assertIn(repository.resolve(), roots)
        self.assertNotIn(repository.resolve().parent, roots)

    def test_cli_accepts_explicit_root_and_emits_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _saved_bundle(root)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "local-artifact-status", "--root", str(root),
                ])
            payload = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["is_valid"])
        self.assertEqual(payload["status"], "predictions_found")
        self.assertEqual(payload["valid_bundle_count"], 1)
        self.assertEqual(payload["ready_for_evaluation_count"], 0)


if __name__ == "__main__":
    unittest.main()
