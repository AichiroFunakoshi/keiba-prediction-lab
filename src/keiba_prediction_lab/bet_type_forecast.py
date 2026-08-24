"""Pre-race probability tables and non-purchased candidates for every bet type."""

import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations as unordered_combinations
from itertools import permutations
from pathlib import Path

from .domain import BetType, PredictionRecord, validate_race_predictions
from .frozen import PredictionPhase
from .trifecta import TrifectaCombination, rank_trifecta_combinations


BET_TYPE_FORECAST_SCHEMA_VERSION = "1.0"
BET_TYPE_GENERATOR_VERSION = "plackett-luce-marginals-v1"
_UNORDERED_BET_TYPES = frozenset((BetType.QUINELLA, BetType.TRIO))
_PROBABILITY_TOLERANCE = 1e-8


@dataclass(frozen=True)
class BetTypeProbability:
    """One outcome probability for a specific bet type."""

    bet_type: BetType
    selection: tuple[str, ...]
    probability: float

    def __post_init__(self) -> None:
        if not isinstance(self.bet_type, BetType):
            raise ValueError("bet_type must be a BetType value")
        if len(self.selection) != self.bet_type.selection_size:
            raise ValueError(
                f"{self.bet_type.value} requires "
                f"{self.bet_type.selection_size} selections"
            )
        if any(not horse_id.strip() for horse_id in self.selection):
            raise ValueError("selection identifiers must not be empty")
        if len(set(self.selection)) != len(self.selection):
            raise ValueError("selection identifiers must be unique")
        if (
            self.bet_type in _UNORDERED_BET_TYPES
            and self.selection != tuple(sorted(self.selection))
        ):
            raise ValueError("unordered bet selections must use canonical order")
        if not 0.0 <= self.probability <= 1.0:
            raise ValueError("probability must be between 0 and 1")


@dataclass(frozen=True)
class BetTypeForecast:
    """Complete probability tables and the top shadow candidate per bet type."""

    race_id: str
    place_payout_slots: int
    probabilities: tuple[BetTypeProbability, ...]
    candidates: tuple[BetTypeProbability, ...]

    def __post_init__(self) -> None:
        if not self.race_id.strip():
            raise ValueError("race_id must not be empty")
        if not self.probabilities:
            raise ValueError("probability tables must not be empty")

        groups = {
            bet_type: tuple(
                row for row in self.probabilities if row.bet_type is bet_type
            )
            for bet_type in BetType
        }
        if any(not rows for rows in groups.values()):
            raise ValueError("probability tables must contain every bet type")
        expected_order = tuple(
            row
            for bet_type in BetType
            for row in sorted(
                groups[bet_type], key=lambda item: (-item.probability, item.selection)
            )
        )
        if self.probabilities != expected_order:
            raise ValueError("probability tables must use deterministic rank order")

        win_selections = {row.selection for row in groups[BetType.WIN]}
        runner_ids = {selection[0] for selection in win_selections}
        if len(runner_ids) < 5 or len(runner_ids) != len(win_selections):
            raise ValueError("bet type forecast requires at least five unique runners")
        if (
            type(self.place_payout_slots) is not int
            or self.place_payout_slots not in (2, 3)
            or self.place_payout_slots > len(runner_ids)
        ):
            raise ValueError("place_payout_slots must be 2 or 3 and fit the race")

        ordered_runners = tuple(sorted(runner_ids))
        expected_selections = {
            BetType.WIN: {(horse_id,) for horse_id in ordered_runners},
            BetType.PLACE: {(horse_id,) for horse_id in ordered_runners},
            BetType.QUINELLA: set(unordered_combinations(ordered_runners, 2)),
            BetType.EXACTA: set(permutations(ordered_runners, 2)),
            BetType.TRIO: set(unordered_combinations(ordered_runners, 3)),
            BetType.TRIFECTA: set(permutations(ordered_runners, 3)),
        }
        expected_totals = {
            BetType.WIN: 1.0,
            BetType.PLACE: float(self.place_payout_slots),
            BetType.QUINELLA: 1.0,
            BetType.EXACTA: 1.0,
            BetType.TRIO: 1.0,
            BetType.TRIFECTA: 1.0,
        }
        for bet_type, rows in groups.items():
            selections = [row.selection for row in rows]
            if len(set(selections)) != len(selections):
                raise ValueError(f"{bet_type.value} selections must be unique")
            if set(selections) != expected_selections[bet_type]:
                raise ValueError(
                    f"{bet_type.value} probability table must cover every outcome"
                )
            if abs(sum(row.probability for row in rows) - expected_totals[bet_type]) > (
                _PROBABILITY_TOLERANCE
            ):
                raise ValueError(
                    f"{bet_type.value} probabilities have an invalid total"
                )

        if tuple(row.bet_type for row in self.candidates) != tuple(BetType):
            raise ValueError("candidates must contain one row for every bet type")
        if any(
            candidate != groups[candidate.bet_type][0]
            for candidate in self.candidates
        ):
            raise ValueError("each candidate must be the highest-ranked outcome")

    def for_bet_type(self, bet_type: BetType) -> tuple[BetTypeProbability, ...]:
        """Return the complete ranked probability table for one bet type."""
        if not isinstance(bet_type, BetType):
            raise ValueError("bet_type must be a BetType value")
        return tuple(row for row in self.probabilities if row.bet_type is bet_type)

    def candidate_for(self, bet_type: BetType) -> BetTypeProbability:
        """Return the frozen top candidate for one bet type."""
        if not isinstance(bet_type, BetType):
            raise ValueError("bet_type must be a BetType value")
        return next(row for row in self.candidates if row.bet_type is bet_type)


