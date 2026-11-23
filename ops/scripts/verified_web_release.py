"""Verify the entry actually used by index.html; switch only managed web releases."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from uuid import uuid4


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.modules = []
        self.dependencies = []
        self.icons = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and a.get("type") == "module" and a.get("src"):
            self.modules.append(a["src"])
        if tag == "link" and a.get("rel") in {"modulepreload","stylesheet"}:
            self.dependencies.append(a.get("href",""))
        if tag == "link" and "icon" in a.get("rel","").lower().split():
            self.icons.append(a.get("href",""))


def verified_entry(root, version):
    root = Path(root)
    page = Scripts()
    page.feed((root/"index.html").read_text())
    if len(page.modules)!=1 or not re.fullmatch(r"/assets/index-[\w-]+\.js",page.modules[0]):
        raise ValueError("Cannot identify unique application entry")
    if not re.fullmatch(r"[a-f0-9]{7,40}",version):
        raise ValueError("Invalid release version")
    for href in page.dependencies:
        if not re.fullmatch(r"/assets/[\w.-]+",href) or not (root/href.lstrip("/")).is_file():
            raise ValueError("Active page references an unavailable dependency")
    for href in page.icons:
        if not re.fullmatch(r"/(?:assets/)?[\w-]+\.(?:svg|png|ico)", href):
            raise ValueError("Unexpected application icon path")
        icon = root / href.lstrip("/")
        if not icon.is_file() or (os.name == "posix" and not (icon.stat().st_mode & 0o004)):
            raise ValueError("Application icon is missing or not publicly readable")
    entry = root/page.modules[0].lstrip("/")
    if not re.search(rb"(?<![0-9a-f])"+version.encode()+rb"(?![0-9a-f])",entry.read_bytes()):
        raise ValueError("Active JavaScript entry is not the requested version")
    return entry


def fingerprint(root, version):
    root = Path(root)
    verified_entry(root,version)
    hashes = {}
    for f in sorted(root.rglob("*")):
        if f.is_symlink():
            raise ValueError("Unexpected symlink in web artifact")
        if f.is_file() and f.name not in {".brp-release.json", ".accepted", ".pinned"}:
            hashes[f.relative_to(root).as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
    return hashes


def switch(web, target):
    link = web/(".next-"+uuid4().hex)
    link.symlink_to(os.path.relpath(target,web),target_is_directory=True)
    try:
        os.replace(link,web/"dist")
    finally:
        link.unlink(missing_ok=True)


def check_serving(root, manifest, reader=None):
    if os.name == "posix" and not root.stat().st_mode & 0o001:
        raise ValueError("Static release directory cannot be traversed by the web server")
    if reader:
        # Read each served file as the actual worker, including lazy chunks and icons.
        subprocess.run(["sudo", "-n", "-u", reader, "--", "/usr/bin/head", "-q", "-c", "1", "--",
                        *(str(root / path) for path in manifest)], stdout=subprocess.DEVNULL, check=True)


def activate(web, source, version, reader=None):
    root = web/".brp-releases"
    root.mkdir(exist_ok=True)
    expected = fingerprint(source,version)
    key = version+"-"+hashlib.sha256(json.dumps(expected,sort_keys=True).encode()).hexdigest()[:16]
    target = root/key
    if not target.exists():
        with tempfile.TemporaryDirectory(dir=root) as stage:
            tmp = Path(stage)/"bundle"
            shutil.copytree(source,tmp)
            if os.name == "posix":
                # copytree inherits mktemp's 0700; only the installed static root needs traversal.
                tmp.chmod(tmp.stat().st_mode | 0o011)
            if fingerprint(tmp,version)!=expected:
                raise ValueError("Staged artifact changed during activation")
            (tmp/".brp-release.json").write_text(json.dumps(expected))
            tmp.rename(target)
    if fingerprint(target,version)!=expected:
        raise ValueError("Existing release failed manifest verification")
    check_serving(target, expected, reader)
    dist = web/"dist"
    previous = dist.resolve(strict=True)
    if not dist.is_symlink():
        previous = root/("legacy-"+uuid4().hex)
        dist.rename(previous)
    try:
        switch(web,target)
    except BaseException:
        if not dist.exists():
            previous.rename(dist)
        raise
    return previous


def prune(web, keep, apply=False):
    if keep<2:
        raise ValueError("Keep at least two successful releases")
    root = web/".brp-releases"
    candidates=sorted((d for d in root.iterdir() if not d.is_symlink() and
        re.fullmatch(r"[0-9a-f]{7,40}-[0-9a-f]{16}",d.name) and (d/".brp-release.json").is_file()
        and (d/".accepted").is_file()),key=lambda d:(d/".accepted").stat().st_mtime,reverse=True)
    for d in candidates[keep:]:
        if d.resolve()!=(web/"dist").resolve() and not (d/".pinned").exists():
            print(d.name)
            if apply:
                shutil.rmtree(d)


def main():
    cli=argparse.ArgumentParser()
    cli.add_argument("action",choices=["activate","restore","prune","verify"])
    cli.add_argument("web",type=Path)
    cli.add_argument("--source",type=Path)
    cli.add_argument("--version")
    cli.add_argument("--reader", help="Check every static asset as this web server user")
    cli.add_argument("--keep",type=int,default=3)
    cli.add_argument("--apply",action="store_true")
    a=cli.parse_args()
    if a.action=="activate":
        print(activate(a.web,a.source,a.version,a.reader))
    elif a.action=="restore":
        target=a.source.resolve(strict=True)
        if target.parent!=(a.web/".brp-releases").resolve():
            raise ValueError("Rollback is restricted to this application's release directory")
        switch(a.web,target)
    elif a.action=="verify":
        manifest=fingerprint(a.web/"dist",a.version)
        if manifest!=fingerprint(a.source,a.version):
            raise ValueError("Production artifact differs from staging")
        check_serving(a.web/"dist", manifest, a.reader)
    else:
        prune(a.web,a.keep,a.apply)
if __name__=="__main__":
    main()
