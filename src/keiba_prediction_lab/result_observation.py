"""Read result-time weather and six official payouts from saved JRA HTML.

These are retrospective observations, never a source of pre-race weather.
"""
import re
from datetime import datetime
from .jra_web_fetch import _doc, _clean, _class_text, parse_result
from .bet_type_settlement import BetTypePayout, BetTypeRacePayouts
from .domain import BetType
from .snapshot_adapter import _normalized_name


def parse_result_observation(content: bytes, url: str, acquired_at: datetime) -> dict:
    if acquired_at.tzinfo is None or acquired_at.utcoffset() is None:
        raise ValueError("acquired_at must be timezone-aware")
    result = parse_result(content, url)
    day = result['date']
    scheduled = datetime.fromisoformat(f'{day[:4]}-{day[4:6]}-{day[6:]}T{result["start"]}:00+09:00')
    if acquired_at <= scheduled:
        raise ValueError("result acquisition must be after the race")
    text = _clean(_doc(content))
    match = re.search(r'天候\s*[:：]?\s*(小雨|小雪|晴|曇|雨|雪)', text)
    if match is None:
        raise ValueError("official result weather is missing")
    conditions = dict(re.findall(r'(芝|ダート)\s*[:：]?\s*(不良|稍重|重|良)', text))
    return {**result, 'weather': match.group(1), 'surface_conditions': conditions,
            'acquired_at': acquired_at.isoformat(), 'usage': 'post_event_diagnostic_only'}


def parse_result_payouts(content: bytes, url: str) -> BetTypeRacePayouts:
    result = parse_result(content, url)
    horses = {r['number']: _normalized_name('horse', r['name']) for r in result['runners']}
    doc = _doc(content)
    rows = []
    types = {'win': BetType.WIN, 'place': BetType.PLACE, 'umaren': BetType.QUINELLA,
             'umatan': BetType.EXACTA, 'trio': BetType.TRIO, 'tierce': BetType.TRIFECTA}
    for css, kind in types.items():
        for line in doc.xpath(f'//div[contains(concat(" ", normalize-space(@class), " "), " refund_area ")]//li[@class="{css}"]//div[@class="line"]'):
            numbers = tuple(int(n) for n in re.findall(r'\d+', _class_text(line, 'num')))
            amount = _class_text(line, 'yen').replace(',', '').replace('円', '').strip()
            if not amount.isdigit() or any(n not in horses for n in numbers):
                raise ValueError('unsupported or incomplete official payout')
            selection = tuple(horses[n] for n in numbers)
            if kind in (BetType.QUINELLA, BetType.TRIO):
                selection = tuple(sorted(selection))
            rows.append(BetTypePayout(result['race_id'], kind, selection, int(amount)))
    return BetTypeRacePayouts(result['race_id'], tuple(rows))