def _validate_trifecta_distribution(
    predictions: tuple[PredictionRecord, ...],
    combinations: tuple[TrifectaCombination, ...],
) -> None:
    if not combinations:
        raise ValueError("trifecta combinations are required")
    runner_ids = tuple(sorted(row.horse_id for row in predictions))
    expected = set(permutations(runner_ids, 3))
    actual = [row.selection for row in combinations]
    if len(set(actual)) != len(actual):
        raise ValueError("trifecta combinations must be unique")
    if set(actual) != expected:
        raise ValueError("trifecta combinations must cover every ordered outcome")
    if abs(sum(row.probability for row in combinations) - 1.0) > (
        _PROBABILITY_TOLERANCE
    ):
        raise ValueError("trifecta combination probabilities must sum to 1")


def _resolve_place_payout_slots(
    runner_count: int, place_payout_slots: int | None
) -> int:
    if place_payout_slots is None:
        return 3 if runner_count >= 8 else 2
    if (
        type(place_payout_slots) is not int
        or place_payout_slots not in (2, 3)
        or place_payout_slots > runner_count
    ):
        raise ValueError("place_payout_slots must be 2 or 3 and fit the race")
    return place_payout_slots


def build_bet_type_forecast_from_combinations(
    predictions: Sequence[PredictionRecord],
    combinations: Sequence[TrifectaCombination],
    *,
    place_payout_slots: int | None = None,
) -> BetTypeForecast:
    """Derive all six bet-type tables from one ordered top-three distribution."""
    predictions = tuple(predictions)
    combinations = tuple(combinations)
    if len(predictions) < 5:
        raise ValueError("at least five runners are required for all six bet types")
    validate_race_predictions(predictions, tolerance=_PROBABILITY_TOLERANCE)
    _validate_trifecta_distribution(predictions, combinations)
    place_payout_slots = _resolve_place_payout_slots(
        len(predictions), place_payout_slots
    )

    probability_maps: dict[BetType, dict[tuple[str, ...], float]] = {
        bet_type: defaultdict(float) for bet_type in BetType
    }
    for row in combinations:
        exacta = row.selection[:2]
        quinella = tuple(sorted(exacta))
        trio = tuple(sorted(row.selection))
        probability_maps[BetType.WIN][(row.selection[0],)] += row.probability
        for horse_id in row.selection[:place_payout_slots]:
            probability_maps[BetType.PLACE][(horse_id,)] += row.probability
        probability_maps[BetType.EXACTA][exacta] += row.probability
        probability_maps[BetType.QUINELLA][quinella] += row.probability
        probability_maps[BetType.TRIO][trio] += row.probability
        probability_maps[BetType.TRIFECTA][row.selection] = row.probability

    ranked = tuple(
        probability
        for bet_type in BetType
        for probability in sorted(
            (
                BetTypeProbability(bet_type, selection, value)
                for selection, value in probability_maps[bet_type].items()
            ),
            key=lambda item: (-item.probability, item.selection),
        )
    )
    candidates = tuple(
        next(row for row in ranked if row.bet_type is bet_type)
        for bet_type in BetType
    )
    return BetTypeForecast(
        race_id=predictions[0].race_id,
        place_payout_slots=place_payout_slots,
        probabilities=ranked,
        candidates=candidates,
    )


