"""Read-only discovery and audit of prediction artifacts kept on one Mac."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .bet_type_settlement import (
    load_bet_type_race_payouts,
    settle_frozen_bet_type_candidates,
)
from .bundle_audit import load_audited_prediction_bundle


_SKIP_DIRECTORIES = frozenset({
    ".git", ".venv", "__pycache__", "node_modules", "Library",
})
_PROJECT_NAME_MARKERS = ("keiba", "競馬", "racing")
_MAX_VISITED_DIRECTORIES = 20_000


@dataclass(frozen=True)
class LocalArtifactCandidate:
    path: Path
    bundle_status: str
    result_status: str
    race_id: str | None
    scheduled_at: str | None
    frozen_at: str | None
    error: str | None

    def to_dict(self) -> dict[str, object]:
        """Return a stable JSON-ready status without reading files again."""
        return {
            "path": str(self.path),
            "bundle_status": self.bundle_status,
            "result_status": self.result_status,
            "race_id": self.race_id,
            "scheduled_at": self.scheduled_at,
            "frozen_at": self.frozen_at,
            "error": self.error,
        }


@dataclass(frozen=True)
class LocalArtifactStatus:
    status: str
    searched_roots: tuple[Path, ...]
    missing_roots: tuple[Path, ...]
    candidates: tuple[LocalArtifactCandidate, ...]
    visited_directory_count: int
    search_truncated: bool

    def to_dict(self) -> dict[str, object]:
        """Return counts and candidates for CLI or another local UI."""
        valid = sum(row.bundle_status == "valid" for row in self.candidates)
        ready = sum(row.result_status == "valid" for row in self.candidates)
        return {
            "is_valid": True,
            "status": self.status,
            "searched_roots": [str(path) for path in self.searched_roots],
            "missing_roots": [str(path) for path in self.missing_roots],
            "visited_directory_count": self.visited_directory_count,
            "search_truncated": self.search_truncated,
            "candidate_count": len(self.candidates),
            "valid_bundle_count": valid,
            "invalid_bundle_count": len(self.candidates) - valid,
            "ready_for_evaluation_count": ready,
            "candidates": [row.to_dict() for row in self.candidates],
        }


def _project_named_children(parent: Path) -> tuple[Path, ...]:
    if not parent.is_dir():
        return ()
    try:
        children = tuple(parent.iterdir())
    except OSError:
        return ()
    return tuple(
        path for path in children
        if path.is_dir()
        and any(marker in path.name.casefold() for marker in _PROJECT_NAME_MARKERS)
    )


def default_local_artifact_roots(repository: str | Path) -> tuple[Path, ...]:
    """Choose narrow project-like roots without scanning an entire home folder."""
    repo = Path(repository).resolve()
    home = Path.home()
    candidates = [
        repo,
        home / "keiba-prediction-lab",
        home / "競馬",
        home / "Documents" / "keiba-prediction-lab",
        home / "Documents" / "競馬",
        home / "Desktop" / "keiba-prediction-lab",
        home / "Desktop" / "競馬",
        *_project_named_children(repo.parent),
        *_project_named_children(home / "Documents"),
        *_project_named_children(home / "Desktop"),
    ]
    unique: dict[Path, None] = {}
    for path in candidates:
        resolved = path.resolve()
        unique.setdefault(resolved, None)
    return tuple(unique)


def _candidate_status(directory: Path) -> LocalArtifactCandidate:
    try:
        audited = load_audited_prediction_bundle(directory)
    except (OSError, ValueError, UnicodeError) as error:
        return LocalArtifactCandidate(
            directory, "invalid", "not_checked", None, None, None, str(error)
        )
    payout_path = directory / "bet-types-payouts.json"
    result_status = "missing"
    error: str | None = None
    if payout_path.is_file():
        try:
            payouts = load_bet_type_race_payouts(payout_path)
            settle_frozen_bet_type_candidates(
                audited.bundle.bet_type_shadow, payouts
            )
            result_status = "valid"
        except (OSError, ValueError, UnicodeError) as payout_error:
            result_status = "invalid"
            error = str(payout_error)
    audit = audited.audit
    return LocalArtifactCandidate(
        directory,
        "valid",
        result_status,
        audit.race_id,
        audit.scheduled_at.isoformat(),
        audit.frozen_at.isoformat(),
        error,
    )


def inspect_local_artifacts(
    roots: tuple[str | Path, ...],
) -> LocalArtifactStatus:
    """Find manifest directories below explicit roots and audit them read-only."""
    if not roots:
        raise ValueError("at least one local artifact search root is required")
    existing: dict[Path, None] = {}
    missing: dict[Path, None] = {}
    for raw_root in roots:
        root = Path(raw_root).resolve()
        (existing if root.is_dir() else missing).setdefault(root, None)

    candidate_paths: set[Path] = set()
    visited = 0
    truncated = False
    for root in existing:
        for current, directories, files in os.walk(root, followlinks=False):
            directories[:] = sorted(
                name for name in directories
                if name not in _SKIP_DIRECTORIES and not name.startswith(".")
            )
            visited += 1
            if visited > _MAX_VISITED_DIRECTORIES:
                truncated = True
                break
            if "manifest.json" in files:
                candidate_paths.add(Path(current).resolve())
        if truncated:
            break

    candidates = tuple(
        _candidate_status(path) for path in sorted(candidate_paths)
    )
    if any(row.result_status == "valid" for row in candidates):
        status = "ready_for_evaluation"
    elif any(row.bundle_status == "valid" for row in candidates):
        status = "predictions_found"
    elif candidates:
        status = "invalid_candidates_only"
    else:
        status = "no_candidates"
    return LocalArtifactStatus(
        status,
        tuple(existing),
        tuple(missing),
        candidates,
        min(visited, _MAX_VISITED_DIRECTORIES),
        truncated,
    )
