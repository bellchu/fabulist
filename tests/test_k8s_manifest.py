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


def test_deploy_waits_for_running_ai_runs_before_replacing_the_server():
    deploy = open(os.path.join(os.path.dirname(APP_PATH), "..", "..", "scripts", "deploy.sh")).read()
    assert deploy.index("scripts/wait-for-runs.sh") < deploy.index("deploy/k8s/app.yaml | kubectl apply"), "the wait must come before the new image is applied"


def _wait_for_runs(tmp_path, counts, minutes, env_minutes=None, name="run"):
    """Run scripts/wait-for-runs.sh with a stub kubectl that reports the given sequence of running-run counts, and a sleep that does not sleep."""
    import subprocess
    stubs = tmp_path / name / "bin"
    stubs.mkdir(parents=True)
    state = tmp_path / name / "calls"
    state.write_text("0")
    (stubs / "kubectl").write_text(
        "#!/bin/bash\n"
        '[ "$3" = "get" ] && exit 0\n'  # `kubectl -n NS get deploy/fabulist`: the app exists
        "n=$(cat %s); echo $((n+1)) > %s\n"
        "counts=(%s)\n"
        'i=$n; [ "$i" -ge "${#counts[@]}" ] && i=$((${#counts[@]}-1))\n'
        'echo "${counts[$i]}"\n' % (state, state, " ".join(str(c) for c in counts)))
    (stubs / "sleep").write_text("#!/bin/bash\nexit 0\n")
    for f in stubs.iterdir():
        f.chmod(0o755)
    script = os.path.join(os.path.dirname(APP_PATH), "..", "..", "scripts", "wait-for-runs.sh")
    env = {"PATH": "%s:/usr/bin:/bin" % stubs}
    if env_minutes is not None:
        env["DEPLOY_WAIT_FOR_RUNS"] = str(env_minutes)
    return subprocess.run(["bash", script, "fabulist"] + ([str(minutes)] if minutes is not None else []), capture_output=True, text=True, env=env, timeout=30)


def test_the_wait_for_runs_returns_as_soon_as_the_runs_finish(tmp_path):
    r = _wait_for_runs(tmp_path, [2, 1, 0, 0], 15)
    assert r.returncode == 0 and "waiting up to 15 minutes" in r.stdout and "No AI runs are executing any more" in r.stdout and not r.stderr, (r.stdout, r.stderr)


def test_the_wait_for_runs_gives_up_after_the_time_and_warns_without_failing_the_deploy(tmp_path):
    r = _wait_for_runs(tmp_path, [3], 1)  # always 3 running; one minute = three 20 s waits in the stub
    assert r.returncode == 0 and "WARNING: 3 AI run(s) still executing after 1 minutes" in r.stderr, (r.stdout, r.stderr)


def test_the_wait_for_runs_can_be_skipped_and_does_nothing_when_idle(tmp_path):
    skipped = _wait_for_runs(tmp_path, [5], None, env_minutes=0)
    assert skipped.returncode == 0 and skipped.stdout == "" and skipped.stderr == "", "DEPLOY_WAIT_FOR_RUNS=0 does not even look"
    idle = _wait_for_runs(tmp_path, [0], 15, name="idle")
    assert idle.returncode == 0 and idle.stdout == "" and idle.stderr == "", "nothing running: no message, no wait"


def test_every_environment_setting_is_documented_in_the_readme():
    import re
    root = os.path.join(os.path.dirname(APP_PATH), "..", "..")
    sources = "".join(open(os.path.join(root, "app", f)).read() for f in ("config.py", "db.py", "sso.py", "llm.py"))
    names = set(re.findall(r'(?:environ\.get|_int|_bool|getenv)\(\s*"([A-Z][A-Z0-9_]+)"', sources))
    readme = open(os.path.join(root, "README.md")).read()
    assert len(names) > 30, "the extractor found the settings"
    assert sorted(n for n in names if n not in readme) == [], "settings the README does not mention"


def test_every_cluster_script_checks_the_cluster_first_and_says_what_is_wrong(tmp_path):
    """The scripts that run on the cluster fail at once, with the reason, when it cannot be reached (a dropped SSH tunnel looked like a
    websocket error halfway through a test run)."""
    import glob
    import subprocess
    root = os.path.join(os.path.dirname(APP_PATH), "..", "..")
    stubs = tmp_path / "bin"
    stubs.mkdir()
    (stubs / "kubectl").write_text("#!/bin/bash\necho 'The connection to the server 127.0.0.1:16443 was refused' >&2\nexit 1\n")
    (stubs / "kubectl").chmod(0o755)
    scripts = sorted(glob.glob(os.path.join(root, "scripts", "*-k8s.sh")))
    assert len(scripts) >= 5, scripts
    for script in scripts:
        r = subprocess.run(["bash", script] + (["x.py"] if script.endswith("run-py-k8s.sh") else []), capture_output=True, text=True, timeout=60,
                           env={"PATH": "%s:/usr/bin:/bin" % stubs, "HOME": str(tmp_path)}, cwd=root)
        assert r.returncode == 2 and "cannot reach the Kubernetes cluster" in r.stderr and "SSH tunnel" in r.stderr, (os.path.basename(script), r.returncode, r.stderr[-300:])


def test_a_deploy_does_not_drop_requests():
    # the old pod keeps serving until the new one is ready, and lingers a few seconds after leaving the endpoints
    assert "maxUnavailable: 0" in APP and "maxSurge: 1" in APP
    assert "preStop" in APP and "sleep" in APP


def test_a_database_outage_does_not_restart_the_app():
    live = APP.split("livenessProbe:")[1].split("resources:")[0]
    ready = APP.split("readinessProbe:")[1].split("livenessProbe:")[0]
    assert "/livez" in live and "/healthz" in ready
