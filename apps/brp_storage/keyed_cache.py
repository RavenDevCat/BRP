"""Optional per-key persistence. Legacy JSON is a read-only import, not a dual writer."""
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4


def enabled(path):
    return (os.getenv("BRP_KEYED_CACHE", "0") == "1" and
            (Path(path).name in {"geocode_cache.json", "final_route_traffic_cache.json",
                                 "planner_result_cache.json", "amap_display_geometry.json"}
             or str(path) == os.getenv("BRP_AMAP_DISPLAY_GEOMETRY_CACHE_PATH")))


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(raw):
    return hashlib.sha256(raw.encode()).digest() if raw is not None else None


class Snapshot(dict):
    def __init__(self, path, generation, rows):
        self.path = Path(path).resolve()
        self.generation = generation
        self.versions = {}
        super().__init__()
        for key, revision, raw in rows:
            self.versions[key] = (revision, _digest(raw))
            if raw is not None:
                self[key] = json.loads(raw)


@contextmanager
def _transaction(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(path)+".sqlite", timeout=10)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("CREATE TABLE IF NOT EXISTS cache_meta (generation TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, rev INTEGER NOT NULL, body TEXT)")
        row = conn.execute("SELECT generation FROM cache_meta").fetchone()
        if row is None:
            data = json.loads(path.read_text(encoding="utf8")) if path.exists() else {}
            if not isinstance(data, dict):
                raise ValueError("Legacy cache must be a JSON object")
            conn.executemany("INSERT INTO entries VALUES(?,1,?)", ((k,_encode(v)) for k,v in data.items()))
            generation = uuid4().hex
            conn.execute("INSERT INTO cache_meta VALUES(?)", (generation,))
        else:
            generation = row[0]
        yield conn, generation


def load(path):
    with _transaction(path) as (conn, generation):
        return Snapshot(path, generation, conn.execute("SELECT key,rev,body FROM entries"))


def for_batch(data):
    """Give each task a fresh, private keyed snapshot; keep legacy JSON semantics."""
    return load(data.path) if isinstance(data, Snapshot) else data


def clear(path):
    with _transaction(path) as (conn, _):
        conn.execute("DELETE FROM entries")
        conn.execute("UPDATE cache_meta SET generation=?", (uuid4().hex,))


def save(path, data, sanitize):
    # Compute deltas before the write lock. A stale writer cannot undo a clear or a newer revision.
    tracked = isinstance(data, Snapshot)
    if tracked and Path(path).resolve() != data.path:
        raise ValueError("Cache snapshot belongs to a different file")
    before = data.versions.copy() if tracked else {}
    latest = {}
    for k,v in data.copy().items():
        raw = _encode(sanitize(v))
        if before.get(k,(0,None))[1] != _digest(raw):
            latest[k] = raw
    for k,(_,digest) in before.items():
        if k not in data and digest is not None:
            latest[k] = None
    updates = []
    with _transaction(path) as (conn, generation):
        if tracked and generation != data.generation:
            fresh = Snapshot(path,generation,conn.execute("SELECT key,rev,body FROM entries"))
            data.clear()
            data.update(fresh)
            data.generation, data.versions = generation, fresh.versions
            return
        for key,raw in latest.items():
            expected = before.get(key,(0,None))[0]
            row = conn.execute("SELECT rev,body FROM entries WHERE key=?", (key,)).fetchone()
            if row and row[0] != expected:
                updates.append((key, raw, *row))
                continue
            if not row and expected:
                raise ValueError("Cache revision lost; reload before saving")
            rev = expected+1
            conn.execute("INSERT INTO entries VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET rev=excluded.rev, body=excluded.body",
                         (key, rev, raw))
            updates.append((key, raw, rev, raw))
    if tracked:
        for key,submitted,rev,saved in updates:
            data.versions[key] = rev, _digest(saved)
            if submitted != saved and (key not in data and submitted is None or
                    key in data and _encode(sanitize(data[key])) == submitted):
                if saved is None:
                    data.pop(key,None)
                else:
                    data[key] = json.loads(saved)


def export(path, destination):
    """Quiesce cache writers, export to a new file, then switch the flag off."""
    if not Path(str(path)+".sqlite").is_file():
        raise FileNotFoundError("No keyed cache to export")
    with _transaction(path) as (conn,_):
        data = {k:json.loads(v) for k,v in conn.execute("SELECT key,body FROM entries WHERE body IS NOT NULL")}
        with Path(destination).open("x", encoding="utf8") as out:
            json.dump(data,out,ensure_ascii=False,allow_nan=False)
