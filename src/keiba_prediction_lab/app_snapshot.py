"""Read-only, UI-facing snapshots built only from audited artifacts."""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .bundle_audit import load_audited_prediction_bundle
from .evaluation import BET_TYPE_LABELS_JA
from .pipeline import PIPELINE_POLICY_VERSION
from .walk_forward_report import load_walk_forward_artifact
from .win5 import load_win5_forecast


@dataclass(frozen=True)
class RunnerSnapshot:
    predicted_rank: int
    horse_id: str
    win_probability: float
    top3_probability: float


@dataclass(frozen=True)
class ShadowPortfolioSnapshot:
    generator: str
    strategy: str
    ticket_count: int
    cumulative_probability: float
    stake_yen: int = 0


@dataclass(frozen=True)
class BetTypeCandidateSnapshot:
    bet_type: str
    label_ja: str
    selection: tuple[str, ...]
    probability: float
    stake_yen: int = 0


@dataclass(frozen=True)
class PredictionAppSnapshot:
    race_id: str
    scheduled_at: str
    frozen_at: str
    model_version: str
    input_data_version: str
    runners: tuple[RunnerSnapshot, ...]
    actual_selection: tuple[str, str, str]
    actual_stake_yen: int
    shadow_portfolios: tuple[ShadowPortfolioSnapshot, ...]
    bet_type_candidates: tuple[BetTypeCandidateSnapshot, ...]


@dataclass(frozen=True)
class WalkForwardAppSnapshot:
    fold_count: int
    evaluation_race_count: int
    evaluation_runner_count: int
    model_top1_accuracy: float
    uniform_top1_accuracy: float
    model_win_brier_score: float
    uniform_win_brier_score: float
    model_win_log_loss: float
    uniform_win_log_loss: float
    expected_calibration_error: float
    training_sha256: str
    windows_sha256: str


@dataclass(frozen=True)
class Win5LegAppSnapshot:
    race_id: str
    scheduled_at: str
    selected_horse_id: str
    selected_win_probability: float


@dataclass(frozen=True)
class Win5AppSnapshot:
    frozen_at: str
    selection: tuple[str, str, str, str, str]
    joint_probability: float
    independence_assumption: str
    stake_yen: int
    legs: tuple[Win5LegAppSnapshot, ...]


@dataclass(frozen=True)
class RunnerDisplayAppSnapshot:
    horse_id: str
    horse_number: int
    horse_name: str
    frame_number: int | None = None


@dataclass(frozen=True)
class RaceDayRaceAppSnapshot:
    race_number: int
    prediction: PredictionAppSnapshot
    runner_display: tuple[RunnerDisplayAppSnapshot, ...] = ()


@dataclass(frozen=True)
class RaceDayVenueAppSnapshot:
    venue: str
    races: tuple[RaceDayRaceAppSnapshot, ...]


@dataclass(frozen=True)
class RaceDayAppSnapshot:
    race_date: str
    venues: tuple[RaceDayVenueAppSnapshot, ...]


@dataclass(frozen=True)
class ReadOnlyAppSnapshot:
    policy_version: str
    actual_purchase_policy: str
    prediction: PredictionAppSnapshot | None
    walk_forward: WalkForwardAppSnapshot | None
    win5: Win5AppSnapshot | None = None
    race_day: RaceDayAppSnapshot | None = None

    def __post_init__(self) -> None:
        if (
            self.prediction is None
            and self.walk_forward is None
            and self.win5 is None
            and self.race_day is None
        ):
            raise ValueError("at least one audited artifact is required")

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "actual_purchase_policy": self.actual_purchase_policy,
            "prediction": _prediction_dict(self.prediction),
            "walk_forward": _walk_forward_dict(self.walk_forward),
            "win5": _win5_dict(self.win5),
            "race_day": _race_day_dict(self.race_day),
        }


