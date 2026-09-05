import tempfile
import unittest
from pathlib import Path

from keiba_prediction_lab.market_blend import (
    blend_probabilities,
    load_market_blend_forecast,
    save_market_blend_forecast,
)


class MarketBlendTest(unittest.TestCase):
    def test_log_pool_uses_market_without_becoming_market_only(self) -> None:
        model = {"a": 0.60, "b": 0.30, "c": 0.10}
        odds = {"a": 10.0, "b": 2.0, "c": 5.0}

        market, blended = blend_probabilities(model, odds, market_weight=0.35)

        self.assertAlmostEqual(sum(market.values()), 1.0)
        self.assertAlmostEqual(sum(blended.values()), 1.0)
        self.assertGreater(blended["b"], model["b"])
        self.assertGreater(blended["a"], market["a"])
        self.assertNotEqual(blended, model)
        self.assertNotEqual(blended, market)

    def test_requires_complete_matching_positive_odds(self) -> None:
        with self.assertRaises(ValueError):
            blend_probabilities({"a": 1.0}, {"a": None})
        with self.assertRaises(ValueError):
            blend_probabilities({"a": 1.0}, {"b": 2.0})

    def test_integrity_loader_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market-blend.json"
            path.write_text(
                '{"schema_version":"1.0","sha256":"bad","payload":{}}',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_market_blend_forecast(path)
