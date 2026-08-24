"""Command-line entry points for local, non-redistributing data audits."""

import argparse
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from .bet_type_bootstrap import (
    BootstrapResamplingUnit,
    DEFAULT_BOOTSTRAP_SAMPLES,
    DEFAULT_BOOTSTRAP_SEED,
    bootstrap_bet_type_evaluation_report_files,
)
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
from .model_artifact import (
    ModelTrainingParameters,
    save_trained_model_artifact,
    train_local_model_artifact,
)
from .prediction_report import build_prediction_bundle_markdown
from .walk_forward_report import (
    evaluate_local_walk_forward,
    save_walk_forward_artifact,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="keiba-lab")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sources = subparsers.add_parser("list-sources", help="show source audit status")
    sources.add_argument(
        "--registry", type=Path, default=Path("data/sources.json")
    )

    audit = subparsers.add_parser("audit-csv", help="audit a standardized CSV")
    audit.add_argument("path", type=Path)

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
    predict_race.add_argument("--output", type=Path, required=True)

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

    audit_bundle = subparsers.add_parser(
        "audit-prediction-bundle",
        help="verify a saved prediction directory without modifying it",
    )
    audit_bundle.add_argument("directory", type=Path)

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

    if args.command == "prepare-features":
        bundle = build_local_feature_bundle(args.history, args.targets)
        save_local_feature_bundle(bundle, args.output)
        print(json.dumps({
            "output": str(args.output),
            "feature_count": len(bundle.features),
            "input_data_version": bundle.input_data_version,
            "history_sha256": bundle.history_sha256,
            "targets_sha256": bundle.targets_sha256,
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
            artifact = evaluate_local_walk_forward(args.training, args.windows)
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
