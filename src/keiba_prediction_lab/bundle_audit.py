"""Read-only integrity audit for a saved formal prediction bundle."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .bet_type_forecast import (
    load_frozen_bet_type_forecast_bytes,
)
from .frozen import load_frozen_prediction_bytes
from .local_pipeline import build_local_input_data_version
from .pipeline import (
    PIPELINE_MANIFEST_SCHEMA_VERSION,
    PIPELINE_POLICY_VERSION,
    RacePredictionBundle,
)
from .shadow_snapshot import load_frozen_shadow_forecast_bytes


@dataclass(frozen=True)
class PredictionBundleAudit:
    race_id: str
    scheduled_at: datetime
    frozen_at: datetime
    model_version: str
    input_data_version: str
    runner_count: int
    actual_ticket_count: int
    actual_stake_yen: int
    shadow_stake_yen: int


@dataclass(frozen=True)
class AuditedPredictionBundle:
    """A verified bundle and the audit metadata from the same byte snapshot."""

    audit: PredictionBundleAudit
    bundle: RacePredictionBundle


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"prediction bundle contains duplicate key: {key}")
        payload[key] = value
    return payload


def _json_bytes(content: bytes, label: str) -> dict[str, object]:
    value = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _exact_keys(
    payload: dict[str, object], expected: frozenset[str], label: str
) -> None:
    missing = expected - payload.keys()
    unexpected = payload.keys() - expected
    if missing or unexpected:
        raise ValueError(
            f"invalid {label} keys: missing={sorted(missing)}, "
            f"unexpected={sorted(unexpected)}"
        )


def _envelope_digest(content: bytes, label: str) -> str:
    envelope = _json_bytes(content, label)
    digest = envelope.get("sha256")
    if not isinstance(digest, str):
        raise ValueError(f"{label} sha256 must be a string")
    return digest


def _verify_provenance(
    content: bytes, input_data_version: str
) -> None:
    envelope = _json_bytes(content, "input provenance")
    _exact_keys(
        envelope, frozenset({"schema_version", "sha256", "payload"}),
        "input provenance envelope",
    )
    if envelope["schema_version"] != "1.0":
        raise ValueError("unsupported input provenance schema_version")
    payload = envelope["payload"]
    if not isinstance(payload, dict):
        raise ValueError("input provenance payload must be an object")
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if digest != envelope["sha256"]:
        raise ValueError("input provenance integrity check failed")
    expected_keys = frozenset({
        "input_data_version", "model_sha256", "history_sha256",
        "targets_sha256", "pace_profiles_sha256", "pace_scenario_sha256",
    })
    _exact_keys(payload, expected_keys, "input provenance payload")
    component_hashes = {
        "model": payload["model_sha256"],
        "history": payload["history_sha256"],
        "targets": payload["targets_sha256"],
        "pace_profiles": payload["pace_profiles_sha256"],
        "pace_scenario": payload["pace_scenario_sha256"],
    }
    if any(not isinstance(value, str) for value in component_hashes.values()):
        raise ValueError("input provenance component hashes must be strings")
    combined = build_local_input_data_version(component_hashes)
    if payload["input_data_version"] != combined:
        raise ValueError("input provenance version does not match component hashes")
    if combined != input_data_version:
        raise ValueError("input provenance does not match prediction artifacts")


def load_audited_prediction_bundle(
    directory: str | Path,
) -> AuditedPredictionBundle:
    """Load and verify one immutable byte snapshot of a prediction bundle."""
    root = Path(directory)
    required = {
        "manifest.json", "actual.json", "baseline-shadow.json",
        "pace-shadow.json", "bet-types-shadow.json", "input-provenance.json",
    }
    missing = sorted(name for name in required if not (root / name).is_file())
    if missing:
        raise ValueError(f"prediction bundle missing required files: {missing}")
    contents = {name: (root / name).read_bytes() for name in required}
    manifest = _json_bytes(contents["manifest.json"], "pipeline manifest")
    _exact_keys(
        manifest,
        frozenset({"schema_version", "policy_version", "race_id", "actual", "shadows"}),
        "pipeline manifest",
    )
    if manifest["schema_version"] != PIPELINE_MANIFEST_SCHEMA_VERSION:
        raise ValueError("unsupported pipeline manifest schema_version")
    if manifest["policy_version"] != PIPELINE_POLICY_VERSION:
        raise ValueError("unsupported pipeline manifest policy_version")

    actual_entry = manifest["actual"]
    shadow_entries = manifest["shadows"]
    if not isinstance(actual_entry, dict) or not isinstance(shadow_entries, list):
        raise ValueError("pipeline manifest artifact entries have invalid types")
    _exact_keys(
        actual_entry,
        frozenset({"file", "sha256", "generator_version", "ticket_count", "stake_yen"}),
        "pipeline manifest actual",
    )
    if actual_entry["file"] != "actual.json":
        raise ValueError("pipeline manifest actual file is invalid")
    if len(shadow_entries) != 3 or any(
        not isinstance(entry, dict) for entry in shadow_entries
    ):
        raise ValueError("pipeline manifest must contain three shadow entries")
    shadows_by_file = {entry.get("file"): entry for entry in shadow_entries}
    expected_shadow_files = {
        "baseline-shadow.json", "pace-shadow.json", "bet-types-shadow.json",
    }
    if set(shadows_by_file) != expected_shadow_files:
        raise ValueError("pipeline manifest shadow files are invalid")
    standard_shadow_keys = frozenset({
        "file", "sha256", "artifact_type", "generator_version", "stake_yen",
    })
    _exact_keys(
        shadows_by_file["baseline-shadow.json"], standard_shadow_keys,
        "baseline shadow manifest entry",
    )
    _exact_keys(
        shadows_by_file["pace-shadow.json"], standard_shadow_keys,
        "pace shadow manifest entry",
    )
    _exact_keys(
        shadows_by_file["bet-types-shadow.json"],
        standard_shadow_keys | {"candidate_count", "place_payout_slots"},
        "bet-type shadow manifest entry",
    )

    artifact_files = {"actual.json", *expected_shadow_files}
    manifest_digests = {
        "actual.json": actual_entry["sha256"],
        **{name: entry.get("sha256") for name, entry in shadows_by_file.items()},
    }
    for name in artifact_files:
        if manifest_digests[name] != _envelope_digest(contents[name], name):
            raise ValueError(f"pipeline manifest digest mismatch for {name}")

    actual = load_frozen_prediction_bytes(contents["actual.json"])
    baseline = load_frozen_shadow_forecast_bytes(contents["baseline-shadow.json"])
    pace = load_frozen_shadow_forecast_bytes(contents["pace-shadow.json"])
    bet_types = load_frozen_bet_type_forecast_bytes(
        contents["bet-types-shadow.json"]
    )
    bundle = RacePredictionBundle(
        policy_version=manifest["policy_version"],
        actual_prediction=actual,
        baseline_shadow=baseline,
        pace_shadow=pace,
        bet_type_shadow=bet_types,
    )
    if manifest["race_id"] != actual.race_id:
        raise ValueError("pipeline manifest race_id does not match artifacts")
    if actual_entry["ticket_count"] != 1 or actual_entry["stake_yen"] != 100:
        raise ValueError("pipeline manifest actual stake policy is invalid")
    if actual_entry["generator_version"] != baseline.generator_version:
        raise ValueError("pipeline manifest actual generator is invalid")
    if any(entry.get("stake_yen") != 0 for entry in shadow_entries):
        raise ValueError("pipeline manifest shadow stake must be zero")
    for name, artifact in (
        ("baseline-shadow.json", baseline),
        ("pace-shadow.json", pace),
        ("bet-types-shadow.json", bet_types),
    ):
        entry = shadows_by_file[name]
        if entry["generator_version"] != artifact.generator_version:
            raise ValueError(f"pipeline manifest generator mismatch for {name}")
    if any(
        shadows_by_file[name]["artifact_type"] != "trifecta_portfolios"
        for name in ("baseline-shadow.json", "pace-shadow.json")
    ) or (
        shadows_by_file["bet-types-shadow.json"]["artifact_type"]
        != "bet_type_candidates"
    ):
        raise ValueError("pipeline manifest shadow artifact_type is invalid")
    bet_entry = shadows_by_file["bet-types-shadow.json"]
    if (
        bet_entry.get("candidate_count") != len(bet_types.forecast.candidates)
        or bet_entry.get("place_payout_slots")
        != bet_types.forecast.place_payout_slots
    ):
        raise ValueError("pipeline manifest bet-type metadata is invalid")
    _verify_provenance(
        contents["input-provenance.json"], actual.input_data_version
    )
    audit = PredictionBundleAudit(
        race_id=actual.race_id,
        scheduled_at=actual.scheduled_at,
        frozen_at=actual.frozen_at,
        model_version=actual.model_version,
        input_data_version=actual.input_data_version,
        runner_count=len(actual.predictions),
        actual_ticket_count=len(actual.trifecta_tickets),
        actual_stake_yen=actual.trifecta_tickets[0].stake_yen,
        shadow_stake_yen=0,
    )
    return AuditedPredictionBundle(audit=audit, bundle=bundle)


def audit_prediction_bundle(directory: str | Path) -> PredictionBundleAudit:
    """Verify required files, their digests, and all cross-file contracts."""
    return load_audited_prediction_bundle(directory).audit
