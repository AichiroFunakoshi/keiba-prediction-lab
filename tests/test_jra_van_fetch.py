import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from keiba_prediction_lab.jra_van_fetch import (
    fetch_jv_data, fetch_jv_realtime, fetch_jra_van_on_windows,
)


class FakeJvLink:
    def __init__(self, reads=None, *, init_code=0, open_code=0):
        self.reads = list(reads or [])
        self.init_code = init_code
        self.open_code = open_code
        self.closed = False
        self.open_args = None

    def JVInit(self, sid):
        self.sid = sid
        return self.init_code

    def JVOpen(self, *args):
        self.open_args = args
        return self.open_code, 2, 1, "20260829090000"

    def JVRead(self, _buffer, _size, _filename):
        return self.reads.pop(0) if self.reads else (0, "", "", "")

    def JVClose(self):
        self.closed = True

    def JVRTOpen(self, dataspec, key):
        self.realtime_args = (dataspec, key)
        return self.open_code


class JraVanFetchTest(unittest.TestCase):
    def test_fetches_raw_records_and_integrity_manifest(self):
        fake = FakeJvLink([
            (8, "RArecord", "file-1", "20260829090000"),
            (-3, "", "", ""),
            (8, "SErecord", "file-1", "20260829090000"),
            (0, "", "", ""),
        ])
        waits = []
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "snapshot"
            result = fetch_jv_data(
                fake, output,
                now=lambda: datetime.fromisoformat("2026-08-29T09:00:00+09:00"),
                wait=waits.append,
            )
            lines = [json.loads(line) for line in result.records_path.read_text(encoding="utf-8").splitlines()]
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual([line["record_type"] for line in lines], ["RA", "SE"])
        self.assertEqual(result.record_count, 2)
        self.assertEqual(len(result.records_sha256), 64)
        self.assertEqual(manifest["source_id"], "jra-van-data-lab")
        self.assertEqual(manifest["raw_data_redistribution"], "prohibited")
        self.assertEqual(waits, [0.25])
        self.assertTrue(fake.closed)
        self.assertEqual(fake.open_args[:3], ("RACE", "00000000000000", 2))

    def test_failure_is_atomic_and_closes_jv_link(self):
        fake = FakeJvLink([(-203, "", "", "")])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "snapshot"
            with self.assertRaisesRegex(RuntimeError, "-203"):
                fetch_jv_data(fake, output)
            self.assertFalse(output.exists())
        self.assertTrue(fake.closed)

    def test_init_failure_leaves_no_output(self):
        fake = FakeJvLink(init_code=-101)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "snapshot"
            with self.assertRaisesRegex(RuntimeError, "JVInit"):
                fetch_jv_data(fake, output)
            self.assertFalse(output.exists())

    def test_mac_entry_point_refuses_to_claim_jv_link_support(self):
        with patch("keiba_prediction_lab.jra_van_fetch.platform.system", return_value="Darwin"):
            with self.assertRaisesRegex(RuntimeError, "Windows-only"):
                fetch_jra_van_on_windows("unused")

    def test_rejects_invalid_query_before_creating_output(self):
        fake = FakeJvLink()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "snapshot"
            with self.assertRaisesRegex(ValueError, "fromtime"):
                fetch_jv_data(fake, output, fromtime="20260829")
            self.assertFalse(output.exists())

    def test_fetches_realtime_weather_stream(self):
        fake = FakeJvLink([(8, "WErecord", "rt", "20260829091000"), (0, "", "", "")])
        with tempfile.TemporaryDirectory() as directory:
            result = fetch_jv_realtime(
                fake, Path(directory) / "weather", dataspec="0B14", key="20260829"
            )
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(fake.realtime_args, ("0B14", "20260829"))
        self.assertTrue(manifest["query"]["realtime"])
        self.assertEqual(result.record_count, 1)


if __name__ == "__main__":
    unittest.main()
