"""Latest acquired odds for display only; never changes frozen probabilities."""
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from .jra_web_fetch import SOURCE_ID
from .snapshot_adapter import _normalized_name


def latest_display_odds(directory):
    latest = {}
    for path in Path(directory).rglob('acquisition-manifest.json'):
        try:
            manifest=json.loads(path.read_text())
            if manifest.get('source_id') != SOURCE_ID or manifest.get('private_use_only') is not True:continue
            observed=datetime.fromisoformat(manifest['acquired_at'])
            if observed.tzinfo is None or observed > datetime.now(timezone.utc):continue
            content=(path.parent/'cards.json').read_bytes()
            if hashlib.sha256(content).hexdigest()!=manifest['outputs']['cards.json']['sha256']:continue
            cards=json.loads(content)
            for card in cards:
                runners=card['horses'];data={};valid=True
                for h in runners:
                    horse_id=_normalized_name('horse',h['name']);odds=h.get('odds');pop=h.get('popularity')
                    if horse_id in data or type(h['number']) is not int or h['number']<1:valid=False;break
                    if odds is not None and (type(odds) not in (int,float) or not math.isfinite(odds) or odds<=0):valid=False;break
                    if pop is not None and (type(pop) is not int or not 1<=pop<=len(runners)):valid=False;break
                    data[horse_id]=dict(number=h['number'],odds=odds,popularity=pop,observed_at=observed.isoformat(),popularity_source='jra' if pop else None)
                if not valid or not data:continue
                # Only infer a rank if the entire field has odds; equal displayed odds share rank.
                if all(h['odds'] is not None for h in data.values()):
                    for h in data.values():
                        if h['popularity'] is None:
                            h['popularity']=1+sum(o['odds']<h['odds'] for o in data.values());h['popularity_source']='odds_order'
                rid=card['race_id']
                if rid not in latest or observed>latest[rid][0]:latest[rid]=(observed,data)
        except (OSError,ValueError,TypeError,KeyError,AttributeError):
            continue
    return {rid:data for rid,(_,data) in latest.items()}
