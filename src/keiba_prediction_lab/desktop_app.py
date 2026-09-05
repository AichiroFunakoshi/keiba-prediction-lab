"""Native macOS window for the audited RaceWeave read-only UI."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import importlib
import json
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Sequence

from .app_snapshot import (
    PredictionAppSnapshot,
    ReadOnlyAppSnapshot,
    RunnerDisplayAppSnapshot,
    RunnerSnapshot,
    ShadowPortfolioSnapshot,
    build_read_only_app_snapshot,
)
from .bundle_audit import load_audited_prediction_bundle
from .frame import jra_frame_number
from .local_http import LOOPBACK_HOST, create_read_only_server
from .local_adapter import load_targets_csv_bytes
from .race_day_pipeline import audit_local_race_day
from .market_blend import load_market_blend_forecast
from .ui_demo import create_ui_demo, load_ui_demo


APP_NAME = "RaceWeave"
APP_NAME_JA = "レースウィーヴ"
APP_IDENTIFIER = "jp.aichiro.raceweave"
DEFAULT_WINDOW_SIZE = (1440, 960)
MINIMUM_WINDOW_SIZE = (1051, 720)


_RACE_DAY_CHOOSER_SCRIPT = """\
try
    set selectedFile to choose file with prompt "監査して表示する race-day.json を選択してください。キャンセルすると合成デモを開きます。"
    return POSIX path of selectedFile
on error number -128
    return ""
end try
"""

_ERROR_ALERT_SCRIPT = """\
on run argv
    display alert "RaceWeaveを起動できません" message (item 1 of argv) as critical
end run
"""


def default_demo_directory(home: str | Path | None = None) -> Path:
    """Return the private per-user location for the bundled synthetic demo."""
    base = Path.home() if home is None else Path(home)
    return base / "Library" / "Application Support" / APP_NAME / "ui-demo-v1"


def repository_local_directory(
    executable: str | Path | None = None,
) -> Path | None:
    """Find this checkout's private local directory when the app lives in dist/."""
    starts = (
        Path(sys.executable if executable is None else executable).resolve(),
        Path(__file__).resolve(),
    )
    for start in starts:
        for candidate in (start.parent, *start.parents):
            if (
                (candidate / "AGENTS.md").is_file()
                and (candidate / "src" / "keiba_prediction_lab").is_dir()
            ):
                local = candidate / "local"
                return local if local.is_dir() else None
    return None


def latest_audited_race_day_manifest(
    local_directory: str | Path | None,
) -> Path | None:
    """Return the newest valid local race day without changing any artifact."""
    if local_directory is None:
        return None
    root = Path(local_directory)
    if not root.is_dir() or root.is_symlink():
        return None
    candidates: list[tuple[object, object, str, Path]] = []
    for manifest in root.rglob("race-day.json"):
        if manifest.is_symlink() or not manifest.is_file():
            continue
        try:
            audit = audit_local_race_day(manifest.parent)
        except (OSError, ValueError, UnicodeError):
            continue
        candidates.append((
            audit.race_date,
            audit.frozen_at,
            manifest.as_posix(),
            manifest,
        ))
    return max(candidates)[-1] if candidates else None


def adjacent_related_artifacts(
    manifest: str | Path,
) -> tuple[Path | None, Path | None]:
    """Resolve optional audited UI companions only from the selected day folder."""
    root = Path(manifest).parent
    walk_forward = root / "walk-forward.json"
    revised_win5 = root / "win5-market-blend.json"
    win5 = revised_win5 if revised_win5.is_file() else root / "win5.json"
    return (
        walk_forward if walk_forward.is_file() and not walk_forward.is_symlink() else None,
        win5 if win5.is_file() and not win5.is_symlink() else None,
    )


def verified_runner_display_by_race(
    manifest: str | Path,
    local_directory: str | Path | None,
) -> dict[str, tuple[RunnerDisplayAppSnapshot, ...]]:
    """Join result-free target labels only when their exact saved hash matches."""
    if local_directory is None:
        return {}
    root = Path(local_directory)
    if not root.is_dir() or root.is_symlink():
        return {}
    manifest_path = Path(manifest)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    candidates_by_name: dict[str, list[Path]] = {}
    for candidate in root.rglob("*.csv"):
        if candidate.is_file() and not candidate.is_symlink():
            candidates_by_name.setdefault(candidate.name, []).append(candidate)
    result: dict[str, tuple[RunnerDisplayAppSnapshot, ...]] = {}
    for venue in payload["venues"]:
        for race in venue["races"]:
            bundle_path = manifest_path.parent / race["prediction_bundle"]
            audited = load_audited_prediction_bundle(bundle_path)
            actual = audited.bundle.actual_prediction
            expected_ids = {row.horse_id for row in actual.predictions}
            for candidate in candidates_by_name.get(f"{actual.race_id}.csv", []):
                content = candidate.read_bytes()
                if hashlib.sha256(content).hexdigest() != audited.audit.targets_sha256:
                    continue
                targets = load_targets_csv_bytes(content, candidate.name)
                if {row.horse_id for row in targets} != expected_ids:
                    continue
                display = tuple(
                    RunnerDisplayAppSnapshot(
                        horse_id=row.horse_id,
                        horse_number=row.post_position,
                        horse_name=row.horse_id.removeprefix("horse:name:"),
                        frame_number=jra_frame_number(
                            row.post_position,
                            max(item.post_position for item in targets),
                        ),
                    )
                    for row in sorted(targets, key=lambda item: item.post_position)
                )
                result[actual.race_id] = display
                break
    return result


