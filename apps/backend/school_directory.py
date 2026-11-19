"""School membership registry. Never writes to the legacy task/ACL database."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4


ROLES = ("school_admin", "operator", "viewer")


class DirectoryError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def configured_path():
    value = os.getenv("BRP_SCHOOL_DIRECTORY_DB", "").strip()
    return Path(value) if value and Path(value).is_absolute() else None


def email(value):
    value = value.strip().lower() if isinstance(value,str) else ""
    if len(value) > 254 or not re.fullmatch(r"[^@\s/]+@[^@\s/]+\.[^@\s/]+", value):
        raise DirectoryError(422, "Invalid email address")
    return value


SCHEMA = """
CREATE TABLE IF NOT EXISTS schools (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS memberships (
    school_id TEXT NOT NULL REFERENCES schools(id), email TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('school_admin','operator','viewer')),
    PRIMARY KEY(school_id,email));
CREATE INDEX IF NOT EXISTS memberships_email ON memberships(email);
CREATE TABLE IF NOT EXISTS changes (
    seq INTEGER PRIMARY KEY, school_id TEXT NOT NULL REFERENCES schools(id),
    actor TEXT NOT NULL, subject TEXT NOT NULL, action TEXT NOT NULL,
    previous TEXT, current TEXT, at TEXT NOT NULL);
"""


class Directory:
    def __init__(self, path):
        self.path = Path(path)

    def initialize(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript(SCHEMA)

    @contextmanager
    def transaction(self, write=False):
        with closing(sqlite3.connect(self.path.resolve().as_uri()+("?mode=rw" if write else "?mode=ro"), uri=True, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield db

    @staticmethod
    def log(db, school, actor, subject, action, previous, current):
        db.execute("INSERT INTO changes(school_id,actor,subject,action,previous,current,at) VALUES(?,?,?,?,?,?,?)",
                   (school,actor,subject,action,previous,current,datetime.now(timezone.utc).isoformat()))

    def create(self, name, manager, actor):
        name = name.strip() if isinstance(name,str) else ""
        if not name or len(name)>100 or any(ord(c)<32 for c in name):
            raise DirectoryError(422,"Invalid school name")
        manager, actor, school = email(manager), email(actor), uuid4().hex
        with self.transaction(True) as db:
            if db.execute("SELECT 1 FROM schools WHERE name=?",(name,)).fetchone():
                raise DirectoryError(409,"School already exists")
            db.execute("INSERT INTO schools VALUES(?,?)",(school,name))
            db.execute("INSERT INTO memberships VALUES(?,?,?)",(school,manager,"school_admin"))
            self.log(db,school,actor,manager,"school_created",None,"school_admin")
        return {"id":school,"name":name}

    def change(self, school, member, role, expected, actor):
        member,actor=email(member),email(actor)
        if role is not None and role not in ROLES:
            raise DirectoryError(422,"Invalid school role")
        with self.transaction(True) as db:
            if not db.execute("SELECT 1 FROM schools WHERE id=?",(school,)).fetchone():
                raise DirectoryError(404,"School not found")
            row=db.execute("SELECT role FROM memberships WHERE school_id=? AND email=?",(school,member)).fetchone()
            previous=row[0] if row else None
            if previous!=expected:
                raise DirectoryError(409,"Membership changed; refresh and try again")
            if previous==role:return
            if previous=="school_admin" and db.execute(
                "SELECT count(*) FROM memberships WHERE school_id=? AND role='school_admin'",(school,)
            ).fetchone()[0]<=1:
                raise DirectoryError(409,"Keep at least one school administrator")
            if role is None:
                db.execute("DELETE FROM memberships WHERE school_id=? AND email=?",(school,member))
            else:
                db.execute("INSERT INTO memberships VALUES(?,?,?) ON CONFLICT(school_id,email) DO UPDATE SET role=excluded.role",
                           (school,member,role))
            self.log(db,school,actor,member,"membership",previous,role)

    def mine(self, member):
        with self.transaction() as db:
            return [dict(r) for r in db.execute("""SELECT s.id,s.name,m.role FROM memberships m
                JOIN schools s ON s.id=m.school_id WHERE email=? ORDER BY s.name""",(email(member),))]

    def snapshot(self):
        with self.transaction() as db:
            return {"schools":[dict(r) for r in db.execute("SELECT id,name FROM schools ORDER BY name")],
                    "members":[dict(r) for r in db.execute("SELECT school_id,email,role FROM memberships ORDER BY email,school_id")],
                    "changes":[dict(r) for r in db.execute("SELECT * FROM changes ORDER BY seq DESC LIMIT 100")]}


def legacy_directory(path, admins):
    # Use indexed identity/summary columns only, never parse route results or addresses.
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro",uri=True)) as db:
        db.row_factory=sqlite3.Row
        db.execute("BEGIN")
        users={a:{"email":a,"owned_tasks":0,"folder_roles":[]} for a in admins}
        def user(address):
            return users.setdefault(address,{"email":address,"owned_tasks":0,"folder_roles":[]})
        shared=0
        for table in ("jobs","side_tool_runs"):
            shared+=db.execute("SELECT count(*) FROM "+table+" WHERE shared_with_all=1").fetchone()[0]
            for a,count in db.execute("SELECT owner_email,count(*) FROM "+table+" WHERE owner_email<>'' GROUP BY owner_email"):
                user(a)["owned_tasks"]+=count
        groups=db.execute("""SELECT g.name,g.scope,g.owner_email AS email,'owner' AS role FROM history_groups g
            UNION ALL SELECT g.name,g.scope,m.member_email,m.role FROM history_group_members m
            JOIN history_groups g ON g.group_id=m.group_id WHERE m.member_email<>g.owner_email ORDER BY name""")
        for name,scope,a,role in groups:
            user(a)["folder_roles"].append({"name":name,"scope":scope,"role":role})
        for u in users.values():u["system_admin"]=u["email"] in admins
        return {"users":sorted(users.values(),key=lambda u:u["email"]), "shared_records":shared}


def audit_access(path, records, user, admin, page):
    start=page*12
    rows=records[start:start+12]
    folders={}
    if rows:
        ids=[r["job_id"] for r in rows]
        with closing(sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro",uri=True)) as db:
            for jid,name in db.execute("""SELECT i.item_id,g.name FROM history_group_items i
                JOIN history_groups g ON g.group_id=i.group_id
                LEFT JOIN history_group_members m ON m.group_id=g.group_id AND m.member_email=?
                WHERE i.scope='route_audit' AND (g.owner_email=? OR m.member_email IS NOT NULL)
                AND i.item_id IN ("""+",".join("?" for _ in ids)+")",[user,user,*ids]):
                folders.setdefault(jid,[]).append(name)
    return {"total":len(records),"items":[{"id":r["job_id"],"title":(r.get("metadata") or {}).get("job_name") or r["job_id"],
        "own":r["owner_email"]==user,"shared":bool(r.get("shared_with_all")),"admin":admin,
        "folders":folders.get(r["job_id"],[])} for r in rows]}