def _prediction_snapshot(directory: str | Path) -> PredictionAppSnapshot:
    audited = load_audited_prediction_bundle(directory)
    bundle = audited.bundle
    actual = bundle.actual_prediction
    runners = tuple(
        RunnerSnapshot(
            row.predicted_rank,
            row.horse_id,
            row.win_probability,
            row.top3_probability,
        )
        for row in sorted(actual.predictions, key=lambda item: item.predicted_rank)
    )
    shadow_portfolios = tuple(
        ShadowPortfolioSnapshot(
            generator=generator,
            strategy=portfolio.strategy.value,
            ticket_count=portfolio.ticket_count,
            cumulative_probability=portfolio.cumulative_probability,
        )
        for generator, frozen in (
            ("baseline", bundle.baseline_shadow),
            ("pace", bundle.pace_shadow),
        )
        for portfolio in frozen.forecast.shadow_portfolios
    )
    candidates = tuple(
        BetTypeCandidateSnapshot(
            candidate.bet_type.value,
            BET_TYPE_LABELS_JA[candidate.bet_type],
            candidate.selection,
            candidate.probability,
        )
        for candidate in bundle.bet_type_shadow.forecast.candidates
    )
    ticket = actual.trifecta_tickets[0]
    return PredictionAppSnapshot(
        race_id=actual.race_id,
        scheduled_at=actual.scheduled_at.isoformat(),
        frozen_at=actual.frozen_at.isoformat(),
        model_version=actual.model_version,
        input_data_version=actual.input_data_version,
        runners=runners,
        actual_selection=ticket.selection,
        actual_stake_yen=ticket.stake_yen,
        shadow_portfolios=shadow_portfolios,
        bet_type_candidates=candidates,
    )


def _walk_forward_snapshot(path: str | Path) -> WalkForwardAppSnapshot:
    artifact = load_walk_forward_artifact(path)
    result = artifact.result
    model = result.aggregate_model_score
    uniform = result.aggregate_uniform_score
    return WalkForwardAppSnapshot(
        fold_count=len(result.folds),
        evaluation_race_count=model.race_count,
        evaluation_runner_count=model.runner_count,
        model_top1_accuracy=model.top1_accuracy,
        uniform_top1_accuracy=uniform.top1_accuracy,
        model_win_brier_score=model.win_brier_score,
        uniform_win_brier_score=uniform.win_brier_score,
        model_win_log_loss=model.win_log_loss,
        uniform_win_log_loss=uniform.win_log_loss,
        expected_calibration_error=result.calibration.expected_calibration_error,
        training_sha256=artifact.training_sha256,
        windows_sha256=artifact.windows_sha256,
    )


def _win5_snapshot(path: str | Path) -> Win5AppSnapshot:
    forecast = load_win5_forecast(path)
    return Win5AppSnapshot(
        frozen_at=forecast.frozen_at.isoformat(),
        selection=forecast.selection,
        joint_probability=forecast.joint_probability,
        independence_assumption=(
            "5レース間を独立と仮定し、各選択馬の1着確率を掛け合わせる"
        ),
        stake_yen=forecast.stake_yen,
        legs=tuple(
            Win5LegAppSnapshot(
                race_id=leg.race_id,
                scheduled_at=leg.scheduled_at.isoformat(),
                selected_horse_id=leg.runners[0].horse_id,
                selected_win_probability=leg.runners[0].win_probability,
            )
            for leg in forecast.legs
        ),
    )


