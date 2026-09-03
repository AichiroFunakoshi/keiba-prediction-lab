"""Private-use, rate-limited acquisition from public JRA race pages.

The downloader is intentionally separate from prediction code.  It records the
exact public URLs and hashes of every response, refuses silent partial output,
and requires an explicit acknowledgement that the resulting snapshot stays on
the user's machine and is not redistributed.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from lxml import html


JST = ZoneInfo("Asia/Tokyo")
SOURCE_ID = "jra-public-web-private-use"
FETCHER_VERSION = "jra-public-web-v1"
REFRESHER_VERSION = "jra-public-web-race-day-refresh-v1"
BASE = "https://www.jra.go.jp"
USER_AGENT = (
    "keiba-prediction-lab/0.1 "
    "(+https://github.com/AichiroFunakoshi/keiba-prediction-lab)"
)
CARD_ENDPOINT = f"{BASE}/JRADB/accessD.html"
INFO_ENDPOINT = f"{BASE}/JRADB/accessI.html"
RESULT_ENDPOINT = f"{BASE}/JRADB/accessS.html"
COURSE_TO_VENUE = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
    "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉",
}


@dataclass(frozen=True)
class HttpRecord:
    url: str
    method: str
    cname: str | None
    acquired_at: datetime
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class JraWebFetchResult:
    output_directory: Path
    manifest_path: Path
    cards_path: Path
    history_path: Path
    track_conditions_path: Path
    acquired_at: datetime
    race_count: int
    history_race_count: int


class JraWebClient:
    """Small auditable HTTP client with conservative request pacing."""

    def __init__(
        self,
        *,
        delay_seconds: float = 1.0,
        timeout_seconds: float = 60.0,
        transport: Callable[[Request, float], bytes] | None = None,
    ) -> None:
        if delay_seconds < 1.0 and transport is None:
            raise ValueError("live JRA requests require at least a 1 second delay")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.delay_seconds = delay_seconds
        self.timeout_seconds = timeout_seconds
        self._transport = transport or self._urlopen
        self._last_request_at: float | None = None
        self.records: list[HttpRecord] = []

    @staticmethod
    def _urlopen(request: Request, timeout: float) -> bytes:
        with urlopen(request, timeout=timeout) as response:
            return response.read()

    def _request(self, url: str, *, cname: str | None = None) -> bytes:
        if self._last_request_at is not None:
            remaining = self.delay_seconds - (time.monotonic() - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)
        data = urlencode({"CNAME": cname}).encode("ascii") if cname else None
        headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "ja-JP,ja;q=0.9",
            "Referer": f"{BASE}/",
        }
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = Request(url, data=data, headers=headers)
        content = self._transport(request, self.timeout_seconds)
        self._last_request_at = time.monotonic()
        self.records.append(HttpRecord(
            url=url,
            method="POST" if data is not None else "GET",
            cname=cname,
            acquired_at=datetime.now(JST),
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        ))
        return content

    def get(self, url: str) -> bytes:
        return self._request(url)

    def post_cname(self, url: str, cname: str) -> bytes:
        return self._request(url, cname=cname)


def _decode(content: bytes) -> str:
    # JRA declares Shift_JIS but some names use Windows-31J extensions.
    return content.decode("cp932", errors="strict")


def _doc(content: bytes):
    return html.fromstring(_decode(content))


def _clean(element) -> str:
    return " ".join(element.text_content().split()) if element is not None else ""


def _first(element, xpath: str):
    matches = element.xpath(xpath)
    return matches[0] if matches else None


def _class_text(element, class_name: str) -> str:
    return _clean(_first(
        element,
        f'.//*[contains(concat(" ",normalize-space(@class)," ")," {class_name} ")]',
    ))


def _cname_values(document) -> set[str]:
    values: set[str] = set()
    for raw in document.xpath("//@href|//@onclick"):
        values.update(re.findall(r"(pw\d+[A-Za-z0-9]+(?:/[A-Z0-9]+)?)", raw))
    return values


def _venue_selections(content: bytes, race_date: date) -> dict[str, str]:
    result: dict[str, str] = {}
    expected = race_date.strftime("%Y%m%d")
    pattern = re.compile(
        rf"pw01drl[01]0(?P<course>\d{{2}}){race_date.year}\d{{4}}{expected}/[A-Z0-9]+"
    )
    for value in _cname_values(_doc(content)):
        match = pattern.fullmatch(value)
        if match and match.group("course") in COURSE_TO_VENUE:
            result[COURSE_TO_VENUE[match.group("course")]] = value
    if not result:
        raise ValueError(f"JRA card index has no meeting for {race_date.isoformat()}")
    return result


def _detail_cards(content: bytes, race_date: date) -> dict[int, str]:
    expected = race_date.strftime("%Y%m%d")
    pattern = re.compile(
        rf"pw01dde(?:01|10)\d{{2}}{race_date.year}\d{{4}}(?P<race>\d{{2}}){expected}/[A-Z0-9]+"
    )
    result: dict[int, str] = {}
    for value in _cname_values(_doc(content)):
        match = pattern.fullmatch(value)
        if match:
            result[int(match.group("race"))] = value
    if not result:
        raise ValueError("JRA meeting page has no detailed race-card links")
    return result


def _surface(text: str) -> str:
    if "障害" in text:
        return "jump"
    if "ダート" in text or "ダ" in text:
        return "dirt"
    if "芝" in text:
        return "turf"
    raise ValueError(f"unsupported JRA surface: {text}")


def _card_identity(cname: str) -> tuple[str, int, str]:
    match = re.fullmatch(
        r"pw01dde(?:01|10)(?P<course>\d{2})20\d{6}(?P<race>\d{2})(?P<date>\d{8})/[A-Z0-9]+",
        cname,
    )
    if match is None or match.group("course") not in COURSE_TO_VENUE:
        raise ValueError(f"unsupported JRA card parameter: {cname}")
    return COURSE_TO_VENUE[match.group("course")], int(match.group("race")), match.group("date")


def parse_card(content: bytes, cname: str) -> dict:
    document = _doc(content)
    venue, race_number, race_date = _card_identity(cname)
    contents = _first(document, '//*[@id="contents"]')
    if contents is None:
        contents = document
    whole = _clean(contents)
    start_match = re.search(r"発走時刻：(\d{1,2})時(\d{2})分", whole)
    distance_match = re.search(r"コース：(\d[\d,]*)メートル（([^）]+)）", whole)
    if start_match is None or distance_match is None:
        raise ValueError(f"JRA card is missing race metadata: {cname}")
    horses: list[dict] = []
    rows = document.xpath(
        '//tr[td[contains(concat(" ",normalize-space(@class)," ")," horse ")] '
        'and td[contains(concat(" ",normalize-space(@class)," ")," num ")]]'
    )
    for row in rows:
        number_cell = _first(row, './td[contains(concat(" ",normalize-space(@class)," ")," num ")]')
        horse_cell = _first(row, './td[contains(concat(" ",normalize-space(@class)," ")," horse ")]')
        jockey_cell = _first(row, './td[contains(concat(" ",normalize-space(@class)," ")," jockey ")]')
        name = _clean(_first(horse_cell, './/div[contains(concat(" ",normalize-space(@class)," ")," name ")]/a'))
        number_match = re.search(r"\d+", _clean(number_cell))
        weight_match = re.search(r"\d+(?:\.\d+)?", _class_text(jockey_cell, "weight"))
        body_match = re.search(r"\d+", _class_text(horse_cell, "weight"))
        if not name or number_match is None or weight_match is None:
            continue
        pasts: list[dict] = []
        for past in row.xpath('./td[contains(concat(" ",normalize-space(@class)," ")," past ")]'):
            result_link = _first(past, './/a[contains(@href,"accessS.html")]')
            if result_link is None:
                continue
            href = result_link.get("href") or ""
            pasts.append({
                "date": _class_text(past, "date"),
                "venue": _class_text(past, "rc"),
                "finish": _class_text(past, "place"),
                "distance_surface": _class_text(past, "dist"),
                "condition": _class_text(past, "condition"),
                "last3f": _class_text(past, "f3"),
                "result_url": href if href.startswith("http") else BASE + href,
            })
        odds_text = _clean(_first(horse_cell, './/div[contains(concat(" ",normalize-space(@class)," ")," odds ")]//strong'))
        trainer_cell = _first(
            horse_cell,
            './/p[contains(concat(" ",normalize-space(@class)," ")," trainer ")]',
        )
        trainer = _clean(_first(trainer_cell, './a'))
        if not trainer:
            trainer = _clean(trainer_cell)
            trainer = re.sub(r"[（(][^）)]*[）)]\s*$", "", trainer).strip()
        horses.append({
            "number": int(number_match.group()),
            "name": name,
            "jockey": _class_text(jockey_cell, "jockey"),
            "trainer": trainer,
            "weight": float(weight_match.group()),
            "body_weight_kg": int(body_match.group()) if body_match else None,
            "odds": float(odds_text) if re.fullmatch(r"\d+(?:\.\d+)?", odds_text) else None,
            "pasts": pasts,
        })
    if len(horses) < 2:
        raise ValueError(f"JRA card has fewer than two runners: {cname}")
    horses.sort(key=lambda item: item["number"])
    return {
        "race_id": f"{race_date}-{venue}-{race_number:02d}",
        "date": race_date,
        "venue": venue,
        "race": race_number,
        "start": f"{start_match.group(1).zfill(2)}:{start_match.group(2)}",
        "distance": int(distance_match.group(1).replace(",", "")),
        "surface": _surface(distance_match.group(2)),
        "horses": horses,
        "source_url": f"{CARD_ENDPOINT}?CNAME={cname}",
    }


def _result_identity(url: str) -> tuple[str, int, str]:
    match = re.search(
        r"pw01sde(?:01|10)(?P<course>\d{2})20\d{6}(?P<race>\d{2})(?P<date>\d{8})/[A-Z0-9]+",
        url,
    )
    if match is None or match.group("course") not in COURSE_TO_VENUE:
        raise ValueError(f"unsupported JRA result URL: {url}")
    return COURSE_TO_VENUE[match.group("course")], int(match.group("race")), match.group("date")


def parse_result(content: bytes, url: str) -> dict:
    document = _doc(content)
    venue, race_number, race_date = _result_identity(url)
    contents = _first(document, '//*[@id="contents"]')
    if contents is None:
        contents = document
    whole = _clean(contents)
    start_match = re.search(r"発走時刻：(\d{1,2})時(\d{2})分", whole)
    distance_match = re.search(r"コース：(\d[\d,]*)メートル（([^）]+)）", whole)
    condition_match = re.search(r"(?:芝|ダート)(良|稍重|重|不良)", whole)
    if start_match is None or distance_match is None:
        raise ValueError(f"JRA result is missing race metadata: {url}")
    pending: list[dict] = []
    for row in document.xpath(
        '//tr[td[contains(concat(" ",normalize-space(@class)," ")," place ")] '
        'and td[contains(concat(" ",normalize-space(@class)," ")," num ")] '
        'and td[contains(concat(" ",normalize-space(@class)," ")," horse ")]]'
    ):
        place_match = re.match(r"(\d+)", _class_text(row, "place"))
        number_match = re.match(r"(\d+)", _class_text(row, "num"))
        carried_match = re.search(r"\d+(?:\.\d+)?", _class_text(row, "weight"))
        if place_match is None or number_match is None or carried_match is None:
            continue
        corner_values = [int(value) for value in re.findall(r"\d+", _class_text(row, "corner"))]
        final_time_match = re.search(r"\d+(?:\.\d+)?", _class_text(row, "f_time"))
        body_match = re.match(r"\d+", _class_text(row, "h_weight"))
        pending.append({
            "finish": int(place_match.group(1)),
            "number": int(number_match.group(1)),
            "name": _class_text(row, "horse"),
            "jockey": _class_text(row, "jockey"),
            "trainer": _class_text(row, "trainer"),
            "carried_weight_kg": float(carried_match.group()),
            "body_weight_kg": int(body_match.group()) if body_match else None,
            "first_corner_position": corner_values[0] if corner_values else None,
            "final_corner_position": corner_values[-1] if corner_values else None,
            "last_3f_seconds": float(final_time_match.group()) if final_time_match else None,
        })
    if len(pending) < 2:
        raise ValueError(f"JRA result has fewer than two finishers: {url}")
    times = [
        item["last_3f_seconds"]
        for item in pending if item["last_3f_seconds"] is not None
    ]
    ranks = {value: 1 + sum(other < value for other in times) for value in set(times)}
    for item in pending:
        item["last_3f_rank"] = ranks.get(item["last_3f_seconds"])
    return {
        "race_id": f"{race_date}-{venue}-{race_number:02d}",
        "date": race_date,
        "venue": venue,
        "race": race_number,
        "start": f"{start_match.group(1).zfill(2)}:{start_match.group(2)}",
        "distance": int(distance_match.group(1).replace(",", "")),
        "surface": _surface(distance_match.group(2)),
        "track_condition": condition_match.group(1) if condition_match else "不明",
        "runners": pending,
        "result_url": url,
    }


def parse_track_conditions(content: bytes, race_date: date) -> dict[str, str]:
    document = _doc(content)
    text = _clean(document)
    date_label = f"{race_date.month}月{race_date.day}日"
    if date_label not in text:
        raise ValueError(f"JRA meeting information is not for {race_date.isoformat()}")
    table = next((table for table in document.xpath('//table') if "現在の天候・馬場状態" in _clean(table)), None)
    if table is None:
        raise ValueError("JRA meeting information has no current track table")
    venues = [_clean(item) for item in table.xpath('.//thead//th[contains(concat(" ",normalize-space(@class)," ")," rc ")]')]
    cells = table.xpath('.//tr[contains(concat(" ",normalize-space(@class)," ")," baba ")]/td')
    if len(venues) != len(cells) or not venues:
        raise ValueError("JRA current track table is malformed")
    result: dict[str, str] = {}
    for venue, cell in zip(venues, cells):
        for item in cell.xpath('.//li'):
            cap = _class_text(item, "cap")
            condition = _class_text(item, "main")
            if "芝" in cap:
                result[f"{venue}:turf"] = condition
            elif "ダート" in cap:
                result[f"{venue}:dirt"] = condition
        result[f"{venue}:jump"] = result.get(f"{venue}:turf", "不明")
    return result


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def fetch_jra_web_race_day(
    race_date: date,
    output_directory: str | Path,
    *,
    max_history_races: int = 360,
    delay_seconds: float = 1.0,
    accept_private_use_terms: bool = False,
    client: JraWebClient | None = None,
) -> JraWebFetchResult:
    """Acquire a result-free race card plus bounded past results."""
    if not accept_private_use_terms:
        raise ValueError("--accept-private-use-terms is required for JRA public-web acquisition")
    if not 1 <= max_history_races <= 500:
        raise ValueError("max_history_races must be from 1 to 500")
    destination = Path(output_directory)
    if destination.exists():
        raise FileExistsError(f"output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    active_client = client or JraWebClient(delay_seconds=delay_seconds)
    try:
        index = active_client.post_cname(CARD_ENDPOINT, "pw01dli00/F3")
        selections = _venue_selections(index, race_date)
        card_params: list[str] = []
        for venue in sorted(selections):
            meeting = active_client.post_cname(CARD_ENDPOINT, selections[venue])
            card_params.extend(_detail_cards(meeting, race_date).values())
        cards = []
        for cname in sorted(set(card_params)):
            cards.append(parse_card(active_client.get(f"{CARD_ENDPOINT}?CNAME={cname}"), cname))
        cards.sort(key=lambda item: (item["venue"], item["race"]))

        info = active_client.post_cname(INFO_ENDPOINT, "pw01ide01/4F")
        conditions = parse_track_conditions(info, race_date)

        result_urls = {
            past["result_url"]
            for card in cards for horse in card["horses"] for past in horse["pasts"]
            if past.get("result_url")
        }
        def history_key(url: str) -> tuple[str, str]:
            _, _, race_day = _result_identity(url)
            return race_day, url
        selected_urls = sorted(result_urls, key=history_key, reverse=True)[:max_history_races]
        history = [parse_result(active_client.get(url), url) for url in selected_urls]
        history.sort(key=lambda item: (item["date"], item["start"], item["venue"], item["race"]))

        acquired_at = datetime.now(JST)
        outputs = {
            "cards.json": _json_bytes(cards),
            "history.json": _json_bytes(history),
            "track-conditions.json": _json_bytes(conditions),
        }
        for name, content in outputs.items():
            (work / name).write_bytes(content)
        manifest = {
            "schema_version": "1.0",
            "fetcher_version": FETCHER_VERSION,
            "source_id": SOURCE_ID,
            "race_date": race_date.isoformat(),
            "acquired_at": acquired_at.isoformat(),
            "private_use_only": True,
            "redistribution_allowed": False,
            "request_delay_seconds": active_client.delay_seconds,
            "race_count": len(cards),
            "runner_count": sum(len(card["horses"]) for card in cards),
            "history_race_count": len(history),
            "history_runner_count": sum(len(race["runners"]) for race in history),
            "requests": [
                {
                    "url": item.url, "method": item.method, "cname": item.cname,
                    "acquired_at": item.acquired_at.isoformat(), "sha256": item.sha256,
                    "size_bytes": item.size_bytes,
                }
                for item in active_client.records
            ],
            "outputs": {
                name: {"sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}
                for name, content in sorted(outputs.items())
            },
        }
        (work / "acquisition-manifest.json").write_bytes(_json_bytes(manifest))
        work.rename(destination)
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return JraWebFetchResult(
        destination,
        destination / "acquisition-manifest.json",
        destination / "cards.json",
        destination / "history.json",
        destination / "track-conditions.json",
        acquired_at,
        len(cards),
        len(history),
    )


def _validated_snapshot_bytes(
    directory: Path,
) -> tuple[dict, date, dict[str, bytes]]:
    """Load an unchanged private-use snapshot for a result-free refresh."""
    manifest_content = (directory / "acquisition-manifest.json").read_bytes()
    manifest = json.loads(manifest_content.decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("source_id") != SOURCE_ID:
        raise ValueError("not a supported JRA public-web snapshot")
    if manifest.get("private_use_only") is not True:
        raise ValueError("JRA public-web snapshot is missing private-use restriction")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError("JRA public-web manifest has no outputs")
    contents: dict[str, bytes] = {}
    for name in ("cards.json", "history.json", "track-conditions.json"):
        metadata = outputs.get(name)
        if not isinstance(metadata, dict) or not isinstance(metadata.get("sha256"), str):
            raise ValueError(f"JRA public-web manifest is missing {name}")
        content = (directory / name).read_bytes()
        if hashlib.sha256(content).hexdigest() != metadata["sha256"]:
            raise ValueError(f"JRA public-web snapshot hash mismatch: {name}")
        contents[name] = content
    try:
        race_date = date.fromisoformat(manifest["race_date"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("JRA public-web manifest race_date is invalid") from error
    manifest["_manifest_sha256"] = hashlib.sha256(manifest_content).hexdigest()
    return manifest, race_date, contents


def refresh_jra_web_race_day(
    snapshot_directory: str | Path,
    output_directory: str | Path,
    *,
    delay_seconds: float = 1.0,
    accept_private_use_terms: bool = False,
    client: JraWebClient | None = None,
) -> JraWebFetchResult:
    """Refetch only same-day cards and conditions while preserving history bytes.

    This is intended for a second acquisition shortly before the first race so
    body weights, scratches, odds, and track conditions are not inherited from
    an overnight snapshot.  The historical corpus is copied byte-for-byte and
    linked to its original manifest hash.
    """
    if not accept_private_use_terms:
        raise ValueError("--accept-private-use-terms is required for JRA public-web refresh")
    source = Path(snapshot_directory)
    source_manifest, race_date, source_contents = _validated_snapshot_bytes(source)
    destination = Path(output_directory)
    if destination.exists():
        raise FileExistsError(f"output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    active_client = client or JraWebClient(delay_seconds=delay_seconds)
    try:
        index = active_client.post_cname(CARD_ENDPOINT, "pw01dli00/F3")
        selections = _venue_selections(index, race_date)
        card_params: list[str] = []
        for venue in sorted(selections):
            meeting = active_client.post_cname(CARD_ENDPOINT, selections[venue])
            card_params.extend(_detail_cards(meeting, race_date).values())
        cards = [
            parse_card(active_client.get(f"{CARD_ENDPOINT}?CNAME={cname}"), cname)
            for cname in sorted(set(card_params))
        ]
        cards.sort(key=lambda item: (item["venue"], item["race"]))
        previous_cards = json.loads(source_contents["cards.json"].decode("utf-8"))
        previous_races = {item["race_id"] for item in previous_cards}
        refreshed_races = {item["race_id"] for item in cards}
        if previous_races != refreshed_races:
            raise ValueError(
                "refreshed JRA cards do not match the source race set: "
                f"missing={sorted(previous_races - refreshed_races)}, "
                f"unexpected={sorted(refreshed_races - previous_races)}"
            )

        info = active_client.post_cname(INFO_ENDPOINT, "pw01ide01/4F")
        conditions = parse_track_conditions(info, race_date)
        acquired_at = datetime.now(JST)
        outputs = {
            "cards.json": _json_bytes(cards),
            "history.json": source_contents["history.json"],
            "track-conditions.json": _json_bytes(conditions),
        }
        for name, content in outputs.items():
            (work / name).write_bytes(content)
        manifest = {
            "schema_version": "1.0",
            "fetcher_version": REFRESHER_VERSION,
            "source_id": SOURCE_ID,
            "race_date": race_date.isoformat(),
            "acquired_at": acquired_at.isoformat(),
            "private_use_only": True,
            "redistribution_allowed": False,
            "request_delay_seconds": active_client.delay_seconds,
            "race_count": len(cards),
            "runner_count": sum(len(card["horses"]) for card in cards),
            "history_race_count": source_manifest["history_race_count"],
            "history_runner_count": source_manifest["history_runner_count"],
            "source_snapshot_manifest_sha256": source_manifest["_manifest_sha256"],
            "history_reused_without_network": True,
            "requests": [
                {
                    "url": item.url, "method": item.method, "cname": item.cname,
                    "acquired_at": item.acquired_at.isoformat(), "sha256": item.sha256,
                    "size_bytes": item.size_bytes,
                }
                for item in active_client.records
            ],
            "outputs": {
                name: {"sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}
                for name, content in sorted(outputs.items())
            },
        }
        (work / "acquisition-manifest.json").write_bytes(_json_bytes(manifest))
        work.rename(destination)
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return JraWebFetchResult(
        destination,
        destination / "acquisition-manifest.json",
        destination / "cards.json",
        destination / "history.json",
        destination / "track-conditions.json",
        acquired_at,
        len(cards),
        int(source_manifest["history_race_count"]),
    )
