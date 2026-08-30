import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs

from keiba_prediction_lab.jra_web_adapter import prepare_jra_web_race_day
from keiba_prediction_lab.jra_web_fetch import (
    CARD_ENDPOINT,
    INFO_ENDPOINT,
    JraWebClient,
    fetch_jra_web_race_day,
    parse_result,
    refresh_jra_web_race_day,
)
from keiba_prediction_lab.local_adapter import load_targets_csv
from keiba_prediction_lab.pace_estimation import load_pace_history_csv
from keiba_prediction_lab.race_day_pipeline import load_local_race_day_plan


RACE_DATE = date(2099, 1, 2)
VENUE_CNAME = "pw01drl10052099010120990102/AA"
CARD_CNAME = "pw01dde1005209901010120990102/AB"
RESULT_URL = (
    "https://www.jra.go.jp/JRADB/accessS.html?"
    "CNAME=pw01sde1005209801010120981230/BB"
)


def _encoded(text: str) -> bytes:
    return text.encode("cp932")


INDEX = f"""
<html><body><a onclick="doAction('/JRADB/accessD.html','{VENUE_CNAME}')">東京</a></body></html>
"""
MEETING = f"""
<html><body><a href="/JRADB/accessD.html?CNAME={CARD_CNAME}">1R</a></body></html>
"""
CARD = f"""
<html><body><div id="contents">
発走時刻：<strong>12時00分</strong>
<span>コース：</span>1,600<span>メートル</span><span>（芝・左）</span>
<table><tbody>
<tr><td class="num">1</td><td class="horse"><div class="name"><a>テストホースA</a></div>
<div class="odds"><strong>2.5</strong></div><div class="weight">480kg</div>
<p class="trainer"><a>調教師A</a></p></td>
<td class="jockey"><p class="weight">55.0kg</p><p class="jockey">騎手A</p></td>
<td class="past"><div class="date">2098年12月30日</div><div class="rc">東京</div>
<div class="place">1着</div><div class="dist">1600芝</div><div class="condition">良</div>
<div class="f3">3F 34.0</div><a href="{RESULT_URL}">結果</a></td></tr>
<tr><td class="num">2</td><td class="horse"><div class="name"><a>テストホースB</a></div>
<div class="odds"><strong>4.0</strong></div><div class="weight">470kg</div>
<p class="trainer">調教師B<span class="division">(本会外)</span></p></td>
<td class="jockey"><p class="weight">56.0kg</p><p class="jockey">騎手B</p></td>
<td class="past"><a href="{RESULT_URL}">結果</a></td></tr>
</tbody></table></div></body></html>
"""
INFO = """
<html><body><h2>1月2日 開催情報</h2><table><caption>現在の天候・馬場状態</caption>
<thead><tr><th class="rc">東京</th></tr></thead><tbody><tr class="baba"><td><ul>
<li><span class="cap turf">芝</span><span class="main">良</span></li>
<li><span class="cap dirt">ダート</span><span class="main">稍重</span></li>
</ul></td></tr></tbody></table></body></html>
"""
RESULT = """
<html><body><div id="contents">発走時刻：<strong>12時10分</strong> 天候晴 芝良
<span>コース：</span>1,600<span>メートル</span><span>（芝・左）</span>
<table><tbody>
<tr><td class="place">1</td><td class="num">1</td><td class="horse">テストホースA</td>
<td class="weight">55.0</td><td class="jockey">騎手A</td><td class="corner">1 1</td>
<td class="f_time">34.0</td><td class="h_weight">478(+2)</td><td class="trainer">調教師A</td></tr>
<tr><td class="place">2</td><td class="num">2</td><td class="horse">テストホースB</td>
<td class="weight">56.0</td><td class="jockey">騎手B</td><td class="corner">2 2</td>
<td class="f_time">35.0</td><td class="h_weight">468(-2)</td><td class="trainer">調教師B</td></tr>
</tbody></table></div></body></html>
"""


def _transport(request, _timeout: float) -> bytes:
    cname = None
    if request.data:
        cname = parse_qs(request.data.decode("ascii"))["CNAME"][0]
    if request.full_url == CARD_ENDPOINT and cname == "pw01dli00/F3":
        return _encoded(INDEX)
    if request.full_url == CARD_ENDPOINT and cname == VENUE_CNAME:
        return _encoded(MEETING)
    if request.full_url == f"{CARD_ENDPOINT}?CNAME={CARD_CNAME}":
        return _encoded(CARD)
    if request.full_url == INFO_ENDPOINT and cname == "pw01ide01/4F":
        return _encoded(INFO)
    if request.full_url == RESULT_URL:
        return _encoded(RESULT)
    raise AssertionError((request.full_url, cname))


