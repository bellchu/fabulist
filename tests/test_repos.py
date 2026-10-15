import os
import subprocess

import pytest

from app import repos


def test_validate_accepts_https_only():
    repos.validate("https://github.com/org/brds.git", "main", "docs/*.md")
    for url in ("http://github.com/org/x.git", "file:///etc", "git@github.com:org/x.git", "ssh://host/x", "https://host", "https://h/x y"):
        with pytest.raises(repos.RepoError):
            repos.validate(url, "", "*")
    for branch in ("-x", "a..b", "a b"):
        with pytest.raises(repos.RepoError):
            repos.validate("https://github.com/org/x.git", branch, "*")
    with pytest.raises(repos.RepoError):
        repos.validate("https://github.com/org/x.git", "", "/etc/*")


def test_matches_multiple_patterns():
    assert repos.matches("docs/brd/order.md", "docs/*.md")
    assert repos.matches("a.pdf", "*.md, *.pdf") and not repos.matches("a.txt", "*.md,*.pdf")


def test_token_goes_to_env_not_argv():
    env = repos._git_env("secret-token")
    values = [env["GIT_CONFIG_VALUE_%d" % i] for i in range(int(env["GIT_CONFIG_COUNT"]))]
    assert any(v.startswith("Authorization: Basic ") for v in values)
    assert "never" in values and env["GIT_TERMINAL_PROMPT"] == "0"


def test_local_file_protocol_is_blocked(tmp_path):
    src = tmp_path / "src"
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    (src / "brd.md").write_text("# BRD")
    subprocess.run(["git", "-C", str(src), "-c", "user.email=t@t", "-c", "user.name=t", "add", "."], check=True)
    subprocess.run(["git", "-C", str(src), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"], check=True)
    with pytest.raises(repos.RepoError, match="git clone failed"):
        repos.fetch("file://" + str(src), "", "", str(tmp_path / "dest"))


def test_collect_filters_and_skips(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("# A\nThe system shall do A.")
    (tmp_path / "docs" / "b.exe").write_bytes(b"x")
    (tmp_path / "docs" / "empty.txt").write_text("   ")
    (tmp_path / "other.md").write_text("# Other")
    found, skipped = repos.collect(str(tmp_path), "docs/*")
    assert list(found) == ["docs/a.md"] and any("empty.txt" in s for s in skipped)
