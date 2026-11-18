from copy import deepcopy
from app.config import REQUIRED,NUMERIC,DATES,TIMES,DEFAULT_STATION
from app.errors import DomainError
from app.modules.m2_mapping.coerce import number,day,instant,SENTINELS

def normalize(state):
    state=deepcopy(state);good=[];groups={}
    for row in state['rows']:
        try:
            for field in REQUIRED:
                if str(row.get(field,'')).strip().upper() in SENTINELS:raise DomainError('MISSING','必填值缺失')
            item=dict(row)
            for field in NUMERIC:
                if field in item and item[field] != '':item[field]=number(item[field])
            for field in DATES:
                if field in item and item[field] != '':item[field]=day(item[field])
            for field in TIMES:
                if field in item and item[field] != '':item[field]=instant(item[field])
            # Station is optional: absent means the legacy single-station source.
            # Station-scoped report ids: two stations may independently number
            # their own reports 00001, so the dedup key includes the station
            # whenever one is declared.
            station=str(item.get('station','')).strip()
            item['station_explicit']=bool(station)
            item['station']=station or DEFAULT_STATION
            key=(item['station'] if item['station_explicit'] else '',item['id'],str(item.get('version','1')))
            groups.setdefault(key,[]).append(item)
        except DomainError as error:state['quarantine'].append({'line':row['_line'],'id':row.get('id',''),'code':error.code,'stage':'m2_mapping','raw':row['_raw']})
    for key,rows in groups.items():
        unique={str(sorted((k,str(v)) for k,v in r.items() if not k.startswith('_'))) for r in rows}
        if len(unique)>1:
            for r in rows:state['quarantine'].append({'line':r['_line'],'id':r['id'],'code':'CONFLICT','stage':'m2_mapping','raw':r['_raw']})
        else:
            good.append(rows[0]);state['duplicate_count']+=len(rows)-1
    state['rows']=good
    return state
