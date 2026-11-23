"""Request-local school boundary, in addition to the existing resource ACLs."""
from contextvars import ContextVar
from dataclasses import dataclass, field
import os
from pathlib import Path


def enabled():
    return os.getenv("BRP_SCHOOL_SCOPES") == "1"


def check_runtime():
    db = os.getenv("BRP_RUNTIME_DB_PATH")
    if not enabled():
        if db and Path(db).parent.joinpath(".school-scope-required").exists():
            raise RuntimeError("School-scoped history requires school access enforcement")
        return
    root = Path(os.getenv("BRP_SCHOOL_DATA_ROOT", "")).resolve()
    if not os.getenv("BRP_SCHOOL_DATA_ROOT") or root == Path(root.anchor):
        raise RuntimeError("A dedicated school data root is required")
    for key in ("BRP_RUNTIME_DB_PATH","BRP_SCHOOL_DIRECTORY_DB","BRP_BACKEND_JOBS_DIR","BRP_SIDE_TOOLS_DIR"):
        value = Path(os.getenv(key, ""))
        if not value.is_absolute() or root not in value.resolve().parents:
            raise RuntimeError("All school state must reside inside the dedicated data root")
    parent = Path(db).resolve().parent
    parent.mkdir(parents=True,exist_ok=True)
    marker = parent/".school-scope-required"
    if not marker.exists() and Path(db).exists():
        raise RuntimeError("Refusing to activate school scopes on an existing unmarked database")
    marker.touch(mode=0o600,exist_ok=True)


@dataclass(frozen=True)
class Access:
    roles: dict
    selected: str
    admin: bool
    private: dict = field(default_factory=dict)


class Denied(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


current = ContextVar("brp_school_access", default=None)
PRIVATE = "request:"


def private_scope(value):
    return isinstance(value, str) and value.startswith(PRIVATE)


def visible(school):
    access = current.get()
    if access is not None and private_scope(school):
        return school in access.private
    return access is None or access.admin or school in access.roles


def writable(school):
    access = current.get()
    if access is not None and private_scope(school):
        if access.private.get(school) != "pending":
            raise Denied(403, "Pending application required")
        return
    if access is not None and not access.admin and access.roles.get(school) not in {"school_admin","operator"}:
        raise Denied(403,"School write access required")


def submission():
    access = current.get()
    if access is None:
        return None
    school = access.selected
    if not school and "pending" not in access.private.values() and len(access.roles) == 1:
        school = next(iter(access.roles))
    if private_scope(school):
        writable(school)
        return school
    if not access.roles:
        raise Denied(403,"No school workspace available")
    if not school:
        raise Denied(422,"Select a school workspace")
    if school not in access.roles:
        raise Denied(403,"School access denied")
    writable(school)
    return school


def record_school(record):
    data = (record.get("metadata") if "job_id" in record else record.get("summary")) or {}
    return data.get("school_id") or (PRIVATE + data["school_request_id"] if data.get("school_request_id") else None)


def stamp(values):
    values.pop("school_id", None)
    values.pop("school_request_id", None)
    school = submission()
    if private_scope(school):
        values["school_request_id"] = school[len(PRIVATE):]
    elif school:
        values["school_id"] = school


def group_school(conn, group):
    row = conn.execute("SELECT school_id FROM school_folders WHERE group_id=?", (group,)).fetchone()
    return row[0] if row else None


def item_school(conn, scope, item):
    if scope in {"route_audit","distance_direct_school"}:
        row = conn.execute("SELECT coalesce(json_extract(metadata_json,'$.school_id'),'request:'||json_extract(metadata_json,'$.school_request_id')) FROM jobs WHERE job_id=?", (item,)).fetchone()
    else:
        keys = {"distance_reference":("reference_distance","distance_checker"), "distance_route_cost":("route_cost","distance_checker")}.get(scope,(scope,scope))
        row = conn.execute("SELECT coalesce(json_extract(summary_json,'$.school_id'),'request:'||json_extract(summary_json,'$.school_request_id')) FROM side_tool_runs WHERE run_id=? AND tool_key IN (?,?)",(item,*keys)).fetchone()
    return row[0] if row else None


def group_target(conn, scope, items):
    schools = {item_school(conn,scope,item) for item in items}
    if len(schools) != 1:
        raise Denied(409,"A folder cannot span school workspaces")
    school = schools.pop()
    if private_scope(school):
        raise Denied(409,"Private tasks cannot be placed in shared folders")
    writable(school)
    return school
