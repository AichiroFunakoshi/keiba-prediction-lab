import csv
import tempfile
import unittest
from pathlib import Path

from keiba_prediction_lab.data_audit import (
    RedistributionStatus,
    SourceStatus,
    assert_pre_race_features,
    audit_standard_csv,
    load_source_registry,
    sha256_file,
)


ROOT = Path(__file__).resolve().parents[1]


class DataSourceRegistryTest(unittest.TestCase):
    def test_registry_is_valid_and_limits_approved_real_sources(self) -> None:
        sources = load_source_registry(ROOT / "data" / "sources.json")

        self.assertEqual(len(sources), 6)
        self.assertEqual(len({source.source_id for source in sources}), 6)
        jra_van = next(source for source in sources if source.source_id == "jra-van-data-lab")
        self.assertIs(jra_van.status, SourceStatus.APPROVED)
        self.assertIs(jra_van.redistribution, RedistributionStatus.PROHIBITED)
        jra_web = next(
            source for source in sources
            if source.source_id == "jra-public-web-private-use"
        )
        self.assertIs(jra_web.status, SourceStatus.REVIEW_REQUIRED)
        self.assertIs(jra_web.redistribution, RedistributionStatus.PROHIBITED)
        unapproved_real = [
            source for source in sources
            if source.scope != "synthetic"
            and source.source_id != "jra-van-data-lab"
        ]
        self.assertTrue(unapproved_real)
        self.assertTrue(all(
            source.status is SourceStatus.REVIEW_REQUIRED
            for source in unapproved_real
        ))

    def test_feature_gate_rejects_result_columns(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-pre-race"):
            assert_pre_race_features({"venue", "post_position", "finish_position"})

    def test_feature_gate_accepts_pre_race_columns(self) -> None:
        assert_pre_race_features({"race_date", "venue", "post_position"})


class CsvAuditTest(unittest.TestCase):
    def test_synthetic_fixture_passes_quality_audit(self) -> None:
        path = ROOT / "tests" / "fixtures" / "synthetic_race_results.csv"
        report = audit_standard_csv(path)

        self.assertTrue(report.is_valid)
        self.assertEqual(report.row_count, 3)
        self.assertEqual(report.duplicate_key_count, 0)
        self.assertEqual(report.sha256, sha256_file(path))

    def test_duplicate_and_invalid_rows_are_reported(self) -> None:
        fieldnames = [
            "race_id",
            "race_date",
            "horse_id",
            "horse_name",
            "post_position",
            "finish_position",
            "result_status",
        ]
        rows = [
            {
                "race_id": "race-1",
                "race_date": "not-a-date",
                "horse_id": "horse-1",
                "horse_name": "Alpha",
                "post_position": "1",
                "finish_position": "0",
                "result_status": "finished",
            },
            {
                "race_id": "race-1",
                "race_date": "2026-01-01",
                "horse_id": "horse-1",
                "horse_name": "Alpha",
                "post_position": "1",
                "finish_position": "1",
                "result_status": "finished",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.csv"
            with path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            report = audit_standard_csv(path)

        self.assertFalse(report.is_valid)
        self.assertEqual(report.duplicate_key_count, 1)
        self.assertEqual(report.invalid_date_count, 1)
        self.assertEqual(report.invalid_finish_position_count, 1)
