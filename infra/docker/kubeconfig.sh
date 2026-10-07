#!/usr/bin/env bash
# Writes the kubeconfig the backend container uses to reach the Kind cluster.
#
# `~/.kube/config` points at 127.0.0.1:<port>, which inside a container is
# the container itself. `--internal` addresses the API server by its
# container name instead, reachable because compose attaches the backend
# to Kind's `kind` Docker network. The file holds cluster-admin
# credentials (Kind's default), so it's gitignored.
set -euo pipefail
cluster="${KIND_CLUSTER:-kube-troubleshoot}"
out="$(dirname "$0")/kubeconfig"
kind get kubeconfig --internal --name "$cluster" > "$out"
# Readable by the container's non-root user.
chmod 644 "$out"
echo "wrote $out for Kind cluster '$cluster'"
