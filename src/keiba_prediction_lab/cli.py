"""Command-line entry points for local, non-redistributing data audits."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

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
