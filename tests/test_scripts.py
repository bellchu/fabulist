"""Lint for the shell scripts that run on the cluster."""
import glob
import os
import re

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")


def test_a_kubectl_exec_fed_by_a_heredoc_passes_stdin_on():
    # Without -i, kubectl drops the heredoc: the program reads nothing, does nothing and exits 0 (the restore drill's checks once passed that way).
    bad = []
    for path in glob.glob(os.path.join(SCRIPTS, "*.sh")):
        for n, line in enumerate(open(path), 1):
            if re.search(r"kubectl .*\bexec\b", line) and "<<" in line and not re.search(r"\bexec\b[^|]*\s-[a-z]*i\b", line):
                bad.append("%s:%d" % (os.path.basename(path), n))
    assert not bad, bad


def test_dependency_updates_are_proposed_and_no_github_actions_are_used():
    root = os.path.dirname(SCRIPTS)
    config = open(os.path.join(root, ".github", "dependabot.yml")).read()
    assert "package-ecosystem: pip" in config and "package-ecosystem: docker" in config
    assert not os.path.exists(os.path.join(root, ".github", "workflows")), "no budget for GitHub Actions: checks run on the cluster"
