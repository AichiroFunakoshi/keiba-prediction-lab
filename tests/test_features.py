import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from keiba_prediction_lab.features import (
    RacePerformance,
    Surface,
    TargetRunner,
    distance_band,
    generate_features,
)


UTC = timezone.utc
RACE_1 = datetime(2026, 1, 1, 6, 0, tzinfo=UTC)
RACE_2 = datetime(2026, 1, 15, 6, 0, tzinfo=UTC)
TARGET_TIME = datetime(2026, 2, 1, 6, 0, tzinfo=UTC)
OBSERVED = TARGET_TIME - timedelta(hours=2)


def performance(
    race_id: str,
    scheduled_at: datetime,
    horse_id: str,
    finish_position: int,
    *,
    jockey_id: str = "jockey-a",
    trainer_id: str = "trainer-a",
    venue: str = "Tokyo",
    surface: Surface = Surface.TURF,
    track_condition: str = "good",
    distance_m: int = 1600,
) -> RacePerformance:
    return RacePerformance(
        race_id=race_id,
        scheduled_at=scheduled_at,
        result_known_at=scheduled_at + timedelta(minutes=20),
        horse_id=horse_id,
        jockey_id=jockey_id,
        trainer_id=trainer_id,
        venue=venue,
        surface=surface,
        track_condition=track_condition,
        distance_m=distance_m,
        post_position=finish_position,
        carried_weight_kg=56.0,
        body_weight_kg=480,
        finish_position=finish_position,
    )


def history() -> tuple[RacePerformance, ...]:
    return (
        performance("race-1", RACE_1, "horse-a", 1),
        performance("race-1", RACE_1, "horse-b", 2, jockey_id="jockey-b"),
        performance("race-1", RACE_1, "horse-c", 3, trainer_id="trainer-c"),
        performance("race-1", RACE_1, "horse-d", 4),
        performance("race-2", RACE_2, "horse-a", 1),
        performance(
            "race-2", RACE_2, "horse-b", 2, jockey_id="jockey-b",
            venue="Nakayama", surface=Surface.DIRT, track_condition="muddy",
            distance_m=1200,
        ),
        performance("race-2", RACE_2, "horse-e", 3),
        performance("race-2", RACE_2, "horse-f", 4),
    )


def targets() -> tuple[TargetRunner, ...]:
    return (
        TargetRunner(
            "race-3", TARGET_TIME, OBSERVED, "horse-a", "jockey-a", "trainer-a",
            "Tokyo", Surface.TURF, "good", 1800, 1, 57.0, 482,
        ),
        TargetRunner(
            "race-3", TARGET_TIME, OBSERVED, "horse-new", "jockey-new",
            "trainer-new", "Tokyo", Surface.TURF, "good", 1800, 2, 55.0,
        ),
    )


class FeatureGenerationTest(unittest.TestCase):
    def test_generates_reproducible_historical_features(self) -> None:
        first = generate_features(history(), targets(), prior_strength=2.0)
        second = generate_features(history(), targets(), prior_strength=2.0)

        self.assertEqual(first, second)
        horse_a = first[0]
        self.assertEqual(horse_a.horse_starts, 2)
        self.assertEqual(horse_a.horse_venue_starts, 2)
        self.assertEqual(horse_a.horse_surface_starts, 2)
        self.assertEqual(horse_a.horse_track_condition_starts, 2)
        self.assertEqual(horse_a.horse_distance_band_starts, 2)
        self.assertEqual(horse_a.jockey_starts, 6)
        self.assertEqual(horse_a.trainer_starts, 7)
        self.assertEqual(horse_a.days_since_last_run, 17)
        self.assertGreater(horse_a.horse_win_rate, first[1].horse_win_rate)

    def test_unseen_runner_uses_smoothed_global_prior(self) -> None:
        row = generate_features(history(), targets(), prior_strength=2.0)[1]

        self.assertEqual(row.horse_starts, 0)
        self.assertEqual(row.horse_win_rate, 0.25)
        self.assertEqual(row.horse_top3_rate, 0.75)
        self.assertIsNone(row.days_since_last_run)

    def test_rejects_result_not_known_at_observation_time(self) -> None:
        leaked = performance("future", TARGET_TIME, "horse-x", 1)

        with self.assertRaisesRegex(ValueError, "known by observed_at"):
            generate_features(history() + (leaked,), targets())

    def test_rejects_duplicate_history_runner(self) -> None:
        duplicate = history()[0]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            generate_features(history() + (duplicate,), targets())

    def test_target_observation_must_precede_start(self) -> None:
        with self.assertRaisesRegex(ValueError, "before scheduled_at"):
            replace(targets()[0], observed_at=TARGET_TIME)

    def test_target_runners_must_share_observation_time(self) -> None:
        changed = replace(targets()[1], observed_at=OBSERVED - timedelta(minutes=1))
        with self.assertRaisesRegex(ValueError, "share one race"):
            generate_features(history(), (targets()[0], changed))

    def test_distance_bands_have_stable_boundaries(self) -> None:
        self.assertEqual(distance_band(1400), "sprint")
        self.assertEqual(distance_band(1800), "mile")
        self.assertEqual(distance_band(2400), "middle")
        self.assertEqual(distance_band(2401), "long")

    def test_dead_heat_win_credit_is_split(self) -> None:
        tied = (
            performance("tie", RACE_1, "horse-a", 1),
            performance("tie", RACE_1, "horse-b", 1),
            performance("tie", RACE_1, "horse-c", 3),
        )
        rows = generate_features(tied, targets(), prior_strength=1.0)
        self.assertAlmostEqual(rows[0].horse_win_rate, 5 / 12)


if __name__ == "__main__":
    unittest.main()
