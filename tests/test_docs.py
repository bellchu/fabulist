"""docs/ARCHITECTURE.md names files and settings by hand: they must exist, or the document is lying."""
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = open(os.path.join(REPO, "docs", "ARCHITECTURE.md")).read()


def test_the_files_it_names_exist():
    paths = set(re.findall(r"`((?:app|scripts|deploy)/[\w./-]+)`", DOC))
    assert len(paths) >= 12
    assert [p for p in sorted(paths) if not os.path.exists(os.path.join(REPO, p))] == []


def test_the_settings_it_names_exist():
    from app import config
    names = set(re.findall(r"`([A-Z][A-Z_]{4,})`", DOC)) - {"IF"}
    assert {"MAX_CONCURRENT_RUNS", "MAX_QUEUED_RUNS", "LLM_MAX_ATTEMPTS"} <= names
    assert [n for n in sorted(names) if not hasattr(config, n) and n not in ("SETTINGS_ENCRYPTION_KEY",)] == []


def test_the_readme_links_to_it():
    assert "docs/ARCHITECTURE.md" in open(os.path.join(REPO, "README.md")).read()


def test_the_api_cookbook_only_uses_routes_that_exist(client):
    """The README's automation example names routes by hand: every one must be in the API description."""
    readme = open(os.path.join(REPO, "README.md")).read()
    cookbook = readme.split("### Automation cookbook")[1].split("## Who can see a project")[0]
    paths = set(re.findall(r"\$HOST(/api/[\w/{}$]+)", cookbook.replace("$PID", "{pid}"))) | {"/api/projects/{pid}/requirements/import-file"}
    spec = client.get("/openapi.json").json()["paths"]
    normalised = {re.sub(r"\{[^}]+\}", "{}", k) for k in spec}
    assert len(paths) >= 6
    assert [p for p in sorted(paths) if re.sub(r"\{[^}]+\}", "{}", p) not in normalised] == []


def test_the_changelog_has_an_unreleased_section_on_top_and_dated_releases_below():
    import re
    text = open(os.path.join(REPO, "CHANGELOG.md")).read()
    headings = re.findall(r"^## (.+)$", text, re.M)
    assert headings[0] == "Unreleased" and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}( .*)?", h) for h in headings[1:]), headings


def test_every_test_the_acceptance_page_names_exists():
    text = open(os.path.join(REPO, "docs", "ACCEPTANCE.md")).read()
    paths = set(re.findall(r"`((?:tests|scripts)/[\w./-]+)`", text))
    assert len(paths) >= 30
    assert [p for p in sorted(paths) if not os.path.exists(os.path.join(REPO, p))] == []


def test_every_link_in_the_readme_contents_leads_somewhere():
    import re
    text = open(os.path.join(REPO, "README.md")).read()
    contents = re.search(r"\*\*Contents:\*\*(.*)", text).group(1)
    slugs = {re.sub(r"[^a-z0-9 -]", "", h.lower()).strip().replace(" ", "-") for h in re.findall(r"^#{2,3} (.+)$", text, re.M)}
    anchors = re.findall(r"\]\(#([\w-]+)\)", contents)
    assert len(anchors) >= 12
    assert [a for a in anchors if a not in slugs] == [], slugs
    files = re.findall(r"\]\(([\w./-]+\.md)\)", contents)
    assert [f for f in files if not os.path.exists(os.path.join(REPO, f))] == []
