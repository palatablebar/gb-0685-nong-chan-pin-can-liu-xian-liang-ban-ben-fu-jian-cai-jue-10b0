from app.modules.m7_archive.export_out import checksum,evidence_count

def execute(state):
    reconcile=state.get('reconcile',{})
    evidence=reconcile.get('evidence',{})
    certificate=state.get('adjudication',{}).get('certificate',{})
    ready=bool(reconcile.get('balanced')) and bool(evidence.get('reconciled')) and bool(reconcile.get('grades_reconciled')) and bool(evidence.get('raw_balanced'))
    state['archive']={'sha256':checksum(state),'evidence_count':evidence_count(state),
        'ready':ready,'certificate_allowed':bool(certificate.get('allowed')),
        'certificate_verdict':certificate.get('verdict','—'),
        'raw_reports':len(state.get('raw_reports',[])),'superseded':len(state.get('superseded',[]))}
    return state
