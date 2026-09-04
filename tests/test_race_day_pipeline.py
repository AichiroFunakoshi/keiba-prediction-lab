import contextlib
import csv
import hashlib
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from keiba_prediction_lab.app_snapshot import build_read_only_app_snapshot
from keiba_prediction_lab.bundle_audit import audit_prediction_bundle
from keiba_prediction_lab.cli import main
from keiba_prediction_lab.race_day_pipeline import (
    _publish_directory_no_replace,
    audit_local_race_day,
    build_and_save_local_race_day,
    load_local_race_day_plan,
)
from tests.test_local_pipeline import _files


def _changed_csv(source: Path, target: Path, changes: dict[str, str]) -> None:
    with source.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        rows = [{**row, **changes} for row in reader]
    with target.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _race_day_files(root: Path) -> tuple[Path, Path, Path]:
    model, history, targets_1, profiles_1, scenario_1 = _files(root)
    targets_2 = root / "targets-2.csv"
    profiles_2 = root / "pace-profiles-2.csv"
    scenario_2 = root / "pace-scenario-2.json"
    _changed_csv(targets_1, targets_2, {
        "race_id": "target-2",
        "scheduled_at": "2026-02-01T13:00:00+09:00",
    })
    _changed_csv(profiles_1, profiles_2, {"race_id": "target-2"})
    scenario_2.write_text(json.dumps({
        "race_id": "target-2",
        "observed_at": "2026-02-01T10:00:00+09:00",
        "expected_pace": "average",
        "confidence": 0.6,
    }), encoding="utf-8")
    plan = root / "race-day-plan.json"
    plan.write_text(json.dumps({
        "schema_version": "1.0",
        "race_date": "2026-02-01",
        "races": [
            {
                "venue": "東京",
                "race_number": 1,
                "targets": targets_1.name,
                "pace_profiles": profiles_1.name,
                "pace_scenario": scenario_1.name,
            },
            {
                "venue": "東京",
                "race_number": 2,
                "targets": targets_2.name,
                "pace_profiles": profiles_2.name,
                "pace_scenario": scenario_2.name,
            },
        ],
    }, ensure_ascii=False), encoding="utf-8")
    return model, history, plan


class RaceDayPipelineTest(unittest.TestCase):
    def test_exclusive_directory_publish_never_replaces_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            target.mkdir()
            marker = target / "keep.txt"
            marker.write_text("keep", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                _publish_directory_no_replace(source, target)

            self.assertTrue(source.exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_atomically_builds_audited_race_day_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, history, plan = _race_day_files(root)
            output = root / "race-day-output"
            result = build_and_save_local_race_day(
                model,
                history,
                plan,
                output,
                frozen_at=datetime.fromisoformat(
                    "2026-02-01T10:05:00+09:00"
                ),
            )
            snapshot = build_read_only_app_snapshot(
                race_day_manifest=result.race_day_manifest
            )
            provenance = json.loads(result.provenance.read_text(encoding="utf-8"))
            canonical = json.dumps(
                provenance["payload"], ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            )
            audits = [
                audit_prediction_bundle(output / "predictions" / f"race-{index:03d}")
                for index in (1, 2)
            ]
            race_day_audit = audit_local_race_day(output)

        self.assertEqual(result.race_count, 2)
        self.assertEqual(result.venue_count, 1)
        self.assertEqual(len(snapshot.race_day.venues[0].races), 2)
        self.assertEqual([row.race_id for row in audits], ["target-1", "target-2"])
        self.assertEqual(
            provenance["sha256"], hashlib.sha256(canonical.encode()).hexdigest()
        )
        self.assertEqual(provenance["payload"]["phase"], "pre_odds")
        self.assertEqual(race_day_audit.race_count, 2)
        self.assertEqual(race_day_audit.frozen_at.isoformat(), "2026-02-01T10:05:00+09:00")

    def test_cli_reports_saved_race_day(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, history, plan = _race_day_files(root)
            output = root / "output"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "predict-race-day", str(model), str(history), str(plan),
                    "--frozen-at", "2026-02-01T10:05:00+09:00",
                    "--output", str(output),
                ])
            payload = json.loads(stdout.getvalue())
            audit_stdout = io.StringIO()
            with contextlib.redirect_stdout(audit_stdout):
                audit_exit_code = main(["audit-race-day", str(output)])
            audit_payload = json.loads(audit_stdout.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["is_valid"])
        self.assertEqual(payload["race_count"], 2)
        self.assertEqual(payload["venue_count"], 1)
        self.assertEqual(audit_exit_code, 0)
        self.assertTrue(audit_payload["is_valid"])
        self.assertEqual(audit_payload["phase"], "pre_odds")

    def test_invalid_later_race_leaves_no_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, history, plan = _race_day_files(root)
            output = root / "output"
            scenario = root / "pace-scenario-2.json"
            payload = json.loads(scenario.read_text(encoding="utf-8"))
            payload["race_id"] = "wrong-race"
            scenario.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "must match"):
                build_and_save_local_race_day(
                    model,
                    history,
                    plan,
                    output,
                    frozen_at=datetime.fromisoformat(
                        "2026-02-01T10:05:00+09:00"
                    ),
                )

            self.assertFalse(output.exists())

    def test_final_gate_rejects_missing_weight_without_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, history, plan = _race_day_files(root)
            targets = root / "targets-2.csv"
            with targets.open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                fieldnames = reader.fieldnames
                rows = list(reader)
            rows[-1]["body_weight_kg"] = ""
            targets.unlink()
            with targets.open("x", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            output = root / "output"

            with self.assertRaisesRegex(ValueError, "missing 1/5 runners"):
                build_and_save_local_race_day(
                    model,
                    history,
                    plan,
                    output,
                    frozen_at=datetime.fromisoformat(
                        "2026-02-01T10:05:00+09:00"
                    ),
                    require_complete_body_weight=True,
                )

            self.assertFalse(output.exists())

    def test_audit_rejects_changed_race_day_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, history, plan = _race_day_files(root)
            output = root / "output"
            build_and_save_local_race_day(
                model,
                history,
                plan,
                output,
                frozen_at=datetime.fromisoformat(
                    "2026-02-01T10:05:00+09:00"
                ),
            )
            manifest = output / "race-day.json"
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["venues"][0]["venue"] = "改変"
            manifest.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "does not match provenance"):
                audit_local_race_day(output)

    def test_audit_cross_checks_rehashed_false_model_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, history, plan = _race_day_files(root)
            output = root / "output"
            build_and_save_local_race_day(
                model,
                history,
                plan,
                output,
                frozen_at=datetime.fromisoformat(
                    "2026-02-01T10:05:00+09:00"
                ),
            )
            path = output / "race-day-provenance.json"
            envelope = json.loads(path.read_text(encoding="utf-8"))
            envelope["payload"]["model_sha256"] = "0" * 64
            canonical = json.dumps(
                envelope["payload"], ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            )
            envelope["sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
            path.write_text(json.dumps(envelope), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "model snapshot is inconsistent"):
                audit_local_race_day(output)

    def test_rejects_duplicate_race_and_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, history, plan = _race_day_files(root)
            payload = json.loads(plan.read_text(encoding="utf-8"))
            payload["races"][1]["race_number"] = 1
            plan.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be unique"):
                load_local_race_day_plan(plan)

            output = root / "existing"
            output.mkdir()
            marker = output / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                build_and_save_local_race_day(
                    model,
                    history,
                    plan,
                    output,
                    frozen_at=datetime.fromisoformat(
                        "2026-02-01T10:05:00+09:00"
                    ),
                )
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
