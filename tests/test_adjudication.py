import csv
import io

import pytest

from app import store
from app.errors import DomainError
from app.modules.m6_reconcile.adjudicate import build, finalize
from app.workflow import load, run


@pytest.fixture
def cross():
    return run(load('cross_station.csv'), {})


def _project(state, sample_id, analyte='P01'):
    return next(p for p in state['adjudication']['projects'] if p['sample_id'] == sample_id and p['analyte'] == analyte)


def _batch(state, batch):
    return next(b for b in state['adjudication']['batches'] if b['batch'] == batch)


def test_conflicting_single_sample_held(cross):
    project = _project(cross, 'S101')
    assert project['verdict'] == 'HOLD' and project['code'] == 'FAIL'
    assert {s['station'] for s in project['stations']} == {'A站', 'B站'}
    assert '无仲裁报告' in project['basis']


def test_arbiter_resolves_same_sample(cross):
    project = _project(cross, 'S102')
    assert project['verdict'] == 'ARBITER' and project['code'] == 'PASS'
    assert '01005' in project['basis']


def test_incomplete_basis_needs_supplement(cross):
    project = _project(cross, 'S103')
    assert project['verdict'] == 'SUPPLEMENT'
    assert project['impaired'][0]['station'] == 'B站'


def test_consistent_stations_adopt(cross):
    assert _project(cross, 'S104')['verdict'] == 'ADOPT'
    assert _project(cross, 'S105', 'P02')['verdict'] == 'ADOPT'


def test_batch_inherits_strictest_project(cross):
    assert _batch(cross, 'B101')['verdict'] == 'HOLD'
    assert _batch(cross, 'B102')['verdict'] == 'SUPPLEMENT'
    assert _batch(cross, 'B103')['verdict'] == 'ADOPT'


def test_certificate_blocked_when_any_batch_provisional(cross):
    certificate = cross['adjudication']['certificate']
    assert certificate['allowed'] is False and certificate['verdict'] == '暂缓出证'
    reasons = ' '.join(certificate['reasons'])
    assert 'B101' in reasons and 'B102' in reasons


def test_both_station_reports_survive_in_archive(cross):
    from app.modules.m7_archive.export_out import bundle

    rows = list(csv.reader(io.StringIO(bundle(cross).lstrip('﻿'))))
    raw_ids = [r[3] for r in rows if r[0] == '原始报告']
    assert '01001' in raw_ids and '01002' in raw_ids and '01007' in raw_ids


def test_report_and_evidence_accounts_balance(cross):
    rec = cross['reconcile']
    assert rec['balanced'] and rec['evidence']['raw_balanced']
    assert rec['evidence']['reconciled'] and rec['grades_reconciled']


def test_evidence_count_matches_export_rows(cross):
    from app.modules.m7_archive.export_out import bundle

    rows = list(csv.reader(io.StringIO(bundle(cross).lstrip('﻿'))))
    assert cross['archive']['evidence_count'] == len(rows) - 1


def test_grades_match_project_counts(cross):
    for batch in cross['adjudication']['batches']:
        assert batch['judged_projects'] == batch['projects']
        assert batch['projects_reconciled'] is True


def test_broken_evidence_blocks_certificate(cross):
    from copy import deepcopy

    broken = deepcopy(cross)
    broken['reconcile']['evidence']['reconciled'] = False
    finalize(broken)
    assert broken['adjudication']['certificate']['allowed'] is False
    assert broken['adjudication']['certificate']['structural'] is False


def test_legacy_single_station_still_certifies():
    state = run(load('dirty.csv'), {})
    assert state['adjudication']['summary']['multi_station'] is False
    assert state['adjudication']['certificate']['allowed'] is True


def test_conclusion_is_content_addressed_and_idempotent(cross, tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'r.db'))
    store.save(cross['run_id'], cross)
    first = store.conclusions()
    store.save(cross['run_id'], cross)
    assert len(store.conclusions()) == len(first)


def test_confirmed_conclusion_survives_interrupted_supplement(cross, tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'r.db'))
    store.save(cross['run_id'], cross)
    store.review(cross['run_id'], 1, '通过')
    text = load('cross_station.csv').decode('utf-8-sig').rstrip('\n')
    text += '\n01014,S101,B101,苹果,P01,LC-MS/MS,2,2026-08-01,2026-08-04,0.004,mg/kg,0.001,,仲裁站,仲裁复测合格\n'
    revision = run(text.encode(), {})
    store.save(revision['run_id'], revision)
    assert _batch(revision, 'B101')['verdict'] == 'ARBITER'
    effective = {c['batch']: c for c in store.conclusions() if c['effective']}
    assert effective['B101']['verdict'] == 'HOLD' and effective['B101']['confirmed'] is True
    pending = next(c for c in store.conclusions() if c['batch'] == 'B101' and not c['effective'])
    assert pending['state'] == '待确认' and pending['supersedes'] == effective['B101']['conclusion_id']


