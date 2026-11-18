import io,csv,json,hashlib,base64

EVIDENCE_KINDS=('运行清单','对账','证据对账','项目裁决','批次裁决','出证裁决','计算','替代记录','原始报告','隔离')

def bundle(state):
    out=io.StringIO(newline='');writer=csv.writer(out)
    writer.writerow(['记录类型','批次','来源行','业务ID','原因码','内容'])
    rid=state.get('run_id','preview')
    writer.writerow(['运行清单',rid,'','','',json.dumps(state.get('manifest',{}),ensure_ascii=False,sort_keys=True)])
    writer.writerow(['对账',rid,'','','',json.dumps(state.get('reconcile',{}),ensure_ascii=False,sort_keys=True)])
    evidence=state.get('reconcile',{}).get('evidence',{})
    writer.writerow(['证据对账',rid,'','','',json.dumps(evidence,ensure_ascii=False,sort_keys=True)])
    adjudication=state.get('adjudication') or {}
    certificate=adjudication.get('certificate',{})
    if certificate:
        writer.writerow(['出证裁决','','','','',json.dumps(certificate,ensure_ascii=False,sort_keys=True)])
    for row in adjudication.get('projects',[]):
        writer.writerow(['项目裁决',row.get('batch',''),'',row.get('sample_id','')+'/'+row.get('analyte',''),row.get('verdict',''),json.dumps(row,ensure_ascii=False,sort_keys=True)])
    for row in adjudication.get('batches',[]):
        writer.writerow(['批次裁决',row.get('batch',''),'',row.get('batch',''),row.get('verdict',''),json.dumps(row,ensure_ascii=False,sort_keys=True)])
    for row in state.get('calculations',[]):writer.writerow(['计算',rid,row.get('line',''),row.get('id',''),row.get('code',''),json.dumps(row,ensure_ascii=False,sort_keys=True)])
    for row in state.get('superseded',[]):writer.writerow(['替代记录',row.get('batch',''),row.get('line',''),row.get('id',''),row.get('code',''),json.dumps(row,ensure_ascii=False,sort_keys=True)])
    for row in state.get('raw_reports',[]):
        writer.writerow(['原始报告',row.get('batch',''),row.get('line',''),row.get('id',''),'RAW',json.dumps(row,ensure_ascii=False,sort_keys=True)])
    for row in state['quarantine']:writer.writerow(['隔离',rid,row['line'],row.get('id',''),row['code'],json.dumps(row.get('raw',{}),ensure_ascii=False,sort_keys=True)])
    return '﻿'+out.getvalue()

def checksum(state):
    return hashlib.sha256(bundle(state).encode('utf-8')).hexdigest()

def adjudication_csv(state):
    """Flat, human-readable adjudication result for the workbench download."""
    out=io.StringIO(newline='');writer=csv.writer(out)
    writer.writerow(['记录','批次','样本','项目','检测站','结论','采信状态','依据'])
    adjudication=state.get('adjudication') or {}
    certificate=adjudication.get('certificate',{})
    writer.writerow(['出证裁决','','','','',certificate.get('verdict',''),'可出证' if certificate.get('allowed') else '不得出证','；'.join(certificate.get('reasons',[]))])
    for batch in adjudication.get('batches',[]):
        writer.writerow(['批次判级',batch.get('batch',''),'','','',batch.get('code',''),batch.get('verdict_label',''),'；'.join(batch.get('basis',[]))])
    for project in adjudication.get('projects',[]):
        stations='、'.join(f"{s['station']}={s['code']}" for s in project.get('stations',[]))
        stations+=('；' if stations else '')+'、'.join(f"{i['station']}=隔离/{i['code']}" for i in project.get('impaired',[]))
        writer.writerow(['项目裁决',project.get('batch',''),project.get('sample_id',''),project.get('analyte',''),stations,project.get('code',''),project.get('verdict_label',''),project.get('basis','')])
    return '﻿'+out.getvalue()

def evidence_count(state):
    """Count of evidence rows written to the bundle (excludes the two headers)."""
    text=bundle(state).lstrip('﻿')
    return max(len(list(csv.reader(io.StringIO(text))))-1,0)
