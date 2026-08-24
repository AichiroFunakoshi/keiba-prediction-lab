"""Safe starter files for local-only training and race inputs."""

import csv
import json
import shutil
from pathlib import Path

from .local_adapter import HISTORY_COLUMNS, TARGET_COLUMNS, TRAINING_COLUMNS
from .local_pipeline import PACE_PROFILE_COLUMNS


INPUT_TEMPLATE_FILES = (
    ".gitignore",
    "INPUT_GUIDE.md",
    "history.csv",
    "pace-profiles.csv",
    "pace-scenario.json",
    "targets.csv",
    "training.csv",
)


def _write_header(path: Path, columns: frozenset[str]) -> None:
    with path.open("x", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(sorted(columns))


def create_local_input_templates(directory: str | Path) -> tuple[Path, ...]:
    """Create an untracked starter directory without replacing existing data."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=False)
    try:
        (target / ".gitignore").write_text(
            "*\n!.gitignore\n!INPUT_GUIDE.md\n", encoding="utf-8"
        )
        _write_header(target / "training.csv", TRAINING_COLUMNS)
        _write_header(target / "history.csv", HISTORY_COLUMNS)
        _write_header(target / "targets.csv", TARGET_COLUMNS)
        _write_header(target / "pace-profiles.csv", PACE_PROFILE_COLUMNS)
        with (target / "pace-scenario.json").open(
            "x", encoding="utf-8"
        ) as handle:
            json.dump({
                "race_id": "_REPLACE_RACE_ID_",
                "observed_at": "_REPLACE_TIMEZONE_AWARE_ISO8601_",
                "expected_pace": "_REPLACE_slow_average_or_fast_",
                "confidence": "_REPLACE_NUMBER_FROM_0_TO_1_",
            }, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
        (target / "INPUT_GUIDE.md").write_text(
            "# ローカル入力雛形\n\n"
            "この雛形は意図的に未完成です。必須値をすべて入力するまで "
            "正式予測には使用できません。\n\n"
            "- 日時はタイムゾーン付きISO 8601で入力する。\n"
            "- `targets.csv`へ結果列を追加しない。\n"
            "- 対象、脚質、想定ペースでレース、出走馬、観測時刻を揃える。\n"
            "- `_REPLACE_..._`をすべて置き換える。\n"
            "- モデルは手編集せず`train-model`で生成する。\n"
            "- `predict-race`の前に`audit-race-inputs`を実行する。\n"
            "- 外部データは利用許諾がない限りコミットしない。\n",
            encoding="utf-8",
        )
    except Exception:
        shutil.rmtree(target)
        raise
    return tuple(target / name for name in INPUT_TEMPLATE_FILES)
