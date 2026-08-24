"""Command-line entry points for local, non-redistributing data audits."""

import argparse
import json
from collections.abc import Sequence
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
from .data_audit import audit_standard_csv, load_source_registry
from .local_adapter import (
    build_local_feature_bundle,
    build_time_safe_training_bundle,
    save_local_feature_bundle,
    save_local_training_bundle,
)
from .model_artifact import (
    ModelTrainingParameters,
    save_trained_model_artifact,
    train_local_model_artifact,
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
