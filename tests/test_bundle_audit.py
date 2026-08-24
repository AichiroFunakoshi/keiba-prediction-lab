import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from keiba_prediction_lab.bundle_audit import audit_prediction_bundle
from keiba_prediction_lab.cli import main
from tests.test_local_pipeline import _files


def _saved_bundle(root: Path) -> Path:
    paths = _files(root)
    output = root / "prediction"
    with contextlib.redirect_stdout(io.StringIO()):
        exit_code = main([
            "predict-race", *(str(path) for path in paths),
            "--frozen-at", "2026-02-01T10:05:00+09:00",
            "--output", str(output),
        ])
    if exit_code != 0:
        raise AssertionError("test prediction setup failed")
    return output


class PredictionBundleAuditTest(unittest.TestCase):
    def test_cli_audits_complete_bundle_without_modifying_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = _saved_bundle(Path(directory))
            before = {
                path.name: path.read_bytes() for path in output.iterdir()
            }
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "audit-prediction-bundle", str(output),
                ])
            report = json.loads(stdout.getvalue())
            after = {
                path.name: path.read_bytes() for path in output.iterdir()
            }

        self.assertEqual(exit_code, 0)
        self.assertTrue(report["is_valid"])
        self.assertEqual(report["actual_ticket_count"], 1)
        self.assertEqual(report["actual_stake_yen"], 100)
        self.assertEqual(report["shadow_stake_yen"], 0)
        self.assertEqual(after, before)

    def test_rejects_tampered_actual_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = _saved_bundle(Path(directory))
            path = output / "actual.json"
            envelope = json.loads(path.read_text(encoding="utf-8"))
            envelope["payload"]["predictions"][0]["predicted_rank"] = 99
            path.write_text(json.dumps(envelope), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "integrity check failed"):
                audit_prediction_bundle(output)

    def test_allows_separate_post_race_files_without_changing_prediction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = _saved_bundle(Path(directory))
            (output / "bet-types-payouts.json").write_text(
                "{}\n", encoding="utf-8"
            )

            report = audit_prediction_bundle(output)

        self.assertEqual(report.race_id, "target-1")

    def test_rejects_shadow_file_substitution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = _saved_bundle(Path(directory))
            (output / "baseline-shadow.json").write_bytes(
                (output / "pace-shadow.json").read_bytes()
            )

            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                audit_prediction_bundle(output)

    def test_rejects_changed_manifest_stake(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = _saved_bundle(Path(directory))
            path = output / "manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["actual"]["stake_yen"] = 200
            path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "stake policy"):
                audit_prediction_bundle(output)

    def test_rejects_tampered_input_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = _saved_bundle(Path(directory))
            path = output / "input-provenance.json"
            envelope = json.loads(path.read_text(encoding="utf-8"))
            envelope["payload"]["history_sha256"] = "0" * 64
            path.write_text(json.dumps(envelope), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "integrity check failed"):
                audit_prediction_bundle(output)

    def test_cli_reports_missing_required_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = _saved_bundle(Path(directory))
            (output / "pace-shadow.json").unlink()
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "audit-prediction-bundle", str(output),
                ])
            report = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["is_valid"])
        self.assertIn("pace-shadow.json", report["error"])


if __name__ == "__main__":
    unittest.main()
