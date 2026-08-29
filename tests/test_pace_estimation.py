import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from keiba_prediction_lab.cli import main
from keiba_prediction_lab.local_adapter import TARGET_COLUMNS
from keiba_prediction_lab.local_pipeline import load_local_pace_profiles, load_local_pace_scenario
from keiba_prediction_lab.pace import ExpectedPace, RunningStyle
from keiba_prediction_lab.pace_estimation import (
    PACE_ESTIMATOR_VERSION,
    PACE_HISTORY_COLUMNS,
    build_automatic_pace_inputs,
    save_automatic_pace_inputs,
)


def _write_csv(path: Path, columns: frozenset[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted(columns))
        writer.writeheader()
        writer.writerows(rows)


def _targets() -> list[dict[str, str]]:
    return [{
        "race_id": "target-1",
        "scheduled_at": "2026-09-01T12:00:00+09:00",
        "observed_at": "2026-09-01T09:00:00+09:00",
        "horse_id": f"horse-{horse}",
        "jockey_id": f"jockey-{horse}",
        "trainer_id": f"trainer-{horse}",
        "venue": "Tokyo", "surface": "turf", "track_condition": "good",
        "distance_m": "1600", "post_position": str(horse),
        "carried_weight_kg": "56", "body_weight_kg": "480",
    } for horse in range(1, 5)]


def _history() -> list[dict[str, str]]:
    rows = []
    for run in range(1, 6):
        for horse in range(1, 5):
            if horse == 1:
                first, final, finish, last = 1, 1, 1, 4
            elif horse == 2:
                first, final, finish, last = 2, 2, 2, 3
            elif horse == 3:
                first, final, finish, last = 3, 3, 3, 2
            else:
                first, final, finish, last = 4, 4, 2, 1
            rows.append({
                "race_id": f"past-{run}",
                "scheduled_at": f"2026-08-{run:02d}T12:00:00+09:00",
                "result_known_at": f"2026-08-{run:02d}T12:20:00+09:00",
                "horse_id": f"horse-{horse}", "field_size": "4",
                "first_corner_position": str(first),
                "final_corner_position": str(final),
                "finish_position": str(finish), "last_3f_rank": str(last),
            })
    return rows


class AutomaticPaceEstimationTest(unittest.TestCase):
    def _files(self, root: Path) -> tuple[Path, Path]:
        history = root / "pace-history.csv"
        targets = root / "targets.csv"
        _write_csv(history, PACE_HISTORY_COLUMNS, _history())
        _write_csv(targets, TARGET_COLUMNS, _targets())
        return history, targets

    def test_derives_profiles_and_fast_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            history, targets = self._files(Path(directory))
            inputs = build_automatic_pace_inputs(history, targets)

        by_horse = {row.horse_id: row for row in inputs.profiles}
        self.assertEqual(by_horse["horse-1"].running_style, RunningStyle.LEADER)
        self.assertEqual(by_horse["horse-4"].running_style, RunningStyle.CLOSER)
        self.assertGreater(by_horse["horse-1"].early_speed, by_horse["horse-4"].early_speed)
        self.assertGreater(by_horse["horse-4"].late_speed, by_horse["horse-1"].late_speed)
        self.assertEqual(inputs.scenario.expected_pace, ExpectedPace.FAST)
        self.assertEqual(inputs.runners_with_history, 4)
        self.assertEqual(inputs.history_rows_used, 20)

    def test_excludes_results_known_after_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history, targets = self._files(root)
            rows = _history()
            rows[0]["result_known_at"] = "2026-09-01T09:00:01+09:00"
            _write_csv(history, PACE_HISTORY_COLUMNS, rows)
            inputs = build_automatic_pace_inputs(history, targets)

        self.assertEqual(inputs.history_rows_used, 19)

    def test_no_history_uses_neutral_average_with_zero_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "pace-history.csv"
            targets = root / "targets.csv"
            _write_csv(history, PACE_HISTORY_COLUMNS, [])
            _write_csv(targets, TARGET_COLUMNS, _targets())
            inputs = build_automatic_pace_inputs(history, targets)

        self.assertEqual(inputs.scenario.expected_pace, ExpectedPace.AVERAGE)
        self.assertEqual(inputs.scenario.confidence, 0.0)
        self.assertTrue(all(row.early_speed == 0.5 for row in inputs.profiles))

    def test_saved_outputs_feed_existing_pipeline_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history, targets = self._files(root)
            output = root / "pace"
            inputs = build_automatic_pace_inputs(history, targets)
            profiles_path, scenario_path, manifest_path = save_automatic_pace_inputs(inputs, output)
            profiles = load_local_pace_profiles(profiles_path)
            scenario = load_local_pace_scenario(scenario_path)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(len(profiles), 4)
        self.assertEqual(scenario.race_id, "target-1")
        self.assertEqual(manifest["generator_version"], PACE_ESTIMATOR_VERSION)
        self.assertEqual(len(manifest["pace_history_sha256"]), 64)

    def test_cli_does_not_overwrite_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history, targets = self._files(root)
            output = root / "pace"
            output.mkdir()
            marker = output / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "generate-pace-inputs", str(history), str(targets),
                    "--output", str(output),
                ])
            self.assertEqual(exit_code, 1)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertFalse(json.loads(stdout.getvalue())["is_valid"])

    def test_rejects_duplicate_history_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history, targets = self._files(root)
            rows = _history()
            rows.append(dict(rows[0]))
            _write_csv(history, PACE_HISTORY_COLUMNS, rows)
            with self.assertRaisesRegex(ValueError, "duplicate"):
                build_automatic_pace_inputs(history, targets)


if __name__ == "__main__":
    unittest.main()
