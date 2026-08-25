import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from keiba_prediction_lab.win5 import (
    Win5Leg,
    Win5Runner,
    build_win5_forecast,
    build_win5_forecast_from_legs,
    load_win5_forecast,
    save_win5_forecast,
)


JST = timezone(timedelta(hours=9))


def _legs() -> tuple[Win5Leg, ...]:
    return tuple(
        Win5Leg(
            race_id=f"race-{index}",
            scheduled_at=datetime(2026, 8, 30, 14, index * 10, tzinfo=JST),
            model_version="model-v1",
            input_data_version=f"input-{index}",
            runners=(
                Win5Runner(f"winner-{index}", 0.4),
                Win5Runner(f"second-{index}", 0.3),
                Win5Runner(f"third-{index}", 0.2),
                Win5Runner(f"fourth-{index}", 0.1),
            ),
        )
        for index in range(1, 6)
    )


class Win5ForecastTest(unittest.TestCase):
    def test_builds_from_five_audited_bundle_results(self) -> None:
        legs = _legs()
        audited = [
            SimpleNamespace(
                audit=SimpleNamespace(
                    race_id=leg.race_id,
                    scheduled_at=leg.scheduled_at,
                    frozen_at=datetime(2026, 8, 30, 12, 0, tzinfo=JST),
                    model_version=leg.model_version,
                    input_data_version=leg.input_data_version,
                ),
                bundle=SimpleNamespace(
                    actual_prediction=SimpleNamespace(
                        predictions=leg.runners
                    )
                ),
            )
            for leg in legs
        ]
        with patch(
            "keiba_prediction_lab.win5.load_audited_prediction_bundle",
            side_effect=audited,
        ) as loader:
            forecast = build_win5_forecast(
                [Path(f"race-{index}") for index in range(1, 6)],
                frozen_at=datetime(2026, 8, 30, 13, 0, tzinfo=JST),
            )

        self.assertEqual(loader.call_count, 5)
        self.assertEqual(forecast.selection[0], "winner-1")
        self.assertAlmostEqual(forecast.joint_probability, 0.4 ** 5)

    def test_builds_five_winner_shadow_with_joint_probability(self) -> None:
        frozen_at = datetime(2026, 8, 30, 13, 0, tzinfo=JST)
        forecast = build_win5_forecast_from_legs(
            tuple(reversed(_legs())), frozen_at=frozen_at
        )

        self.assertEqual(
            forecast.selection,
            ("winner-1", "winner-2", "winner-3", "winner-4", "winner-5"),
        )
        self.assertAlmostEqual(forecast.joint_probability, 0.4 ** 5)
        self.assertEqual(forecast.stake_yen, 0)
        self.assertEqual(forecast.to_dict()["purchase_status"], "shadow_only")

    def test_rejects_wrong_race_count_date_and_late_freeze(self) -> None:
        legs = _legs()
        frozen_at = datetime(2026, 8, 30, 13, 0, tzinfo=JST)

        with self.assertRaisesRegex(ValueError, "exactly five"):
            build_win5_forecast_from_legs(legs[:4], frozen_at=frozen_at)

        changed = list(legs)
        changed[-1] = Win5Leg(
            race_id="race-5",
            scheduled_at=datetime(2026, 8, 31, 14, 50, tzinfo=JST),
            model_version="model-v1",
            input_data_version="input-5",
            runners=legs[-1].runners,
        )
        with self.assertRaisesRegex(ValueError, "share one scheduled date"):
            build_win5_forecast_from_legs(changed, frozen_at=frozen_at)

        with self.assertRaisesRegex(ValueError, "before every race"):
            build_win5_forecast_from_legs(
                legs,
                frozen_at=datetime(2026, 8, 30, 15, 0, tzinfo=JST),
            )

    def test_saves_loads_detects_tampering_and_never_overwrites(self) -> None:
        forecast = build_win5_forecast_from_legs(
            _legs(), frozen_at=datetime(2026, 8, 30, 13, 0, tzinfo=JST)
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "win5.json"
            save_win5_forecast(forecast, path)
            loaded = load_win5_forecast(path)

            self.assertEqual(loaded, forecast)
            with self.assertRaises(FileExistsError):
                save_win5_forecast(forecast, path)

            envelope = json.loads(path.read_text(encoding="utf-8"))
            envelope["payload"]["selection"][0] = "tampered"
            path.write_text(json.dumps(envelope), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "integrity check failed"):
                load_win5_forecast(path)

            envelope["payload"]["selection"][0] = "winner-1"
            envelope["payload"]["purchase_status"] = "actual"
            canonical = json.dumps(
                envelope["payload"], ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            )
            envelope["sha256"] = hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest()
            path.write_text(json.dumps(envelope), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "zero-stake shadow"):
                load_win5_forecast(path)


if __name__ == "__main__":
    unittest.main()
