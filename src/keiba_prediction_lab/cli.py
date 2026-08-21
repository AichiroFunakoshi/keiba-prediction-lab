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
from .data_audit import audit_standard_csv, load_source_registry


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="keiba-lab")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sources = subparsers.add_parser("list-sources", help="show source audit status")
    sources.add_argument(
        "--registry", type=Path, default=Path("data/sources.json")
    )

    audit = subparsers.add_parser("audit-csv", help="audit a standardized CSV")
    audit.add_argument("path", type=Path)

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
