import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from keiba_prediction_lab.features import Surface
from keiba_prediction_lab.race_context import (
    RaceContext,
    load_race_context,
    save_race_context,
)


def context() -> RaceContext:
    return RaceContext(
        "race-1",
        datetime(2026, 8, 22, 4, 0, tzinfo=timezone.utc),
        "Tokyo",
        Surface.TURF,
        "good",
        1600,
        "G1",
        16,
    )


class RaceContextTest(unittest.TestCase):
    def test_round_trip_is_integrity_protected_and_not_overwritten(self) -> None:
        original = context()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "race-context.json"
            digest = save_race_context(original, path)
            loaded = load_race_context(path)

            with self.assertRaises(FileExistsError):
                save_race_context(original, path)

            envelope = json.loads(path.read_text(encoding="utf-8"))
            envelope["payload"]["distance_m"] = 1800
            path.write_text(json.dumps(envelope), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "integrity check failed"):
                load_race_context(path)

        self.assertEqual(loaded, original)
        self.assertEqual(len(digest), 64)
        self.assertEqual(original.distance_band, "mile")
        self.assertEqual(original.field_size_bucket, "large-13-plus")

    def test_rejects_invalid_or_raw_context_values(self) -> None:
        original = context()
        with self.assertRaisesRegex(ValueError, "surface"):
            replace(original, surface="turf")  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "observed_at"):
            replace(original, observed_at=datetime(2026, 8, 22, 4, 0))
        with self.assertRaisesRegex(ValueError, "distance_m"):
            replace(original, distance_m=0)
        with self.assertRaisesRegex(ValueError, "field_size"):
            replace(original, field_size=0)
        with self.assertRaisesRegex(ValueError, "race_class"):
            replace(original, race_class=" ")


if __name__ == "__main__":
    unittest.main()
