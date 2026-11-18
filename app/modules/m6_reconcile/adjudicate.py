"""Cross-station adjudication.

A sample may be split between two testing stations.  Each station keeps its own
primary-report chain (M5 already guarantees one root per station).  This module
compares the *station-level conclusions* and decides, per sample/analyte
project and per batch, whether the evidence may be adopted, must be held, or
still needs to be completed.

Nothing here mutates or discards raw reports: every station report stays in
``state['rows']`` / ``state['quarantine']`` for the archive.
"""
import hashlib
import json
from collections import defaultdict

from app.config import ARBITER_STATION, DEFAULT_STATION

RANK = {'PASS': 0, 'UNCERTAIN': 1, 'FAIL': 2}
VERDICT_LABEL = {
    'ADOPT': '可采信',
    'ARBITER': '可采信（仲裁）',
    'HOLD': '暂缓',
    'SUPPLEMENT': '仍需补齐',
}
# A batch inherits the strictest project verdict.
VERDICT_PRIORITY = {'ADOPT': 0, 'ARBITER': 0, 'SUPPLEMENT': 1, 'HOLD': 2}


def _worst(codes):
    known = [c for c in codes if c in RANK]
    return max(known, key=lambda c: RANK[c]) if known else 'UNCERTAIN'


def _station(record):
    return record.get('station') or DEFAULT_STATION


def _batch_code(projects):
    """Batch risk grades only from definitive codes; pending work ranks highest."""
    codes = [p['code'] for p in projects]
    known = [c for c in codes if c in RANK]
    return max(known, key=lambda c: RANK[c]) if known else 'UNCERTAIN'


