"""Push stories to issue trackers (Jira, Azure DevOps). Idempotent via external_links."""
import base64
import hashlib
import hmac
import html
import json
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Body, HTTPException

from . import db, settings

router = APIRouter()
KINDS = {"jira": "Jira (Cloud or Data Center)", "azure": "Azure DevOps Boards", "github": "GitHub Issues",
         "confluence": "Confluence page (Cloud or Data Center)", "webhook": "Webhook (JSON POST)"}
_URL = re.compile(r"^https://[A-Za-z0-9.-]+(:\d{1,5})?(/[\w.~%@+-]+)*/?$")


class TrackerError(RuntimeError):
    pass


def _request(method: str, url: str, headers: Dict[str, str], body: Any = None, content_type: str = "application/json",
             timeout: float = 30) -> Any:
    """HTTP call with bounded retries on 429/5xx/network errors."""
    last = "unknown error"
    for attempt in range(3):
        try:
            resp = httpx.request(method, url, headers=dict(headers, **{"Content-Type": content_type, "Accept": "application/json"}),
                                 json=body if body is not None else None, timeout=timeout)
        except httpx.HTTPError as exc:
            last = "%s contacting tracker" % type(exc).__name__
        else:
            if resp.status_code == 429 or resp.status_code >= 500:
                last = "tracker returned HTTP %d" % resp.status_code
            elif resp.status_code >= 400:
                raise TrackerError(_http_error(resp))
            else:
                return resp.json() if resp.content else {}
        if attempt < 2:
            time.sleep(1.5 * (2 ** attempt))
    raise TrackerError(last)


SYSTEMIC = {401: "check the account and API token (credentials)", 403: "the token lacks permission for this project or space",
            404: "not found: check the base URL and the target project or space"}


def _http_error(resp: httpx.Response) -> str:
    detail, hint = _error_text(resp).strip(), SYSTEMIC.get(resp.status_code)
    return "HTTP %d%s%s" % (resp.status_code, ": " + detail if detail else "", " (%s)" % hint if hint else "")


