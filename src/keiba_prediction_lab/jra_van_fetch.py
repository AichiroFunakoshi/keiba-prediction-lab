"""Licensed local acquisition through the official Windows-only JV-Link COM API.

This module intentionally stores untouched JV-Data records plus an integrity
manifest.  Parsing belongs to a separately versioned adapter so an SDK schema
change cannot silently alter model inputs.
"""

from __future__ import annotations

import hashlib
import json
import math
import platform
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


JRA_VAN_SOURCE_ID = "jra-van-data-lab"
JV_FETCH_SCHEMA_VERSION = "1.0"
MAX_RECORD_BYTES = 1_048_576
DEFAULT_MAX_POLL_SECONDS = 600.0


@dataclass(frozen=True)
class JvFetchResult:
    output_directory: Path
    records_path: Path
    manifest_path: Path
    record_count: int
    records_sha256: str
    acquired_at: datetime


def _read_tuple(value: Any) -> tuple[int, str, str, str]:
    if not isinstance(value, tuple) or len(value) != 4:
        raise RuntimeError("JVRead returned an unexpected value")
    count, record, filename, download_timestamp = value
    if type(count) is not int or not isinstance(record, str):
        raise RuntimeError("JVRead returned invalid field types")
    return count, record, str(filename), str(download_timestamp)


