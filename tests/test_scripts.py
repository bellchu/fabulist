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


def test_every_cluster_script_is_documented():
    readme = open(os.path.join(os.path.dirname(SCRIPTS), "README.md")).read()
    missing = [os.path.basename(p) for p in glob.glob(os.path.join(SCRIPTS, "*-k8s.sh")) if os.path.basename(p) not in readme]
    assert missing == []


def test_the_ship_script_checks_before_it_publishes_and_never_merges_on_a_failed_check():
    text = open(os.path.join(SCRIPTS, "ship-k8s.sh")).read()
    assert "set -euo pipefail" in text
    check, push, merge, deploy = (text.index(x) for x in ("release-check-k8s.sh ${STAGES", "git push", "gh pr merge", "scripts/deploy.sh"))
    assert check < push < merge < deploy
    assert "|| true" not in text and "set +e" not in text, "a failed check must stop the script"
    assert '"$branch" != main' in text and "git status --porcelain" in text
