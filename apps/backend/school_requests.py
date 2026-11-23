"""Opt-in school requests: personal submissions belong to an explicit application."""
from contextlib import closing
import json
import os
from uuid import uuid4

import school_scope
from school_directory import DirectoryError


def enabled():
    return school_scope.enabled() and os.getenv("BRP_SCHOOL_REQUESTS") == "1"


def setup(history):
    if not enabled() or os.getenv("BRP_JOB_QUEUE_SCOPE") not in {"staging", "prod"}:
        raise RuntimeError("School requests require explicit opt-in and a known queue scope")
    history.initialize()
    with closing(history.store.connect()) as db, db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS school_requests (
          id TEXT PRIMARY KEY, email TEXT NOT NULL, school TEXT NOT NULL, requested TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending', decided_by TEXT, granted TEXT,
          created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, decided TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS one_open_school_request
          ON school_requests(email,school) WHERE status='pending';
        CREATE TABLE IF NOT EXISTS request_tasks (
          ref TEXT PRIMARY KEY, request TEXT NOT NULL REFERENCES school_requests(id),
          created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS request_tasks_parent ON request_tasks(request);
        CREATE TABLE IF NOT EXISTS application_jobs AS SELECT * FROM main.jobs WHERE 0;
        CREATE UNIQUE INDEX IF NOT EXISTS app_job_id ON application_jobs(job_id);
        CREATE INDEX IF NOT EXISTS app_job_status ON application_jobs(status);
        CREATE TABLE IF NOT EXISTS application_tool_runs AS SELECT * FROM main.side_tool_runs WHERE 0;
        CREATE UNIQUE INDEX IF NOT EXISTS app_tool_id ON application_tool_runs(tool_key,run_id);
        CREATE TABLE IF NOT EXISTS application_checkpoints AS SELECT * FROM main.job_checkpoint_parts WHERE 0;
        CREATE UNIQUE INDEX IF NOT EXISTS app_checkpoint_id ON application_checkpoints(job_id,kind,slot);
        """)


PRIVATE_TABLES={"jobs":"application_jobs","side_tool_runs":"application_tool_runs","job_checkpoint_parts":"application_checkpoints"}


def read_views(db):
    # Connection-local projections let each environment use its existing scheduler.
    # An older deployment reads main.jobs only, so private records do not exist for it.
    if enabled() and db.execute("SELECT 1 FROM main.sqlite_master WHERE name='application_checkpoints'").fetchone():
        for public,private in PRIVATE_TABLES.items():
            db.execute(f"CREATE TEMP VIEW {public} AS SELECT * FROM main.{public} UNION ALL SELECT * FROM main.{private}")


def storage(db,table,ref):
    if enabled():
        row=db.execute("""SELECT q.status FROM request_tasks t JOIN school_requests q ON q.id=t.request
                          WHERE t.ref=?""",(ref,)).fetchone()
        if row and row[0]!='approved':return 'main.'+PRIVATE_TABLES[table]
    return 'main.'+table


def mine(store,email):
    with closing(store.connect()) as db:
        return {school_scope.PRIVATE+r[0]:r[1] for r in db.execute(
            "SELECT id,status FROM school_requests WHERE email=? AND status!='approved'",(email,))}


def page(history, actor, admin):
    with history.session() as db:
        schools=[dict(r) for r in db.execute("SELECT id,name FROM roster.schools ORDER BY name")]
        rows=db.execute("""SELECT q.id,q.email,q.school,s.name,q.requested,q.status,q.granted,q.created
            FROM school_requests q JOIN roster.schools s ON s.id=q.school WHERE q.email=? OR
            (q.status='pending' AND (? OR EXISTS (SELECT 1 FROM roster.memberships m
             WHERE m.school_id=q.school AND m.email=? AND m.role='school_admin'))) ORDER BY q.created DESC""",
            (actor,int(admin),actor))
        return {"schools":schools,"applications":[dict(r) for r in rows]}


def apply(history, actor, school, role):
    if role not in ('operator','viewer'):
        raise DirectoryError(422,"Request viewer or operator access")
    with history.session(True) as db:
        if not db.execute("SELECT 1 FROM roster.schools WHERE id=?",(school,)).fetchone():
            raise DirectoryError(404,"School not found")
        if db.execute("SELECT 1 FROM roster.memberships WHERE school_id=? AND email=?",(school,actor)).fetchone():
            raise DirectoryError(409,"Already a member of this school")
        old=db.execute("SELECT id,requested FROM school_requests WHERE email=? AND school=? AND status='pending'",(actor,school)).fetchone()
        if old:
            if old['requested']!=role:raise DirectoryError(409,"An application is already pending")
            return {"id":old['id']}
        rid=uuid4().hex
        db.execute("INSERT INTO school_requests(id,email,school,requested) VALUES(?,?,?,?)",(rid,actor,school,role))
        return {"id":rid}


def guard_save(db, ref, owner, values):
    """Serialize submission against approval. A stale worker must never undo a completed adoption."""
    rid=values.get("school_request_id")
    if not rid:return
    if not db.in_transaction:db.execute("BEGIN IMMEDIATE")
    req=db.execute("SELECT email,status,school FROM school_requests WHERE id=?",(rid,)).fetchone()
    if not req or req["email"]!=owner:raise school_scope.Denied(403,"Private application owner mismatch")
    previous=db.execute("SELECT request FROM request_tasks WHERE ref=?",(ref,)).fetchone()
    if previous:
        if previous[0]!=rid:raise school_scope.Denied(409,"Application binding changed")
        if req["status"]=="approved":values["school_id"]=req["school"]
        return
    if req["status"]!="pending" or values.get("school_id"):
        raise school_scope.Denied(409,"Application changed; reselect the workspace")
    count=db.execute("""SELECT count(*) FROM request_tasks t JOIN school_requests q ON q.id=t.request
        WHERE q.email=? AND t.created>=datetime('now','-1 day')""",(owner,)).fetchone()[0]
    if count>=int(os.getenv("BRP_PERSONAL_TASKS_PER_DAY","20")):
        raise school_scope.Denied(429,"Personal task limit reached")
    db.execute("INSERT INTO request_tasks(ref,request) VALUES(?,?)",(ref,rid))


def decide(history,actor,admin,rid,action,role):
    if action not in ('approved','rejected','withdrawn') or (action=='approved' and role not in ('operator','viewer')):
        raise DirectoryError(422,"Invalid decision or school role")
    with history.session(True) as db:
        q=db.execute("SELECT * FROM school_requests WHERE id=?",(rid,)).fetchone()
        if not q:raise DirectoryError(404,"Application not found")
        school,owner=q['school'],q['email']
        manager=db.execute("SELECT 1 FROM roster.memberships WHERE email=? AND school_id=? AND role='school_admin'",(actor,school)).fetchone()
        if ((action=='withdrawn' and owner!=actor) or (action!='withdrawn' and (not (admin or manager) or actor==owner))):
            raise DirectoryError(403,"This decision is not permitted")
        if q['status']!='pending':
            if q['status']==action and (action!='approved' or role==q['granted']):return {"status":action,"unchanged":True}
            raise DirectoryError(409,"Application is already closed")
        if action=='approved':
            if db.execute("SELECT 1 FROM roster.memberships WHERE school_id=? AND email=?",(school,owner)).fetchone():
                raise DirectoryError(409,"Membership changed; reconcile before approval")
            _adopt(db,rid,owner,school,actor,history)
            db.execute("INSERT INTO roster.memberships VALUES(?,?,?)",(school,owner,role))
            db.execute("""INSERT INTO roster.changes(school_id,actor,subject,action,previous,current,at)
                VALUES(?,?,?,'application_approved',NULL,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))""",(school,actor,owner,role))
        db.execute("UPDATE school_requests SET status=?,decided_by=?,granted=?,decided=CURRENT_TIMESTAMP WHERE id=?",
                   (action,actor,role if action=='approved' else None,rid))
        return {"status":action}


def _adopt(db,rid,owner,school,actor,history):
    jobs={}; rows=[]
    for ref, in db.execute("SELECT ref FROM request_tasks WHERE request=?",(rid,)):
        kind,key=ref.split(':',1)
        if kind=='job':
            table,col,path,where,ids='jobs','metadata_json','metadata','job_id=?',(key,)
        else:
            table,col,path,where,ids='side_tool_runs','summary_json','summary','tool_key=? AND run_id=?',tuple(key.split(':',1))
        private=PRIVATE_TABLES[table]
        row=db.execute(f"SELECT owner_email,{col},json_extract(record_json,'$.{path}.school_request_id') FROM main.{private} WHERE {where}",ids).fetchone()
        if not row:continue  # A deleted task is not recreated.
        data=json.loads(row[1])
        if row[0]!=owner or data.get('school_id') or data.get('school_request_id')!=rid or row[2]!=rid:
            raise DirectoryError(409,"Inconsistent application binding")
        if kind=='job':jobs[key]=data
        rows.append((ref,table,col,path,where,ids))
    for jid,data in jobs.items():
        if not data.get('pair_id') and not data.get('paired_job_ids'):continue
        ids=data.get('paired_job_ids') or {}
        if (set(ids)!={'am','pm'} or len(set(ids.values()))!=2 or ids.get(data.get('period'))!=jid
                or any(j not in jobs or jobs[j].get('paired_job_ids')!=ids or jobs[j].get('period')!=p
                       or jobs[j].get('pair_id')!=data.get('pair_id') for p,j in ids.items())):
            raise DirectoryError(409,"Incomplete application morning/return pair")
    batch=uuid4().hex
    for ref,table,col,path,where,ids in rows:
        private=PRIVATE_TABLES[table]
        db.execute(f"""UPDATE main.{private} SET {col}=json_set({col},'$.school_id',?),
            record_json=json_set(record_json,'$.{path}.school_id',?) WHERE {where}""",(school,school,*ids))
        db.execute(f"INSERT INTO main.{table} SELECT * FROM main.{private} WHERE {where}",ids)
        if table=='jobs':
            db.execute("INSERT INTO main.job_checkpoint_parts SELECT * FROM main.application_checkpoints WHERE job_id=?",ids)
            db.execute("DELETE FROM main.application_checkpoints WHERE job_id=?",ids)
        db.execute(f"DELETE FROM main.{private} WHERE {where}",ids)
        history.event(db,actor,"application_adopt",{"ref":ref,"owner":owner},None,school,batch)
