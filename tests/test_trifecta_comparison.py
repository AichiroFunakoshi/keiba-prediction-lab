import itertools
import unittest
from datetime import datetime, timedelta, timezone

from keiba_prediction_lab.domain import PredictionRecord
from keiba_prediction_lab.trifecta import (
    TrifectaCombination,
    TrifectaRaceResult,
    TrifectaStrategy,
    build_trifecta_forecast_from_combinations,
)
from keiba_prediction_lab.trifecta_comparison import compare_trifecta_generators
from keiba_prediction_lab.trifecta_comparison import compare_frozen_trifecta_generators
from keiba_prediction_lab.frozen import PredictionPhase
from keiba_prediction_lab.shadow_snapshot import freeze_built_shadow_forecast


NOW = datetime(2026, 8, 21, tzinfo=timezone.utc)
BASELINE_TOP = ("horse-1", "horse-2", "horse-3")
CANDIDATE_TOP = ("horse-1", "horse-3", "horse-2")


def predictions(race_id: str) -> tuple[PredictionRecord, ...]:
    win = (0.40, 0.25, 0.15, 0.12, 0.08)
    top3 = (0.80, 0.70, 0.60, 0.50, 0.40)
    return tuple(
        PredictionRecord(
            race_id, f"horse-{index}", NOW, "model-v1",
            win_probability, top3_probability, index,
        )
        for index, (win_probability, top3_probability) in enumerate(
            zip(win, top3), start=1
        )
    )


def forecast(race_id: str, favored: tuple[str, str, str]):
    horse_ids = [f"horse-{index}" for index in range(1, 6)]
    selections = list(itertools.permutations(horse_ids, 3))
    weights = [10.0 if selection == favored else 1.0 for selection in selections]
    total = sum(weights)
    combinations = tuple(
        TrifectaCombination(selection, weight / total)
        for selection, weight in zip(selections, weights)
    )
    return build_trifecta_forecast_from_combinations(
        predictions(race_id), combinations
    )


def frozen(race_id: str, favored: tuple[str, str, str], generator: str):
    return freeze_built_shadow_forecast(
        forecast(race_id, favored),
        scheduled_at=NOW + timedelta(hours=2),
        frozen_at=NOW + timedelta(minutes=1),
        source_predicted_at=NOW,
        phase=PredictionPhase.PRE_ODDS,
        input_data_version="sha256:input-v1",
        model_version="model-v1",
        generator_version=generator,
    )


class TrifectaGeneratorComparisonTest(unittest.TestCase):
    def test_paired_comparison_counts_model_specific_hits(self) -> None:
        race_ids = ("race-1", "race-2", "race-3")
        baselines = tuple(forecast(race_id, BASELINE_TOP) for race_id in race_ids)
        candidates = tuple(forecast(race_id, CANDIDATE_TOP) for race_id in race_ids)
        results = (
            TrifectaRaceResult("race-1", (CANDIDATE_TOP,)),
            TrifectaRaceResult("race-2", (CANDIDATE_TOP,)),
            TrifectaRaceResult("race-3", (BASELINE_TOP,)),
        )

        report = compare_trifecta_generators(
            "baseline", baselines, "pace", candidates, results
        )
        row = next(
            row for row in report.rows
            if row.strategy is TrifectaStrategy.SINGLE_WINNER_ANCHOR
            and row.ticket_count == 1
        )

        self.assertEqual(row.candidate_only_hit, 2)
        self.assertEqual(row.baseline_only_hit, 1)
        self.assertEqual(row.net_candidate_hits, 1)
        self.assertLess(report.candidate_mean_log_loss, report.baseline_mean_log_loss)

    def test_identical_forecasts_are_all_ties(self) -> None:
        baseline = forecast("race-1", BASELINE_TOP)
        result = TrifectaRaceResult("race-1", (BASELINE_TOP,))

        report = compare_trifecta_generators(
            "a", (baseline,), "b", (baseline,), (result,)
        )

        self.assertEqual(report.log_loss_improvement, 0.0)
        self.assertTrue(all(row.baseline_only_hit == 0 for row in report.rows))
        self.assertTrue(all(row.candidate_only_hit == 0 for row in report.rows))
        self.assertTrue(all(row.discordant_exact_p_value == 1.0 for row in report.rows))

    def test_requires_identical_races(self) -> None:
        with self.assertRaisesRegex(ValueError, "identical races"):
            compare_trifecta_generators(
                "a", (forecast("race-1", BASELINE_TOP),),
                "b", (forecast("race-2", CANDIDATE_TOP),),
                (TrifectaRaceResult("race-1", (BASELINE_TOP,)),),
            )

    def test_markdown_forbids_automatic_model_update(self) -> None:
        baseline = forecast("race-1", BASELINE_TOP)
        candidate = forecast("race-1", CANDIDATE_TOP)
        result = TrifectaRaceResult("race-1", (CANDIDATE_TOP,))

        report = compare_trifecta_generators(
            "baseline", (baseline,), "pace", (candidate,), (result,)
        )

        self.assertIn("モデルや係数を自動更新しない", report.to_markdown())

    def test_frozen_comparison_requires_same_non_generator_inputs(self) -> None:
        baseline = frozen("race-1", BASELINE_TOP, "baseline-v1")
        candidate = frozen("race-1", CANDIDATE_TOP, "pace-v1")
        result = TrifectaRaceResult("race-1", (CANDIDATE_TOP,))

        report = compare_frozen_trifecta_generators(
            (baseline,), (candidate,), (result,)
        )

        self.assertEqual(report.baseline_label, "baseline-v1")
        self.assertEqual(report.candidate_label, "pace-v1")

    def test_frozen_comparison_rejects_different_input_version(self) -> None:
        baseline = frozen("race-1", BASELINE_TOP, "baseline-v1")
        candidate = freeze_built_shadow_forecast(
            forecast("race-1", CANDIDATE_TOP),
            scheduled_at=NOW + timedelta(hours=2),
            frozen_at=NOW + timedelta(minutes=1),
            source_predicted_at=NOW,
            phase=PredictionPhase.PRE_ODDS,
            input_data_version="sha256:different-input",
            model_version="model-v1",
            generator_version="pace-v1",
        )
        result = TrifectaRaceResult("race-1", (CANDIDATE_TOP,))

        with self.assertRaisesRegex(ValueError, "identical timing"):
            compare_frozen_trifecta_generators(
                (baseline,), (candidate,), (result,)
            )


if __name__ == "__main__":
    unittest.main()
