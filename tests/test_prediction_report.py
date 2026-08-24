import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from keiba_prediction_lab.cli import main
from keiba_prediction_lab.prediction_report import (
    build_prediction_bundle_markdown,
)
from tests.test_bundle_audit import _saved_bundle


class PredictionBundleReportTest(unittest.TestCase):
    def test_report_distinguishes_actual_ticket_from_all_shadows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = _saved_bundle(Path(directory))

            markdown = build_prediction_bundle_markdown(output)

        self.assertIn("実購入候補は三連単1点100円だけ", markdown)
        self.assertIn("| 三連単 | horse-1 → horse-2 → horse-3 | 100円 |", markdown)
        self.assertIn("## 1着予測順位", markdown)
        self.assertIn("### 基準モデル", markdown)
        self.assertIn("### ペース条件付きモデル", markdown)
        self.assertIn("| 単一1着固定 | 1 |", markdown)
        self.assertIn("| 複数1着シナリオ | 10 |", markdown)
        self.assertIn("## 全6馬券種の影予測（購入しない）", markdown)
        self.assertEqual(markdown.count("| 0円 |"), 22)

    def test_cli_saves_report_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _saved_bundle(root)
            report_path = root / "report.md"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "report-prediction-bundle", str(prediction),
                    "--output", str(report_path),
                ])
            summary = json.loads(stdout.getvalue())

            second_stdout = io.StringIO()
            with contextlib.redirect_stdout(second_stdout):
                second_exit_code = main([
                    "report-prediction-bundle", str(prediction),
                    "--output", str(report_path),
                ])
            report_content = report_path.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertEqual(summary["output"], str(report_path))
        self.assertIn("# レース予測レポート", report_content)
        self.assertEqual(second_exit_code, 1)
        self.assertFalse(json.loads(second_stdout.getvalue())["is_valid"])

    def test_cli_rejects_tampered_bundle_before_rendering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = _saved_bundle(Path(directory))
            (output / "actual.json").write_text("{}", encoding="utf-8")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "report-prediction-bundle", str(output),
                ])

        self.assertEqual(exit_code, 1)
        self.assertFalse(json.loads(stdout.getvalue())["is_valid"])


if __name__ == "__main__":
    unittest.main()
