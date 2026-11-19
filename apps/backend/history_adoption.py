"""Explicit adoption of unassigned history. No name/address-based matching."""
from collections import defaultdict, deque
from contextlib import closing, contextmanager
import hashlib
import json
from pathlib import Path
import secrets
import time
from uuid import uuid4

from runtime_store_sqlite import HISTORY_SCOPE_TOOL_KEYS, utc_now_iso
from school_directory import DirectoryError

DONE = {"succeeded", "failed", "canceled", "cancelled"}
TTL = 900


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def reject(message, status=409):
    raise DirectoryError(status, message)


def object_row(kind, key, title, owner, school, scope, status=""):
    return dict(ref=kind+":"+key, kind=kind, title=title or key, owner=owner, school=school, scope=scope, status=status)


class Catalog:
    """Only project identity, ACL and dependency fields; never hydrate route results."""
    def __init__(self, db, admins):
        self.rows, self.pairs, self.broken = {}, {}, {}
        self.edges, self.acl = defaultdict(set), defaultdict(set)
        self.shared, self.users = set(), set(admins)
        self.admins = set(admins)
        self.members = defaultdict(dict)
        self.schools = dict(db.execute("SELECT id,name FROM roster.schools"))
        for school,email,role in db.execute("SELECT school_id,email,role FROM roster.memberships"):
            self.members[school][email] = role
            self.users.add(email)
        pair_meta, parents, keys = {}, {}, defaultdict(set)
        for jid,owner,shared,status,school,title,kind,pair,period,ids,queue,parent in db.execute("""
            SELECT job_id,owner_email,shared_with_all,status,json_extract(metadata_json,'$.school_id'),
            coalesce(json_extract(metadata_json,'$.job_name'),json_extract(metadata_json,'$.name'),json_extract(metadata_json,'$.workbook_name')),
            json_extract(metadata_json,'$.job_kind'),json_extract(metadata_json,'$.pair_id'),
            json_extract(metadata_json,'$.period'),json_extract(metadata_json,'$.paired_job_ids'),
            json_extract(metadata_json,'$.job_queue_scope'),json_extract(metadata_json,'$.deep_verification_parent_job_id') FROM jobs"""):
            scope = "distance_direct_school" if kind=="direct_school_analysis" else "route_audit"
            row=object_row("job",jid,title,owner,school,scope,status)
            ref=self._add(row,shared)
            keys[scope,jid].add(ref)
            # The historical store also filed direct-school jobs in the audit scope.
            keys["route_audit",jid].add(ref)
            if pair: pair_meta[ref]=(pair,period,ids,queue,owner)
            if parent: parents[ref]="job:"+parent
        for tool,rid,owner,shared,title,school in db.execute("""SELECT tool_key,run_id,owner_email,shared_with_all,
                title,json_extract(summary_json,'$.school_id') FROM side_tool_runs"""):
            ref=self._add(object_row("run",tool+":"+rid,title,owner,school,tool),shared)
            for scope,tools in HISTORY_SCOPE_TOOL_KEYS.items():
                if tool in tools: keys[scope,rid].add(ref)
        for gid,scope,owner,title,school in db.execute("""SELECT g.group_id,g.scope,g.owner_email,g.name,s.school_id
                FROM history_groups g LEFT JOIN school_folders s USING(group_id)"""):
            self._add(object_row("folder",gid,title,owner,school,scope),False)
        for gid,email in db.execute("SELECT group_id,member_email FROM history_group_members"):
            self.acl["folder:"+gid].add(email)
            self.users.add(email)
        for gid,scope,iid in db.execute("SELECT group_id,scope,item_id FROM history_group_items"):
            folder="folder:"+gid
            matches=keys[scope,iid]
            if len(matches)!=1:
                self.broken[folder]="Unresolved folder item: "+iid
                for ref in matches: self.broken[ref]="Ambiguous folder item: "+iid
            else:
                item=next(iter(matches))
                self.link(folder,item,"folder")
                if self.rows[item]["kind"]!="job" or self.rows[item]["scope"]==scope:
                    self.acl[item].update(self.acl[folder])
        for ref,(pair,period,raw,queue,owner) in pair_meta.items():
            refs=set()
            try:
                ids=json.loads(raw)
                refs={"job:"+ids[p] for p in ("am","pm")}
                if (set(ids)!={"am","pm"} or len(refs)!=2 or "job:"+ids[period]!=ref
                        or any(r not in pair_meta or pair_meta[r][0]!=pair or pair_meta[r][3:]!=(queue,owner)
                               or json.loads(pair_meta[r][2])!=ids or "job:"+ids[pair_meta[r][1]]!=r for r in refs)):
                    raise ValueError()
            except (TypeError,ValueError,KeyError):
                for invalid in ({ref}|refs)&self.rows.keys():
                    self.broken[invalid]="Incomplete or inconsistent morning/return pair"
                continue
            other=next(iter(refs-{ref}))
            self.pairs[ref]=other
            self.link(ref,other,"pair")
        for child,parent in parents.items():
            if parent not in self.rows: self.broken[child]="Missing parent job"
            else: self.link(child,parent,"derived")
        self.users.discard("")
        self.busy={"job:"+r[0] for r in db.execute("""SELECT parent_job_id FROM deep_verifications
            WHERE status IN ('queued','running','pausing','yielding')""")}
        for jid,tool,rid in db.execute("""SELECT source_job_id,source_tool_key,source_run_id FROM route_measurement_reviews
                WHERE status IN ('queued','running','pausing','yielding')"""):
            if jid:self.busy.add("job:"+jid)
            elif tool and rid:self.busy.add("run:"+tool+":"+rid)

    def _add(self,row,shared):
        ref=row["ref"]
        self.rows[ref]=row
        self.users.add(row["owner"])
        self.acl[ref].add(row["owner"])
        if shared:self.shared.add(ref)
        return ref

    def link(self,a,b,why):
        self.edges[a].add((b,why))
        self.edges[b].add((a,why))

    def plan(self,school,selected):
        if school not in self.schools:reject("School not found",404)
        if any(k not in self.rows for k in selected):reject("A selected object no longer exists")
        if any(self.rows[k]["school"] for k in selected):reject("Only unassigned history can be selected")
        why={k:"selected" for k in selected}
        queue=deque(sorted(why))
        while queue:
            key=queue.popleft()
            row=self.rows[key]
            if key in self.broken:reject(key+": "+self.broken[key])
            if key in self.busy:reject("Object has an active verification or review: "+key)
            if row["school"] not in (None,school):reject("Connected object belongs to another school: "+key)
            if row["kind"]=="job" and row["status"] not in DONE:reject("Active or scheduled job: "+key)
            for other,reason in sorted(self.edges[key]):
                if other not in why:
                    why[other]=reason
                    queue.append(other)
            if len(why)>500:reject("Connected selection exceeds 500 objects")
        resources=[dict(self.rows[k],reason=why[k],shared=k in self.shared) for k in sorted(why)]
        changes=defaultdict(lambda:{"gained":[],"lost":[],"legacy_lost":[]})
        for r in resources:
            if r["school"]==school:continue
            k=r["ref"]
            people=self.users if k in self.shared else self.acl[k]
            for user in sorted(people-self.admins-{""}):
                now=user in self.members.get(r["school"],{})
                after=user in self.members[school]
                if now!=after: changes[user]["gained" if after else "lost"].append(k)
                if not after:changes[user]["legacy_lost"].append(k)
        preview=dict(school=school,school_name=self.schools[school],objects=resources,
            users=[dict(email=u,**changes[u]) for u in sorted(changes)],open_legacy=any(r["shared"] and r["school"] is None for r in resources))
        sig=fingerprint([preview,sorted(self.members[school].items()),
            [(k,sorted(self.acl[k]),sorted(self.edges[k])) for k in sorted(why)]])
        return preview,sig