def _error_text(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except Exception:
        return resp.text[:200]
    if isinstance(data, dict):
        msgs = data.get("errorMessages") or []
        errs = data.get("errors") or {}
        parts = list(msgs) + ["%s: %s" % kv for kv in (errs.items() if isinstance(errs, dict) else [])]
        return "; ".join(str(p) for p in parts)[:300] or str(data.get("message", ""))[:300]
    return str(data)[:200]


# ---- formatting ----------------------------------------------------------

def _jira_description(s: Dict[str, Any]) -> str:
    out = [s["narrative"], "", "h3. Acceptance criteria"] + ["* " + a for a in s["acceptance_criteria"]]
    if s.get("variant_criteria"):
        out += ["", "h3. Product variants"] + ["* *%s*: %s" % (v["scope"], v["criteria"]) for v in s["variant_criteria"]]
    if s.get("open_questions"):
        out += ["", "h3. Open questions"] + ["* " + q for q in s["open_questions"]]
    out += ["", "Requirements: %s | Fabulist %s" % (", ".join(s.get("requirement_ids") or []) or "none", s["key"])]
    return "\n".join(out)


def _labels(s: Dict[str, Any]) -> List[str]:
    return sorted({re.sub(r"\s+", "-", x.strip())[:60] for x in s.get("labels", []) + s.get("compliance_tags", []) if x.strip()} | {"fabulist"})


def _html_list(items: List[str]) -> str:
    return "<ul>" + "".join("<li>%s</li>" % html.escape(i) for i in items) + "</ul>"


# ---- trackers ------------------------------------------------------------

class Jira:
    def __init__(self, conn: Dict[str, Any], token: str):
        self.base = conn["base_url"].rstrip("/")
        auth = ("%s:%s" % (conn["username"], token)) if conn["username"] else None
        self.headers = {"Authorization": "Basic " + base64.b64encode(auth.encode()).decode()} if auth else {"Authorization": "Bearer " + token}

    def test(self) -> str:
        me = _request("GET", self.base + "/rest/api/2/myself", self.headers)
        return "Signed in as %s" % (me.get("displayName") or me.get("name") or "user")

    def _url(self, key: str) -> str:
        return "%s/browse/%s" % (self.base, key)

    def upsert_epic(self, target: str, name: str, existing: Optional[str]) -> Tuple[str, str]:
        fields = {"summary": name[:250]}
        if existing:
            _request("PUT", "%s/rest/api/2/issue/%s" % (self.base, existing), self.headers, {"fields": fields})
            return existing, self._url(existing)
        data = _request("POST", self.base + "/rest/api/2/issue", self.headers,
                        {"fields": dict(fields, project={"key": target}, issuetype={"name": "Epic"}, labels=["fabulist"])})
        return data["key"], self._url(data["key"])

    def upsert_story(self, target: str, s: Dict[str, Any], epic_key: Optional[str], existing: Optional[str]) -> Tuple[str, str]:
        fields: Dict[str, Any] = {"summary": s["title"][:250], "description": _jira_description(s), "labels": _labels(s)}
        if existing:
            _request("PUT", "%s/rest/api/2/issue/%s" % (self.base, existing), self.headers, {"fields": fields})
            return existing, self._url(existing)
        fields.update(project={"key": target}, issuetype={"name": "Story"})
        if epic_key:
            fields["parent"] = {"key": epic_key}
        data = _request("POST", self.base + "/rest/api/2/issue", self.headers, {"fields": fields})
        return data["key"], self._url(data["key"])


class Azure:
    PRIORITY = {"High": 1, "Medium": 2, "Low": 3}

    def __init__(self, conn: Dict[str, Any], token: str):
        self.base = conn["base_url"].rstrip("/")  # https://dev.azure.com/{organization}
        self.headers = {"Authorization": "Basic " + base64.b64encode((":" + token).encode()).decode()}

    def test(self) -> str:
        data = _request("GET", self.base + "/_apis/projects?api-version=7.1", self.headers)
        return "Connected: %d projects visible" % int(data.get("count", 0))

    def _wit(self, target: str, kind: str) -> str:
        return "%s/%s/_apis/wit/workitems/$%s?api-version=7.1" % (self.base, quote(target), quote(kind))

    def _save(self, target: str, kind: str, ops: List[Dict[str, Any]], existing: Optional[str]) -> Tuple[str, str]:
        if existing:
            url = "%s/_apis/wit/workitems/%s?api-version=7.1" % (self.base, existing)
            data = _request("PATCH", url, self.headers, [dict(o, op="add") for o in ops], "application/json-patch+json")
        else:
            data = _request("POST", self._wit(target, kind), self.headers, [dict(o, op="add") for o in ops], "application/json-patch+json")
        wid = str(data["id"])
        link = (data.get("_links") or {}).get("html", {}).get("href") or "%s/%s/_workitems/edit/%s" % (self.base, quote(target), wid)
        return wid, link

    def upsert_epic(self, target: str, name: str, existing: Optional[str]) -> Tuple[str, str]:
        return self._save(target, "Epic", [{"path": "/fields/System.Title", "value": name[:250]},
                                           {"path": "/fields/System.Tags", "value": "fabulist"}], existing)

    def upsert_story(self, target: str, s: Dict[str, Any], epic_key: Optional[str], existing: Optional[str]) -> Tuple[str, str]:
        desc = "<p>%s</p>" % html.escape(s["narrative"])
        if s.get("variant_criteria"):
            desc += "<h3>Product variants</h3>" + _html_list(["%s: %s" % (v["scope"], v["criteria"]) for v in s["variant_criteria"]])
        desc += "<p>Requirements: %s · Fabulist %s</p>" % (html.escape(", ".join(s.get("requirement_ids") or []) or "none"), s["key"])
        ops = [{"path": "/fields/System.Title", "value": s["title"][:250]},
               {"path": "/fields/System.Description", "value": desc},
               {"path": "/fields/Microsoft.VSTS.Common.AcceptanceCriteria", "value": _html_list(s["acceptance_criteria"])},
               {"path": "/fields/Microsoft.VSTS.Common.Priority", "value": self.PRIORITY.get(s["priority"], 2)},
               {"path": "/fields/System.Tags", "value": "; ".join(_labels(s))}]
        if s.get("story_points"):
            ops.append({"path": "/fields/Microsoft.VSTS.Scheduling.StoryPoints", "value": s["story_points"]})
        if epic_key and not existing:
            ops.append({"path": "/relations/-", "value": {"rel": "System.LinkTypes.Hierarchy-Reverse",
                                                          "url": "%s/_apis/wit/workItems/%s" % (self.base, epic_key)}})
        return self._save(target, "User Story", ops, existing)


class GitHub:
    """Epics become issues labelled 'epic'; stories reference their epic issue in the body."""

    def __init__(self, conn: Dict[str, Any], token: str):
        self.base = (conn["base_url"] or "https://api.github.com").rstrip("/")
        self.headers = {"Authorization": "Bearer " + token, "X-GitHub-Api-Version": "2022-11-28"}

    def test(self) -> str:
        me = _request("GET", self.base + "/user", self.headers)
        return "Signed in as %s" % me.get("login", "user")

    def _repo(self, target: str) -> str:
        if not re.match(r"^[\w.-]+/[\w.-]+$", target):
            raise TrackerError("GitHub target must be owner/repository")
        return "%s/repos/%s/issues" % (self.base, target)

    def _save(self, target: str, payload: Dict[str, Any], existing: Optional[str]) -> Tuple[str, str]:
        if existing:
            data = _request("PATCH", "%s/%s" % (self._repo(target), existing.lstrip("#")), self.headers, payload)
        else:
            data = _request("POST", self._repo(target), self.headers, payload)
        return "#%d" % data["number"], data["html_url"]

    def upsert_epic(self, target: str, name: str, existing: Optional[str]) -> Tuple[str, str]:
        return self._save(target, {"title": "Epic: " + name[:240], "labels": ["epic", "fabulist"]}, existing)

    def upsert_story(self, target: str, s: Dict[str, Any], epic_key: Optional[str], existing: Optional[str]) -> Tuple[str, str]:
        body = [s["narrative"], "", "### Acceptance criteria"] + ["- [ ] " + a for a in s["acceptance_criteria"]]
        if s.get("variant_criteria"):
            body += ["", "### Product variants"] + ["- **%s**: %s" % (v["scope"], v["criteria"]) for v in s["variant_criteria"]]
        if s.get("open_questions"):
            body += ["", "### Open questions"] + ["- " + q for q in s["open_questions"]]
        body += ["", "Epic: %s · Requirements: %s · Fabulist %s" % (epic_key or "-", ", ".join(s.get("requirement_ids") or []) or "none", s["key"])]
        return self._save(target, {"title": s["title"][:250], "body": "\n".join(body), "labels": _labels(s)}, existing)


class Webhook:
    """Delivers the whole backlog as one signed JSON POST (X-Fabulist-Signature: sha256=<hmac>)."""

    def __init__(self, conn: Dict[str, Any], token: str):
        self.url, self.secret = conn["base_url"], token

    def _post(self, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json", "User-Agent": "Fabulist-Webhook"}
        if self.secret:
            headers["X-Fabulist-Signature"] = "sha256=" + hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
        last = "unknown error"
        for attempt in range(3):
            try:
                resp = httpx.post(self.url, content=body, headers=headers, timeout=30)
            except httpx.HTTPError as exc:
                last = "%s contacting webhook" % type(exc).__name__
            else:
                if resp.status_code < 400:
                    return
                if resp.status_code != 429 and resp.status_code < 500:
                    raise TrackerError("webhook returned HTTP %d" % resp.status_code)
                last = "webhook returned HTTP %d" % resp.status_code
            if attempt < 2:
                time.sleep(1.5 * (2 ** attempt))
        raise TrackerError(last)

    def test(self) -> str:
        self._post({"event": "ping", "sent_at": db.now()})
        return "Ping delivered"

    def deliver(self, project_id: str, target: str, stories: List[Dict[str, Any]]) -> Dict[str, Any]:
        project = db.one("SELECT id, name, platform FROM projects WHERE id=%s", (project_id,)) or {}
        fields = ("key", "epic", "feature", "title", "persona", "narrative", "acceptance_criteria", "priority", "story_points",
                  "requirement_ids", "labels", "product_scope", "variant_criteria", "compliance_tags", "open_questions", "status")
        self._post({"event": "backlog.pushed", "sent_at": db.now(), "target": target, "project": project,
                    "stories": [{k: s.get(k) for k in fields} for s in stories]})
        return {"created": len(stories), "updated": 0, "failed": [], "items": []}


class Confluence:
    """Publishes the backlog as one page per project in a space; later publishes add a new page version."""

    def __init__(self, conn: Dict[str, Any], token: str):
        self.base = conn["base_url"].rstrip("/")  # Cloud: https://<site>.atlassian.net/wiki
        auth = ("%s:%s" % (conn["username"], token)) if conn["username"] else None
        self.headers = {"Authorization": "Basic " + base64.b64encode(auth.encode()).decode()} if auth else {"Authorization": "Bearer " + token}

    def test(self) -> str:
        data = _request("GET", self.base + "/rest/api/space?limit=1", self.headers)
        return "Connected: %d space(s) visible" % int(data.get("size", len(data.get("results", []))))

    def publish(self, space: str, title: str, body: str, page_id: Optional[str]) -> Tuple[str, str, bool]:
        content = {"type": "page", "title": title[:255], "space": {"key": space},
                   "body": {"storage": {"value": body, "representation": "storage"}}}
        if page_id:
            current = _request("GET", "%s/rest/api/content/%s?expand=version" % (self.base, page_id), self.headers)
            content.update(id=page_id, version={"number": int(current["version"]["number"]) + 1})
            data = _request("PUT", "%s/rest/api/content/%s" % (self.base, page_id), self.headers, content)
            created = False
        else:
            data = _request("POST", self.base + "/rest/api/content", self.headers, content)
            created = True
        link = self.base + ((data.get("_links") or {}).get("webui") or "/pages/viewpage.action?pageId=%s" % data["id"])
        return str(data["id"]), link, created


def publish_page(project_id: str, conn: Dict[str, Any], space: str, title: str, body: str) -> Dict[str, Any]:
    client = client_for(conn)
    row = db.one("SELECT external_key FROM external_links WHERE integration_id=%s AND target=%s AND item_type='page' AND item_ref=%s",
                 (conn["id"], space, project_id))
    try:
        page_id, url, created = client.publish(space, title, body, row["external_key"] if row else None)
    except TrackerError as exc:
        return {"created": 0, "updated": 0, "failed": [{"item": "Page: " + title, "error": str(exc)}], "items": []}
    db.execute("INSERT INTO external_links (id, project_id, integration_id, target, item_type, item_ref, external_key, external_url, updated) "
               "VALUES (%s,%s,%s,%s,'page',%s,%s,%s,%s) ON CONFLICT (integration_id, target, item_type, item_ref) "
               "DO UPDATE SET external_key=EXCLUDED.external_key, external_url=EXCLUDED.external_url, updated=EXCLUDED.updated",
               (db.new_id(), project_id, conn["id"], space, project_id, page_id, url, db.now()))
    return {"created": int(created), "updated": int(not created), "failed": [], "items": [{"page": title, "url": url}]}


TRACKERS: Dict[str, Callable[[Dict[str, Any], str], Any]] = {"jira": Jira, "azure": Azure, "github": GitHub,
                                                            "confluence": Confluence, "webhook": Webhook}


def client_for(conn: Dict[str, Any]) -> Any:
    token = settings.decrypt_secret(conn["token_enc"]) if conn["token_enc"] else ""
    if not token and conn["kind"] != "webhook":  # a webhook secret is optional
        raise TrackerError("No API token saved for this integration")
    return TRACKERS[conn["kind"]](conn, token)


def push(project_id: str, conn: Dict[str, Any], target: str, stories: List[Dict[str, Any]],
         progress: Callable[[str], None] = lambda m: None, out_of_time: Callable[[], bool] = lambda: False) -> Dict[str, Any]:
    tracker = client_for(conn)
    if hasattr(tracker, "deliver"):
        return tracker.deliver(project_id, target, stories)
    links = {(r["item_type"], r["item_ref"]): r["external_key"] for r in db.rows(
        "SELECT item_type, item_ref, external_key FROM external_links WHERE integration_id=%s AND target=%s AND project_id=%s",
        (conn["id"], target, project_id))}
    result: Dict[str, Any] = {"created": 0, "updated": 0, "failed": [], "items": [], "remaining": 0}

    def save_link(item_type: str, ref: str, key: str, url: str) -> None:
        db.execute("INSERT INTO external_links (id, project_id, integration_id, target, item_type, item_ref, external_key, external_url, updated) "
                   "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (integration_id, target, item_type, item_ref) "
                   "DO UPDATE SET external_key=EXCLUDED.external_key, external_url=EXCLUDED.external_url, updated=EXCLUDED.updated",
                   (db.new_id(), project_id, conn["id"], target, item_type, ref, key, url, db.now()))

    epic_keys: Dict[str, Optional[str]] = {}
    for epic in dict.fromkeys(s["epic"] for s in stories):
        existing = links.get(("epic", epic))
        try:
            key, url = tracker.upsert_epic(target, epic, existing)
            save_link("epic", epic, key, url)
            epic_keys[epic] = key
            result["updated" if existing else "created"] += 1
        except TrackerError as exc:
            epic_keys[epic] = None
            result["failed"].append({"item": "Epic: " + epic, "error": str(exc)})
    failed_epics = [f["error"] for f in result["failed"]]
    if epic_keys and not any(epic_keys.values()) and len(set(failed_epics)) == 1 and failed_epics[0].startswith(
            tuple("HTTP %d" % code for code in SYSTEMIC)):
        # Every epic failed with the same credentials/permission/URL error: trying each story would only repeat it.
        raise TrackerError(failed_epics[0])
    for i, s in enumerate(stories, 1):
        if out_of_time():
            result["remaining"] = len(stories) - i + 1  # pushing again continues: linked stories are updated, not duplicated
            break
        progress("Pushing %d of %d" % (i, len(stories)))
        existing = links.get(("story", s["id"]))
        try:
            key, url = tracker.upsert_story(target, s, epic_keys.get(s["epic"]), existing)
            save_link("story", s["id"], key, url)
            result["updated" if existing else "created"] += 1
            result["items"].append({"story": s["key"], "external_key": key, "url": url})
        except TrackerError as exc:
            result["failed"].append({"item": s["key"], "error": str(exc)})
    return result


# ---- admin CRUD ----------------------------------------------------------

def _public(r: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: r[k] for k in ("id", "kind", "name", "base_url", "username", "target", "key_hint", "created")}
    out.update(kind_label=KINDS.get(r["kind"], r["kind"]), has_token=bool(r["token_enc"]))
    return out


def get(iid: str) -> Dict[str, Any]:
    r = db.one("SELECT * FROM integrations WHERE id=%s", (iid,))
    if not r:
        raise HTTPException(404, "Integration not found")
    return r


@router.get("/api/integrations")
def list_integrations():
    return {"integrations": [_public(r) for r in db.rows("SELECT * FROM integrations ORDER BY created")], "kinds": KINDS}


def _save(body: Dict[str, Any], iid: Optional[str]) -> Dict[str, Any]:
    kind = str(body.get("kind") or "")
    name = str(body.get("name") or "").strip()[:80]
    base_url = str(body.get("base_url") or "").strip().rstrip("/")[:300]
    username = str(body.get("username") or "").strip()[:120]
    target = str(body.get("target") or "").strip()[:120]
    token = str(body.get("token") or "").strip()
    if kind not in KINDS:
        raise HTTPException(400, "Unknown integration type")
    if not name:
        raise HTTPException(400, "Name is required")
    if not _URL.match(base_url):
        raise HTTPException(400, "Base URL must be https://, e.g. https://your-site.atlassian.net or https://dev.azure.com/your-org")
    current = get(iid) if iid else None
    enc = current["token_enc"] if current else None
    hint = current["key_hint"] if current else ""
    if token:
        enc, hint = settings.encrypt_secret(token), "…" + token[-4:]
    if iid:
        db.execute("UPDATE integrations SET kind=%s, name=%s, base_url=%s, username=%s, target=%s, token_enc=%s, key_hint=%s WHERE id=%s",
                   (kind, name, base_url, username, target, enc, hint, iid))
    else:
        iid = db.new_id()
        db.execute("INSERT INTO integrations (id, kind, name, base_url, username, target, token_enc, key_hint, created) "
                   "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", (iid, kind, name, base_url, username, target, enc, hint, db.now()))
    return _public(get(iid))


@router.post("/api/admin/integrations", status_code=201)
def create_integration(body: Dict[str, Any] = Body(...)):
    return _save(body, None)


@router.put("/api/admin/integrations/{iid}")
def update_integration(iid: str, body: Dict[str, Any] = Body(...)):
    return _save(body, iid)


@router.delete("/api/admin/integrations/{iid}", status_code=204)
def delete_integration(iid: str):
    get(iid)
    db.execute("DELETE FROM integrations WHERE id=%s", (iid,))


@router.post("/api/admin/integrations/{iid}/test")
def test_integration(iid: str):
    try:
        return {"ok": True, "message": client_for(get(iid)).test()}
    except (TrackerError, settings.SettingsError) as exc:
        return {"ok": False, "error": str(exc)}
