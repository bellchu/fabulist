"""A run whose SSH tunnel dropped cannot clean up after itself, so the next run sweeps what it left: only its own temporary namespaces, and only old ones."""
import os
import subprocess
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sweep(tmp_path, names, extra_env=None):
    log = tmp_path / "deleted.txt"
    stub = tmp_path / "kubectl"
    stub.write_text('#!/bin/bash\ncase "$1" in\n  get) printf "%s\\n" ' + " ".join('"%s"' % n for n in names) + ';;\n  delete) shift; echo "$@" >> "' + str(log) + '";;\nesac\n')
    stub.chmod(0o755)
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ["PATH"], **(extra_env or {}))
    subprocess.run(["bash", "-c", "source %s/scripts/lib-cluster.sh && sweep_stale_namespaces" % REPO], check=True, env=env)
    return log.read_text().split() if log.exists() else []


def test_only_old_temporary_namespaces_of_ours_are_deleted(tmp_path):
    now = int(time.time())
    old, fresh = now - 4 * 3600, now - 600
    deleted = _sweep(tmp_path, [
        "fabulist", "kube-system", "fabulist-ci-%d-111" % old, "fabulist-ui-%d-222" % old, "fabulist-load-%d-333" % fresh,
        "fabulist-build-abc", "someone-else-ci-%d-1" % old, "fabulist-ci-notatimestamp-1"])
    assert sorted(x for x in deleted if x.startswith("fabulist")) == sorted(["fabulist-ci-%d-111" % old, "fabulist-ui-%d-222" % old])
    assert "fabulist" not in deleted and "fabulist-build-abc" not in deleted and "kube-system" not in deleted


def test_the_age_limit_can_be_changed(tmp_path):
    now = int(time.time())
    name = "fabulist-py-%d-9" % (now - 600)
    assert _sweep(tmp_path, [name]) == []
    assert name in _sweep(tmp_path, [name], {"STALE_NAMESPACE_SECONDS": "60"})
