import subprocess

import pytest

from app import repos


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(repos, "_resolve", lambda host: ["140.82.112.3"])


def test_private_hosts_are_refused(monkeypatch):
    from app import config
    for host in ("https://localhost/x.git", "https://git.fabulist.svc/x.git", "https://kubernetes.default.svc.cluster.local/x.git"):
        with pytest.raises(repos.RepoError, match="internal"):
            repos.validate(host, "", "*")
    for addr in ("10.1.2.3", "127.0.0.1", "169.254.169.254", "192.168.1.5", "::1"):
        monkeypatch.setattr(repos, "_resolve", lambda host, a=addr: [a])
        with pytest.raises(repos.RepoError, match="private"):
            repos.validate("https://git.corp.example/x.git", "", "*")
    monkeypatch.setattr(config, "ALLOW_PRIVATE_REPO_HOSTS", True)
    repos.validate("https://git.corp.example/x.git", "", "*")


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


# ---- clone hardening, exercised with a fake `git` executable on PATH ---------

def _fake_git(tmp_path, monkeypatch, clone_body):
    """Put a `git` on PATH that records the environment it was started with and runs clone_body for `clone`."""
    import os
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "git"
    script.write_text('#!/bin/sh\nif [ "$1" = clone ]; then\n  env | grep ^GIT_CONFIG > "%s"\n  for last; do :; done\n  mkdir -p "$last"\n%s\n'
                      'else echo %s; fi\n' % (tmp_path / "env.txt", clone_body, "a" * 40))
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    return tmp_path / "env.txt"


def _config(env_file):
    lines = dict(l.split("=", 1) for l in env_file.read_text().splitlines())
    return {lines["GIT_CONFIG_KEY_%d" % i]: lines["GIT_CONFIG_VALUE_%d" % i] for i in range(int(lines["GIT_CONFIG_COUNT"]))}


def test_clone_is_pinned_to_the_checked_address_and_never_follows_redirects(tmp_path, monkeypatch):
    env_file = _fake_git(tmp_path, monkeypatch, "  :")
    monkeypatch.setattr(repos, "_resolve", lambda host: ["93.184.216.34", "2606:2800:220:1::1"])
    assert repos.fetch("https://git.example.com/org/brds.git", "", "tok", str(tmp_path / "dest")) == "a" * 40
    cfg = _config(env_file)
    # the connection uses the address that passed the private-network check, not a second DNS answer
    assert cfg["http.curloptResolve"] == "git.example.com:443:93.184.216.34"
    # a public host cannot bounce the clone to an internal one
    assert cfg["http.followRedirects"] == "false" and cfg["protocol.allow"] == "never"


def test_clone_refuses_a_private_resolution_even_when_it_changes_after_adding_the_repo(tmp_path, monkeypatch):
    env_file = _fake_git(tmp_path, monkeypatch, "  :")
    monkeypatch.setattr(repos, "_resolve", lambda host: ["10.0.0.7"])  # DNS now points inside the network
    with pytest.raises(repos.RepoError, match="private address"):
        repos.fetch("https://git.example.com/org/brds.git", "", "", str(tmp_path / "dest"))
    assert not env_file.exists(), "git must not even start"


def test_internal_git_hosts_are_not_pinned_when_an_admin_allows_them(tmp_path, monkeypatch):
    from app import config
    env_file = _fake_git(tmp_path, monkeypatch, "  :")
    monkeypatch.setattr(config, "ALLOW_PRIVATE_REPO_HOSTS", True)
    monkeypatch.setattr(repos, "_resolve", lambda host: ["10.0.0.7"])
    repos.fetch("https://git.corp.internal/org/brds.git", "", "", str(tmp_path / "dest"))
    assert "http.curloptResolve" not in _config(env_file)


def test_a_clone_that_grows_past_the_size_limit_is_killed(tmp_path, monkeypatch):
    from app import config
    # writes 1 MB every 0.3 s for up to 30 s: only the size cap can stop it quickly
    _fake_git(tmp_path, monkeypatch, '  i=0; while [ $i -lt 100 ]; do dd if=/dev/zero of="$last/f$i" bs=1048576 count=1 2>/dev/null; sleep 0.3; i=$((i+1)); done')
    monkeypatch.setattr(config, "MAX_REPO_MB", 2)
    monkeypatch.setattr(repos, "_resolve", lambda host: ["93.184.216.34"])
    import time
    started = time.monotonic()
    with pytest.raises(repos.RepoError, match="larger than the 2 MB limit"):
        repos.fetch("https://git.example.com/org/huge.git", "", "", str(tmp_path / "dest"))
    assert time.monotonic() - started < 10
    time.sleep(1)  # and it stays dead: nothing keeps writing
    written = len(list((tmp_path / "dest").iterdir()))
    time.sleep(1)
    assert len(list((tmp_path / "dest").iterdir())) == written and written < 30


def test_a_blocked_redirect_tells_the_user_what_to_do(tmp_path, monkeypatch):
    _fake_git(tmp_path, monkeypatch, "  echo \"fatal: unable to access 'https://git.example.com/old/name.git/': The requested URL returned error: 301\" >&2; exit 128")
    monkeypatch.setattr(repos, "_resolve", lambda host: ["93.184.216.34"])
    with pytest.raises(repos.RepoError, match="canonical https URL"):
        repos.fetch("https://git.example.com/old/name.git", "", "", str(tmp_path / "dest"))
