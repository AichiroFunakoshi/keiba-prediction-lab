"""Create a self-contained synthetic snapshot for the read-only local UI."""

import csv
import json
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .app_snapshot import build_read_only_app_snapshot
from .local_adapter import HISTORY_COLUMNS, TARGET_COLUMNS, TRAINING_COLUMNS
from .local_pipeline import (
    PACE_PROFILE_COLUMNS,
    build_local_race_prediction,
    save_local_pipeline_run,
)
from .model_artifact import (
    ModelTrainingParameters,
    save_trained_model_artifact,
    train_local_model_artifact,
)
from .walk_forward_report import (
    evaluate_local_walk_forward,
    save_walk_forward_artifact,
)


JST = ZoneInfo("Asia/Tokyo")
DEMO_RACE_COUNT = 12
DEMO_TRAINING_RACE_COUNT = 20
DEMO_CALIBRATION_RACE_COUNT = 10
DEMO_EVALUATION_RACE_COUNT = 300


@dataclass(frozen=True)
class UiDemoArtifacts:
    root: Path
    race_day_manifest: Path
    walk_forward_report: Path
    race_count: int


def _write_csv(
    path: Path,
    columns: frozenset[str],
    rows: list[dict[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted(columns))
        writer.writeheader()
        writer.writerows(rows)


def _historical_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    first_day = datetime(2025, 1, 1, 10, 0, tzinfo=JST)
    total_races = (
        DEMO_TRAINING_RACE_COUNT
        + DEMO_CALIBRATION_RACE_COUNT
        + DEMO_EVALUATION_RACE_COUNT
    )
    for race_index in range(total_races):
        observed_at = first_day + timedelta(days=race_index)
        scheduled_at = observed_at + timedelta(hours=2)
        result_known_at = scheduled_at + timedelta(minutes=20)
        for horse in range(1, 6):
            finish = ((horse + race_index - 1) % 5) + 1
            rows.append({
                "race_id": f"synthetic-history-{race_index + 1:03d}",
                "scheduled_at": scheduled_at.isoformat(),
                "observed_at": observed_at.isoformat(),
                "result_known_at": result_known_at.isoformat(),
                "horse_id": f"合成デモ馬{horse}",
                "jockey_id": f"合成デモ騎手{horse}",
                "trainer_id": f"合成デモ調教師{horse}",
                "venue": "合成東京",
                "surface": "turf",
                "track_condition": "good",
                "distance_m": "1600",
                "post_position": str(horse),
                "carried_weight_kg": "56",
                "body_weight_kg": str(470 + horse * 2 + race_index % 3),
                "finish_position": str(finish),
            })
    return rows


def _target_rows(race_number: int, scheduled_at: datetime) -> list[dict[str, str]]:
    race_id = f"synthetic-ui-demo-tokyo-{race_number:02d}"
    observed_at = datetime(2026, 2, 1, 9, 0, tzinfo=JST).isoformat()
    rows: list[dict[str, str]] = []
    for horse in range(1, 6):
        rows.append({
            "race_id": race_id,
            "scheduled_at": scheduled_at.isoformat(),
            "observed_at": observed_at,
            "horse_id": f"合成デモ馬{horse}",
            "jockey_id": f"合成デモ騎手{horse}",
            "trainer_id": f"合成デモ調教師{horse}",
            "venue": "合成東京",
            "surface": "turf",
            "track_condition": "good",
            "distance_m": str(1200 + 200 * ((race_number - 1) % 5)),
            "post_position": str(((horse + race_number - 2) % 5) + 1),
            "carried_weight_kg": str(55 + (horse % 3) * 0.5),
            "body_weight_kg": str(474 + horse * 2 + race_number % 4),
        })
    return rows


def _pace_rows(race_number: int) -> list[dict[str, str]]:
    race_id = f"synthetic-ui-demo-tokyo-{race_number:02d}"
    styles = ("leader", "presser", "stalker", "closer", "closer")
    return [{
        "race_id": race_id,
        "horse_id": f"合成デモ馬{horse}",
        "observed_at": "2026-02-01T09:00:00+09:00",
        "running_style": styles[horse - 1],
        "early_speed": str(0.95 - horse * 0.11),
        "late_speed": str(0.25 + horse * 0.12),
        "pace_resilience": str(0.55 + horse * 0.05),
    } for horse in range(1, 6)]


def _write_demo(root: Path) -> None:
    inputs = root / "inputs"
    predictions = root / "predictions"
    historical = _historical_rows()
    training = inputs / "training.csv"
    history = inputs / "history.csv"
    model = root / "model.json"
    windows = inputs / "walk-forward-windows.json"
    report = root / "walk-forward.json"

    _write_csv(training, TRAINING_COLUMNS, historical)
    _write_csv(
        history,
        HISTORY_COLUMNS,
        [{key: value for key, value in row.items() if key != "observed_at"}
         for row in historical],
    )
    first_day = datetime(2025, 1, 1, 10, 0, tzinfo=JST)
    train_end = first_day + timedelta(days=DEMO_TRAINING_RACE_COUNT - 1)
    calibration_end = train_end + timedelta(days=DEMO_CALIBRATION_RACE_COUNT)
    evaluation_end = calibration_end + timedelta(days=DEMO_EVALUATION_RACE_COUNT)
    windows.write_text(json.dumps([{
        "train_end": train_end.isoformat(),
        "calibration_end": calibration_end.isoformat(),
        "evaluation_end": evaluation_end.isoformat(),
    }], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    trained = train_local_model_artifact(
        training, parameters=ModelTrainingParameters(epochs=20)
    )
    save_trained_model_artifact(trained, model)
    save_walk_forward_artifact(
        evaluate_local_walk_forward(training, windows), report
    )

    race_entries: list[dict[str, object]] = []
    first_start = datetime(2026, 2, 1, 10, 0, tzinfo=JST)
    frozen_at = datetime(2026, 2, 1, 9, 5, tzinfo=JST)
    for race_number in range(1, DEMO_RACE_COUNT + 1):
        race_id = f"synthetic-ui-demo-tokyo-{race_number:02d}"
        target = inputs / f"targets-{race_number:02d}.csv"
        profiles = inputs / f"pace-profiles-{race_number:02d}.csv"
        scenario = inputs / f"pace-scenario-{race_number:02d}.json"
        scheduled_at = first_start + timedelta(minutes=30 * (race_number - 1))
        _write_csv(target, TARGET_COLUMNS, _target_rows(race_number, scheduled_at))
        _write_csv(profiles, PACE_PROFILE_COLUMNS, _pace_rows(race_number))
        scenario.write_text(json.dumps({
            "race_id": race_id,
            "observed_at": "2026-02-01T09:00:00+09:00",
            "expected_pace": ("fast" if race_number % 3 == 0 else "average"),
            "confidence": 0.7,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output = predictions / f"tokyo-{race_number:02d}"
        run = build_local_race_prediction(
            model, history, target, profiles, scenario, frozen_at=frozen_at
        )
        save_local_pipeline_run(run, output)
        race_entries.append({
            "race_number": race_number,
            "prediction_bundle": f"predictions/tokyo-{race_number:02d}",
        })

    manifest = root / "race-day.json"
    manifest.write_text(json.dumps({
        "schema_version": "1.0",
        "race_date": "2026-02-01",
        "venues": [{"venue": "東京（合成デモ）", "races": race_entries}],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "README.txt").write_text(
        "このフォルダはUI確認専用の合成デモです。実レースの予想、精度評価、購入判断には使用できません。\n",
        encoding="utf-8",
    )
    build_read_only_app_snapshot(
        race_day_manifest=manifest,
        walk_forward_report=report,
    )


def create_ui_demo(directory: str | Path) -> UiDemoArtifacts:
    """Create audited synthetic demo artifacts without overwriting any path."""
    target = Path(directory)
    if target.exists():
        raise FileExistsError(f"UI demo output already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(
        prefix=f".{target.name}-creating-", dir=target.parent
    ))
    try:
        _write_demo(temporary)
        temporary.rename(target)
    except Exception:
        shutil.rmtree(temporary)
        raise
    return UiDemoArtifacts(
        root=target,
        race_day_manifest=target / "race-day.json",
        walk_forward_report=target / "walk-forward.json",
        race_count=DEMO_RACE_COUNT,
    )


def load_ui_demo(directory: str | Path) -> UiDemoArtifacts:
    """Validate the expected demo artifacts before they are served."""
    root = Path(directory)
    manifest = root / "race-day.json"
    report = root / "walk-forward.json"
    snapshot = build_read_only_app_snapshot(
        race_day_manifest=manifest,
        walk_forward_report=report,
    )
    if snapshot.race_day is None:
        raise ValueError("UI demo does not contain a race-day snapshot")
    race_count = sum(len(venue.races) for venue in snapshot.race_day.venues)
    return UiDemoArtifacts(root, manifest, report, race_count)
