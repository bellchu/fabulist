"""scripts/deploy.sh must only ship a clean, current main: the image is built from the working tree."""
import os
import shutil
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]


def _run(cwd, *args, env=None):
    return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, env=dict(os.environ, **(env or {})))


@pytest.fixture()
def checkout(tmp_path):
    """A clone of a bare origin containing only scripts/deploy.sh, on main and up to date."""
    origin = tmp_path / "origin.git"
    _run(tmp_path, "git", "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    _run(tmp_path, "git", "clone", "-q", str(origin), str(work))
    (work / "scripts").mkdir()
    shutil.copy(os.path.join(REPO, "scripts", "deploy.sh"), work / "scripts" / "deploy.sh")
    _run(work, "git", "checkout", "-q", "-b", "main")
    _run(work, *GIT, "add", ".")
    _run(work, *GIT, "commit", "-qm", "init")
    _run(work, "git", "push", "-q", "-u", "origin", "main")
    return work


def deploy(work, **env):
    return _run(work, "bash", "scripts/deploy.sh", env=dict({"FABULIST_HOST": "x.example.com", "DEPLOY_SSH": "u@h", "PATH": "/usr/bin:/bin"}, **env))


def test_clean_up_to_date_main_passes_the_guard(checkout):
    r = deploy(checkout)
    assert "Refusing" not in r.stderr and "Kubernetes API is unreachable" in r.stderr  # got past the guard to the next stage


def test_uncommitted_changes_are_refused(checkout):
    (checkout / "scripts" / "deploy.sh").write_text((checkout / "scripts" / "deploy.sh").read_text() + "\n# edit\n")
    r = deploy(checkout)
    assert r.returncode != 0 and "Refusing to deploy: uncommitted changes" in r.stderr
    (checkout / "scripts" / "deploy.sh").write_text((checkout / "scripts" / "deploy.sh").read_text().replace("\n# edit\n", ""))
    (checkout / "stray.js").write_text("broken(")  # untracked files are shipped by the build too
    assert "uncommitted changes" in deploy(checkout).stderr


def test_other_branches_are_refused(checkout):
    _run(checkout, "git", "checkout", "-q", "-b", "feat/x")
    r = deploy(checkout)
    assert r.returncode != 0 and "Refusing to deploy from branch feat/x" in r.stderr


def test_a_main_that_is_behind_origin_is_refused(checkout, tmp_path):
    other = tmp_path / "other"
    _run(tmp_path, "git", "clone", "-q", str(tmp_path / "origin.git"), str(other))
    (other / "new.txt").write_text("merged since")
    _run(other, *GIT, "add", ".")
    _run(other, *GIT, "commit", "-qm", "newer")
    _run(other, "git", "push", "-q", "origin", "main")
    r = deploy(checkout)
    assert r.returncode != 0 and "not the latest origin/main" in r.stderr


def test_the_escape_hatch_skips_the_guard(checkout):
    (checkout / "stray.js").write_text("wip")
    r = deploy(checkout, ALLOW_DIRTY_DEPLOY="1")
    assert "Refusing" not in r.stderr


def test_a_deploy_ends_with_a_look_at_what_was_deployed():
    deploy = open(os.path.join(REPO, "scripts", "deploy.sh")).read()
    assert "scripts/live-smoke-k8s.sh" in deploy and "SKIP_SMOKE" in deploy and "rollout undo" in deploy
    assert deploy.index("Deployed ${IMAGE}") < deploy.index("live-smoke-k8s.sh"), "the smoke test runs after the rollout, never instead of it"


def test_the_readme_describes_what_the_deploy_script_really_does():
    deploy = open(os.path.join(REPO, "scripts", "deploy.sh")).read()
    for marker in ("scan-image-k8s.sh", "wait-for-runs.sh", "CREATE ROLE fabulist_app", "live-smoke-k8s.sh", "SKIP_IMAGE_SCAN", "SKIP_SMOKE", "DEPLOY_WAIT_FOR_RUNS", "restore-check.yaml"):
        assert marker in deploy, marker
    readme = open(os.path.join(REPO, "README.md")).read()
    for claim in ("SKIP_IMAGE_SCAN=1", "DEPLOY_WAIT_FOR_RUNS", "fabulist_app", "SKIP_SMOKE=1", "MIGRATION_DATABASE_URL", "ship-k8s.sh"):
        assert claim in readme, claim