def _with_verified_runner_display(
    snapshot: ReadOnlyAppSnapshot,
    display_by_race: dict[str, tuple[RunnerDisplayAppSnapshot, ...]],
) -> ReadOnlyAppSnapshot:
    if snapshot.race_day is None or not display_by_race:
        return snapshot
    venues = tuple(
        replace(
            venue,
            races=tuple(
                replace(
                    race,
                    runner_display=display_by_race.get(
                        race.prediction.race_id, race.runner_display
                    ),
                )
                for race in venue.races
            ),
        )
        for venue in snapshot.race_day.venues
    )
    return replace(snapshot, race_day=replace(snapshot.race_day, venues=venues))


def _with_market_blend(
    snapshot: ReadOnlyAppSnapshot,
    market_blend_path: str | Path | None,
    race_day_manifest: str | Path,
) -> ReadOnlyAppSnapshot:
    """Overlay only integrity-checked post-odds races on the read-only view."""
    if market_blend_path is None or snapshot.race_day is None:
        return snapshot
    forecast = load_market_blend_forecast(market_blend_path)
    manifest_content = Path(race_day_manifest).read_bytes()
    if hashlib.sha256(manifest_content).hexdigest() != forecast.race_day_manifest_sha256:
        raise ValueError("市場混合予測と開催日マニフェストが一致しません")
    blend_by_race = {race.race_id: race for race in forecast.races}
    venues = []
    for venue in snapshot.race_day.venues:
        races = []
        for race in venue.races:
            blended = blend_by_race.get(race.prediction.race_id)
            if blended is None:
                races.append(race)
                continue
            prediction = PredictionAppSnapshot(
                race_id=blended.race_id,
                scheduled_at=blended.scheduled_at.isoformat(),
                frozen_at=forecast.observed_at.isoformat(),
                model_version=blended.model_version,
                input_data_version=blended.input_data_version,
                runners=tuple(RunnerSnapshot(
                    row.predicted_rank, row.horse_id, row.blended_probability,
                    row.top3_probability,
                ) for row in blended.runners),
                actual_selection=blended.trifecta_selection,
                actual_stake_yen=100,
                shadow_portfolios=tuple(ShadowPortfolioSnapshot(
                    "baseline", strategy, ticket_count, probability
                ) for strategy, ticket_count, probability in blended.shadow_portfolios),
                bet_type_candidates=(),
            )
            races.append(replace(race, prediction=prediction))
        venues.append(replace(venue, races=tuple(races)))
    return replace(snapshot, race_day=replace(snapshot.race_day, venues=tuple(venues)))


def load_or_create_demo_snapshot(directory: str | Path) -> ReadOnlyAppSnapshot:
    """Create the synthetic demo once, then re-audit it on every launch."""
    root = Path(directory)
    if not root.exists():
        create_ui_demo(root)
    demo = load_ui_demo(root)
    return build_read_only_app_snapshot(
        race_day_manifest=demo.race_day_manifest,
        walk_forward_report=demo.walk_forward_report,
    )


def choose_race_day_manifest() -> Path | None:
    """Ask macOS for one local race-day manifest without retaining its path."""
    if sys.platform != "darwin":
        return None
    completed = subprocess.run(
        ["/usr/bin/osascript", "-e", _RACE_DAY_CHOOSER_SCRIPT],
        check=True,
        capture_output=True,
        text=True,
    )
    selected = completed.stdout.strip()
    return Path(selected) if selected else None


def _show_native_error(message: str) -> None:
    """Show a Finder-visible error without interpolating it into AppleScript."""
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            ["/usr/bin/osascript", "-e", _ERROR_ALERT_SCRIPT, message],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        pass


def load_audited_race_day_snapshot(
    manifest: str | Path,
    *,
    walk_forward_report: str | Path | None = None,
    win5_forecast: str | Path | None = None,
    runner_display_search_root: str | Path | None = None,
    market_blend_forecast: str | Path | None = None,
) -> ReadOnlyAppSnapshot:
    """Re-audit a complete race day before exposing it to the desktop UI."""
    selected = Path(manifest)
    if selected.is_dir():
        selected = selected / "race-day.json"
    if selected.name != "race-day.json":
        raise ValueError("開催日成果物の race-day.json を選択してください")
    audit_local_race_day(selected.parent)
    snapshot = build_read_only_app_snapshot(
        race_day_manifest=selected,
        walk_forward_report=walk_forward_report,
        win5_forecast=win5_forecast,
    )
    snapshot = _with_verified_runner_display(
        snapshot,
        verified_runner_display_by_race(selected, runner_display_search_root),
    )
    snapshot = _with_market_blend(snapshot, market_blend_forecast, selected)
    # Detect changes that occurred while the display snapshot was assembled.
    audit_local_race_day(selected.parent)
    return snapshot