def build_bet_type_forecast(
    predictions: Sequence[PredictionRecord],
    *,
    place_payout_slots: int | None = None,
) -> BetTypeForecast:
    """Build baseline bet-type tables using Plackett-Luce finish probabilities."""
    predictions = tuple(predictions)
    if len(predictions) < 5:
        raise ValueError("at least five runners are required for all six bet types")
    return build_bet_type_forecast_from_combinations(
        predictions,
        rank_trifecta_combinations(predictions),
        place_payout_slots=place_payout_slots,
    )


@dataclass(frozen=True)
class FrozenBetTypeForecast:
    """Immutable pre-race snapshot of every research-only bet-type candidate."""

    scheduled_at: datetime
    frozen_at: datetime
    source_predicted_at: datetime
    phase: PredictionPhase
    input_data_version: str
    model_version: str
    generator_version: str
    forecast: BetTypeForecast

    def __post_init__(self) -> None:
        for field_name in ("scheduled_at", "frozen_at", "source_predicted_at"):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware")
        if self.frozen_at >= self.scheduled_at:
            raise ValueError("frozen_at must be before scheduled_at")
        if self.source_predicted_at > self.frozen_at:
            raise ValueError("source_predicted_at must not be later than frozen_at")
        if not isinstance(self.phase, PredictionPhase):
            raise ValueError("phase must be a PredictionPhase value")
        if not all((
            self.input_data_version.strip(),
            self.model_version.strip(),
            self.generator_version.strip(),
        )):
            raise ValueError("snapshot version identifiers must not be empty")


def freeze_built_bet_type_forecast(
    forecast: BetTypeForecast,
    *,
    scheduled_at: datetime,
    frozen_at: datetime,
    source_predicted_at: datetime,
    phase: PredictionPhase,
    input_data_version: str,
    model_version: str,
    generator_version: str = BET_TYPE_GENERATOR_VERSION,
) -> FrozenBetTypeForecast:
    """Freeze a pre-built, versioned set of six bet-type probability tables."""
    return FrozenBetTypeForecast(
        scheduled_at=scheduled_at,
        frozen_at=frozen_at,
        source_predicted_at=source_predicted_at,
        phase=phase,
        input_data_version=input_data_version,
        model_version=model_version,
        generator_version=generator_version,
        forecast=forecast,
    )


def freeze_bet_type_forecast(
    predictions: Sequence[PredictionRecord],
    *,
    scheduled_at: datetime,
    frozen_at: datetime,
    phase: PredictionPhase,
    input_data_version: str,
    generator_version: str = BET_TYPE_GENERATOR_VERSION,
    place_payout_slots: int | None = None,
) -> FrozenBetTypeForecast:
    """Build and freeze baseline candidates before the race starts."""
    predictions = tuple(predictions)
    if not predictions:
        raise ValueError("at least one prediction is required")
    predicted_times = {row.predicted_at for row in predictions}
    model_versions = {row.model_version for row in predictions}
    if len(predicted_times) != 1 or len(model_versions) != 1:
        raise ValueError("source predictions must share predicted_at and model_version")
    return freeze_built_bet_type_forecast(
        build_bet_type_forecast(
            predictions, place_payout_slots=place_payout_slots
        ),
        scheduled_at=scheduled_at,
        frozen_at=frozen_at,
        source_predicted_at=next(iter(predicted_times)),
        phase=phase,
        input_data_version=input_data_version,
        model_version=next(iter(model_versions)),
        generator_version=generator_version,
    )


