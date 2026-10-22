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
