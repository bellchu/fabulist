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


def test_deploy_scans_the_image_before_rolling_it_out_and_has_an_override():
    deploy = open(os.path.join(os.path.dirname(APP_PATH), "..", "..", "scripts", "deploy.sh")).read()
    scan, apply = deploy.index("scan-image-k8s.sh"), deploy.index("deploy/k8s/app.yaml | kubectl apply")
    assert scan < apply, "the scan must run before the new image is applied"
    assert "SKIP_IMAGE_SCAN" in deploy


def test_the_image_limits_malloc_arenas_so_big_uploads_do_not_fragment_the_heap_into_the_memory_limit():
    dockerfile = open(os.path.join(os.path.dirname(APP_PATH), "..", "..", "Dockerfile")).read()
    assert "MALLOC_ARENA_MAX=2" in dockerfile


def test_giving_memory_back_never_raises():
    from app import workspace
    workspace._give_memory_back()


def test_deploy_waits_for_running_ai_runs_before_replacing_the_server_and_can_be_told_not_to():
    deploy = open(os.path.join(os.path.dirname(APP_PATH), "..", "..", "scripts", "deploy.sh")).read()
    wait, apply = deploy.index("DEPLOY_WAIT_FOR_RUNS"), deploy.index("deploy/k8s/app.yaml | kubectl apply")
    assert wait < apply, "the wait must come before the new image is applied (a restart fails the runs that are executing)"
    assert "status = 'running'" in deploy or "status='running'" in deploy
    assert "DEPLOY_WAIT_FOR_RUNS=0" in deploy, "and the wait is documented as skippable"
