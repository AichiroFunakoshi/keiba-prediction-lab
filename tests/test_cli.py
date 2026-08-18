import contextlib
import io
import json
import unittest
from pathlib import Path

from keiba_prediction_lab.cli import main


ROOT = Path(__file__).resolve().parents[1]


class CliTest(unittest.TestCase):
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