class JraWebWorkflowTest(unittest.TestCase):
    def test_result_parser_accepts_current_and_legacy_url_prefixes(self) -> None:
        current = RESULT_URL.replace("pw01sde10", "pw01sde01")
        self.assertEqual(parse_result(_encoded(RESULT), RESULT_URL)["race"], 1)
        self.assertEqual(parse_result(_encoded(RESULT), current)["race"], 1)

    def test_requires_explicit_private_use_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "accept-private-use"):
                fetch_jra_web_race_day(
                    RACE_DATE,
                    Path(directory) / "raw",
                    client=JraWebClient(delay_seconds=0, transport=_transport),
                )

    def test_fetches_and_prepares_formal_race_day_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw"
            fetched = fetch_jra_web_race_day(
                RACE_DATE,
                raw,
                max_history_races=1,
                accept_private_use_terms=True,
                client=JraWebClient(delay_seconds=0, transport=_transport),
            )
            prepared_path = root / "prepared"
            prepared = prepare_jra_web_race_day(raw, prepared_path)
            targets = load_targets_csv(
                prepared_path / "targets" / "targets" / "20990102-東京-01.csv"
            )
            pace_history = load_pace_history_csv(prepared_path / "pace-history.csv")
            plan = load_local_race_day_plan(prepared_path / "race-day-plan.json")
            manifest = json.loads(fetched.manifest_path.read_text(encoding="utf-8"))
            scenario_exists = (
                prepared_path / "pace" / "20990102-東京-01" / "pace-scenario.json"
            ).exists()

        self.assertEqual(fetched.race_count, 1)
        self.assertEqual(fetched.history_race_count, 1)
        self.assertEqual(len(manifest["requests"]), 5)
        self.assertTrue(manifest["private_use_only"])
        self.assertEqual(len(targets), 2)
        self.assertEqual(targets[0].body_weight_kg, 480)
        self.assertEqual(targets[1].trainer_id, "trainer:name:調教師b")
        self.assertEqual(len(pace_history), 2)
        self.assertEqual(pace_history[0].first_corner_position, 1)
        self.assertEqual(len(plan.races), 1)
        self.assertEqual(prepared["race_count"], 1)
        self.assertTrue(scenario_exists)

    def test_changed_snapshot_is_rejected_without_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw"
            fetch_jra_web_race_day(
                RACE_DATE,
                raw,
                max_history_races=1,
                accept_private_use_terms=True,
                client=JraWebClient(delay_seconds=0, transport=_transport),
            )
            (raw / "cards.json").write_text("[]\n", encoding="utf-8")
            prepared = root / "prepared"
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                prepare_jra_web_race_day(raw, prepared)
            self.assertFalse(prepared.exists())

    def test_no_corner_history_falls_back_to_average_pace(self) -> None:
        without_corners = RESULT.replace(
            '<td class="corner">1 1</td>', '<td class="corner"></td>'
        ).replace(
            '<td class="corner">2 2</td>', '<td class="corner"></td>'
        )

        def transport(request, timeout: float) -> bytes:
            if request.full_url == RESULT_URL:
                return _encoded(without_corners)
            return _transport(request, timeout)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw"
            fetch_jra_web_race_day(
                RACE_DATE,
                raw,
                max_history_races=1,
                accept_private_use_terms=True,
                client=JraWebClient(delay_seconds=0, transport=transport),
            )
            prepared = root / "prepared"
            result = prepare_jra_web_race_day(raw, prepared)
            scenario = json.loads(
                (prepared / "pace" / "20990102-東京-01" / "pace-scenario.json")
                .read_text(encoding="utf-8")
            )

        self.assertEqual(result["pace_history_row_count"], 0)
        self.assertEqual(scenario["expected_pace"], "average")
        self.assertEqual(scenario["confidence"], 0.0)

    def test_refresh_reuses_history_and_updates_same_day_inputs(self) -> None:
        refreshed_card = CARD.replace("480kg", "488kg").replace(
            "<strong>2.5</strong>", "<strong>3.2</strong>"
        )
        refreshed_info = INFO.replace(
            '<span class="main">良</span>',
            '<span class="main">重</span>',
            1,
        )

        def refresh_transport(request, timeout: float) -> bytes:
            cname = None
            if request.data:
                cname = parse_qs(request.data.decode("ascii"))["CNAME"][0]
            if request.full_url == f"{CARD_ENDPOINT}?CNAME={CARD_CNAME}":
                return _encoded(refreshed_card)
            if request.full_url == INFO_ENDPOINT and cname == "pw01ide01/4F":
                return _encoded(refreshed_info)
            return _transport(request, timeout)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw"
            fetch_jra_web_race_day(
                RACE_DATE,
                raw,
                max_history_races=1,
                accept_private_use_terms=True,
                client=JraWebClient(delay_seconds=0, transport=_transport),
            )
            original_history = (raw / "history.json").read_bytes()
            refreshed = root / "refreshed"
            result = refresh_jra_web_race_day(
                raw,
                refreshed,
                accept_private_use_terms=True,
                client=JraWebClient(delay_seconds=0, transport=refresh_transport),
            )
            refreshed_history = result.history_path.read_bytes()
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            prepared = root / "prepared-refreshed"
            prepare_jra_web_race_day(refreshed, prepared)
            targets = load_targets_csv(
                prepared / "targets" / "targets" / "20990102-東京-01.csv"
            )

        self.assertEqual(result.race_count, 1)
        self.assertEqual(result.history_race_count, 1)
        self.assertEqual(original_history, refreshed_history)
        self.assertTrue(manifest["history_reused_without_network"])
        self.assertEqual(len(manifest["source_snapshot_manifest_sha256"]), 64)
        self.assertEqual(targets[0].body_weight_kg, 488)
        self.assertEqual(targets[0].track_condition, "重")


if __name__ == "__main__":
    unittest.main()
