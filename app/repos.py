"""Git repositories as BRD sources: shallow clone, filter by path, upsert documents by content hash."""
import base64
import fnmatch
import hashlib
import ipaddress
import logging
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, Body, HTTPException, Request

from . import config, db, embeddings, history, parsing, settings

log = logging.getLogger("fabulist.repos")
router = APIRouter(prefix="/api/projects/{pid}/repos")
MAX_FILES = 200
_URL = re.compile(r"^https://[A-Za-z0-9.-]+(:\d{1,5})?(/[\w.~%@+-]+)+/?$")
_BRANCH = re.compile(r"^[\w][\w./-]{0,99}$")


class RepoError(ValueError):
    pass


def _resolve(host: str) -> List[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)})
    except socket.gaierror:
        raise RepoError("Cannot resolve repository host %s" % host)


def check_public_host(url: str) -> List[str]:
    """Refuse hosts that are internal to the cluster or private networks (SSRF guard).

    Returns the addresses that passed, so the caller can connect to exactly those (empty when the guard is off)."""
    if config.ALLOW_PRIVATE_REPO_HOSTS:
        return []
    host = (urlsplit(url).hostname or "").lower()
    if host == "localhost" or host.endswith((".svc", ".cluster.local", ".internal", ".local", ".localhost")):
        raise RepoError("Repository host %s is internal; ask an administrator to allow private hosts" % host)
    addrs = _resolve(host)
    for addr in addrs:
        ip = ipaddress.ip_address(addr)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise RepoError("Repository host %s resolves to a private address; ask an administrator to allow private hosts" % host)
    return addrs


def validate(url: str, branch: str, path_glob: str) -> None:
    if not _URL.match(url):
        raise RepoError("Repository URL must be an https:// Git URL, e.g. https://github.com/org/brds.git")
    if branch and (not _BRANCH.match(branch) or ".." in branch):
        raise RepoError("Invalid branch name")
    if len(path_glob) > 300 or any(p.strip().startswith("/") for p in path_glob.split(",")):
        raise RepoError("Path filter must be relative, e.g. docs/brd/*.md")
    check_public_host(url)


def _git_env(token: str, pin: str = "") -> Dict[str, str]:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="true")
    # Config via environment keeps the token out of the process list (git >= 2.31).
    # followRedirects=false: a public host must not be able to bounce the clone to an internal one.
    cfg = [("protocol.allow", "never"), ("protocol.https.allow", "always"), ("credential.helper", ""),
           ("http.followRedirects", "false")]
    if pin:  # connect to the address that passed the private-network check, not to a second DNS answer
        cfg.append(("http.curloptResolve", pin))
    if token:
        cfg.append(("http.extraHeader", "Authorization: Basic " + base64.b64encode(("x-access-token:" + token).encode()).decode()))
    env["GIT_CONFIG_COUNT"] = str(len(cfg))
    for i, (k, v) in enumerate(cfg):
        env["GIT_CONFIG_KEY_%d" % i], env["GIT_CONFIG_VALUE_%d" % i] = k, v
    return env


def _tree_bytes(path: str) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass  # vanished while git was writing
    return total