def _probability_payload(row: BetTypeProbability) -> dict[str, object]:
    return {
        "bet_type": row.bet_type.value,
        "selection": list(row.selection),
        "probability": row.probability,
    }


def _payload(snapshot: FrozenBetTypeForecast) -> dict[str, object]:
    return {
        "race_id": snapshot.forecast.race_id,
        "scheduled_at": snapshot.scheduled_at.isoformat(),
        "frozen_at": snapshot.frozen_at.isoformat(),
        "source_predicted_at": snapshot.source_predicted_at.isoformat(),
        "phase": snapshot.phase.value,
        "input_data_version": snapshot.input_data_version,
        "model_version": snapshot.model_version,
        "generator_version": snapshot.generator_version,
        "place_payout_slots": snapshot.forecast.place_payout_slots,
        "probabilities": [
            _probability_payload(row) for row in snapshot.forecast.probabilities
        ],
        "candidates": [
            _probability_payload(row) for row in snapshot.forecast.candidates
        ],
    }


def _canonical(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def save_frozen_bet_type_forecast(
    snapshot: FrozenBetTypeForecast, path: str | Path
) -> str:
    """Create a new integrity-protected snapshot without overwriting a prior one."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _payload(snapshot)
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    envelope = {
        "schema_version": BET_TYPE_FORECAST_SCHEMA_VERSION,
        "sha256": digest,
        "payload": payload,
    }
    with target.open("x", encoding="utf-8") as handle:
        json.dump(envelope, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    return digest


def _load_probability(payload: dict[str, object]) -> BetTypeProbability:
    return BetTypeProbability(
        bet_type=BetType(payload["bet_type"]),  # type: ignore[arg-type]
        selection=tuple(payload["selection"]),  # type: ignore[arg-type]
        probability=payload["probability"],  # type: ignore[arg-type]
    )


def load_frozen_bet_type_forecast_bytes(
    content: bytes,
) -> FrozenBetTypeForecast:
    """Load one immutable byte snapshot of a bet-type forecast."""
    envelope = json.loads(content.decode("utf-8"))
    if envelope.get("schema_version") != BET_TYPE_FORECAST_SCHEMA_VERSION:
        raise ValueError("unsupported frozen bet type schema_version")
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("frozen bet type payload must be an object")
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    if digest != envelope.get("sha256"):
        raise ValueError("frozen bet type forecast integrity check failed")

    forecast = BetTypeForecast(
        race_id=payload["race_id"],
        place_payout_slots=payload["place_payout_slots"],
        probabilities=tuple(
            _load_probability(row) for row in payload["probabilities"]
        ),
        candidates=tuple(
            _load_probability(row) for row in payload["candidates"]
        ),
    )
    return FrozenBetTypeForecast(
        scheduled_at=datetime.fromisoformat(payload["scheduled_at"]),
        frozen_at=datetime.fromisoformat(payload["frozen_at"]),
        source_predicted_at=datetime.fromisoformat(payload["source_predicted_at"]),
        phase=PredictionPhase(payload["phase"]),
        input_data_version=payload["input_data_version"],
        model_version=payload["model_version"],
        generator_version=payload["generator_version"],
        forecast=forecast,
    )


def load_frozen_bet_type_forecast(
    path: str | Path,
) -> FrozenBetTypeForecast:
    return load_frozen_bet_type_forecast_bytes(Path(path).read_bytes())
