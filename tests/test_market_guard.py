import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from keiba_prediction_lab.market_guard import (
    MarketGuardPolicy,
    MarketGuardReport,
    assess_market_guard,
    load_market_guard_report,
    save_market_guard_report,
)


class MarketGuardTest(unittest.TestCase):
    def test_market_rank_gate_does_not_change_model_probability(self) -> None:
        row = assess_market_guard(
            race_id="race-1",
            predicted_horse_id="horse:d",
            model_win_probability=0.18,
            odds_by_horse={
                "horse:a": 2.0,
                "horse:b": 3.0,
                "horse:c": 4.0,
                "horse:d": 8.0,
            },
            policy=MarketGuardPolicy(max_market_rank=3),
        )

        self.assertEqual(row.model_win_probability, 0.18)
        self.assertEqual(row.market_rank, 4)
        self.assertFalse(row.eligible)
        self.assertEqual(row.reason, "market-rank-above-limit")

    def test_market_ties_share_rank_and_missing_odds_abstains(self) -> None:
        tied = assess_market_guard(
            race_id="race-1",
            predicted_horse_id="horse:b",
            model_win_probability=0.2,
            odds_by_horse={"horse:a": 2.0, "horse:b": 3.0, "horse:c": 3.0},
            policy=MarketGuardPolicy(max_market_rank=2),
        )
        missing = assess_market_guard(
            race_id="race-2",
            predicted_horse_id="horse:x",
            model_win_probability=0.2,
            odds_by_horse={"horse:x": None, "horse:y": 2.0},
        )

        self.assertEqual(tied.market_rank, 2)
        self.assertTrue(tied.eligible)
        self.assertIsNone(missing.market_rank)
        self.assertFalse(missing.eligible)
        self.assertEqual(missing.reason, "missing-market-odds")

    def test_saved_report_is_integrity_protected(self) -> None:
        row = assess_market_guard(
            race_id="race-1",
            predicted_horse_id="horse:a",
            model_win_probability=0.25,
            odds_by_horse={"horse:a": 2.0, "horse:b": 4.0},
        )
        report = MarketGuardReport(
            observed_at=datetime(2099, 1, 2, 9, 0, tzinfo=timezone.utc),
            policy=MarketGuardPolicy(),
            cards_sha256="a" * 64,
            race_day_manifest_sha256="b" * 64,
            rows=(row,),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market-guard.json"
            digest = save_market_guard_report(report, path)
            loaded = load_market_guard_report(path)
            envelope = json.loads(path.read_text(encoding="utf-8"))
            envelope["payload"]["eligible_race_count"] = 0
            path.write_text(json.dumps(envelope), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "integrity"):
                load_market_guard_report(path)

        self.assertEqual(len(digest), 64)
        self.assertEqual(loaded.eligible_race_count, 1)
        self.assertEqual(loaded.rows[0], row)


if __name__ == "__main__":
    unittest.main()
