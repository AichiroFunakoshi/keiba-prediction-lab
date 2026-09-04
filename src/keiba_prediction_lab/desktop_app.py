"""Native macOS window for the audited RaceWeave read-only UI."""

from __future__ import annotations

import argparse
import importlib
import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import Sequence

from .app_snapshot import ReadOnlyAppSnapshot, build_read_only_app_snapshot
from .local_http import LOOPBACK_HOST, create_read_only_server
from .race_day_pipeline import audit_local_race_day
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
            webview.create_window(
                f"{APP_NAME} — {APP_NAME_JA}",
                url,
                width=DEFAULT_WINDOW_SIZE[0],
                height=DEFAULT_WINDOW_SIZE[1],
                min_size=MINIMUM_WINDOW_SIZE,
            )
            webview.start()
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
            selected = choose_race_day_manifest()
            snapshot = (
                load_audited_race_day_snapshot(selected)
                if selected is not None
                else load_or_create_demo_snapshot(default_demo_directory())
            )
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
