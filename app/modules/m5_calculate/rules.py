from collections import defaultdict
from decimal import Decimal
from app.errors import DomainError

def _station(report):
    return report.get('station') or ''

def _pick(reports):
    """Collapse revisions per (station, report id), then per station group."""
    versions={};dropped=[]
    for r in reports:
        key=(_station(r),r['id'])
        if key not in versions:versions[key]=r
        elif Decimal(str(r['version']))>Decimal(str(versions[key]['version'])):dropped.append(versions[key]);versions[key]=r
        else:dropped.append(r)
    reports=list(versions.values());lookup={(_station(r),r['id']):r for r in reports};bad={};valid=[]
    # A relation is a replacement chain; detect cycles before following any edge.
    for r in reports:
        chain=[];current=r;marker=(_station(r),r['id'])
        while current.get('relation'):
            here=(_station(current),current['id'])
            if here in chain:
                for rid in chain:bad[rid]='REFERENCE'
                break
            chain.append(here);parent=lookup.get((_station(current),current['relation']))
            if parent is None:bad[marker]='REFERENCE';break
            shared=('sample_id','batch','product','analyte','method','sampled')
            if any(current[k]!=parent[k] for k in shared) or current['reported']<=parent['reported']:
                bad[marker]='REFERENCE';break
            current=parent
        if (_station(r),r['id']) in bad:bad[marker]='REFERENCE'
    grouped=defaultdict(list)
    for r in reports:
        if (_station(r),r['id']) not in bad:grouped[(r['sample_id'],r['batch'],r['product'],r['analyte'],_station(r))].append(r)
    superseded=[]
    for key,rows in grouped.items():
        roots=[r for r in rows if not r['relation']]
        if len(roots)!=1:
            for r in rows:bad[(_station(r),r['id'])]='CONFLICT'
            continue
        # Only reports connected to the single primary sample result may supersede it.
        candidates=[r for r in rows if (_station(r),r['id']) not in bad]
        rank=max((r['reported'],Decimal(str(r['version']))) for r in candidates)
        newest=[r for r in candidates if (r['reported'],Decimal(str(r['version'])))==rank]
        if len({(r['lower'],r['upper']) for r in newest})>1:
            for r in rows:bad[(_station(r),r['id'])]='CONFLICT'
            continue
        chosen=sorted(newest,key=lambda x:x['id'])[0];valid.append(chosen)
        superseded.extend(r for r in candidates if (_station(r),r['id'])!=chosen_marker(chosen))
    # Older revisions collapsed before the chain walk are retained as adoption
    # evidence (they stay in rows, like relation-superseded reports) so no
    # original report line is lost from the account.  Revisions of a report id
    # whose newest version was quarantined are covered by that quarantine.
    chosen_markers={chosen_marker(r) for r in valid}
    for r in dropped:
        if chosen_marker(r) in chosen_markers:superseded.append(r)
    return valid,bad,superseded


def chosen_marker(report):
    return (_station(report),report['id'])

def _flat(bad):
    """Bare-id view kept for legacy single-station callers and tests."""
    return {marker[1]:code for marker,code in bad.items()}

def select_reports(reports):
    valid,bad,_=_pick(reports);return valid,_flat(bad)

def select_reports_strict(reports):
    valid,bad,superseded=_pick(reports);return valid,bad,superseded

def apply(state):
    chosen,index,superseded=_pick(state['measurements']);lines=set()
    for r in state['measurements']:
        marker=(_station(r),r['id'])
        if marker in index:
            lines.add(r['_line']);state['quarantine'].append({'line':r['_line'],'id':r['id'],'code':index[marker],'stage':'m5_calculate','raw':r['_raw']})
    state['rows']=[r for r in state['rows'] if r['_line'] not in lines]
    calculations=[{'id':r['id'],'line':r['_line'],'sample_id':r['sample_id'],'batch':r['batch'],'product':r['product'],'analyte':r['analyte'],'method':r['method'],'station':r.get('station',''),'station_explicit':bool(r.get('station_explicit')),'standard':r['standard_id'],'lower':r['lower'],'upper':r['upper'],'upper_exclusive':r['upper_exclusive'],'limit':r['limit'],'code':r['code'],'sampled':r['sampled'],'reported':r['reported'],'replaces':r['relation']} for r in chosen]
    chosen_ids={(c['sample_id'],c['batch'],c['product'],c['analyte'],_station(c)):c['id'] for c in chosen}
    state['calculations']=calculations
    state['superseded']=[{'id':r['id'],'line':r['_line'],'sample_id':r['sample_id'],'batch':r['batch'],'product':r['product'],'analyte':r['analyte'],'station':_station(r),'version':str(r['version']),'lower':r['lower'],'upper':r['upper'],'limit':r['limit'],'code':r['code'],'reported':r['reported'],'replaces':r.get('relation',''),'superseded_by':chosen_ids.get((r['sample_id'],r['batch'],r['product'],r['analyte'],_station(r)),'')} for r in superseded]
    state['business']={'total':len(calculations),'pass':sum(r['code']=='PASS' for r in calculations),'fail':sum(r['code']=='FAIL' for r in calculations),'uncertain':sum(r['code']=='UNCERTAIN' for r in calculations),'unit':'检测项目'};return state
