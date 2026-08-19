"""Leakage-resistant features built only from information known before a race."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Surface(str, Enum):
    TURF = "turf"
    DIRT = "dirt"
    JUMP = "jump"


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def distance_band(distance_m: int) -> str:
    """Return a stable, coarse distance category."""
    if distance_m <= 0:
        raise ValueError("distance_m must be positive")
    if distance_m <= 1400:
        return "sprint"
    if distance_m <= 1800:
        return "mile"
    if distance_m <= 2400:
        return "middle"
    return "long"


@dataclass(frozen=True)
class RacePerformance:
    race_id: str
    scheduled_at: datetime
    result_known_at: datetime
    horse_id: str
    jockey_id: str
    trainer_id: str
    venue: str
    surface: Surface
    track_condition: str
    distance_m: int
    post_position: int
    carried_weight_kg: float
    body_weight_kg: int | None
    finish_position: int

    def __post_init__(self) -> None:
        for field_name in (
            "race_id", "horse_id", "jockey_id", "trainer_id", "venue",
            "track_condition",
        ):
            _require_text(getattr(self, field_name), field_name)
        _require_aware(self.scheduled_at, "scheduled_at")
        _require_aware(self.result_known_at, "result_known_at")
        if self.result_known_at <= self.scheduled_at:
            raise ValueError("result_known_at must be after scheduled_at")
        _validate_runner_values(
            self.distance_m,
            self.post_position,
            self.carried_weight_kg,
            self.body_weight_kg,
        )
        if self.finish_position < 1:
            raise ValueError("finish_position must be positive")


@dataclass(frozen=True)
class TargetRunner:
    race_id: str
    scheduled_at: datetime
    observed_at: datetime
    horse_id: str
    jockey_id: str
    trainer_id: str
    venue: str
    surface: Surface
    track_condition: str
    distance_m: int
    post_position: int
    carried_weight_kg: float
    body_weight_kg: int | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "race_id", "horse_id", "jockey_id", "trainer_id", "venue",
            "track_condition",
        ):
            _require_text(getattr(self, field_name), field_name)
        _require_aware(self.scheduled_at, "scheduled_at")
        _require_aware(self.observed_at, "observed_at")
        if self.observed_at >= self.scheduled_at:
            raise ValueError("observed_at must be before scheduled_at")
        _validate_runner_values(
            self.distance_m,
            self.post_position,
            self.carried_weight_kg,
            self.body_weight_kg,
        )


def _validate_runner_values(
    distance_m: int,
    post_position: int,
    carried_weight_kg: float,
    body_weight_kg: int | None,
) -> None:
    distance_band(distance_m)
    if post_position < 1:
        raise ValueError("post_position must be positive")
    if carried_weight_kg <= 0:
        raise ValueError("carried_weight_kg must be positive")
    if body_weight_kg is not None and body_weight_kg <= 0:
        raise ValueError("body_weight_kg must be positive or None")


@dataclass(frozen=True)
class FeatureRow:
    race_id: str
    horse_id: str
    observed_at: datetime
    distance_band: str
    post_position: int
    carried_weight_kg: float
    body_weight_kg: int | None
    days_since_last_run: int | None
    horse_starts: int
    horse_win_rate: float
    horse_top3_rate: float
    horse_venue_starts: int
    horse_venue_win_rate: float
    horse_surface_starts: int
    horse_surface_win_rate: float
    horse_track_condition_starts: int
    horse_track_condition_win_rate: float
    horse_distance_band_starts: int
    horse_distance_band_win_rate: float
    jockey_starts: int
    jockey_win_rate: float
    trainer_starts: int
    trainer_win_rate: float


@dataclass
class _Rate:
    starts: int = 0
    wins: float = 0.0
    top3: float = 0.0

    def add(self, win_credit: float, top3_credit: float) -> None:
        self.starts += 1
        self.wins += win_credit
        self.top3 += top3_credit


def _smoothed(successes: float, starts: int, prior: float, strength: float) -> float:
    return (successes + prior * strength) / (starts + strength)


def generate_features(
    history: tuple[RacePerformance, ...],
    targets: tuple[TargetRunner, ...],
    *,
    prior_strength: float = 10.0,
) -> tuple[FeatureRow, ...]:
    """Build deterministic pre-race features for runners in one race."""
    if not targets:
        raise ValueError("at least one target runner is required")
    if prior_strength <= 0:
        raise ValueError("prior_strength must be positive")

    race_ids = {runner.race_id for runner in targets}
    scheduled_times = {runner.scheduled_at for runner in targets}
    observed_times = {runner.observed_at for runner in targets}
    horse_ids = {runner.horse_id for runner in targets}
    post_positions = {runner.post_position for runner in targets}
    if len(race_ids) != 1 or len(scheduled_times) != 1 or len(observed_times) != 1:
        raise ValueError("target runners must share one race and observation time")
    if len(horse_ids) != len(targets):
        raise ValueError("target horse_id must be unique")
    if len(post_positions) != len(targets):
        raise ValueError("target post_position must be unique")

    observed_at = targets[0].observed_at
    seen_history: set[tuple[str, str]] = set()
    for performance in history:
        key = (performance.race_id, performance.horse_id)
        if key in seen_history:
            raise ValueError("history contains duplicate race_id and horse_id")
        seen_history.add(key)
        if performance.result_known_at > observed_at:
            raise ValueError("history result must be known by observed_at")

    by_race: dict[str, list[RacePerformance]] = defaultdict(list)
    for performance in history:
        by_race[performance.race_id].append(performance)

    win_credit: dict[tuple[str, str], float] = {}
    top3_credit: dict[tuple[str, str], float] = {}
    for race_id, performances in by_race.items():
        winners = [item for item in performances if item.finish_position == 1]
        if not winners:
            raise ValueError(f"historical race {race_id} has no winner")
        placed = [item for item in performances if item.finish_position <= 3]
        place_credit = min(1.0, min(3, len(performances)) / len(placed))
        for item in performances:
            key = (race_id, item.horse_id)
            win_credit[key] = 1.0 / len(winners) if item.finish_position == 1 else 0.0
            top3_credit[key] = place_credit if item.finish_position <= 3 else 0.0

    global_rate = _Rate()
    horse_rates: dict[str, _Rate] = defaultdict(_Rate)
    venue_rates: dict[tuple[str, str], _Rate] = defaultdict(_Rate)
    surface_rates: dict[tuple[str, Surface], _Rate] = defaultdict(_Rate)
    condition_rates: dict[tuple[str, str], _Rate] = defaultdict(_Rate)
    distance_rates: dict[tuple[str, str], _Rate] = defaultdict(_Rate)
    jockey_rates: dict[str, _Rate] = defaultdict(_Rate)
    trainer_rates: dict[str, _Rate] = defaultdict(_Rate)
    latest_run: dict[str, datetime] = {}

    for item in sorted(history, key=lambda row: (row.scheduled_at, row.race_id, row.horse_id)):
        key = (item.race_id, item.horse_id)
        values = (win_credit[key], top3_credit[key])
        global_rate.add(*values)
        horse_rates[item.horse_id].add(*values)
        venue_rates[(item.horse_id, item.venue)].add(*values)
        surface_rates[(item.horse_id, item.surface)].add(*values)
        condition_rates[(item.horse_id, item.track_condition)].add(*values)
        distance_rates[(item.horse_id, distance_band(item.distance_m))].add(*values)
        jockey_rates[item.jockey_id].add(*values)
        trainer_rates[item.trainer_id].add(*values)
        latest_run[item.horse_id] = item.scheduled_at

    win_prior = global_rate.wins / global_rate.starts if global_rate.starts else 0.5
    top3_prior = global_rate.top3 / global_rate.starts if global_rate.starts else 0.5

    def win_rate(rate: _Rate) -> float:
        return _smoothed(rate.wins, rate.starts, win_prior, prior_strength)

    rows = []
    for runner in targets:
        horse = horse_rates[runner.horse_id]
        venue = venue_rates[(runner.horse_id, runner.venue)]
        surface = surface_rates[(runner.horse_id, runner.surface)]
        condition = condition_rates[(runner.horse_id, runner.track_condition)]
        band = distance_band(runner.distance_m)
        distance = distance_rates[(runner.horse_id, band)]
        jockey = jockey_rates[runner.jockey_id]
        trainer = trainer_rates[runner.trainer_id]
        previous = latest_run.get(runner.horse_id)
        rows.append(FeatureRow(
            race_id=runner.race_id,
            horse_id=runner.horse_id,
            observed_at=runner.observed_at,
            distance_band=band,
            post_position=runner.post_position,
            carried_weight_kg=runner.carried_weight_kg,
            body_weight_kg=runner.body_weight_kg,
            days_since_last_run=(runner.scheduled_at - previous).days if previous else None,
            horse_starts=horse.starts,
            horse_win_rate=win_rate(horse),
            horse_top3_rate=_smoothed(horse.top3, horse.starts, top3_prior, prior_strength),
            horse_venue_starts=venue.starts,
            horse_venue_win_rate=win_rate(venue),
            horse_surface_starts=surface.starts,
            horse_surface_win_rate=win_rate(surface),
            horse_track_condition_starts=condition.starts,
            horse_track_condition_win_rate=win_rate(condition),
            horse_distance_band_starts=distance.starts,
            horse_distance_band_win_rate=win_rate(distance),
            jockey_starts=jockey.starts,
            jockey_win_rate=win_rate(jockey),
            trainer_starts=trainer.starts,
            trainer_win_rate=win_rate(trainer),
        ))
    return tuple(rows)
