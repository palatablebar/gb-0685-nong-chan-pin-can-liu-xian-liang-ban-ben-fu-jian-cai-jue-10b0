from collections import defaultdict

from app.modules.m6_reconcile.adjudicate import build, finalize, _batch_code

RANK = {'PASS': 0, 'UNCERTAIN': 1, 'FAIL': 2}


def _report_account(state):
    accepted = len(state['rows'])
    isolated = len(state['quarantine'])
    accounted = (
        accepted
        + isolated
        + state['blank_count']
        + state['header_count']
        + state['duplicate_count']
    )
    return {
        'input': state['input_count'],
        'accepted': accepted,
        'isolated': isolated,
        'blank': state['blank_count'],
        'headers': state['header_count'],
        'duplicates': state['duplicate_count'],
        'accounted': accounted,
        'balanced': accounted == state['input_count'],
    }


def _project_key(row):
    return (row.get('sample_id'), row.get('batch', ''), row.get('product', ''), row.get('analyte'))


def _evidence_account(state, projects):
    """Business evidence is counted independently of the raw report account.

    * one adopted project  -> one calculation record;
    * every calculation record -> exactly one adjudicated project;
    * every quarantined line -> one isolation record;
    * superseded revisions -> retained adoption evidence, not judged.
    """
    calculations = state.get('calculations', [])
    superseded = state.get('superseded', [])
    quarantine = state['quarantine']
    business_total = state['business']['total'] if state.get('business') else len(calculations)

    project_keys = {_project_key(p) for p in projects}
    covered = {_project_key(c) for c in calculations}
    uncovered = [f"{p['sample_id']}/{p['analyte']}" for p in projects if _project_key(p) not in covered]
    orphan = [c['id'] for c in calculations if _project_key(c) not in project_keys]
    empty = [f"{p['sample_id']}/{p['analyte']}" for p in projects if len(p['stations']) == 0 and not p['impaired']]
    project_reports = sum(len(p['stations']) for p in projects)
    impaired_reports = sum(len(p['impaired']) for p in projects)
    return {
        'business_total': business_total,
        'calculations': len(calculations),
        'projects': len(projects),
        'projects_with_reports': len(projects) - len(empty),
        'station_reports': project_reports,
        'station_reports_total': project_reports + impaired_reports,
        'impaired': impaired_reports,
        'superseded': len(superseded),
        'isolated': len(quarantine),
        'counted': len(calculations) + len(superseded) + len(quarantine),
        'orphan_calculations': orphan,
        'uncovered_projects': uncovered,
        'empty_projects': empty,
        'projects_match': business_total == len(calculations) == project_reports,
        'reconciled': business_total == len(calculations) == project_reports
        and not orphan
        and not uncovered
        and not empty,
    }


def balance(state):
    report = _report_account(state)
    adjudication = build(state)
    projects = adjudication['projects']

    by_batch = defaultdict(list)
    for p in projects:
        by_batch[p['batch']].append(p)
    grades = [
        {
            'batch': batch,
            'code': _batch_code(rows),
            'projects': len(rows),
            'samples': len({p['sample_id'] for p in rows}),
            'calculations': sum(len(p['stations']) for p in rows),
            'impaired': sum(len(p['impaired']) for p in rows),
        }
        for batch, rows in sorted(by_batch.items())
    ]
    for batch in adjudication['batches']:
        grade = next(g for g in grades if g['batch'] == batch['batch'])
        batch['judged_projects'] = grade['projects']
        batch['judged_samples'] = grade['samples']
        batch['judged_code'] = grade['code']
        batch['station_reports'] = grade['calculations'] + grade['impaired']
        batch['projects_reconciled'] = (
            batch['projects'] == grade['projects'] and batch['reports'] == batch['station_reports']
        )

    evidence = _evidence_account(state, projects)
    grades_reconciled = bool(adjudication['batches']) and all(
        b['projects_reconciled'] for b in adjudication['batches']
    ) and sum(b['projects'] for b in adjudication['batches']) == len(projects)
    evidence['grade_projects'] = sum(b['projects'] for b in adjudication['batches'])
    evidence['grade_station_reports'] = sum(b['station_reports'] for b in adjudication['batches'])
    evidence['grades_reconciled'] = grades_reconciled
    evidence['reconciled'] = (
        evidence['reconciled']
        and grades_reconciled
        and evidence['grade_projects'] == evidence['projects']
        and evidence['grade_station_reports'] == evidence['station_reports_total']
    )
    # Every raw report data row ends up in exactly one of: adopted calculation,
    # superseded evidence, quarantine (incl. width/missing/conflict/reference),
    # or a fully-duplicate row collapsed by M2.
    evidence['raw_reports'] = len(state.get('raw_reports', []))
    evidence['raw_counted'] = evidence['counted'] + state.get('duplicate_count', 0)
    evidence['raw_balanced'] = evidence['raw_reports'] == evidence['raw_counted']
    evidence['archive_covered'] = evidence['raw_balanced'] and evidence['reconciled']

    result = dict(report)
    result.update(
        business=state['business'],
        grades=grades,
        evidence=evidence,
        grades_reconciled=grades_reconciled,
        adjudication={
            'projects': projects,
            'batches': adjudication['batches'],
            'summary': adjudication['summary'],
        },
    )
    return result
