# Sourced by the scripts that run on the cluster. Fails at once, with the reason, when the cluster cannot be reached: a dropped SSH tunnel
# otherwise shows up halfway through a run as a websocket error.
require_cluster() {
  kubectl version --request-timeout=10s >/dev/null 2>&1 || {
    echo "Error: cannot reach the Kubernetes cluster. Is the SSH tunnel up (ssh -fN -L 16443:127.0.0.1:16443 user@node)? Check with: kubectl get nodes" >&2
    exit 2
  }
}