class HistoryAdmin:
    def __init__(self,store,roster,admins):
        self.store,self.roster,self.admins=store,Path(roster),admins

    def initialize(self):
        self.store.initialize()
        with closing(self.store.connect()) as db, db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='history_adoption_tickets'").fetchone():return
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='history_adoption_tickets'").fetchone():return
            db.execute("""CREATE TABLE history_adoption_tickets(
                token TEXT PRIMARY KEY, actor TEXT NOT NULL, school TEXT NOT NULL, selection TEXT NOT NULL,
                fingerprint TEXT NOT NULL, expires INTEGER NOT NULL, receipt TEXT)""")
            db.execute("""CREATE TABLE history_school_grants(
                ref TEXT PRIMARY KEY, school TEXT NOT NULL, batch TEXT NOT NULL)""")
            db.execute("""CREATE TABLE history_admin_events(
                seq INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
                ref TEXT NOT NULL, owner TEXT NOT NULL, before_value TEXT, after_value TEXT NOT NULL, batch TEXT NOT NULL)""")
            for op in ("UPDATE","DELETE"):
                db.execute(f"""CREATE TRIGGER history_events_no_{op} BEFORE {op} ON history_admin_events
                    BEGIN SELECT RAISE(ABORT,'History change log is append-only'); END""")
            # Normal updates remain immutable; only an explicitly granted, first-time adoption can write school_id.
            for name,table,col,reference in (
                ("school_job_immutable","jobs","metadata_json","'job:'||OLD.job_id"),
                ("school_side_immutable","side_tool_runs","summary_json","'run:'||OLD.tool_key||':'||OLD.run_id")):
                old=f"json_extract(OLD.{col},'$.school_id')";new=f"json_extract(NEW.{col},'$.school_id')"
                db.execute("DROP TRIGGER IF EXISTS "+name)
                db.execute(f"""CREATE TRIGGER {name} BEFORE UPDATE OF {col} ON {table}
                    WHEN {old} IS NOT {new} AND NOT ({old} IS NULL AND EXISTS(
                        SELECT 1 FROM history_school_grants WHERE ref=({reference}) AND school={new}))
                    BEGIN SELECT RAISE(ABORT,'Cannot reassign history school'); END""")

    @contextmanager
    def session(self,write=False):
        self.initialize()
        if not self.roster.is_file():reject("School directory unavailable",503)
        with closing(self.store.connect()) as db, db:
            db.execute("ATTACH DATABASE ? AS roster",(str(self.roster),))
            # Lock both databases for confirm so membership and item ACLs cannot drift during the commit.
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db

    def inventory(self):
        with self.session() as db:
            catalog=Catalog(db,self.admins)
            return {"items":[dict(r,paired=r["ref"] in catalog.pairs) for r in
                sorted(catalog.rows.values(),key=lambda r:(r["kind"],r["title"],r["ref"]))]}

    def preview(self,actor,school,selection):
        if not isinstance(school,str) or not isinstance(selection,list) or not 1<=len(selection)<=200 or not all(isinstance(k,str) and 0<len(k)<=256 for k in selection):
            reject("Select 1 to 200 objects and a target school",422)
        selected=sorted(set(selection))
        with self.session(True) as db:
            preview,sig=Catalog(db,self.admins).plan(school,selected)
            token=secrets.token_urlsafe(32);now=int(time.time())
            db.execute("DELETE FROM history_adoption_tickets WHERE expires<=? AND receipt IS NULL",(now,))
            db.execute("INSERT INTO history_adoption_tickets VALUES(?,?,?,?,?,?,NULL)",(token,actor,school,encode(selected),sig,now+TTL))
            return dict(preview,token=token,expires=now+TTL)

    @staticmethod
    def event(db,actor,action,row,before,after,batch):
        db.execute("""INSERT INTO history_admin_events(at,actor,action,ref,owner,before_value,after_value,batch)
            VALUES(?,?,?,?,?,?,?,?)""",(utc_now_iso(),actor,action,row["ref"],row["owner"],before,after,batch))

    def confirm(self,actor,token):
        if not isinstance(token,str) or len(token)>100:reject("Invalid preview",422)
        with self.session(True) as db:
            t=db.execute("SELECT * FROM history_adoption_tickets WHERE token=? AND actor=?",(token,actor)).fetchone()
            if not t:reject("Preview not found",404)
            if t["receipt"]:return dict(json.loads(t["receipt"]),already_applied=True)
            if t["expires"]<=int(time.time()):reject("Preview expired; preview again")
            school=t["school"]
            preview,sig=Catalog(db,self.admins).plan(school,json.loads(t["selection"]))
            if sig!=t["fingerprint"]:reject("Access or selection changed; preview again")
            batch=uuid4().hex;count=0
            for r in preview["objects"]:
                if r["school"]==school:continue
                kind,key=r["ref"].split(":",1)
                if kind=="folder":
                    db.execute("INSERT INTO school_folders VALUES(?,?)",(key,school))
                else:
                    db.execute("INSERT INTO history_school_grants VALUES(?,?,?)",(r["ref"],school,batch))
                    if kind=="job":
                        changed=db.execute("""UPDATE jobs SET metadata_json=json_set(metadata_json,'$.school_id',?),
                            record_json=json_set(record_json,'$.metadata.school_id',?) WHERE job_id=?
                            AND coalesce(json_type(record_json,'$.metadata'),'object')='object'
                            AND json_extract(record_json,'$.metadata.school_id') IS NULL""",(school,school,key))
                    else:
                        tool,rid=key.split(":",1)
                        changed=db.execute("""UPDATE side_tool_runs SET summary_json=json_set(summary_json,'$.school_id',?),
                            record_json=json_set(record_json,'$.summary.school_id',?) WHERE tool_key=? AND run_id=?
                            AND coalesce(json_type(record_json,'$.summary'),'object')='object'
                            AND json_extract(record_json,'$.summary.school_id') IS NULL""",(school,school,tool,rid))
                    if changed.rowcount!=1:reject("Inconsistent stored school metadata: "+r["ref"])
                    db.execute("DELETE FROM history_school_grants WHERE ref=? AND batch=?",(r["ref"],batch))
                self.event(db,actor,"adopt",r,None,school,batch);count+=1
            receipt=dict(batch=batch,count=count,school=school)
            db.execute("UPDATE history_adoption_tickets SET receipt=? WHERE token=?",(encode(receipt),token))
            return receipt

    def rename(self,actor,ref,name,previous=None):
        if not isinstance(ref,str) or not isinstance(name,str) or not 1<=len(name.strip())<=80 or any(ord(c)<32 for c in name):
            reject("Invalid task name",422)
        name=name.strip()
        with self.session(True) as db:
            catalog=Catalog(db,self.admins)
            r=catalog.rows.get(ref)
            if not r or r["kind"]=="folder":reject("Task not found",404)
            if previous is not None and previous!=r["title"]:reject("Task name changed; refresh and try again")
            if ref in catalog.broken:reject(catalog.broken[ref])
            refs=[ref]+([catalog.pairs[ref]] if ref in catalog.pairs else [])
            batch=uuid4().hex;count=0
            for key in refs:
                row=catalog.rows[key];kind,rid=key.split(":",1)
                if row["school"]!=r["school"]:reject("Paired schools do not match")
                if row["status"]=="running":reject("Cannot rename a running task")
                if row["title"]==name:continue
                if kind=="job":
                    db.execute("""UPDATE jobs SET metadata_json=json_set(metadata_json,'$.job_name',?),
                        record_json=json_set(record_json,'$.metadata.job_name',?) WHERE job_id=?""",(name,name,rid))
                else:
                    tool,item=rid.split(":",1)
                    db.execute("UPDATE side_tool_runs SET title=?,record_json=json_set(record_json,'$.title',?) WHERE tool_key=? AND run_id=?",(name,name,tool,item))
                self.event(db,actor,"rename",row,row["title"],name,batch);count+=1
            return dict(batch=batch,count=count,name=name)

    def events(self,before=0):
        with self.session() as db:
            return {"items":[dict(r) for r in db.execute("SELECT * FROM history_admin_events WHERE (?=0 OR seq<?) ORDER BY seq DESC LIMIT 50",(before,before))]}
