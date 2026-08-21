"""Command-line entry points for local, non-redistributing data audits."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from .bet_type_forecast import load_frozen_bet_type_forecast
from .bet_type_settlement import (
    evaluate_frozen_bet_type_candidates,
    load_bet_type_race_payouts,
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
        snapshots = tuple(
            load_frozen_bet_type_forecast(path / "bet-types-shadow.json")
            for path in args.race_directories
        )
        payouts = tuple(
            load_bet_type_race_payouts(path / "bet-types-payouts.json")
            for path in args.race_directories
        )
        print(
            evaluate_frozen_bet_type_candidates(snapshots, payouts).to_markdown(),
            end="",
        )
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
