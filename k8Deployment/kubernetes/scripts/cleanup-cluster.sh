#!/usr/bin/env bash
# This script removes only the local resources created for the CloudDSP k3d
# profile.  It is deliberately separate from cluster.sh: creation/status is a
# routine operation, whereas cleanup destroys the Kubernetes nodes, workloads,
# local PersistentVolumeClaim data, and locally pushed CloudDSP images.

# Exit on a failed command, on an unset variable, and on a failed command in a
# pipeline.  Cleanup must stop and report an error rather than quietly leaving
# an ambiguous mixture of old cluster and registry resources behind.
set -euo pipefail

# These names must match the k3d cluster configuration.  They are fixed
# constants rather than arguments so this script cannot accidentally delete an
# unrelated k3d cluster or local registry owned by another project.
readonly CLUSTER_NAME="clouddsp-local"
readonly REGISTRY_NAME="clouddsp-registry.localhost"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/cleanup-cluster.sh --confirm

Deletes only the CloudDSP local k3d cluster and its dedicated local registry.
This removes cluster workloads, PVC-backed development data, and images stored
in clouddsp-registry.localhost. It does not delete unrelated Docker resources.
USAGE
}

require_command() {
  # Give a clear prerequisite error instead of a shell-dependent "command not
  # found" error later in the destructive workflow.
  local command_name="$1"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    printf 'Required command is unavailable: %s\n' "${command_name}" >&2
    exit 1
  fi
}

require_prerequisites() {
  require_command docker
  require_command k3d

  # k3d identifies and deletes its K3s node and registry containers through
  # Docker.  A daemon check makes a stopped Docker Desktop instance actionable
  # instead of treating it as proof that the cluster is already gone.
  if ! docker info >/dev/null 2>&1; then
    printf 'Docker is installed but its daemon is not reachable. Start Docker Desktop and retry.\n' >&2
    exit 1
  fi
}

cluster_exists() {
  # Match the first table column exactly, preventing a similarly named local
  # cluster (for example clouddsp-local-test) from becoming a deletion target.
  k3d cluster list "${CLUSTER_NAME}" --no-headers | awk -v name="${CLUSTER_NAME}" '$1 == name { found = 1 } END { exit !found }'
}

registry_exists() {
  # The registry is a separate Docker container managed by k3d.  Deleting a
  # cluster does not serve as a safe assumption that an explicitly named
  # registry was removed, so the script checks and deletes this exact one.
  k3d registry list "${REGISTRY_NAME}" --no-headers | awk -v name="${REGISTRY_NAME}" '$1 == name { found = 1 } END { exit !found }'
}

main() {
  # An explicit flag keeps the script non-interactive for repeatable automation
  # while still requiring an intentional acknowledgement of data loss.  Never
  # replace this with `--all`: a developer may run other k3d clusters locally.
  if [[ "$#" -eq 1 && ( "$1" == "-h" || "$1" == "--help" || "$1" == "help" ) ]]; then
    usage
    exit 0
  fi

  if [[ "$#" -ne 1 || "$1" != "--confirm" ]]; then
    usage >&2
    exit 2
  fi

  require_prerequisites

  if ! cluster_exists && ! registry_exists; then
    printf 'CloudDSP local cluster and registry are already absent; no changes made.\n'
    exit 0
  fi

  printf 'Deleting CloudDSP cluster %q and registry %q.\n' "${CLUSTER_NAME}" "${REGISTRY_NAME}"

  if cluster_exists; then
    # k3d removes the K3s server/agent containers, their Docker network, and
    # storage held inside the disposable local nodes.  Kubernetes PVCs backed
    # by the K3s local-path provisioner are therefore not recoverable after
    # this operation unless a separate backup task exported their data first.
    k3d cluster delete "${CLUSTER_NAME}"
  fi

  if registry_exists; then
    # The registry stores locally built images separately from the cluster's
    # Kubernetes objects.  Delete it only after the cluster so no running Pod
    # depends on an image service that is about to disappear.
    k3d registry delete "${REGISTRY_NAME}"
  fi

  # Confirm the exact targets are gone.  This is a postcondition check, not a
  # broad Docker cleanup; unrelated images, containers, networks, and clusters
  # remain outside this script's authority.
  if cluster_exists || registry_exists; then
    printf 'Cleanup did not finish: CloudDSP cluster or registry still exists.\n' >&2
    exit 1
  fi

  printf 'CloudDSP local cluster and registry cleanup completed.\n'
}

main "$@"