def _load_webview() -> ModuleType:
    try:
        return importlib.import_module("webview")
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "RaceWeaveのデスクトップ依存がありません。"
            "python -m pip install -e '.[desktop]' を実行してください。"
        ) from error


def _wait_for_loopback_server(port: int, timeout_seconds: float = 2.0) -> None:
    """Prevent WebKit's first navigation from racing the server thread."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        connection = http.client.HTTPConnection(LOOPBACK_HOST, port, timeout=0.2)
        try:
            connection.request("GET", "/health")
            response = connection.getresponse()
            response.read()
            if response.status == 200:
                return
        except OSError:
            time.sleep(0.02)
        finally:
            connection.close()
    raise RuntimeError("RaceWeaveのローカル表示サーバーを開始できません")


def _load_window_url(window: object, url: str) -> None:
    """Navigate only after pywebview has finished creating the native window."""
    load_url = getattr(window, "load_url", None)
    if not callable(load_url):
        raise RuntimeError("RaceWeaveのWebKitウインドウを初期化できません")
    load_url(url)


def run_desktop_window(
    snapshot: ReadOnlyAppSnapshot,
    *,
    webview_module: ModuleType | None = None,
) -> None:
    """Serve one immutable snapshot to a native WebKit window until it closes."""
    webview = webview_module or _load_webview()
    with create_read_only_server(snapshot, port=0) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://{LOOPBACK_HOST}:{server.server_address[1]}/"
        try:
            _wait_for_loopback_server(server.server_address[1])
            window = webview.create_window(
                f"{APP_NAME} — {APP_NAME_JA}",
                html=(
                    '<!doctype html><html lang="ja"><meta charset="utf-8">'
                    '<body style="font-family:-apple-system;margin:48px">'
                    "監査済みデータを読み込んでいます</body></html>"
                ),
                width=DEFAULT_WINDOW_SIZE[0],
                height=DEFAULT_WINDOW_SIZE[1],
                min_size=MINIMUM_WINDOW_SIZE,
            )
            if window is None:
                raise RuntimeError("RaceWeaveのWebKitウインドウを作成できません")
            webview.start(_load_window_url, (window, url))
        finally:
            server.shutdown()
            thread.join(timeout=5)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="raceweave",
        description="監査済み競馬予測をmacOS専用ウインドウで表示します。",
    )
    parser.add_argument("--demo-directory", type=Path)
    parser.add_argument("--prediction-bundle", type=Path)
    parser.add_argument("--walk-forward-report", type=Path)
    parser.add_argument("--win5-forecast", type=Path)
    parser.add_argument("--race-day-manifest", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Load audited artifacts and open RaceWeave without launching a browser."""
    parser = _parser()
    args = parser.parse_args(argv)
    artifact_paths = (
        args.prediction_bundle,
        args.walk_forward_report,
        args.win5_forecast,
        args.race_day_manifest,
    )
    if args.demo_directory is not None and any(artifact_paths):
        parser.error("--demo-directoryと実成果物の指定は同時に使えません")
    try:
        if args.demo_directory is not None:
            snapshot = load_or_create_demo_snapshot(args.demo_directory)
        elif any(artifact_paths):
            if args.race_day_manifest is not None:
                if args.prediction_bundle is not None:
                    parser.error(
                        "--race-day-manifestと--prediction-bundleは同時に使えません"
                    )
                snapshot = load_audited_race_day_snapshot(
                    args.race_day_manifest,
                    walk_forward_report=args.walk_forward_report,
                    win5_forecast=args.win5_forecast,
                )
            else:
                snapshot = build_read_only_app_snapshot(
                    prediction_directory=args.prediction_bundle,
                    walk_forward_report=args.walk_forward_report,
                    win5_forecast=args.win5_forecast,
                )
        else:
            local_directory = repository_local_directory()
            selected = latest_audited_race_day_manifest(local_directory)
            if selected is None:
                selected = choose_race_day_manifest()
            if selected is not None:
                walk_forward, win5 = adjacent_related_artifacts(selected)
                snapshot = load_audited_race_day_snapshot(
                    selected,
                    walk_forward_report=walk_forward,
                    win5_forecast=win5,
                    runner_display_search_root=local_directory,
                    market_blend_forecast=(
                        selected.parent / "market-blend.json"
                        if (selected.parent / "market-blend.json").is_file()
                        else None
                    ),
                )
            else:
                snapshot = load_or_create_demo_snapshot(default_demo_directory())
        run_desktop_window(snapshot)
    except (
        OSError,
        RuntimeError,
        ValueError,
        UnicodeError,
        subprocess.SubprocessError,
    ) as error:
        print(f"{APP_NAME}を起動できません: {error}", file=sys.stderr)
        _show_native_error(str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