def _race_day_snapshot(path: str | Path) -> RaceDayAppSnapshot:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"race-day manifest contains duplicate key: {key}")
            value[key] = item
        return value

    manifest_path = Path(path)
    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8"),
        object_pairs_hook=unique_object,
    )
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version", "race_date", "venues"
    }:
        raise ValueError("invalid race-day manifest keys")
    if manifest["schema_version"] != "1.0":
        raise ValueError("unsupported race-day manifest schema_version")
    if not isinstance(manifest["race_date"], str):
        raise ValueError("race-day manifest race_date must be a string")
    try:
        race_date = date.fromisoformat(manifest["race_date"])
    except ValueError as error:
        raise ValueError("race-day manifest race_date must be ISO format") from error
    raw_venues = manifest["venues"]
    if not isinstance(raw_venues, list) or not raw_venues:
        raise ValueError("race-day manifest requires at least one venue")
    venues: list[RaceDayVenueAppSnapshot] = []
    seen_venues: set[str] = set()
    seen_race_ids: set[str] = set()
    for raw_venue in raw_venues:
        if not isinstance(raw_venue, dict) or set(raw_venue) != {"venue", "races"}:
            raise ValueError("invalid race-day venue entry")
        venue = raw_venue["venue"]
        raw_races = raw_venue["races"]
        normalized_venue = venue.strip() if isinstance(venue, str) else ""
        if not normalized_venue or normalized_venue in seen_venues:
            raise ValueError("race-day venues must have unique non-empty names")
        if not isinstance(raw_races, list) or not raw_races:
            raise ValueError("each race-day venue requires at least one race")
        races: list[RaceDayRaceAppSnapshot] = []
        seen_numbers: set[int] = set()
        for raw_race in raw_races:
            if not isinstance(raw_race, dict) or not {
                "race_number", "prediction_bundle"
            } <= set(raw_race) or set(raw_race) - {
                "race_number", "prediction_bundle", "runner_display"
            }:
                raise ValueError("invalid race-day race entry")
            race_number = raw_race["race_number"]
            bundle_path = raw_race["prediction_bundle"]
            if type(race_number) is not int or not 1 <= race_number <= 12:
                raise ValueError("race_number must be an integer from 1 to 12")
            if race_number in seen_numbers:
                raise ValueError("race numbers must be unique within a venue")
            if not isinstance(bundle_path, str) or not bundle_path.strip():
                raise ValueError("prediction_bundle must be a non-empty path")
            resolved = Path(bundle_path)
            if not resolved.is_absolute():
                resolved = manifest_path.parent / resolved
            prediction = _prediction_snapshot(resolved)
            raw_display = raw_race.get("runner_display", [])
            if not isinstance(raw_display, list):
                raise ValueError("runner_display must be a list")
            runner_ids = {runner.horse_id for runner in prediction.runners}
            display: list[RunnerDisplayAppSnapshot] = []
            display_ids: set[str] = set()
            horse_numbers: set[int] = set()
            for item in raw_display:
                required_display_keys = {"horse_id", "horse_number", "horse_name"}
                if (
                    not isinstance(item, dict)
                    or not required_display_keys <= set(item)
                    or set(item) - (required_display_keys | {"frame_number"})
                ):
                    raise ValueError("invalid runner_display entry")
                horse_id = item["horse_id"]
                horse_number = item["horse_number"]
                horse_name = item["horse_name"]
                frame_number = item.get("frame_number")
                normalized_horse_id = horse_id.strip() if isinstance(horse_id, str) else ""
                if not normalized_horse_id or normalized_horse_id not in runner_ids:
                    raise ValueError("runner_display horse_id must identify a predicted runner")
                if normalized_horse_id in display_ids:
                    raise ValueError("runner_display horse_id values must be unique")
                if type(horse_number) is not int or not 1 <= horse_number <= 18:
                    raise ValueError("horse_number must be an integer from 1 to 18")
                if horse_number in horse_numbers:
                    raise ValueError("runner_display horse_number values must be unique")
                if not isinstance(horse_name, str) or not horse_name.strip():
                    raise ValueError("horse_name must be non-empty")
                if frame_number is not None and (
                    type(frame_number) is not int or not 1 <= frame_number <= 8
                ):
                    raise ValueError("frame_number must be an integer from 1 to 8")
                display.append(RunnerDisplayAppSnapshot(
                    normalized_horse_id, horse_number, horse_name.strip(), frame_number
                ))
                display_ids.add(normalized_horse_id)
                horse_numbers.add(horse_number)
            scheduled_date = date.fromisoformat(prediction.scheduled_at[:10])
            if scheduled_date != race_date:
                raise ValueError("race-day bundle date does not match race_date")
            if prediction.race_id in seen_race_ids:
                raise ValueError("race-day bundles must have unique race_id values")
            races.append(RaceDayRaceAppSnapshot(race_number, prediction, tuple(display)))
            seen_numbers.add(race_number)
            seen_race_ids.add(prediction.race_id)
        venues.append(RaceDayVenueAppSnapshot(
            normalized_venue,
            tuple(sorted(races, key=lambda row: row.race_number)),
        ))
        seen_venues.add(normalized_venue)
    return RaceDayAppSnapshot(race_date.isoformat(), tuple(venues))


