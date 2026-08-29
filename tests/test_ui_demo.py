import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from keiba_prediction_lab.app_snapshot import build_read_only_app_snapshot
from keiba_prediction_lab.cli import main
from keiba_prediction_lab.ui_demo import create_ui_demo, load_ui_demo


class UiDemoTest(unittest.TestCase):
    def test_creates_twelve_race_audited_synthetic_demo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "ui-demo"
            demo = create_ui_demo(output)
            snapshot = build_read_only_app_snapshot(
                race_day_manifest=demo.race_day_manifest,
                walk_forward_report=demo.walk_forward_report,
            )
            notice = (output / "README.txt").read_text(encoding="utf-8")

        self.assertEqual(demo.race_count, 12)
        self.assertIsNotNone(snapshot.race_day)
        self.assertEqual(snapshot.race_day.venues[0].venue, "東京（合成デモ）")
        self.assertEqual(len(snapshot.race_day.venues[0].races), 12)
        self.assertIn("実レースの予想", notice)

    def test_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "ui-demo"
            output.mkdir()
            marker = output / "keep.txt"
            marker.write_text("keep", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                create_ui_demo(output)

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_cli_reports_created_demo_and_loader_reaudits_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "ui-demo"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["init-ui-demo", "--output", str(output)])
            result = json.loads(stdout.getvalue())
            loaded = load_ui_demo(output)

        self.assertEqual(exit_code, 0)
        self.assertTrue(result["synthetic_demo"])
        self.assertEqual(result["race_count"], 12)
        self.assertEqual(loaded.race_count, 12)


if __name__ == "__main__":
    unittest.main()
