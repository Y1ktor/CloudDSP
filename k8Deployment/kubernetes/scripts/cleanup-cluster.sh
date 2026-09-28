#!/usr/bin/env bash
# This script removes only the CloudDSP k3d cluster. The dedicated registry is
# retained so a new cluster can pull the same digest-pinned images. Use the
# separate purge-registry.sh only when its stored images should also be erased.

# Exit on a failed command, on an unset variable, and on a failed command in a
# pipeline.  Cleanup must stop and report an error rather than quietly leaving
# an ambiguous mixture of old cluster resources behind.
set -euo pipefail

# These names must match the k3d cluster configuration.  They are fixed
# constants rather than arguments so this script cannot accidentally delete an
# unrelated k3d cluster or local registry owned by another project.
readonly CLUSTER_NAME="clouddsp-local"
readonly REGISTRY_NAME="clouddsp-registry.localhost"
readonly RETENTION_NETWORK="clouddsp-registry-hold"
readonly RETENTION_OWNER="registry-hold"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/cleanup-cluster.sh --confirm

Deletes only the CloudDSP local k3d cluster and its Kubernetes resources,
including PVC-backed development data. The dedicated image registry remains.
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
  local listing
  listing="$(k3d registry list "${REGISTRY_NAME}" --no-headers)" || {
    printf 'Could not inspect the CloudDSP registry; cleanup stopped.\n' >&2
    exit 1
  }
  printf '%s\n' "${listing}" |
    awk -v name="${REGISTRY_NAME}" '$1 == name { found = 1 } END { exit !found }'
}

ensure_registry_retention() {
  # k3d v5.9 deletes a registry connected only to its cluster network and
  # Docker's default bridge when deleting that cluster. A second, dedicated
  # network tells k3d that this registry has an independent lifecycle. k3d
  # disconnects the old cluster network while leaving the registry running.
  if ! docker network inspect "${RETENTION_NETWORK}" >/dev/null 2>&1; then
    docker network create --driver bridge \
      --label "clouddsp.io/owner=${RETENTION_OWNER}" \
      "${RETENTION_NETWORK}" >/dev/null
  fi
  local owner
  owner="$(docker network inspect "${RETENTION_NETWORK}" \
    --format '{{index .Labels "clouddsp.io/owner"}}')"
  if [[ "${owner}" != "${RETENTION_OWNER}" ]]; then
    printf 'Registry retention network exists but is not owned by CloudDSP.\n' >&2
    exit 1
  fi
  if ! docker network inspect "${RETENTION_NETWORK}" \
    --format '{{range .Containers}}{{.Name}}{{"\n"}}{{end}}' |
    awk -v name="${REGISTRY_NAME}" '$1 == name { found = 1 } END { exit !found }'; then
    docker network connect "${RETENTION_NETWORK}" "${REGISTRY_NAME}"
  fi
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

  if ! cluster_exists; then
    printf 'CloudDSP local cluster is already absent; registry left unchanged.\n'
    exit 0
  fi

  printf 'Deleting CloudDSP cluster %q; registry remains available.\n' "${CLUSTER_NAME}"

  local retained_registry=0
  if registry_exists; then
    ensure_registry_retention
    retained_registry=1
  fi

  # k3d removes K3s nodes, their network, and local-path PVC data. The
  # retention network above prevents k3d from deleting the image registry.
  k3d cluster delete "${CLUSTER_NAME}"

  # Confirm the exact targets are gone.  This is a postcondition check, not a
  # broad Docker cleanup; unrelated images, containers, networks, and clusters
  # remain outside this script's authority.
  if cluster_exists; then
    printf 'Cleanup did not finish: CloudDSP cluster still exists.\n' >&2
    exit 1
  fi
  if [[ "${retained_registry}" -eq 1 ]] && ! registry_exists; then
    printf 'Cleanup removed the registry unexpectedly; image retention failed.\n' >&2
    exit 1
  fi

  printf 'CloudDSP local cluster cleanup completed; registry retained.\n'
}

main "$@"