def build_read_only_app_snapshot(
    *,
    prediction_directory: str | Path | None = None,
    walk_forward_report: str | Path | None = None,
    win5_forecast: str | Path | None = None,
    race_day_manifest: str | Path | None = None,
) -> ReadOnlyAppSnapshot:
    """Build UI data from explicitly selected and fully audited artifacts."""
    if (
        prediction_directory is None
        and walk_forward_report is None
        and win5_forecast is None
        and race_day_manifest is None
    ):
        raise ValueError("at least one artifact path is required")
    return ReadOnlyAppSnapshot(
        policy_version=PIPELINE_POLICY_VERSION,
        actual_purchase_policy="三連単1点100円。影予測は購入額0円。",
        prediction=(
            _prediction_snapshot(prediction_directory)
            if prediction_directory is not None else None
        ),
        walk_forward=(
            _walk_forward_snapshot(walk_forward_report)
            if walk_forward_report is not None else None
        ),
        win5=(
            _win5_snapshot(win5_forecast)
            if win5_forecast is not None else None
        ),
        race_day=(
            _race_day_snapshot(race_day_manifest)
            if race_day_manifest is not None else None
        ),
    )


def _prediction_dict(value: PredictionAppSnapshot | None) -> object:
    if value is None:
        return None
    return {
        "race_id": value.race_id,
        "scheduled_at": value.scheduled_at,
        "frozen_at": value.frozen_at,
        "model_version": value.model_version,
        "input_data_version": value.input_data_version,
        "runners": [
            {
                "predicted_rank": row.predicted_rank,
                "horse_id": row.horse_id,
                "win_probability": row.win_probability,
                "top3_probability": row.top3_probability,
            }
            for row in value.runners
        ],
        "actual": {
            "bet_type": "trifecta",
            "selection": list(value.actual_selection),
            "stake_yen": value.actual_stake_yen,
        },
        "shadow_portfolios": [
            {
                "generator": row.generator,
                "strategy": row.strategy,
                "ticket_count": row.ticket_count,
                "cumulative_probability": row.cumulative_probability,
                "stake_yen": row.stake_yen,
            }
            for row in value.shadow_portfolios
        ],
        "bet_type_candidates": [
            {
                "bet_type": row.bet_type,
                "label_ja": row.label_ja,
                "selection": list(row.selection),
                "probability": row.probability,
                "stake_yen": row.stake_yen,
            }
            for row in value.bet_type_candidates
        ],
    }


def _walk_forward_dict(value: WalkForwardAppSnapshot | None) -> object:
    if value is None:
        return None
    return {
        "fold_count": value.fold_count,
        "evaluation_race_count": value.evaluation_race_count,
        "evaluation_runner_count": value.evaluation_runner_count,
        "model": {
            "top1_accuracy": value.model_top1_accuracy,
            "win_brier_score": value.model_win_brier_score,
            "win_log_loss": value.model_win_log_loss,
        },
        "uniform": {
            "top1_accuracy": value.uniform_top1_accuracy,
            "win_brier_score": value.uniform_win_brier_score,
            "win_log_loss": value.uniform_win_log_loss,
        },
        "expected_calibration_error": value.expected_calibration_error,
        "training_sha256": value.training_sha256,
        "windows_sha256": value.windows_sha256,
    }


def _win5_dict(value: Win5AppSnapshot | None) -> object:
    if value is None:
        return None
    return {
        "frozen_at": value.frozen_at,
        "purchase_status": "shadow_only",
        "stake_yen": value.stake_yen,
        "selection": list(value.selection),
        "joint_probability": value.joint_probability,
        "independence_assumption": value.independence_assumption,
        "legs": [
            {
                "race_id": leg.race_id,
                "scheduled_at": leg.scheduled_at,
                "selected_horse_id": leg.selected_horse_id,
                "selected_win_probability": leg.selected_win_probability,
            }
            for leg in value.legs
        ],
    }


def _race_day_dict(value: RaceDayAppSnapshot | None) -> object:
    if value is None:
        return None
    return {
        "race_date": value.race_date,
        "venues": [
            {
                "venue": venue.venue,
                "races": [
                    {
                        "race_number": race.race_number,
                        "prediction": _prediction_dict(race.prediction),
                        "runner_display": [
                            {
                                "horse_id": row.horse_id,
                                "horse_number": row.horse_number,
                                "horse_name": row.horse_name,
                                "frame_number": row.frame_number,
                            }
                            for row in race.runner_display
                        ],
                    }
                    for race in venue.races
                ],
            }
            for venue in value.venues
        ],
    }
