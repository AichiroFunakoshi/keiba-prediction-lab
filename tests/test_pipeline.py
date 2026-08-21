import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from keiba_prediction_lab.features import FeatureRow
from keiba_prediction_lab.frozen import PredictionPhase, load_frozen_prediction
from keiba_prediction_lab.model import ConditionalLogitModel
from keiba_prediction_lab.pace import (
    ExpectedPace,
    PaceRunnerProfile,
    RacePaceScenario,
    RunningStyle,
)
from keiba_prediction_lab.pipeline import (
    PIPELINE_POLICY_VERSION,
    run_race_prediction_pipeline,
    save_race_prediction_bundle,
)
from keiba_prediction_lab.shadow_snapshot import load_frozen_shadow_forecast


UTC = timezone.utc
OBSERVED_AT = datetime(2026, 8, 21, 1, 0, tzinfo=UTC)
SCHEDULED_AT = OBSERVED_AT + timedelta(hours=2)
FROZEN_AT = OBSERVED_AT + timedelta(minutes=5)


def feature_rows() -> tuple[FeatureRow, ...]:
    rows = []
    for index, strength in enumerate((0.80, 0.50, 0.30, 0.20, 0.10), start=1):
        rows.append(FeatureRow(
            race_id="race-1", horse_id=f"horse-{index}", observed_at=OBSERVED_AT,
            distance_band="mile", post_position=index, carried_weight_kg=56.0,
            body_weight_kg=480, days_since_last_run=21, horse_starts=10,
            horse_win_rate=strength, horse_top3_rate=min(1.0, strength + 0.15),
            horse_venue_starts=5, horse_venue_win_rate=strength,
            horse_surface_starts=8, horse_surface_win_rate=strength,
            horse_track_condition_starts=4, horse_track_condition_win_rate=strength,
            horse_distance_band_starts=6, horse_distance_band_win_rate=strength,
            jockey_starts=100, jockey_win_rate=strength,
            trainer_starts=100, trainer_win_rate=strength,
        ))
    return tuple(rows)


def model() -> ConditionalLogitModel:
    coefficients = (0.0,) * 17
    coefficients = coefficients[:7] + (2.0,) + coefficients[8:]
    return ConditionalLogitModel(
        coefficients=coefficients,
        means=(0.0,) * 17,
        scales=(1.0,) * 17,
        trained_through=OBSERVED_AT - timedelta(days=1),
        model_version="test-model-v1",
    )


def profiles() -> tuple[PaceRunnerProfile, ...]:
    styles = (
        RunningStyle.LEADER, RunningStyle.CLOSER, RunningStyle.PRESSER,
        RunningStyle.STALKER, RunningStyle.CLOSER,
    )
    return tuple(
        PaceRunnerProfile(
            "race-1", f"horse-{index}", OBSERVED_AT, style,
            1.0 - index * 0.1, index * 0.15, 0.7,
        )
        for index, style in enumerate(styles, start=1)
    )


def bundle():
    return run_race_prediction_pipeline(
        model(), feature_rows(), profiles(),
        RacePaceScenario("race-1", OBSERVED_AT, ExpectedPace.FAST, 0.8),
        scheduled_at=SCHEDULED_AT, frozen_at=FROZEN_AT,
        phase=PredictionPhase.PRE_ODDS, input_data_version="sha256:input-v1",
    )


class RacePredictionPipelineTest(unittest.TestCase):
    def test_keeps_actual_purchase_to_baseline_one_point_100_yen(self) -> None:
        result = bundle()

        self.assertEqual(result.policy_version, PIPELINE_POLICY_VERSION)
        self.assertEqual(len(result.actual_prediction.trifecta_tickets), 1)
        self.assertEqual(result.actual_prediction.trifecta_tickets[0].stake_yen, 100)
        self.assertEqual(
            result.actual_prediction.trifecta_tickets[0].selection,
            result.baseline_shadow.forecast.primary_ticket.selection,
        )
        self.assertEqual(result.pace_shadow.generator_version, "pace-scenario-v1")

    def test_shadow_forecasts_record_all_counterfactual_sizes(self) -> None:
        result = bundle()

        for shadow in (result.baseline_shadow, result.pace_shadow):
            self.assertEqual(
                sorted({row.ticket_count for row in shadow.forecast.shadow_portfolios}),
                [1, 3, 5, 10],
            )

    def test_pipeline_is_deterministic_and_does_not_change_model(self) -> None:
        fitted = model()
        before = fitted
        first = run_race_prediction_pipeline(
            fitted, feature_rows(), profiles(),
            RacePaceScenario("race-1", OBSERVED_AT, ExpectedPace.FAST, 0.8),
            scheduled_at=SCHEDULED_AT, frozen_at=FROZEN_AT,
            phase=PredictionPhase.PRE_ODDS, input_data_version="sha256:input-v1",
        )
        second = bundle()

        self.assertEqual(fitted, before)
        self.assertEqual(first, second)

    def test_saves_separate_immutable_actual_and_shadow_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "race-1"
            manifest_path = save_race_prediction_bundle(bundle(), target)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(load_frozen_prediction(target / "actual.json"), bundle().actual_prediction)
            self.assertEqual(
                load_frozen_shadow_forecast(target / "baseline-shadow.json"),
                bundle().baseline_shadow,
            )
            self.assertEqual(
                load_frozen_shadow_forecast(target / "pace-shadow.json"),
                bundle().pace_shadow,
            )
            self.assertEqual(manifest["actual"]["stake_yen"], 100)
            self.assertTrue(all(row["stake_yen"] == 0 for row in manifest["shadows"]))
            with self.assertRaises(FileExistsError):
                save_race_prediction_bundle(bundle(), target)

    def test_removes_incomplete_bundle_after_save_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "race-1"
            with patch(
                "keiba_prediction_lab.pipeline.save_frozen_shadow_forecast",
                side_effect=OSError("simulated write failure"),
            ):
                with self.assertRaisesRegex(OSError, "simulated write failure"):
                    save_race_prediction_bundle(bundle(), target)

            self.assertFalse(target.exists())
            self.assertTrue(save_race_prediction_bundle(bundle(), target).is_file())


if __name__ == "__main__":
    unittest.main()