def fetch_jv_data(
    jvlink: Any,
    output_directory: str | Path,
    *,
    dataspec: str = "RACE",
    fromtime: str = "00000000000000",
    option: int = 2,
    sid: str = "UNKNOWN",
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    wait: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    max_poll_seconds: float = DEFAULT_MAX_POLL_SECONDS,
) -> JvFetchResult:
    """Fetch records once through an initialized local JV-Link installation."""
    if not dataspec.strip() or not sid.strip():
        raise ValueError("dataspec and sid must not be empty")
    if len(fromtime) != 14 or not fromtime.isdigit():
        raise ValueError("fromtime must use YYYYMMDDhhmmss or fourteen zeroes")
    if option not in (1, 2, 3, 4):
        raise ValueError("option must be a documented JVOpen option")
    if not math.isfinite(max_poll_seconds) or max_poll_seconds <= 0:
        raise ValueError("max_poll_seconds must be a positive finite number")
    acquired_at = now()
    if acquired_at.tzinfo is None or acquired_at.utcoffset() is None:
        raise ValueError("acquisition time must be timezone-aware")
    target = Path(output_directory)
    target.mkdir(parents=True, exist_ok=False)
    records_path = target / "jv-data-records.jsonl"
    manifest_path = target / "jv-fetch-manifest.json"
    initialized = False
    opened = False
    try:
        init_code = int(jvlink.JVInit(sid))
        if init_code != 0:
            raise RuntimeError(f"JVInit failed with code {init_code}")
        initialized = True
        opened_result = jvlink.JVOpen(dataspec, fromtime, option, 0, 0, "")
        if not isinstance(opened_result, tuple) or len(opened_result) != 4:
            raise RuntimeError("JVOpen returned an unexpected value")
        open_code, read_count, download_count, last_timestamp = opened_result
        if int(open_code) != 0:
            raise RuntimeError(f"JVOpen failed with code {open_code}")
        opened = True
        record_count = 0
        stalled_since: float | None = None
        with records_path.open("xb") as handle:
            while True:
                count, record, filename, download_timestamp = _read_tuple(
                    jvlink.JVRead(" " * MAX_RECORD_BYTES, MAX_RECORD_BYTES, "")
                )
                if count > 0:
                    stalled_since = None
                    raw = record[:count]
                    payload = json.dumps({
                        "record_type": raw[:2],
                        "raw": raw,
                        "source_filename": filename,
                        "download_timestamp": download_timestamp,
                    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    handle.write(payload.encode("utf-8") + b"\n")
                    record_count += 1
                elif count == -3:
                    current = monotonic()
                    if stalled_since is None:
                        stalled_since = current
                    elif current - stalled_since >= max_poll_seconds:
                        raise RuntimeError(
                            "JVRead download polling exceeded "
                            f"{max_poll_seconds:g} seconds"
                        )
                    wait(0.25)
                elif count == -1:
                    stalled_since = None
                    continue
                elif count == 0:
                    break
                else:
                    raise RuntimeError(f"JVRead failed with code {count}")
        digest = hashlib.sha256(records_path.read_bytes()).hexdigest()
        manifest = {
            "schema_version": JV_FETCH_SCHEMA_VERSION,
            "source_id": JRA_VAN_SOURCE_ID,
            "acquired_at": acquired_at.isoformat(),
            "query": {
                "dataspec": dataspec,
                "fromtime": fromtime,
                "option": option,
            },
            "jv_open": {
                "read_count": int(read_count),
                "download_count": int(download_count),
                "last_timestamp": str(last_timestamp),
            },
            "record_count": record_count,
            "records_file": records_path.name,
            "records_sha256": digest,
            "raw_data_redistribution": "prohibited",
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return JvFetchResult(
            output_directory=target,
            records_path=records_path,
            manifest_path=manifest_path,
            record_count=record_count,
            records_sha256=digest,
            acquired_at=acquired_at,
        )
    except Exception:
        manifest_path.unlink(missing_ok=True)
        records_path.unlink(missing_ok=True)
        target.rmdir()
        raise
    finally:
        if opened or initialized:
            try:
                jvlink.JVClose()
            except Exception:
                pass


def fetch_jra_van_on_windows(
    output_directory: str | Path,
    *,
    dataspec: str = "RACE",
    fromtime: str = "00000000000000",
    option: int = 2,
    sid: str = "UNKNOWN",
    max_poll_seconds: float = DEFAULT_MAX_POLL_SECONDS,
) -> JvFetchResult:
    """Create the official COM object on Windows and perform one acquisition."""
    if platform.system() != "Windows":
        raise RuntimeError(
            "JV-Link is Windows-only; run this command in a Windows VM or PC"
        )
    try:
        import win32com.client  # type: ignore[import-not-found]
    except ImportError as error:
        raise RuntimeError(
            "pywin32 is required in the Windows environment: pip install pywin32"
        ) from error
    jvlink = win32com.client.Dispatch("JVDTLab.JVLink")
    return fetch_jv_data(
        jvlink,
        output_directory,
        dataspec=dataspec,
        fromtime=fromtime,
        option=option,
        sid=sid,
        max_poll_seconds=max_poll_seconds,
    )


def fetch_jv_realtime(
    jvlink: Any,
    output_directory: str | Path,
    *,
    dataspec: str,
    key: str,
    sid: str = "UNKNOWN",
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    wait: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    max_poll_seconds: float = DEFAULT_MAX_POLL_SECONDS,
) -> JvFetchResult:
    """Fetch a documented real-time JV-Data stream such as 0B14/yyyymmdd."""
    if not dataspec.strip() or not key.strip() or not sid.strip():
        raise ValueError("dataspec, key, and sid must not be empty")
    if not math.isfinite(max_poll_seconds) or max_poll_seconds <= 0:
        raise ValueError("max_poll_seconds must be a positive finite number")
    acquired_at = now()
    if acquired_at.tzinfo is None or acquired_at.utcoffset() is None:
        raise ValueError("acquisition time must be timezone-aware")
    target = Path(output_directory)
    target.mkdir(parents=True, exist_ok=False)
    records_path = target / "jv-data-records.jsonl"
    manifest_path = target / "jv-fetch-manifest.json"
    initialized = False
    opened = False
    try:
        init_code = int(jvlink.JVInit(sid))
        if init_code != 0:
            raise RuntimeError(f"JVInit failed with code {init_code}")
        initialized = True
        open_code = int(jvlink.JVRTOpen(dataspec, key))
        if open_code != 0:
            raise RuntimeError(f"JVRTOpen failed with code {open_code}")
        opened = True
        record_count = 0
        stalled_since: float | None = None
        with records_path.open("xb") as handle:
            while True:
                count, record, filename, download_timestamp = _read_tuple(
                    jvlink.JVRead(" " * MAX_RECORD_BYTES, MAX_RECORD_BYTES, "")
                )
                if count > 0:
                    stalled_since = None
                    raw = record[:count]
                    handle.write((json.dumps({
                        "record_type": raw[:2], "raw": raw,
                        "source_filename": filename,
                        "download_timestamp": download_timestamp,
                    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
                    record_count += 1
                elif count == -3:
                    current = monotonic()
                    if stalled_since is None:
                        stalled_since = current
                    elif current - stalled_since >= max_poll_seconds:
                        raise RuntimeError(
                            "JVRead download polling exceeded "
                            f"{max_poll_seconds:g} seconds"
                        )
                    wait(0.25)
                elif count == -1:
                    stalled_since = None
                    continue
                elif count == 0:
                    break
                else:
                    raise RuntimeError(f"JVRead failed with code {count}")
        digest = hashlib.sha256(records_path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps({
            "schema_version": JV_FETCH_SCHEMA_VERSION,
            "source_id": JRA_VAN_SOURCE_ID,
            "acquired_at": acquired_at.isoformat(),
            "query": {"dataspec": dataspec, "key": key, "realtime": True},
            "record_count": record_count,
            "records_file": records_path.name,
            "records_sha256": digest,
            "raw_data_redistribution": "prohibited",
        }, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return JvFetchResult(target, records_path, manifest_path, record_count, digest, acquired_at)
    except Exception:
        manifest_path.unlink(missing_ok=True)
        records_path.unlink(missing_ok=True)
        target.rmdir()
        raise
    finally:
        if opened or initialized:
            try:
                jvlink.JVClose()
            except Exception:
                pass


def fetch_jra_van_realtime_on_windows(
    output_directory: str | Path,
    *,
    dataspec: str,
    key: str,
    sid: str = "UNKNOWN",
    max_poll_seconds: float = DEFAULT_MAX_POLL_SECONDS,
) -> JvFetchResult:
    if platform.system() != "Windows":
        raise RuntimeError("JV-Link is Windows-only; run this command in a Windows VM or PC")
    try:
        import win32com.client  # type: ignore[import-not-found]
    except ImportError as error:
        raise RuntimeError("pywin32 is required in the Windows environment: pip install pywin32") from error
    return fetch_jv_realtime(
        win32com.client.Dispatch("JVDTLab.JVLink"),
        output_directory, dataspec=dataspec, key=key, sid=sid,
        max_poll_seconds=max_poll_seconds,
    )
