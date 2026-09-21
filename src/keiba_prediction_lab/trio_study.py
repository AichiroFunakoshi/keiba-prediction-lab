"""Read a locally pinned development report for clearly separated UI display."""
import hashlib
import json
import math
from pathlib import Path
from .trio_research import aggregate


def load_trio_study(pointer):
    pointer=Path(pointer)
    if not pointer.is_file():return None
    config=json.loads(pointer.read_bytes())
    if set(config)!={'schema_version','report_path','report_sha256'} or config['schema_version']!='1.0':
        raise ValueError('invalid trio study pointer')
    content=(pointer.parent/config['report_path']).read_bytes()
    if hashlib.sha256(content).hexdigest()!=config['report_sha256']:raise ValueError('trio study report changed')
    report=json.loads(content)
    if report['status']!='development_evaluation_not_untouched':raise ValueError('unsupported study status')
    collected={k:[] for k in ('winner_v5','trio_set')}
    for fold in report['folds']:
        for k in collected:
            rows=fold['rows'][k]
            if len(rows)!=fold['counts']['evaluation'] or len({r['race_id'] for r in rows})!=len(rows):raise ValueError('invalid study race count')
            if any(type(r['hit']) is not int or r['hit'] not in (0,1) or not all(math.isfinite(r[m]) and r[m]>=0 for m in ('log_loss','brier','overlap')) for r in rows):raise ValueError('invalid study scores')
            collected[k].extend(rows)
        if {r['race_id'] for r in fold['rows']['winner_v5']}!={r['race_id'] for r in fold['rows']['trio_set']}:raise ValueError('unpaired study races')
    summary={k:aggregate(v) for k,v in collected.items()}
    if any(len({r['race_id'] for r in v})!=len(v) for v in collected.values()) or summary!=report['summary']:raise ValueError('study aggregate mismatch')
    return dict(status='development_only',summary=summary,
                note='過去の固定期間での開発検証。新方式は未使用開催での確認待ち。現在の予想は変更していません。')
