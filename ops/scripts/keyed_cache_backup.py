"""Create an online backup of selected cache databases or verify an extracted backup."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3


def inspect(path):
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True)) as db:
        if db.execute("pragma integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Cache integrity check failed")
        (generation,), = db.execute("select generation from cache_meta").fetchall()
        rows, active = db.execute("select count(*),count(body) from entries").fetchone()
    return dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                generation=generation, rows=rows, entries=active)


def verify(directory):
    records=json.loads((directory/"manifest.json").read_text())
    if not records:
        raise ValueError("Empty backup")
    for i, item in enumerate(records):
        name=f"{i}.sqlite"
        if item["file"] != name or inspect(directory/name) != item["receipt"]:
            raise ValueError(f"Backup mismatch: {name}")
    return records


def backup(directory, databases):
    sources=[p.resolve(strict=True) for p in databases]
    if not sources or len(sources)!=len(set(sources)):
        raise ValueError("Expected distinct source databases")
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    records=[]
    for i, src in enumerate(sources):
        dest=directory/f"{i}.sqlite"
        with closing(sqlite3.connect(src.as_uri()+"?mode=ro",uri=True)) as live, closing(sqlite3.connect(dest)) as out:
            live.backup(out, pages=128)
        dest.chmod(0o600)
        records.append(dict(source=str(src),file=dest.name,receipt=inspect(dest)))
    (directory/"manifest.json").write_text(json.dumps(records,indent=2))
    return verify(directory)


if __name__=="__main__":
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("action",choices=("backup","verify"))
    cli.add_argument("directory",type=Path)
    cli.add_argument("databases",nargs="*",type=Path)
    args=cli.parse_args()
    items=backup(args.directory,args.databases) if args.action=="backup" else verify(args.directory)
    print(f"{len(items)} verified databases, {sum(i['receipt']['entries'] for i in items)} live entries")
