"""Command-line entry points for local, non-redistributing data audits."""

import argparse
import json
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from .bet_type_bootstrap import (
    BootstrapResamplingUnit,
    DEFAULT_BOOTSTRAP_SAMPLES,
    DEFAULT_BOOTSTRAP_SEED,
    bootstrap_bet_type_evaluation_report_files,
)
from .app_snapshot import build_read_only_app_snapshot
from .bet_type_diagnostics import (
    diagnose_bet_type_evaluation_report_files,
)
from .bet_type_report import (
    evaluate_bet_type_race_directories,
    save_bet_type_evaluation_artifact,
)
from .bet_type_report_comparison import (
    compare_bet_type_evaluation_report_files,
)
from .bet_type_segment_diagnostics import (
    diagnose_bet_type_segment_report_files,
)
from .bundle_audit import audit_prediction_bundle
from .data_audit import audit_standard_csv, load_source_registry
from .frozen import PredictionPhase
from .input_templates import create_local_input_templates
from .jra_van_fetch import (
    fetch_jra_van_on_windows,
    fetch_jra_van_realtime_on_windows,
)
from .jra_van_adapter import prepare_jra_van_race_day
from .jra_web_adapter import prepare_jra_web_race_day
from .jra_web_fetch import fetch_jra_web_race_day, refresh_jra_web_race_day
from .local_adapter import (
    build_local_feature_bundle,
    build_time_safe_training_bundle,
    save_local_feature_bundle,
    save_local_training_bundle,
)
from .local_pipeline import (
    build_local_race_prediction,
    save_local_pipeline_run,
)
from .local_http import DEFAULT_READ_ONLY_API_PORT, serve_read_only_api
from .local_artifact_status import (
    default_local_artifact_roots,
    inspect_local_artifacts,
)
from .market_guard import (
    MarketGuardPolicy,
    build_market_guard_report_from_snapshot,
    save_market_guard_report,
)
from .model_artifact import (
    ModelTrainingParameters,
    save_trained_model_artifact,
    train_local_model_artifact,
)
from .prediction_report import build_prediction_bundle_markdown
from .pace_estimation import (
    build_automatic_pace_inputs,
    save_automatic_pace_inputs,
)
from .race_day_pipeline import audit_local_race_day, build_and_save_local_race_day
from .snapshot_adapter import (
    convert_history_snapshot,
    convert_target_snapshot,
)
from .ui_demo import create_ui_demo, load_ui_demo
from .walk_forward_report import (
    MAX_FORMAL_EVALUATION_RACES,
    MIN_FORMAL_EVALUATION_RACES,
    audit_walk_forward_artifact,
    evaluate_local_walk_forward,
    save_walk_forward_artifact,
)
from .win5 import build_win5_forecast, load_win5_forecast, save_win5_forecast
from .winner_diagnostics import diagnose_winner_misses


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="keiba-lab")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sources = subparsers.add_parser("list-sources", help="show source audit status")
    sources.add_argument(
        "--registry", type=Path, default=Path("data/sources.json")
    )

    audit = subparsers.add_parser("audit-csv", help="audit a standardized CSV")
    audit.add_argument("path", type=Path)

    training_audit = subparsers.add_parser(
        "audit-training-csv",
        help="validate a time-safe local training CSV without saving an artifact",
    )
    training_audit.add_argument("training", type=Path)

    prepare = subparsers.add_parser(
        "prepare-features",
        help="build leakage-checked features from separate local CSV files",
    )
    prepare.add_argument("history", type=Path)
    prepare.add_argument("targets", type=Path)
    prepare.add_argument("--output", type=Path, required=True)

    training = subparsers.add_parser(
        "prepare-training",
        help="build time-safe training rows from a local historical CSV",
    )
    training.add_argument("training", type=Path)
    training.add_argument("--output", type=Path, required=True)

    history_snapshot = subparsers.add_parser(
        "convert-local-history-snapshot",
        help="convert an already acquired local race-history JSON snapshot",
    )
    history_snapshot.add_argument("snapshot", type=Path)
    history_snapshot.add_argument("--source-id", required=True)
    history_snapshot.add_argument("--acquired-at", required=True)
    history_snapshot.add_argument(
        "--observation-offset-minutes", type=int, default=5
    )
    history_snapshot.add_argument("--result-delay-minutes", type=int, default=20)
    history_snapshot.add_argument("--output", type=Path, required=True)

    target_snapshot = subparsers.add_parser(
        "convert-local-target-snapshot",
        help="convert an already acquired result-free race-card JSON snapshot",
    )
    target_snapshot.add_argument("snapshot", type=Path)
    target_snapshot.add_argument("track_conditions", type=Path)
    target_snapshot.add_argument("--source-id", required=True)
    target_snapshot.add_argument("--acquired-at", required=True)
    target_snapshot.add_argument("--race-date", required=True)
    target_snapshot.add_argument("--observed-at", required=True)
    target_snapshot.add_argument("--output", type=Path, required=True)

    generate_pace = subparsers.add_parser(
        "generate-pace-inputs",
        help="derive time-safe running styles and expected pace from past results",
    )
    generate_pace.add_argument("pace_history", type=Path)
    generate_pace.add_argument("targets", type=Path)
    generate_pace.add_argument("--output", type=Path, required=True)

    fetch_jra_van = subparsers.add_parser(
        "fetch-jra-van",
        help="fetch licensed JV-Data through the official Windows-only JV-Link",
    )
    fetch_jra_van.add_argument("--output", type=Path, required=True)
    fetch_jra_van.add_argument("--dataspec", default="RACE")
    fetch_jra_van.add_argument("--fromtime", default="00000000000000")
    fetch_jra_van.add_argument("--option", type=int, default=2)
    fetch_jra_van.add_argument("--sid", default="UNKNOWN")
    fetch_jra_van.add_argument("--max-poll-seconds", type=float, default=600.0)

    fetch_realtime = subparsers.add_parser(
        "fetch-jra-van-realtime",
        help="fetch licensed real-time JV-Data through Windows JV-Link",
    )
    fetch_realtime.add_argument("--output", type=Path, required=True)
    fetch_realtime.add_argument("--dataspec", required=True)
    fetch_realtime.add_argument("--key", required=True)
    fetch_realtime.add_argument("--sid", default="UNKNOWN")
    fetch_realtime.add_argument("--max-poll-seconds", type=float, default=600.0)

    prepare_jra_van = subparsers.add_parser(
        "prepare-jra-van-race-day",
        help="convert licensed JV-Data snapshots into prediction-ready day inputs",
    )
    prepare_jra_van.add_argument("history_snapshot", type=Path)
    prepare_jra_van.add_argument("race_snapshot", type=Path)
    prepare_jra_van.add_argument("realtime_snapshot", type=Path)
    prepare_jra_van.add_argument("--race-date", required=True)
    prepare_jra_van.add_argument("--observed-at", required=True)
    prepare_jra_van.add_argument("--output", type=Path, required=True)

    fetch_jra_web = subparsers.add_parser(
        "fetch-jra-web",
        help="experimentally fetch public JRA pages after an independent terms check",
    )
    fetch_jra_web.add_argument("--race-date", required=True)
    fetch_jra_web.add_argument("--max-history-races", type=int, default=360)
    fetch_jra_web.add_argument("--delay-seconds", type=float, default=1.0)
    fetch_jra_web.add_argument(
        "--accept-private-use-terms",
        action="store_true",
        help="confirm an independent current-terms check, private use, and no redistribution",
    )
    fetch_jra_web.add_argument("--output", type=Path, required=True)

    refresh_jra_web = subparsers.add_parser(
        "refresh-jra-web-race-day",
        help="experimentally refresh same-day JRA data after a current-terms check",
    )
    refresh_jra_web.add_argument("snapshot", type=Path)
    refresh_jra_web.add_argument("--delay-seconds", type=float, default=1.0)
    refresh_jra_web.add_argument(
        "--accept-private-use-terms",
        action="store_true",
        help="confirm an independent current-terms check, private use, and no redistribution",
    )
    refresh_jra_web.add_argument("--output", type=Path, required=True)

    prepare_jra_web = subparsers.add_parser(
        "prepare-jra-web-race-day",
        help="convert a private JRA public-web snapshot into formal race-day inputs",
    )
    prepare_jra_web.add_argument("snapshot", type=Path)
    prepare_jra_web.add_argument("--output", type=Path, required=True)

    train_model = subparsers.add_parser(
        "train-model",
        help="fit and save an integrity-protected model from local training data",
    )
    train_model.add_argument("training", type=Path)
    train_model.add_argument("--output", type=Path, required=True)
    train_model.add_argument("--prior-strength", type=float, default=10.0)
    train_model.add_argument("--epochs", type=int, default=500)
    train_model.add_argument("--learning-rate", type=float, default=0.1)
    train_model.add_argument("--l2-strength", type=float, default=0.01)

    predict_race = subparsers.add_parser(
        "predict-race",
        help="run the formal one-race pipeline from explicit local inputs",
    )
    predict_race.add_argument("model", type=Path)
    predict_race.add_argument("history", type=Path)
    predict_race.add_argument("targets", type=Path)
    predict_race.add_argument("pace_profiles", type=Path)
    predict_race.add_argument("pace_scenario", type=Path)
    predict_race.add_argument("--frozen-at", required=True)
    predict_race.add_argument(
        "--phase", choices=tuple(item.value for item in PredictionPhase),
        default=PredictionPhase.PRE_ODDS.value,
    )
    predict_race.add_argument("--place-payout-slots", type=int)
    predict_race.add_argument(
        "--require-complete-body-weight",
        action="store_true",
        help="reject final prediction when any runner body weight is missing",
    )
    predict_race.add_argument("--output", type=Path, required=True)

    predict_race_day = subparsers.add_parser(
        "predict-race-day",
        help="atomically predict every race in an explicit local race-day plan",
    )
    predict_race_day.add_argument("model", type=Path)
    predict_race_day.add_argument("history", type=Path)
    predict_race_day.add_argument("plan", type=Path)
    predict_race_day.add_argument("--frozen-at", required=True)
    predict_race_day.add_argument(
        "--phase", choices=tuple(item.value for item in PredictionPhase),
        default=PredictionPhase.PRE_ODDS.value,
    )
    predict_race_day.add_argument("--place-payout-slots", type=int)
    predict_race_day.add_argument(
        "--require-complete-body-weight",
        action="store_true",
        help="reject the whole race day when any runner body weight is missing",
    )
    predict_race_day.add_argument("--output", type=Path, required=True)

    audit_race_day = subparsers.add_parser(
        "audit-race-day",
        help="re-audit a saved local race day and all prediction bundles",
    )
    audit_race_day.add_argument("directory", type=Path)

    market_guard = subparsers.add_parser(
        "build-market-guard",
        help="freeze a post-odds abstention guard without changing pre-odds predictions",
    )
    market_guard.add_argument("race_day", type=Path)
    market_guard.add_argument("snapshot", type=Path)
    market_guard.add_argument("--max-market-rank", type=int, default=3)
    market_guard.add_argument("--output", type=Path, required=True)

    predict_win5 = subparsers.add_parser(
        "predict-win5",
        help="freeze a zero-stake WIN5 forecast from five audited race bundles",
    )
    predict_win5.add_argument("race_directories", type=Path, nargs=5)
    predict_win5.add_argument("--frozen-at", required=True)
    predict_win5.add_argument("--output", type=Path, required=True)

    audit_win5 = subparsers.add_parser(
        "audit-win5-forecast",
        help="verify a saved zero-stake WIN5 forecast",
    )
    audit_win5.add_argument("forecast", type=Path)

    templates = subparsers.add_parser(
        "init-input-templates",
        help="create protected starter files for local race inputs",
    )
    templates.add_argument("--output", type=Path, required=True)

    audit_race = subparsers.add_parser(
        "audit-race-inputs",
        help="validate all formal race inputs without saving a prediction",
    )
    audit_race.add_argument("model", type=Path)
    audit_race.add_argument("history", type=Path)
    audit_race.add_argument("targets", type=Path)
    audit_race.add_argument("pace_profiles", type=Path)
    audit_race.add_argument("pace_scenario", type=Path)
    audit_race.add_argument("--frozen-at", required=True)
    audit_race.add_argument(
        "--phase", choices=tuple(item.value for item in PredictionPhase),
        default=PredictionPhase.PRE_ODDS.value,
    )
    audit_race.add_argument("--place-payout-slots", type=int)
    audit_race.add_argument(
        "--require-complete-body-weight",
        action="store_true",
        help="reject inputs when any runner body weight is missing",
    )

    audit_bundle = subparsers.add_parser(
        "audit-prediction-bundle",
        help="verify a saved prediction directory without modifying it",
    )
    audit_bundle.add_argument("directory", type=Path)

    local_status = subparsers.add_parser(
        "local-artifact-status",
        help="find and audit local prediction/results without modifying them",
    )
    local_status.add_argument(
        "--root", type=Path, action="append",
        help="search only this directory; repeat to add roots",
    )

    prediction_report = subparsers.add_parser(
        "report-prediction-bundle",
        help="render an audited saved prediction as Japanese Markdown",
    )
    prediction_report.add_argument("directory", type=Path)
    prediction_report.add_argument("--output", type=Path)

    walk_forward = subparsers.add_parser(
        "evaluate-walk-forward",
        help="run chronological model evaluation from local training data",
    )
    walk_forward.add_argument("training", type=Path)
    walk_forward.add_argument("windows", type=Path)
    walk_forward.add_argument("--report", type=Path)
    walk_forward.add_argument(
        "--min-evaluation-races", type=int,
        default=MIN_FORMAL_EVALUATION_RACES,
    )
    walk_forward.add_argument(
        "--max-evaluation-races", type=int,
        default=MAX_FORMAL_EVALUATION_RACES,
    )

    audit_walk_forward = subparsers.add_parser(
        "audit-walk-forward-report",
        help="verify a saved walk-forward JSON report without modifying it",
    )
    audit_walk_forward.add_argument("report", type=Path)

    inspect_app = subparsers.add_parser(
        "inspect-app-state",
        help="emit read-only UI data from explicitly selected audited artifacts",
    )
    inspect_app.add_argument("--prediction-bundle", type=Path)
    inspect_app.add_argument("--walk-forward-report", type=Path)
    inspect_app.add_argument("--win5-forecast", type=Path)
    inspect_app.add_argument("--race-day-manifest", type=Path)

    serve_api = subparsers.add_parser(
        "serve-read-only-api",
        help="serve audited app state on the IPv4 loopback address only",
    )
    serve_api.add_argument("--prediction-bundle", type=Path)
    serve_api.add_argument("--walk-forward-report", type=Path)
    serve_api.add_argument("--win5-forecast", type=Path)
    serve_api.add_argument("--race-day-manifest", type=Path)
    serve_api.add_argument("--port", type=int, default=DEFAULT_READ_ONLY_API_PORT)
    serve_api.add_argument(
        "--open-browser", action="store_true",
        help="open the local UI in the default browser after startup",
    )

    init_ui_demo = subparsers.add_parser(
        "init-ui-demo",
        help="create audited synthetic artifacts for viewing the local UI",
    )
    init_ui_demo.add_argument("--output", type=Path, required=True)

    serve_ui_demo = subparsers.add_parser(
        "serve-ui-demo",
        help="audit and open a previously created synthetic UI demo",
    )
    serve_ui_demo.add_argument("directory", type=Path)
    serve_ui_demo.add_argument(
        "--port", type=int, default=DEFAULT_READ_ONLY_API_PORT
    )
    serve_ui_demo.add_argument(
        "--no-open-browser", action="store_true",
        help="serve the demo without opening the default browser",
    )

    evaluate = subparsers.add_parser(
        "evaluate-bet-types",
        help="evaluate frozen six-bet-type candidates across race directories",
    )
    evaluate.add_argument(
        "race_directories",
        type=Path,
        nargs="+",
        help=(
            "directories containing bet-types-shadow.json and "
            "bet-types-payouts.json"
        ),
    )

    evaluate.add_argument(
        "--report",
        type=Path,
        help="save an integrity-protected structured evaluation JSON",
    )

    winner_diagnostics = subparsers.add_parser(
        "diagnose-winner-misses",
        help="diagnose top-one errors from audited race directories",
    )
    winner_diagnostics.add_argument("race_directories", type=Path, nargs="+")
    winner_diagnostics.add_argument(
        "--format", choices=("markdown", "json"), default="markdown"
    )

    compare = subparsers.add_parser(
        "compare-bet-type-reports",
        help="compare two reports over identical races and payout files",
    )
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)

    bootstrap = subparsers.add_parser(
        "bootstrap-bet-type-reports",
        help="estimate paired race-level intervals for two evaluation reports",
    )
    bootstrap.add_argument("baseline", type=Path)
    bootstrap.add_argument("candidate", type=Path)
    bootstrap.add_argument(
        "--samples", type=int, default=DEFAULT_BOOTSTRAP_SAMPLES
    )
    bootstrap.add_argument("--seed", type=int, default=DEFAULT_BOOTSTRAP_SEED)
    bootstrap.add_argument(
        "--resampling-unit",
        choices=tuple(unit.value for unit in BootstrapResamplingUnit),
        default=BootstrapResamplingUnit.RACE.value,
        help="resample paired races individually or as race-date clusters",
    )

    diagnose = subparsers.add_parser(
        "diagnose-bet-type-reports",
        help="locate paired payout differences by race and race date",
    )
    diagnose.add_argument("baseline", type=Path)
    diagnose.add_argument("candidate", type=Path)
    diagnose.add_argument(
        "--format", choices=("markdown", "json"), default="markdown"
    )
    diagnose.add_argument("--top-races", type=int, default=5)

    segments = subparsers.add_parser(
        "diagnose-bet-type-segments",
        help="compare bet types across fixed pre-race context segments",
    )
    segments.add_argument("baseline", type=Path)
    segments.add_argument("candidate", type=Path)
    segments.add_argument(
        "--format", choices=("markdown", "json"), default="markdown"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "list-sources":
        sources = load_source_registry(args.registry)
        payload = [
            {
                "source_id": source.source_id,
                "name": source.name,
                "status": source.status.value,
                "redistribution": source.redistribution.value,
            }
            for source in sources
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "evaluate-bet-types":
        artifact = evaluate_bet_type_race_directories(
            tuple(args.race_directories)
        )
        if args.report is not None:
            save_bet_type_evaluation_artifact(artifact, args.report)
        print(artifact.to_markdown(), end="")
        return 0

    if args.command == "audit-training-csv":
        try:
            bundle = build_time_safe_training_bundle(args.training)
        except (OSError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({
            "path": str(args.training),
            "training_sha256": bundle.training_sha256,
            "input_data_version": bundle.input_data_version,
            "training_row_count": len(bundle.rows),
            "training_race_count": len({
                row.features.race_id for row in bundle.rows
            }),
            "is_valid": True,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "local-artifact-status":
        roots = (
            tuple(args.root)
            if args.root
            else default_local_artifact_roots(Path.cwd())
        )
        try:
            report = inspect_local_artifacts(roots)
        except (OSError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "diagnose-winner-misses":
        report = diagnose_winner_misses(tuple(args.race_directories))
        if args.format == "json":
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(report.to_markdown(), end="")
        return 0

    if args.command == "prepare-features":
        bundle = build_local_feature_bundle(args.history, args.targets)
        save_local_feature_bundle(bundle, args.output)
        print(json.dumps({
            "output": str(args.output),
            "feature_count": len(bundle.features),
            "input_data_version": bundle.input_data_version,
            "history_sha256": bundle.history_sha256,
            "targets_sha256": bundle.targets_sha256,
            "feature_history_coverage": {
                "horse": bundle.horse_history_coverage_count,
                "jockey": bundle.jockey_history_coverage_count,
                "trainer": bundle.trainer_history_coverage_count,
                "runner_count": len(bundle.features),
            },
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "prepare-training":
        bundle = build_time_safe_training_bundle(args.training)
        save_local_training_bundle(bundle, args.output)
        print(json.dumps({
            "output": str(args.output),
            "training_row_count": len(bundle.rows),
            "input_data_version": bundle.input_data_version,
            "training_sha256": bundle.training_sha256,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "convert-local-history-snapshot":
        try:
            result = convert_history_snapshot(
                args.snapshot,
                args.output,
                source_id=args.source_id,
                acquired_at=datetime.fromisoformat(args.acquired_at),
                observation_offset_minutes=args.observation_offset_minutes,
                result_delay_minutes=args.result_delay_minutes,
            )
        except (OSError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({
            "output": str(result.output_directory),
            "manifest": str(result.manifest_path),
            "race_count": result.race_count,
            "runner_count": result.runner_count,
            "source_sha256": result.source_sha256,
            "network_access_performed": False,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "convert-local-target-snapshot":
        try:
            result = convert_target_snapshot(
                args.snapshot,
                args.track_conditions,
                args.output,
                source_id=args.source_id,
                acquired_at=datetime.fromisoformat(args.acquired_at),
                race_date=date.fromisoformat(args.race_date),
                observed_at=datetime.fromisoformat(args.observed_at),
            )
        except (OSError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({
            "output": str(result.output_directory),
            "manifest": str(result.manifest_path),
            "race_count": result.race_count,
            "runner_count": result.runner_count,
            "source_sha256": result.source_sha256,
            "network_access_performed": False,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "generate-pace-inputs":
        try:
            inputs = build_automatic_pace_inputs(
                args.pace_history, args.targets
            )
            profiles, scenario, manifest = save_automatic_pace_inputs(
                inputs, args.output
            )
        except (OSError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({
            "is_valid": True,
            "output": str(args.output),
            "pace_profiles": str(profiles),
            "pace_scenario": str(scenario),
            "manifest": str(manifest),
            "race_id": inputs.scenario.race_id,
            "expected_pace": inputs.scenario.expected_pace.value,
            "confidence": inputs.scenario.confidence,
            "runner_count": len(inputs.profiles),
            "runners_with_history": inputs.runners_with_history,
            "history_rows_used": inputs.history_rows_used,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "fetch-jra-van":
        try:
            result = fetch_jra_van_on_windows(
                args.output,
                dataspec=args.dataspec,
                fromtime=args.fromtime,
                option=args.option,
                sid=args.sid,
                max_poll_seconds=args.max_poll_seconds,
            )
        except (OSError, RuntimeError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({
            "is_valid": True,
            "output": str(result.output_directory),
            "records": str(result.records_path),
            "manifest": str(result.manifest_path),
            "record_count": result.record_count,
            "records_sha256": result.records_sha256,
            "acquired_at": result.acquired_at.isoformat(),
            "source_id": "jra-van-data-lab",
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "fetch-jra-van-realtime":
        try:
            result = fetch_jra_van_realtime_on_windows(
                args.output, dataspec=args.dataspec, key=args.key, sid=args.sid,
                max_poll_seconds=args.max_poll_seconds,
            )
        except (OSError, RuntimeError, ValueError, UnicodeError) as error:
            print(json.dumps({"is_valid": False, "error": str(error)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({
            "is_valid": True, "output": str(result.output_directory),
            "records": str(result.records_path), "manifest": str(result.manifest_path),
            "record_count": result.record_count, "records_sha256": result.records_sha256,
            "acquired_at": result.acquired_at.isoformat(), "source_id": "jra-van-data-lab",
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "prepare-jra-van-race-day":
        try:
            plan = prepare_jra_van_race_day(
                args.history_snapshot, args.race_snapshot,
                args.realtime_snapshot, args.output,
                race_date=date.fromisoformat(args.race_date),
                observed_at=datetime.fromisoformat(args.observed_at),
            )
        except (OSError, RuntimeError, ValueError, UnicodeError) as error:
            print(json.dumps({"is_valid": False, "error": str(error)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({
            "is_valid": True, "output": str(args.output),
            "race_day_plan": str(plan), "source_id": "jra-van-data-lab",
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "fetch-jra-web":
        try:
            result = fetch_jra_web_race_day(
                date.fromisoformat(args.race_date),
                args.output,
                max_history_races=args.max_history_races,
                delay_seconds=args.delay_seconds,
                accept_private_use_terms=args.accept_private_use_terms,
            )
        except (OSError, RuntimeError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({
            "is_valid": True,
            "source_id": "jra-public-web-private-use",
            "private_use_only": True,
            "output": str(result.output_directory),
            "manifest": str(result.manifest_path),
            "cards": str(result.cards_path),
            "history": str(result.history_path),
            "track_conditions": str(result.track_conditions_path),
            "acquired_at": result.acquired_at.isoformat(),
            "race_count": result.race_count,
            "history_race_count": result.history_race_count,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "prepare-jra-web-race-day":
        try:
            result = prepare_jra_web_race_day(args.snapshot, args.output)
        except (OSError, RuntimeError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({"is_valid": True, **result}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "refresh-jra-web-race-day":
        try:
            result = refresh_jra_web_race_day(
                args.snapshot,
                args.output,
                delay_seconds=args.delay_seconds,
                accept_private_use_terms=args.accept_private_use_terms,
            )
        except (OSError, RuntimeError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({
            "is_valid": True,
            "source_id": "jra-public-web-private-use",
            "private_use_only": True,
            "output": str(result.output_directory),
            "manifest": str(result.manifest_path),
            "cards": str(result.cards_path),
            "history": str(result.history_path),
            "track_conditions": str(result.track_conditions_path),
            "acquired_at": result.acquired_at.isoformat(),
            "race_count": result.race_count,
            "history_race_count": result.history_race_count,
            "history_reused_without_network": True,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "train-model":
        parameters = ModelTrainingParameters(
            prior_strength=args.prior_strength,
            epochs=args.epochs,
            learning_rate=args.learning_rate,
            l2_strength=args.l2_strength,
        )
        artifact = train_local_model_artifact(
            args.training, parameters=parameters
        )
        digest = save_trained_model_artifact(artifact, args.output)
        print(json.dumps({
            "output": str(args.output),
            "model_sha256": digest,
            "model_version": artifact.model.model_version,
            "trained_through": artifact.model.trained_through.isoformat(),
            "training_row_count": artifact.training_row_count,
            "training_race_count": artifact.training_race_count,
            "input_data_version": artifact.input_data_version,
            "training_sha256": artifact.training_sha256,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "predict-race":
        run = build_local_race_prediction(
            args.model,
            args.history,
            args.targets,
            args.pace_profiles,
            args.pace_scenario,
            frozen_at=datetime.fromisoformat(args.frozen_at),
            phase=PredictionPhase(args.phase),
            place_payout_slots=args.place_payout_slots,
            require_complete_body_weight=args.require_complete_body_weight,
        )
        manifest = save_local_pipeline_run(run, args.output)
        actual = run.prediction.actual_prediction
        print(json.dumps({
            "output": str(args.output),
            "manifest": str(manifest),
            "race_id": actual.race_id,
            "input_data_version": run.input_data_version,
            "model_version": actual.model_version,
            "actual_ticket": list(actual.trifecta_tickets[0].selection),
            "stake_yen": actual.trifecta_tickets[0].stake_yen,
            "shadow_stake_yen": 0,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "predict-race-day":
        try:
            result = build_and_save_local_race_day(
                args.model,
                args.history,
                args.plan,
                args.output,
                frozen_at=datetime.fromisoformat(args.frozen_at),
                phase=PredictionPhase(args.phase),
                place_payout_slots=args.place_payout_slots,
                require_complete_body_weight=args.require_complete_body_weight,
            )
        except (OSError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({
            "is_valid": True,
            "output": str(result.output_directory),
            "race_day_manifest": str(result.race_day_manifest),
            "provenance": str(result.provenance),
            "race_count": result.race_count,
            "venue_count": result.venue_count,
            "frozen_at": args.frozen_at,
            "phase": args.phase,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "audit-race-day":
        try:
            audit = audit_local_race_day(args.directory)
        except (OSError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({
            "is_valid": True,
            "race_date": audit.race_date.isoformat(),
            "frozen_at": audit.frozen_at.isoformat(),
            "phase": audit.phase.value,
            "race_count": audit.race_count,
            "venue_count": audit.venue_count,
            "model_sha256": audit.model_sha256,
            "history_sha256": audit.history_sha256,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "build-market-guard":
        try:
            report = build_market_guard_report_from_snapshot(
                args.race_day,
                args.snapshot,
                policy=MarketGuardPolicy(args.max_market_rank),
            )
            digest = save_market_guard_report(report, args.output)
        except (OSError, RuntimeError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({
            "is_valid": True,
            "status": "research-shadow",
            "output": str(args.output),
            "sha256": digest,
            "race_count": len(report.rows),
            "eligible_race_count": report.eligible_race_count,
            "max_market_rank": report.policy.max_market_rank,
            "observed_at": report.observed_at.isoformat(),
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "predict-win5":
        try:
            forecast = build_win5_forecast(
                args.race_directories,
                frozen_at=datetime.fromisoformat(args.frozen_at),
            )
            save_win5_forecast(forecast, args.output)
        except (OSError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({
            "is_valid": True,
            **forecast.to_dict(),
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "audit-win5-forecast":
        try:
            forecast = load_win5_forecast(args.forecast)
        except (OSError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({
            "is_valid": True,
            **forecast.to_dict(),
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "init-input-templates":
        paths = create_local_input_templates(args.output)
        print(json.dumps({
            "output": str(args.output),
            "files": [path.name for path in paths],
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "audit-race-inputs":
        try:
            run = build_local_race_prediction(
                args.model,
                args.history,
                args.targets,
                args.pace_profiles,
                args.pace_scenario,
                frozen_at=datetime.fromisoformat(args.frozen_at),
                phase=PredictionPhase(args.phase),
                place_payout_slots=args.place_payout_slots,
                require_complete_body_weight=args.require_complete_body_weight,
            )
        except (OSError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        actual = run.prediction.actual_prediction
        print(json.dumps({
            "is_valid": True,
            "prediction_saved": False,
            "race_id": actual.race_id,
            "runner_count": len(actual.predictions),
            "scheduled_at": actual.scheduled_at.isoformat(),
            "frozen_at": actual.frozen_at.isoformat(),
            "phase": actual.phase.value,
            "model_version": actual.model_version,
            "input_data_version": run.input_data_version,
            "model_sha256": run.model_sha256,
            "history_sha256": run.history_sha256,
            "targets_sha256": run.targets_sha256,
            "pace_profiles_sha256": run.pace_profiles_sha256,
            "pace_scenario_sha256": run.pace_scenario_sha256,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "audit-prediction-bundle":
        try:
            report = audit_prediction_bundle(args.directory)
        except (OSError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({
            "is_valid": True,
            "race_id": report.race_id,
            "scheduled_at": report.scheduled_at.isoformat(),
            "frozen_at": report.frozen_at.isoformat(),
            "model_version": report.model_version,
            "input_data_version": report.input_data_version,
            "runner_count": report.runner_count,
            "actual_ticket_count": report.actual_ticket_count,
            "actual_stake_yen": report.actual_stake_yen,
            "shadow_stake_yen": report.shadow_stake_yen,
            "model_sha256": report.model_sha256,
            "history_sha256": report.history_sha256,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "report-prediction-bundle":
        try:
            markdown = build_prediction_bundle_markdown(args.directory)
            if args.output is None:
                print(markdown, end="")
            else:
                with args.output.open("x", encoding="utf-8", errors="strict") as handle:
                    handle.write(markdown)
                print(json.dumps({
                    "output": str(args.output),
                    "prediction_directory": str(args.directory),
                }, ensure_ascii=False, indent=2))
        except (OSError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        return 0

    if args.command == "evaluate-walk-forward":
        try:
            if args.min_evaluation_races < MIN_FORMAL_EVALUATION_RACES:
                raise ValueError("minimum evaluation races must be at least 300")
            if args.max_evaluation_races > MAX_FORMAL_EVALUATION_RACES:
                raise ValueError("maximum evaluation races must be at most 500")
            if args.min_evaluation_races > args.max_evaluation_races:
                raise ValueError(
                    "minimum evaluation races must not exceed maximum"
                )
            artifact = evaluate_local_walk_forward(args.training, args.windows)
            evaluation_races = artifact.result.aggregate_model_score.race_count
            if evaluation_races < args.min_evaluation_races:
                raise ValueError(
                    f"evaluation has {evaluation_races} races; "
                    f"at least {args.min_evaluation_races} are required"
                )
            if evaluation_races > args.max_evaluation_races:
                raise ValueError(
                    f"evaluation has {evaluation_races} races; "
                    f"at most {args.max_evaluation_races} are allowed"
                )
            if args.report is not None:
                save_walk_forward_artifact(artifact, args.report)
        except (OSError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        print(artifact.to_markdown(), end="")
        return 0

    if args.command == "audit-walk-forward-report":
        try:
            audit = audit_walk_forward_artifact(args.report)
        except (OSError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({
            "is_valid": True,
            "schema_version": audit.schema_version,
            "sha256": audit.sha256,
            "fold_count": audit.fold_count,
            "evaluation_race_count": audit.evaluation_race_count,
            "evaluation_runner_count": audit.evaluation_runner_count,
            "training_sha256": audit.training_sha256,
            "windows_sha256": audit.windows_sha256,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "inspect-app-state":
        try:
            snapshot = build_read_only_app_snapshot(
                prediction_directory=args.prediction_bundle,
                walk_forward_report=args.walk_forward_report,
                win5_forecast=args.win5_forecast,
                race_day_manifest=args.race_day_manifest,
            )
        except (OSError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({
            "is_valid": True,
            **snapshot.to_dict(),
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "serve-read-only-api":
        try:
            serve_read_only_api(
                prediction_directory=args.prediction_bundle,
                walk_forward_report=args.walk_forward_report,
                win5_forecast=args.win5_forecast,
                race_day_manifest=args.race_day_manifest,
                port=args.port,
                open_browser=args.open_browser,
            )
        except (OSError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        return 0

    if args.command == "init-ui-demo":
        try:
            demo = create_ui_demo(args.output)
        except (OSError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({
            "is_valid": True,
            "synthetic_demo": True,
            "output": str(demo.root),
            "race_count": demo.race_count,
            "race_day_manifest": str(demo.race_day_manifest),
            "walk_forward_report": str(demo.walk_forward_report),
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "serve-ui-demo":
        try:
            demo = load_ui_demo(args.directory)
            serve_read_only_api(
                race_day_manifest=demo.race_day_manifest,
                walk_forward_report=demo.walk_forward_report,
                port=args.port,
                open_browser=not args.no_open_browser,
            )
        except (OSError, ValueError, UnicodeError) as error:
            print(json.dumps({
                "is_valid": False,
                "error": str(error),
            }, ensure_ascii=False, indent=2))
            return 1
        return 0

    if args.command == "compare-bet-type-reports":
        comparison = compare_bet_type_evaluation_report_files(
            args.baseline, args.candidate
        )
        print(comparison.to_markdown(), end="")
        return 0

    if args.command == "bootstrap-bet-type-reports":
        report = bootstrap_bet_type_evaluation_report_files(
            args.baseline,
            args.candidate,
            samples=args.samples,
            seed=args.seed,
            resampling_unit=BootstrapResamplingUnit(args.resampling_unit),
        )
        print(report.to_markdown(), end="")
        return 0

    if args.command == "diagnose-bet-type-reports":
        report = diagnose_bet_type_evaluation_report_files(
            args.baseline, args.candidate
        )
        output = (
            report.to_json()
            if args.format == "json"
            else report.to_markdown(top_races=args.top_races)
        )
        print(output, end="")
        return 0

    if args.command == "diagnose-bet-type-segments":
        report = diagnose_bet_type_segment_report_files(
            args.baseline, args.candidate
        )
        output = report.to_json() if args.format == "json" else report.to_markdown()
        print(output, end="")
        return 0

    report = audit_standard_csv(args.path)
    payload = {
        "path": str(report.path),
        "sha256": report.sha256,
        "row_count": report.row_count,
        "duplicate_key_count": report.duplicate_key_count,
        "invalid_date_count": report.invalid_date_count,
        "invalid_finish_position_count": report.invalid_finish_position_count,
        "missing_required_by_column": report.missing_required_by_column,
        "is_valid": report.is_valid,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if report.is_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