def fetch(url: str, branch: str, token: str, dest: str) -> str:
    """Shallow-clone `url` into `dest`; return the HEAD commit SHA."""
    pin = ""
    if urlsplit(url).scheme == "https":
        # Check and pin from the same DNS answer (not only when the repository was added), IPv4 only: curl's
        # resolve list is documented for those.
        v4 = [a for a in check_public_host(url) if ipaddress.ip_address(a).version == 4]
        if v4:
            parts = urlsplit(url)
            pin = "%s:%d:%s" % (parts.hostname, parts.port or 443, ",".join(v4))
    cmd = ["git", "clone", "--depth", "1", "--single-branch", "--no-tags"] + (["--branch", branch] if branch else []) + ["--", url, dest]
    env = _git_env(token, pin)
    timeout, limit = config.REPO_CLONE_TIMEOUT, config.MAX_REPO_MB * 1024 * 1024
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    deadline = time.monotonic() + timeout
    while True:
        try:
            _out, err = proc.communicate(timeout=1)
            break
        except subprocess.TimeoutExpired:
            if time.monotonic() > deadline:
                proc.kill(); proc.communicate()
                raise RepoError("Cloning timed out after %ds" % timeout)
            if _tree_bytes(dest) > limit:  # a huge repository would otherwise fill the disk before the timeout
                proc.kill(); proc.communicate()
                raise RepoError("The repository is larger than the %d MB limit. Use a path-limited or smaller repository." % config.MAX_REPO_MB)
    if proc.returncode != 0:
        msg = (err or "").strip().splitlines()
        detail = next((m for m in reversed(msg) if m.startswith("fatal:")), msg[-1] if msg else "unknown error")
        if token:
            detail = detail.replace(token, "***")
        if "redirect" in detail.lower() or re.search(r"error: 30[1278]\b", detail):
            detail += " (redirects are not followed; use the repository's canonical https URL)"
        raise RepoError("git clone failed: " + detail)
    sha = subprocess.run(["git", "-C", dest, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30, env=env)
    return sha.stdout.strip()


def matches(path: str, path_glob: str) -> bool:
    patterns = [p.strip() for p in (path_glob or "*").split(",") if p.strip()] or ["*"]
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def collect(root: str, path_glob: str) -> Tuple[Dict[str, Tuple[str, str]], List[str]]:
    """Return ({path: (text, sha256)}, skipped messages) for supported files under root."""
    found: Dict[str, Tuple[str, str]] = {}
    skipped: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            ext = os.path.splitext(name)[1].lower()
            if ext not in parsing.PARSERS or not matches(rel, path_glob) or os.path.islink(full):
                continue
            if len(found) >= MAX_FILES:
                skipped.append("%s: file limit (%d) reached" % (rel, MAX_FILES))
                continue
            if os.path.getsize(full) > config.MAX_UPLOAD_BYTES:
                skipped.append("%s: larger than the upload limit" % rel)
                continue
            with open(full, "rb") as fh:
                data = fh.read()
            try:
                text = parsing.extract_text(name, data)
            except parsing.ParseError as exc:
                skipped.append("%s: %s" % (rel, exc))
                continue
            found[rel] = (text, hashlib.sha256(data).hexdigest())
    return found, skipped


def _public(r: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: r[k] for k in ("id", "project_id", "url", "branch", "path_glob", "key_hint", "last_sync", "last_commit", "last_error", "created")}
    out["has_token"] = bool(r["token_enc"])
    out["documents"] = db.one("SELECT count(*) AS n FROM documents WHERE repo_id = %s", (r["id"],))["n"]
    return out


def _repo(pid: str, rid: str) -> Dict[str, Any]:
    r = db.one("SELECT * FROM repositories WHERE id = %s AND project_id = %s", (rid, pid))
    if not r:
        raise HTTPException(404, "Repository not found")
    return r


def sync(repo: Dict[str, Any], who: str) -> Dict[str, Any]:
    token = settings.decrypt_secret(repo["token_enc"]) if repo["token_enc"] else ""
    tmp = tempfile.mkdtemp(prefix="fabulist-repo-")
    try:
        try:
            check_public_host(repo["url"])  # DNS may have changed since the repository was added
            sha = fetch(repo["url"], repo["branch"], token, os.path.join(tmp, "src"))
            found, skipped = collect(os.path.join(tmp, "src"), repo["path_glob"])
        except RepoError as exc:
            db.execute("UPDATE repositories SET last_error=%s, last_sync=%s WHERE id=%s", (str(exc)[:500], db.now(), repo["id"]))
            raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    pid = repo["project_id"]
    existing = {d["path"]: d for d in db.rows("SELECT id, path, content_hash FROM documents WHERE repo_id=%s", (repo["id"],))}
    added, updated, removed, reindex = [], [], [], []
    with db.conn() as c:
        for path, (text, digest) in found.items():
            doc = existing.get(path)
            if doc is None:
                did = db.new_id()
                c.execute("INSERT INTO documents (id, project_id, filename, text, created, source, repo_id, path, content_hash) "
                          "VALUES (%s,%s,%s,%s,%s,'repo',%s,%s,%s)", (did, pid, path, text, db.now(), repo["id"], path, digest))
                added.append(path)
                reindex.append((did, text))
            elif doc["content_hash"] != digest:
                c.execute("UPDATE documents SET text=%s, content_hash=%s WHERE id=%s", (text, digest, doc["id"]))
                c.execute("DELETE FROM chunks WHERE document_id=%s", (doc["id"],))
                updated.append(path)
                reindex.append((doc["id"], text))
        for path, doc in existing.items():
            if path not in found:
                c.execute("DELETE FROM documents WHERE id=%s", (doc["id"],))
                removed.append(path)
        c.execute("UPDATE repositories SET last_sync=%s, last_commit=%s, last_error='' WHERE id=%s", (db.now(), sha, repo["id"]))
        history.record(c, pid, "repository", repo["id"], repo["url"].rsplit("/", 1)[-1], "synced", who,
                       "%s @ %s: %d added, %d updated, %d removed" % (repo["url"], sha[:8], len(added), len(updated), len(removed)))
    for did, text in reindex:
        try:
            embeddings.index_document(pid, did, text)
        except Exception:  # search degrades; documents are still usable for generation
            log.exception("indexing failed for document %s", did)
    return {"commit": sha, "added": added, "updated": updated, "removed": removed, "skipped": skipped,
            "unchanged": len(found) - len(added) - len(updated)}


@router.get("")
def list_repos(pid: str):
    return [_public(r) for r in db.rows("SELECT * FROM repositories WHERE project_id=%s ORDER BY created", (pid,))]


@router.post("", status_code=201)
def add_repo(pid: str, body: Dict[str, Any] = Body(...)):
    if not db.one("SELECT 1 AS x FROM projects WHERE id=%s", (pid,)):
        raise HTTPException(404, "Project not found")
    url = str(body.get("url") or "").strip()
    branch = str(body.get("branch") or "").strip()
    path_glob = str(body.get("path_glob") or "*").strip() or "*"
    token = str(body.get("token") or "").strip()
    try:
        validate(url, branch, path_glob)
    except RepoError as exc:
        raise HTTPException(400, str(exc))
    enc = settings.encrypt_secret(token) if token else None
    rid = db.new_id()
    db.execute("INSERT INTO repositories (id, project_id, url, branch, path_glob, token_enc, key_hint, created) "
               "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", (rid, pid, url, branch, path_glob, enc, "…" + token[-4:] if token else "", db.now()))
    return _public(_repo(pid, rid))


@router.post("/{rid}/sync")
def sync_repo(pid: str, rid: str, request: Request):
    try:
        return sync(_repo(pid, rid), history.actor(request))
    except RepoError as exc:
        raise HTTPException(400, str(exc))


@router.delete("/{rid}", status_code=204)
def delete_repo(pid: str, rid: str):
    _repo(pid, rid)
    with db.conn() as c:
        c.execute("DELETE FROM documents WHERE repo_id=%s", (rid,))
        c.execute("DELETE FROM repositories WHERE id=%s", (rid,))