def test_rejected_revision_does_not_change_confirmed_conclusion(cross, tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'r.db'))
    store.save(cross['run_id'], cross)
    store.review(cross['run_id'], 1, '通过')
    text = load('cross_station.csv').decode('utf-8-sig').rstrip('\n')
    text += '\n01014,S101,B101,苹果,P01,LC-MS/MS,2,2026-08-01,2026-08-04,0.004,mg/kg,0.001,,仲裁站,仲裁复测合格\n'
    revision = run(text.encode(), {})
    store.save(revision['run_id'], revision)
    store.review(revision['run_id'], 1, '退回')
    effective = {c['batch']: c for c in store.conclusions() if c['effective']}
    assert effective['B101']['verdict'] == 'HOLD'
    assert not any(c['state'] == '待确认' for c in store.conclusions())


def test_supplement_restores_conflict_is_not_adopted():
    base = load('cross_station.csv').decode('utf-8-sig').rstrip('\n')
    fixed = base.replace(
        '01007,S103,B102,苹果,P01,LC-MS/MS,1,2026-08-01,2026-08-02,0.008,mg/kg,N/A,,B站,B站报告缺检出限',
        '01007,S103,B102,苹果,P01,LC-MS/MS,1,2026-08-01,2026-08-02,0.008,mg/kg,0.001,,B站,B站报告补正检出限',
    )
    state = run(fixed.encode(), {})
    assert _project(state, 'S103')['verdict'] == 'HOLD'
    assert state['adjudication']['certificate']['allowed'] is False


def test_all_batches_resolved_allows_certificate():
    base = load('cross_station.csv').decode('utf-8-sig').rstrip('\n')
    fixed = base.replace(
        '01007,S103,B102,苹果,P01,LC-MS/MS,1,2026-08-01,2026-08-02,0.008,mg/kg,N/A,,B站,B站报告缺检出限',
        '01007,S103,B102,苹果,P01,LC-MS/MS,1,2026-08-01,2026-08-02,0.004,mg/kg,0.001,,B站,B站报告补正',
    ).replace(
        '01002,S101,B101,苹果,P01,LC-MS/MS,1,2026-08-01,2026-08-02,0.008,mg/kg,0.001,,B站,B站复测超限',
        '01002,S101,B101,苹果,P01,LC-MS/MS,1,2026-08-01,2026-08-02,0.004,mg/kg,0.001,,B站,B站复测合格',
    )
    state = run(fixed.encode(), {})
    assert state['adjudication']['certificate']['allowed'] is True
    assert state['archive']['ready'] is True


def test_conclusions_api_and_workbench(client):
    submitted = client.post('/api/runs', json={'sample': 'cross_station.csv'}).get_json()
    listed = client.get('/api/conclusions').get_json()
    assert any(c['batch'] == 'B101' for c in listed)
    page = client.get('/m6_reconcile').data.decode()
    assert '本次出证裁决' in page and '暂缓出证' in page
    assert client.get('/api/archive/' + submitted['run_id'] + '/export.csv').status_code == 200


def test_same_id_two_stations_not_collapsed():
    text = (
        'id,sample_id,batch,product,analyte,method,version,sampled,reported,result,unit,lod,relation,station,note\n'
        '00001,S1,B1,苹果,P01,LC-MS/MS,1,2026-08-01,2026-08-02,0.004,mg/kg,0.001,,A站,合格\n'
        '00001,S1,B1,苹果,P01,LC-MS/MS,1,2026-08-01,2026-08-02,0.008,mg/kg,0.001,,B站,超限\n'
    )
    state = run(text.encode(), {})
    assert state['duplicate_count'] == 0 and len(state['calculations']) == 2
    assert {c['station'] for c in state['calculations']} == {'A站', 'B站'}
    assert state['adjudication']['projects'][0]['verdict'] == 'HOLD'


def test_superseded_revision_kept_in_account():
    text = (
        'id,sample_id,batch,product,analyte,method,version,sampled,reported,result,unit,lod,relation,station,note\n'
        '00001,S1,B1,苹果,P01,LC-MS/MS,1,2026-08-01,2026-08-02,0.008,mg/kg,0.001,,A站,首版\n'
        '00001,S1,B1,苹果,P01,LC-MS/MS,2,2026-08-01,2026-08-03,0.004,mg/kg,0.001,,A站,修订版\n'
    )
    state = run(text.encode(), {})
    evidence = state['reconcile']['evidence']
    assert len(state['calculations']) == 1 and len(state['superseded']) == 1
    assert evidence['raw_balanced'] is True and evidence['reconciled'] is True


def test_every_sample_balances_raw_report_account():
    for name in ('dirty.csv', 'clean.csv', 'legacy.csv', 'cross_station.csv'):
        state = run(load(name), {})
        evidence = state['reconcile']['evidence']
        assert evidence['raw_balanced'] is True, name
        assert evidence['reconciled'] is True, name
