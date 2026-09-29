#!/usr/bin/env bash
# Explicitly erase the dedicated CloudDSP image registry. This is separate
# from ordinary cluster cleanup because digest-pinned images need to survive
# cluster recreation. The fixed names and cluster-absence guard prevent this
# command from breaking an active CloudDSP workload or another local project.
set -euo pipefail

readonly CLUSTER_NAME="clouddsp-local"
readonly REGISTRY_NAME="clouddsp-registry.localhost"
readonly RETENTION_NETWORK="clouddsp-registry-hold"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/purge-registry.sh --confirm

Deletes the dedicated CloudDSP local image registry after the CloudDSP cluster
has been removed. All images stored only in that registry are lost.
USAGE
}

if [[ "$#" -eq 1 && ( "$1" == "-h" || "$1" == "--help" || "$1" == "help" ) ]]; then
  usage
  exit 0
fi
if [[ "$#" -ne 1 || "$1" != "--confirm" ]]; then
  usage >&2
  exit 2
fi

for command_name in docker k3d; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    printf 'Required command is unavailable: %s\n' "${command_name}" >&2
    exit 1
  fi
done
if ! docker info >/dev/null 2>&1; then
  printf 'Docker daemon is not reachable.\n' >&2
  exit 1
fi

cluster_exists() {
  local listing
  listing="$(k3d cluster list --no-headers)" || {
    printf 'Could not inspect the CloudDSP cluster; registry purge stopped.\n' >&2
    exit 1
  }
  printf '%s\n' "${listing}" |
    awk -v name="${CLUSTER_NAME}" '$1 == name { found = 1 } END { exit !found }'
}

registry_exists() {
  local listing
  listing="$(k3d registry list --no-headers)" || {
    printf 'Could not inspect the CloudDSP registry; purge stopped.\n' >&2
    exit 1
  }
  printf '%s\n' "${listing}" |
    awk -v name="${REGISTRY_NAME}" '$1 == name { found = 1 } END { exit !found }'
}

if cluster_exists; then
  printf 'CloudDSP cluster still exists; run deploy-local.sh cleanup first.\n' >&2
  exit 1
fi
registry_volume=""
if registry_exists; then
  # k3d removes anonymous volumes, but a registry migrated from an older
  # cluster may use a named mount. Capture only its /var/lib/registry volume
  # before deleting the container so purge erases the stored images as well.
  registry_volume="$(docker inspect "${REGISTRY_NAME}" \
    --format '{{range .Mounts}}{{if eq .Destination "/var/lib/registry"}}{{if eq .Type "volume"}}{{.Name}}{{end}}{{end}}{{end}}')"
  k3d registry delete "${REGISTRY_NAME}"
fi
if registry_exists; then
  printf 'Registry purge did not finish: CloudDSP registry still exists.\n' >&2
  exit 1
fi
if [[ -n "${registry_volume}" ]] && docker volume inspect "${registry_volume}" >/dev/null 2>&1; then
  docker volume rm "${registry_volume}" >/dev/null
fi
if docker network inspect "${RETENTION_NETWORK}" >/dev/null 2>&1; then
  owner="$(docker network inspect "${RETENTION_NETWORK}" \
    --format '{{index .Labels "clouddsp.io/owner"}}')"
  if [[ "${owner}" != "registry-hold" ]]; then
    printf 'Retention network has unexpected owner; leaving it untouched.\n' >&2
    exit 1
  fi
  # The network is part of the retained registry lifecycle, not the cluster.
  # Docker refuses removal if an unrelated container has joined it.
  docker network rm "${RETENTION_NETWORK}" >/dev/null
fi
printf 'CloudDSP local registry purge completed.\n'
