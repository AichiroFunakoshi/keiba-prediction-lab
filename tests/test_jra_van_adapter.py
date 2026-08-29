import csv
import hashlib
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from keiba_prediction_lab.jra_van_adapter import prepare_jra_van_race_day
from keiba_prediction_lab.local_adapter import load_history_csv, load_targets_csv
from keiba_prediction_lab.local_pipeline import load_local_pace_profiles, load_local_pace_scenario
from keiba_prediction_lab.pace_estimation import load_pace_history_csv
from keiba_prediction_lab.race_day_pipeline import load_local_race_day_plan


def _put(raw: bytearray, position: int, length: int, value: str) -> None:
    content = value.encode("cp932")
    if len(content) > length:
        raise AssertionError(value)
    raw[position - 1:position - 1 + length] = content.ljust(length, b" ")


def _ra(key: str, division: str, *, start="1200", runners="03") -> str:
    raw = bytearray(b" " * 1272)
    _put(raw, 1, 2, "RA"); _put(raw, 3, 1, division); _put(raw, 4, 8, key[:8])
    _put(raw, 12, 16, key); _put(raw, 698, 4, "1600"); _put(raw, 706, 2, "11")
    _put(raw, 874, 4, start); _put(raw, 884, 2, runners)
    _put(raw, 889, 1, "1"); _put(raw, 890, 1, "1")
    return raw.decode("cp932")


def _se(key: str, post: int, division: str, *, finish=0, first=0, final=0, last=0) -> str:
    raw = bytearray(b" " * 555)
    _put(raw, 1, 2, "SE"); _put(raw, 3, 1, division); _put(raw, 4, 8, key[:8])
    _put(raw, 12, 16, key); _put(raw, 29, 2, f"{post:02d}")
    _put(raw, 31, 10, f"20260{post:05d}"); _put(raw, 41, 36, f"Horse{post}")
    _put(raw, 86, 5, f"{10000 + post}"); _put(raw, 289, 3, "560")
    _put(raw, 297, 5, f"{20000 + post}"); _put(raw, 325, 3, str(470 + post))
    if finish:
        _put(raw, 335, 2, f"{finish:02d}"); _put(raw, 352, 2, f"{first:02d}")
        _put(raw, 358, 2, f"{final:02d}"); _put(raw, 391, 3, f"{last:03d}")
    return raw.decode("cp932")


def _we(day: str, venue="05") -> str:
    raw = bytearray(b" " * 42)
    _put(raw, 1, 2, "WE"); _put(raw, 3, 1, "1"); _put(raw, 4, 8, day)
    _put(raw, 12, 8, day); _put(raw, 20, 2, venue); _put(raw, 22, 2, "01")
    _put(raw, 24, 2, "01"); _put(raw, 26, 8, "09010830"); _put(raw, 34, 1, "1")
    _put(raw, 35, 1, "1"); _put(raw, 36, 1, "1"); _put(raw, 37, 1, "1")
    return raw.decode("cp932")


def _wh(key: str, posts: int) -> str:
    raw = bytearray(b" " * 847)
    _put(raw, 1, 2, "WH"); _put(raw, 3, 1, "1"); _put(raw, 4, 8, key[:8]); _put(raw, 12, 16, key)
    _put(raw, 28, 8, "09010900")
    for index in range(posts):
        position = 36 + index * 45
        _put(raw, position, 2, f"{index + 1:02d}")
        _put(raw, position + 2, 36, f"Horse{index + 1}")
        _put(raw, position + 38, 3, str(480 + index))
    return raw.decode("cp932")


def _av(key: str, post: int) -> str:
    raw = bytearray(b" " * 78)
    _put(raw, 1, 2, "AV"); _put(raw, 3, 1, "1"); _put(raw, 4, 8, key[:8]); _put(raw, 12, 16, key)
    _put(raw, 28, 8, "09010905"); _put(raw, 36, 2, f"{post:02d}")
    return raw.decode("cp932")


def _snapshot(root: Path, name: str, records: list[str], acquired: str) -> Path:
    directory = root / name
    directory.mkdir()
    lines = []
    for raw in records:
        lines.append(json.dumps({
            "record_type": raw[:2], "raw": raw,
            "source_filename": name, "download_timestamp": "20260829090000",
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    content = ("\n".join(lines) + "\n").encode("utf-8")
    (directory / "jv-data-records.jsonl").write_bytes(content)
    (directory / "jv-fetch-manifest.json").write_text(json.dumps({
        "schema_version": "1.0", "source_id": "jra-van-data-lab",
        "acquired_at": acquired, "record_count": len(records),
        "records_file": "jv-data-records.jsonl",
        "records_sha256": hashlib.sha256(content).hexdigest(),
    }), encoding="utf-8")
    return directory


class JraVanAdapterTest(unittest.TestCase):
    def _snapshots(self, root: Path):
        past = "2026080105010101"
        current = "2026090105010101"
        history = _snapshot(root, "history", [
            _ra(past, "6"),
            _se(past, 1, "6", finish=1, first=1, final=1, last=370),
            _se(past, 2, "6", finish=2, first=2, final=2, last=360),
            _se(past, 3, "6", finish=3, first=3, final=3, last=350),
        ], "2026-08-02T00:00:00+09:00")
        race = _snapshot(root, "race", [
            _ra(current, "2", runners="04"),
            *[_se(current, post, "2") for post in range(1, 5)],
        ], "2026-09-01T08:00:00+09:00")
        realtime = _snapshot(root, "realtime", [
            _we("20260901"), _wh(current, 4), _av(current, 4),
        ], "2026-09-01T09:05:00+09:00")
        return history, race, realtime

    def test_creates_prediction_ready_day_inputs_and_automatic_pace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history, race, realtime = self._snapshots(root)
            output = root / "prepared"
            plan_path = prepare_jra_van_race_day(
                history, race, realtime, output,
                race_date=date(2026, 9, 1),
                observed_at=datetime.fromisoformat("2026-09-01T09:10:00+09:00"),
            )
            plan = load_local_race_day_plan(plan_path)
            history_rows = load_history_csv(output / "history.csv")
            pace_history = load_pace_history_csv(output / "pace-history.csv")
            targets = load_targets_csv(plan.races[0].targets)
            profiles = load_local_pace_profiles(plan.races[0].pace_profiles)
            scenario = load_local_pace_scenario(plan.races[0].pace_scenario)
            manifest = json.loads((output / "jra-van-adapter-manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(len(plan.races), 1)
        self.assertEqual(len(history_rows), 3)
        self.assertEqual(len(pace_history), 3)
        self.assertEqual(len(targets), 3)  # withdrawn post 4 excluded
        self.assertEqual([row.body_weight_kg for row in targets], [480, 481, 482])
        self.assertEqual(len(profiles), 3)
        self.assertEqual(scenario.race_id, targets[0].race_id)
        self.assertEqual(manifest["race_count"], 1)

    def test_tampered_snapshot_fails_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history, race, realtime = self._snapshots(root)
            with (race / "jv-data-records.jsonl").open("ab") as handle:
                handle.write(b"tampered\n")
            output = root / "prepared"
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                prepare_jra_van_race_day(
                    history, race, realtime, output,
                    race_date=date(2026, 9, 1),
                    observed_at=datetime.fromisoformat("2026-09-01T09:10:00+09:00"),
                )
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
