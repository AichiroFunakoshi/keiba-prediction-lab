"""Data-source registry and dependency-free CSV quality checks."""

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path


class SourceStatus(str, Enum):
    APPROVED = "approved"
    REVIEW_REQUIRED = "review_required"
    REJECTED = "rejected"


class RedistributionStatus(str, Enum):
    ALLOWED = "allowed"
    PROHIBITED = "prohibited"
    UNCLEAR = "unclear"


class ColumnAvailability(str, Enum):
    PRE_RACE = "pre_race"
    RESULT = "result"
    TIMESTAMPED_MARKET = "timestamped_market"


STANDARD_COLUMN_AVAILABILITY = {
    "race_id": ColumnAvailability.PRE_RACE,
    "race_date": ColumnAvailability.PRE_RACE,
    "race_number": ColumnAvailability.PRE_RACE,
    "venue": ColumnAvailability.PRE_RACE,
    "horse_id": ColumnAvailability.PRE_RACE,
    "horse_name": ColumnAvailability.PRE_RACE,
    "post_position": ColumnAvailability.PRE_RACE,
    "finish_position": ColumnAvailability.RESULT,
    "result_status": ColumnAvailability.RESULT,
    "final_odds": ColumnAvailability.RESULT,
    "payout_yen": ColumnAvailability.RESULT,
    "race_time": ColumnAvailability.RESULT,
    "last_3f": ColumnAvailability.RESULT,
    "corner_positions": ColumnAvailability.RESULT,
    "observed_odds": ColumnAvailability.TIMESTAMPED_MARKET,
    "odds_observed_at": ColumnAvailability.TIMESTAMPED_MARKET,
}

REQUIRED_STANDARD_COLUMNS = frozenset(
    {
        "race_id",
        "race_date",
        "horse_id",
        "horse_name",
        "post_position",
        "finish_position",
        "result_status",
    }
)


@dataclass(frozen=True)
class DataSource:
    source_id: str
    name: str
    homepage: str
    scope: str
    declared_license: str
    provenance: str
    redistribution: RedistributionStatus
    status: SourceStatus
    reason: str


@dataclass(frozen=True)
class CsvAuditReport:
    path: Path
    sha256: str
    row_count: int
    duplicate_key_count: int
    invalid_date_count: int
    invalid_finish_position_count: int
    missing_required_by_column: dict[str, int]

    @property
    def is_valid(self) -> bool:
        return (
            self.duplicate_key_count == 0
            and self.invalid_date_count == 0
            and self.invalid_finish_position_count == 0
            and not any(self.missing_required_by_column.values())
        )


def load_source_registry(path: str | Path) -> tuple[DataSource, ...]:
    """Load and validate the versioned JSON source registry."""
    registry_path = Path(path)
    with registry_path.open(encoding="utf-8") as file:
        payload = json.load(file)
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported source registry schema_version")

    sources = tuple(
        DataSource(
            source_id=item["source_id"],
            name=item["name"],
            homepage=item["homepage"],
            scope=item["scope"],
            declared_license=item["declared_license"],
            provenance=item["provenance"],
            redistribution=RedistributionStatus(item["redistribution"]),
            status=SourceStatus(item["status"]),
            reason=item["reason"],
        )
        for item in payload["sources"]
    )
    source_ids = [source.source_id for source in sources]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("source_id values must be unique")
    return sources


def sha256_file(path: str | Path) -> str:
    """Return a stable SHA-256 digest without loading the whole file into memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_pre_race_features(columns: set[str]) -> None:
    """Reject result, unknown, or timestamped-market columns as model features."""
    unknown = columns - STANDARD_COLUMN_AVAILABILITY.keys()
    if unknown:
        raise ValueError(f"unknown feature columns: {sorted(unknown)}")
    disallowed = {
        column
        for column in columns
        if STANDARD_COLUMN_AVAILABILITY[column] is not ColumnAvailability.PRE_RACE
    }
    if disallowed:
        raise ValueError(f"non-pre-race feature columns: {sorted(disallowed)}")


def audit_standard_csv(path: str | Path) -> CsvAuditReport:
    """Audit a standardized horse-per-race CSV without retaining its contents."""
    csv_path = Path(path)
    missing_required = {column: 0 for column in sorted(REQUIRED_STANDARD_COLUMNS)}
    seen_keys: set[tuple[str, str]] = set()
    duplicate_keys = 0
    invalid_dates = 0
    invalid_finish_positions = 0
    row_count = 0

    with csv_path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        fieldnames = set(reader.fieldnames or ())
        absent_columns = REQUIRED_STANDARD_COLUMNS - fieldnames
        if absent_columns:
            raise ValueError(f"missing required columns: {sorted(absent_columns)}")

        for row in reader:
            row_count += 1
            for column in missing_required:
                if (
                    column == "finish_position"
                    and (row.get("result_status") or "").strip() != "finished"
                ):
                    continue
                if not (row.get(column) or "").strip():
                    missing_required[column] += 1

            key = ((row.get("race_id") or "").strip(), (row.get("horse_id") or "").strip())
            if key in seen_keys:
                duplicate_keys += 1
            seen_keys.add(key)

            try:
                date.fromisoformat((row.get("race_date") or "").strip())
            except ValueError:
                invalid_dates += 1

            finish_position = (row.get("finish_position") or "").strip()
            result_status = (row.get("result_status") or "").strip()
            if result_status == "finished":
                try:
                    if int(finish_position) < 1:
                        invalid_finish_positions += 1
                except ValueError:
                    invalid_finish_positions += 1

    return CsvAuditReport(
        path=csv_path,
        sha256=sha256_file(csv_path),
        row_count=row_count,
        duplicate_key_count=duplicate_keys,
        invalid_date_count=invalid_dates,
        invalid_finish_position_count=invalid_finish_positions,
        missing_required_by_column=missing_required,
    )
