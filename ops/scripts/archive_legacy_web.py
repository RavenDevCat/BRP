"""Inventory old generated web bundles; move only recognized, unpinned rollback copies."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time


def inventory(web, now, min_days=30):
    if min_days < 30:
        raise ValueError("Legacy retention must be at least 30 days")
    web=web.resolve(strict=True)
    active=(web/"dist").resolve(strict=True)
    referenced={p.resolve() for p in web.iterdir() if p.is_symlink()} | {active}
    return sorted(p for p in web.glob("dist.*") if re.fullmatch(r"dist\.prev-prod-[a-f0-9]{7,40}-[0-9]{14}",p.name)
                  and not p.is_symlink() and p.is_dir() and p not in referenced and (p/"index.html").is_file()
                  and not any((p/name).exists() for name in (".accepted",".pinned",".brp-release.json"))
                  and p.stat().st_mtime <= now-min_days*86400)


def digest(directory):
    manifest={}
    for f in sorted(directory.rglob("*")):
        if f.is_symlink() or not (f.is_file() or f.is_dir()):
            raise ValueError("Not a standalone bundle")
        if f.is_file():
            with f.open("rb") as stream:
                h=hashlib.sha256()
                for chunk in iter(lambda:stream.read(1024*1024),b""):h.update(chunk)
            manifest[f.relative_to(directory).as_posix()]=h.hexdigest()
    return manifest


def archive(web, target, days=30):
    web=web.resolve(strict=True)
    if target.resolve().is_relative_to(web):
        raise ValueError("Use a private archive outside the serving directory")
    candidates=inventory(web,time.time(),days)
    target.mkdir(mode=0o700,exist_ok=False)
    if web.stat().st_dev!=target.stat().st_dev:raise ValueError("Archive must be on the same filesystem")
    for p in candidates:
        before=digest(p)
        receipt=target/(p.name+".json")
        receipt.write_text(json.dumps(dict(source=str(p),sha256=before),sort_keys=True))
        if p not in inventory(web,time.time(),days):raise ValueError("Bundle eligibility changed")
        os.rename(p,target/p.name)
        if digest(target/p.name)!=before:raise ValueError("Archived artifact changed; check receipt")
    return len(candidates)


if __name__=="__main__":
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("web",type=Path)
    cli.add_argument("--archive",type=Path,help="Move verified bundles into this new directory; default is inventory only")
    args=cli.parse_args()
    print(archive(args.web,args.archive) if args.archive else len(inventory(args.web,time.time())))
