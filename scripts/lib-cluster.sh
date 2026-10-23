# Sourced by the scripts that run on the cluster. Fails at once, with the reason, when the cluster cannot be reached: a dropped SSH tunnel
# otherwise shows up halfway through a run as a websocket error.
require_cluster() {
  kubectl version --request-timeout=10s >/dev/null 2>&1 || {
    echo "Error: cannot reach the Kubernetes cluster. Is the SSH tunnel up (ssh -fN -L 16443:127.0.0.1:16443 user@node)? Check with: kubectl get nodes" >&2
    exit 2
  }
  sweep_stale_namespaces
}

# A run whose SSH tunnel dropped cannot delete its temporary namespace (the pods then sit there for days), so every run clears the ones its predecessors left.
# Only our own run namespaces (fabulist-<kind>-<epoch>-<pid>) older than STALE_NAMESPACE_SECONDS (default 3 h; a load test takes under 30 min) are removed.
sweep_stale_namespaces() {
  local limit="${STALE_NAMESPACE_SECONDS:-10800}" now name stamp
  now=$(date +%s)
  for name in $(kubectl get namespaces -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null); do
    [[ "$name" =~ ^fabulist-(ci|ui|load|drill|sbom|scan|py|smoke)-([0-9]{10})-[0-9]+$ ]] || continue
    stamp="${BASH_REMATCH[2]}"
    [ $((now - stamp)) -gt "$limit" ] && kubectl delete namespace "$name" --wait=false >/dev/null 2>&1 || true
  done
}
