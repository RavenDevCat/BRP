import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import time
import pytest


def tool(name):
    path=Path(__file__).resolve().parents[1]/"ops/scripts"/(name+".py")
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_online_backup_checks_revisions_and_detects_tampering(tmp_path):
    t=tool("keyed_cache_backup")
    source=tmp_path/"source.sqlite"
    with sqlite3.connect(source) as db:
        db.executescript("create table cache_meta(generation);insert into cache_meta values ('g');"
            "create table entries(key,rev,body);insert into entries values('a',1,'{}'),('deleted',2,null);")
    dest=tmp_path/"new"
    m=t.backup(dest,[source])
    assert m[0]["receipt"]["rows"]==2 and m[0]["receipt"]["entries"]==1
    with pytest.raises(FileExistsError):t.backup(dest,[source])
    with sqlite3.connect(dest/"0.sqlite") as db:db.execute("update entries set rev=22")
    with pytest.raises(ValueError,match="mismatch"):t.verify(dest)


def test_legacy_archive_preserves_active_manual_pinned_and_young(tmp_path):
    t=tool("archive_legacy_web")
    web=tmp_path/"web";web.mkdir()
    dirs=[]
    for i in range(5):
        p=web/f"dist.prev-prod-abcdef{i}-20260601000000";p.mkdir()
        (p/"index.html").write_text("index")
        dirs.append(p)
    (web/"dist").symlink_to(dirs[0],target_is_directory=True)
    (dirs[1]/".pinned").touch()
    dirs[2].rename(web/"dist.manual")
    old=time.time()-40*86400
    for p in (dirs[0],dirs[1],dirs[3],web/"dist.manual"):os.utime(p,(old,old))
    (web/"dist.prev-prod-abcedff-20260601000000").symlink_to(dirs[1],target_is_directory=True)
    assert t.inventory(web,time.time())==[dirs[3]]
    dest=tmp_path/"archive"
    assert t.archive(web,dest)==1 and not dirs[3].exists()
    assert (dest/dirs[3].name/"index.html").read_text()=="index"
    assert json.loads((dest/(dirs[3].name+".json")).read_text())["source"]==str(dirs[3])
    assert (web/"dist"/"index.html").exists() and (dirs[1]/".pinned").exists() and dirs[4].exists()
