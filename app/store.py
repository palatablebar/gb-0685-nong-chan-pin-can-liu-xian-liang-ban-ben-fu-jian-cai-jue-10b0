import sqlite3,json,os
from datetime import datetime,timezone
from pathlib import Path
from app.errors import DomainError

def connection():
    path=Path(os.environ.get('DB_PATH','data/records.db'));path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=10);db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, decision TEXT NOT NULL DEFAULT "待复核")')
    db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    # One row per batch conclusion.  conclusion_id is content-addressed, so
    # re-running the same source inserts nothing.  A confirmed conclusion is
    # never overwritten: later evidence is recorded as a new pending revision.
    db.execute('''CREATE TABLE IF NOT EXISTS conclusions (
        conclusion_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        batch TEXT NOT NULL,
        verdict TEXT NOT NULL,
        code TEXT NOT NULL,
        label TEXT NOT NULL,
        basis TEXT NOT NULL,
        confirmed INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL DEFAULT "待确认",
        supersedes TEXT,
        created_at TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS conclusions_batch ON conclusions(batch,created_at)')
    return db

def _record(db,run_id,body):
    batches=(body.get('adjudication') or {}).get('batches') or []
    now=datetime.now(timezone.utc).isoformat()
    for batch in batches:
        cid=batch.get('conclusion_id')
        if not cid:continue
        history=db.execute('SELECT conclusion_id,confirmed FROM conclusions WHERE batch=? ORDER BY rowid DESC',(batch['batch'],)).fetchall()
        confirmed=next((r for r in history if r['confirmed']),None)
        supersedes=confirmed['conclusion_id'] if confirmed and confirmed['conclusion_id']!=cid else (history[0]['conclusion_id'] if history and history[0]['conclusion_id']!=cid else None)
        db.execute('INSERT OR IGNORE INTO conclusions(conclusion_id,run_id,batch,verdict,code,label,basis,confirmed,state,supersedes,created_at) VALUES (?,?,?,?,?,?,?,0,?,?,?)',
            (cid,run_id,batch['batch'],batch['verdict'],batch['code'],batch['verdict_label'],json.dumps(batch.get('basis',[]),ensure_ascii=False),
             '待确认' if confirmed else '有效',supersedes,now))

def save(run_id, body):
    with connection() as db:
        cur=db.execute('INSERT OR IGNORE INTO runs(id,body) VALUES (?,?)',(run_id,json.dumps(body,ensure_ascii=False)))
        if cur.rowcount==1:_record(db,run_id,body)
        db.execute("INSERT INTO meta(key,value) VALUES ('latest',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(run_id,))
    return get(run_id)

def get(run_id):
    with connection() as db:r=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not r:raise DomainError('NOT_FOUND','批次不存在',404)
    body=json.loads(r['body']);body.update(run_id=r['id'],review_version=r['version'],decision=r['decision']);return body

def history():
    with connection() as db:rows=db.execute("SELECT id,version,decision FROM runs ORDER BY CASE WHEN id=(SELECT value FROM meta WHERE key='latest') THEN 0 ELSE 1 END,rowid DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]

def review(run_id, version, decision):
    if decision not in ('通过','退回'):raise DomainError('DECISION','复核状态无效')
    with connection() as db:
        cur=db.execute('UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?',(decision,run_id,version))
        if cur.rowcount!=1:
            if not db.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():raise DomainError('NOT_FOUND','批次不存在',404)
            raise DomainError('STALE','复核版本已更新，请刷新',409)
        body=json.loads(db.execute('SELECT body FROM runs WHERE id=?',(run_id,)).fetchone()['body'])
        batches=(body.get('adjudication') or {}).get('batches') or []
        ids=[b['conclusion_id'] for b in batches if b.get('conclusion_id')]
        # A rejected revision is cancelled; the previously confirmed conclusion
        # stays effective because it was never overwritten.
        db.execute('UPDATE conclusions SET state="已撤销" WHERE run_id=? AND confirmed=0',(run_id,))
        if decision=='通过':
            for batch in batches:
                db.execute('UPDATE conclusions SET state="已替代" WHERE batch=? AND confirmed=1 AND conclusion_id<>?',(batch['batch'],batch['conclusion_id']))
            for cid in ids:
                db.execute('UPDATE conclusions SET run_id=?,confirmed=1,state="已确认" WHERE conclusion_id=?',(run_id,cid))
    return get(run_id)

def conclusions(run_id=None):
    """All recorded conclusions, oldest first, with the effective one marked.

    A batch keeps its confirmed conclusion effective even when a newer pending
    revision exists (e.g. a supplement attempt that was interrupted).  The
    pending revision only becomes effective once it is reviewed and passed.
    """
    with connection() as db:
        rows=db.execute('SELECT * FROM conclusions ORDER BY rowid').fetchall()
        run_rows=db.execute('SELECT id,version,decision FROM runs').fetchall()
    versions={r['id']:dict(r) for r in run_rows}
    out=[]
    for r in rows:
        item=dict(r);item['basis']=json.loads(item['basis'] or '[]');item['confirmed']=bool(item['confirmed'])
        run=versions.get(item['run_id'],{})
        item['review_version']=run.get('version');item['decision']=run.get('decision')
        item['effective']=False
        # A cancelled revision never becomes effective even without a confirmed peer.
        if item['state']!='已撤销':out.append(item)
    by_batch={}
    for item in out:by_batch.setdefault(item['batch'],[]).append(item)
    for batch,items in by_batch.items():
        confirmed=[i for i in items if i['confirmed'] and i['state']=='已确认']
        effective=confirmed[-1] if confirmed else items[-1]
        effective['effective']=True
    if run_id is not None:out=[i for i in out if i['run_id']==run_id]
    return out


def effective_conclusions():
    return [c for c in conclusions() if c['effective']]
