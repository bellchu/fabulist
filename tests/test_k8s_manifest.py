"""The app pod stays hardened: a regression here would silently widen what a compromised process can do."""
import os

APP_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy", "k8s", "app.yaml")
APP = open(APP_PATH).read()


def test_app_pod_is_locked_down():
    for setting in ("runAsNonRoot: true", "allowPrivilegeEscalation: false", "readOnlyRootFilesystem: true", "capabilities: {drop: [ALL]}",
                    "seccompProfile: {type: RuntimeDefault}", "automountServiceAccountToken: false"):
        assert setting in APP, setting


def test_read_only_root_still_has_a_writable_tmp():
    # repository clones and git's config need somewhere to write; /tmp is an emptyDir and HOME points at it
    assert "emptyDir: {}" in APP and "mountPath: /tmp" in APP and "name: HOME\n              value: /tmp" in APP


def test_pod_gets_time_to_stop_runs_cleanly():
    assert "terminationGracePeriodSeconds: 60" in APP


def test_only_the_ingress_controller_and_prometheus_reach_the_app_and_only_this_namespace_reaches_postgres():
    policy = open(os.path.join(os.path.dirname(APP_PATH), "networkpolicy.yaml")).read()
    assert "name: fabulist-app-ingress" in policy and "name: postgres-ingress" in policy
    assert "kubernetes.io/metadata.name: ingress" in policy and "kubernetes.io/metadata.name: observability" in policy
    assert "podSelector: {}" in policy  # postgres: same-namespace pods only (app, backup, audit)
    assert "networkpolicy.yaml" in open(os.path.join(os.path.dirname(APP_PATH), "..", "..", "scripts", "deploy.sh")).read()