def _conclusion_id(batch, projects):
    payload = {
        'batch': batch,
        'projects': sorted(
            (p['sample_id'], p['analyte'], p['code'], p['verdict']) for p in projects
        ),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _station_entries(records):
    latest = {}
    for record in records:
        name = _station(record)
        key = (
            str(record.get('reported', '')),
            str(record.get('version', '1')),
            str(record.get('id', '')),
        )
        if name not in latest or key > latest[name][0]:
            latest[name] = (key, record)
    return [
        {
            'station': name,
            'id': record.get('id'),
            'version': str(record.get('version', '1')),
            'code': record.get('code'),
            'lower': record.get('lower'),
            'upper': record.get('upper'),
            'limit': record.get('limit'),
            'sampled': record.get('sampled'),
            'reported': record.get('reported'),
        }
        for name, (_, record) in sorted(latest.items())
    ]


def _decide(stations, impaired):
    codes = {s['code'] for s in stations}
    if not stations:
        return 'SUPPLEMENT', 'UNCERTAIN'
    if impaired:
        # An unusable sibling report is an unresolved objection: do not assert
        # a definitive code until the missing basis is supplied.
        return 'SUPPLEMENT', 'UNCERTAIN'
    if len(codes) == 1:
        return 'ADOPT', stations[0]['code']
    arbiter = [s for s in stations if s['station'] == ARBITER_STATION]
    if arbiter:
        return 'ARBITER', arbiter[0]['code']
    return 'HOLD', _worst(codes)


def _basis(verdict, stations, impaired):
    if verdict == 'ADOPT':
        if len(stations) <= 1:
            return '单一检测站结论，未见异议报告'
        return '各检测站结论一致：' + '、'.join(
            f"{s['station']}={s['code']}" for s in stations
        )
    if verdict == 'ARBITER':
        arbiter = next(s for s in stations if s['station'] == ARBITER_STATION)
        others = '、'.join(
            f"{s['station']}={s['code']}" for s in stations if s['station'] != ARBITER_STATION
        )
        return f"两站结论冲突（{others}），仲裁站报告 {arbiter['id']} 判定 {arbiter['code']}"
    if verdict == 'HOLD':
        return '检测站结论冲突且无仲裁报告：' + '、'.join(
            f"{s['station']}({s['id']})={s['code']}" for s in stations
        )
    detail = '、'.join(
        f"{i['station']}({i['id']})={i['code']}" for i in impaired
    )
    if not stations:
        return f'报告依据不完整，暂无可采信结论：{detail}'
    return f'存在依据不完整的异议报告，无法完成比对：{detail}'


def build(state):
    """Return the adjudication block (projects, batches, summary).

    The certificate verdict is added later by :func:`finalize` once the batch
    reconciliation has been computed.
    """
    calculations = state.get('calculations', [])
    multi_station = any(c.get('station_explicit') for c in calculations)

    grouped = defaultdict(list)
    for calc in calculations:
        key = (
            calc.get('sample_id'),
            calc.get('batch', ''),
            calc.get('product', ''),
            calc.get('analyte'),
        )
        grouped[key].append(calc)

    # Only a cross-station run treats a quarantined sibling report as an
    # incomplete basis.  Legacy single-station samples keep their old meaning.
    impaired = defaultdict(list)
    if multi_station:
        for item in state.get('quarantine', []):
            raw = item.get('raw')
            if not isinstance(raw, dict) or 'station' not in raw:
                continue
            sample_id, analyte = raw.get('sample_id'), raw.get('analyte')
            if not sample_id or not analyte:
                continue
            impaired[(sample_id, raw.get('batch', ''), raw.get('product', ''), analyte)].append(
                {
                    'line': item.get('line'),
                    'id': item.get('id') or raw.get('id', ''),
                    'station': raw.get('station') or DEFAULT_STATION,
                    'code': item.get('code'),
                }
            )

    projects = []
    for key in sorted(set(grouped) | set(impaired), key=lambda k: tuple(str(x) for x in k)):
        sample_id, batch, product, analyte = key
        stations = _station_entries(grouped.get(key, []))
        missing = impaired.get(key, [])
        verdict, code = _decide(stations, missing)
        projects.append(
            {
                'sample_id': sample_id,
                'batch': batch,
                'product': product,
                'analyte': analyte,
                'code': code,
                'verdict': verdict,
                'verdict_label': VERDICT_LABEL[verdict],
                'provisional': verdict in ('HOLD', 'SUPPLEMENT'),
                'basis': _basis(verdict, stations, missing),
                'stations': stations,
                'impaired': missing,
            }
        )

    batches = []
    for batch in sorted({p['batch'] for p in projects}):
        rows = [p for p in projects if p['batch'] == batch]
        verdict = max((p['verdict'] for p in rows), key=lambda v: VERDICT_PRIORITY[v])
        batches.append(
            {
                'batch': batch,
                'code': _batch_code(rows),
                'verdict': verdict,
                'verdict_label': VERDICT_LABEL[verdict],
                'provisional': verdict in ('HOLD', 'SUPPLEMENT'),
                'projects': len(rows),
                'samples': len({p['sample_id'] for p in rows}),
                'reports': sum(len(p['stations']) + len(p['impaired']) for p in rows),
                'basis': [p['basis'] for p in rows if p['provisional']],
                'conclusion_id': _conclusion_id(batch, rows),
            }
        )

    summary = {
        'projects': len(projects),
        'adopt': sum(p['verdict'] in ('ADOPT', 'ARBITER') for p in projects),
        'arbiter': sum(p['verdict'] == 'ARBITER' for p in projects),
        'hold': sum(p['verdict'] == 'HOLD' for p in projects),
        'supplement': sum(p['verdict'] == 'SUPPLEMENT' for p in projects),
        'batches': len(batches),
        'multi_station': multi_station,
    }
    return {'projects': projects, 'batches': batches, 'summary': summary}


def finalize(state):
    """Attach the certificate verdict, driven by project conclusions."""
    adjudication = state['adjudication']
    reconcile = state.get('reconcile', {})
    evidence = reconcile.get('evidence', {})
    structural = (
        bool(reconcile.get('balanced'))
        and bool(evidence.get('reconciled'))
        and bool(reconcile.get('grades_reconciled'))
    )
    batches = adjudication['batches']
    hold = [b['batch'] for b in batches if b['verdict'] == 'HOLD']
    supplement = [b['batch'] for b in batches if b['verdict'] == 'SUPPLEMENT']
    adopt = [b['batch'] for b in batches if b['verdict'] in ('ADOPT', 'ARBITER')]

    if hold:
        verdict = '暂缓出证'
    elif supplement:
        verdict = '补齐后出证'
    elif structural:
        verdict = '可出证'
    else:
        verdict = '待对账'

    reasons = []
    for batch in batches:
        if batch['verdict'] in ('HOLD', 'SUPPLEMENT'):
            reasons.append(f"{batch['batch']} {batch['verdict_label']}：{'；'.join(batch['basis'])}")
    if not structural:
        reasons.append('报告账或证据对账未平，不能归档出证')

    allowed = bool(structural and not hold and not supplement)
    adjudication['certificate'] = {
        'allowed': allowed,
        'verdict': verdict,
        'reasons': reasons,
        'adopt_batches': adopt,
        'hold_batches': hold,
        'supplement_batches': supplement,
        'structural': structural,
    }
    return adjudication
