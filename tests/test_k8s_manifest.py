"""The app pod stays hardened: a regression here would silently widen what a compromised process can do."""
import os

APP = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy", "k8s", "app.yaml")).read()


def test_app_pod_is_locked_down():
    for setting in ("runAsNonRoot: true", "allowPrivilegeEscalation: false", "readOnlyRootFilesystem: true", "capabilities: {drop: [ALL]}",
                    "seccompProfile: {type: RuntimeDefault}", "automountServiceAccountToken: false"):
        assert setting in APP, setting


def test_read_only_root_still_has_a_writable_tmp():
    # repository clones and git's config need somewhere to write; /tmp is an emptyDir and HOME points at it
    assert "emptyDir: {}" in APP and "mountPath: /tmp" in APP and "name: HOME\n              value: /tmp" in APP


def test_pod_gets_time_to_stop_runs_cleanly():
    assert "terminationGracePeriodSeconds: 60" in APP
