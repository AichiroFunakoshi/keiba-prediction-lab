import unittest
from datetime import datetime, timedelta, timezone

from keiba_prediction_lab.domain import PredictionRecord
from keiba_prediction_lab.pace import (
    PACE_GENERATOR_VERSION,
    ExpectedPace,
    PaceRunnerProfile,
    RacePaceScenario,
    RunningStyle,
    build_pace_conditioned_forecast,
    rank_pace_conditioned_trifectas,
)
from keiba_prediction_lab.frozen import PredictionPhase
from keiba_prediction_lab.shadow_snapshot import freeze_built_shadow_forecast


UTC = timezone.utc
OBSERVED_AT = datetime(2026, 8, 20, 4, 50, tzinfo=UTC)
PREDICTED_AT = OBSERVED_AT + timedelta(minutes=10)


def predictions() -> tuple[PredictionRecord, ...]:
    win = (0.34, 0.33, 0.15, 0.11, 0.07)
    top3 = (0.75, 0.74, 0.60, 0.51, 0.40)
    return tuple(
        PredictionRecord(
            "race-1", f"horse-{index}", PREDICTED_AT, "model-v1",
            win_probability, top3_probability, index,
        )
        for index, (win_probability, top3_probability) in enumerate(
            zip(win, top3), start=1
        )
    )


def profiles() -> tuple[PaceRunnerProfile, ...]:
    values = (
        (RunningStyle.LEADER, 0.95, 0.30, 0.65),
        (RunningStyle.CLOSER, 0.25, 0.95, 0.80),
        (RunningStyle.PRESSER, 0.80, 0.50, 0.70),
        (RunningStyle.STALKER, 0.55, 0.75, 0.85),
        (RunningStyle.CLOSER, 0.20, 0.85, 0.75),
    )
    return tuple(
        PaceRunnerProfile(
            "race-1", f"horse-{index}", OBSERVED_AT, style,
            early, late, resilience,
        )
        for index, (style, early, late, resilience) in enumerate(values, start=1)
    )


def scenario(observed_at: datetime = OBSERVED_AT) -> RacePaceScenario:
    return RacePaceScenario("race-1", observed_at, ExpectedPace.AVERAGE, 0.7)


class PaceConditionedTrifectaTest(unittest.TestCase):
    def test_joint_probabilities_sum_to_one(self) -> None:
        combinations = rank_pace_conditioned_trifectas(
            predictions(), profiles(), scenario()
        )

        self.assertEqual(len(combinations), 5 * 4 * 3)
        self.assertAlmostEqual(sum(row.probability for row in combinations), 1.0)

    def test_front_and_closer_winner_scenarios_change_lower_order(self) -> None:
        combinations = rank_pace_conditioned_trifectas(
            predictions(), profiles(), scenario()
        )
        def second_mass(first: str, second: str) -> float:
            return sum(
                row.probability for row in combinations
                if row.selection[:2] == (first, second)
            )

        leader_ratio = (
            second_mass("horse-1", "horse-3")
            / second_mass("horse-1", "horse-4")
        )
        closer_ratio = (
            second_mass("horse-2", "horse-3")
            / second_mass("horse-2", "horse-4")
        )

        self.assertNotAlmostEqual(
            leader_ratio, closer_ratio,
            msg="the winner scenario must change relative second-place chances",
        )

    def test_first_place_marginals_preserve_winner_model(self) -> None:
        combinations = rank_pace_conditioned_trifectas(
            predictions(), profiles(), scenario()
        )
        for prediction in predictions():
            marginal = sum(
                row.probability for row in combinations
                if row.selection[0] == prediction.horse_id
            )
            self.assertAlmostEqual(marginal, prediction.win_probability)

    def test_builds_same_shadow_portfolio_contract(self) -> None:
        forecast = build_pace_conditioned_forecast(
            predictions(), profiles(), scenario()
        )

        self.assertEqual(forecast.primary_ticket.selection[0], "horse-1")
        self.assertEqual(
            sorted({row.ticket_count for row in forecast.shadow_portfolios}),
            [1, 3, 5, 10],
        )

    def test_pace_forecast_uses_same_pre_race_freeze_contract(self) -> None:
        forecast = build_pace_conditioned_forecast(
            predictions(), profiles(), scenario()
        )
        snapshot = freeze_built_shadow_forecast(
            forecast,
            scheduled_at=PREDICTED_AT + timedelta(hours=2),
            frozen_at=PREDICTED_AT + timedelta(minutes=1),
            source_predicted_at=PREDICTED_AT,
            phase=PredictionPhase.PRE_ODDS,
            input_data_version="sha256:input-v1",
            model_version="model-v1",
            generator_version=PACE_GENERATOR_VERSION,
        )

        self.assertEqual(snapshot.forecast, forecast)
        self.assertEqual(snapshot.generator_version, "pace-scenario-v1")

    def test_rejects_pace_information_observed_after_prediction(self) -> None:
        late = PREDICTED_AT + timedelta(seconds=1)
        with self.assertRaisesRegex(ValueError, "observed by predicted_at"):
            rank_pace_conditioned_trifectas(
                predictions(), profiles(), scenario(late)
            )

    def test_requires_profiles_for_exactly_the_same_runners(self) -> None:
        with self.assertRaisesRegex(ValueError, "match prediction runners"):
            rank_pace_conditioned_trifectas(
                predictions(), profiles()[:-1], scenario()
            )


if __name__ == "__main__":
    unittest.main()
