#!/usr/bin/env bash
# Build and publish the disposable dispatcher handoff smoke-test client:
#
#   normal-path orchestrator + restricted verifier + locked dependencies
#       -> Linux/ARM64 OCI image -> CloudDSP's local k3d registry
#
# This script only creates/pushes an image. It does not create a Kubernetes
# Job, create a Keycloak identity, upload an object, read a queue, or mutate
# PostgreSQL, MinIO, RabbitMQ, or a running Deployment.

set -euo pipefail

# Resolve source paths from this script's location so the command works from
# the workspace root or any other directory. These variables hold repository
# paths and a local registry name only—never runtime Secret values.
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/../lib/paths.sh"
readonly CLIENT_DIRECTORY="${KUBERNETES_DIRECTORY}/tests/dispatcher-smoke/client"
readonly DOCKERFILE="${CLIENT_DIRECTORY}/Dockerfile"
readonly REQUIREMENTS_LOCK="${CLIENT_DIRECTORY}/requirements.lock"

# This tag is readable build provenance. A later Job must use the immutable
# digest printed after push and recorded under `images.dispatcher-smoke-client`
# in images.lock.yaml; it must never use this mutable tag.
readonly REGISTRY_HOST="clouddsp-registry.localhost:5001"
readonly IMAGE_NAME="${REGISTRY_HOST}/dispatcher-smoke-client:0.1.0-normal-path-orchestrator"
readonly TARGET_PLATFORM="linux/arm64"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/images/build-dispatcher-smoke-client-image.sh

Builds the disposable normal-upload dispatcher smoke client for linux/arm64,
runs its isolated Python tests during the Docker build, pushes it to the local
k3d registry, and prints its immutable digest and Docker image size.

It does not create a Kubernetes Job, authenticate to Keycloak, upload a MinIO
object, consume a RabbitMQ message, or change PostgreSQL state.
USAGE
}

require_command() {
  local command_name="$1"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    printf 'Required command is unavailable: %s\n' "${command_name}" >&2
    exit 1
  fi
}

require_prerequisites() {
  require_command docker
  require_command k3d

  if ! docker info >/dev/null 2>&1; then
    printf 'Docker is installed but its daemon is not reachable. Start Docker Desktop and retry.\n' >&2
    exit 1
  fi

  # Check each input before Docker begins. This makes the deliberately small
  # build boundary visible and gives an actionable error for a missing source.
  local required_file
  for required_file in \
    "${DOCKERFILE}" \
    "${REQUIREMENTS_LOCK}" \
    "${CLIENT_DIRECTORY}/dispatcher_smoke_client.py" \
    "${CLIENT_DIRECTORY}/dispatcher_smoke_orchestrator.py" \
    "${CLIENT_DIRECTORY}/test_dispatcher_smoke_client.py" \
    "${CLIENT_DIRECTORY}/test_dispatcher_smoke_orchestrator.py"; do
    if [[ ! -f "${required_file}" ]]; then
      printf 'Required dispatcher smoke-client build input is missing: %s\n' "${required_file}" >&2
      exit 1
    fi
  done

  # The registry is a k3d-managed Docker container, rather than a Kubernetes
  # Service. This read-only check prevents an accidental push outside the
  # intended local teaching cluster.
  if ! k3d registry list "${REGISTRY_HOST%%:*}" --no-headers | awk -v name="${REGISTRY_HOST%%:*}" '$1 == name { found = 1 } END { exit !found }'; then
    printf 'Local registry %q does not exist. Create the clouddsp-local cluster first.\n' "${REGISTRY_HOST%%:*}" >&2
    exit 1
  fi
}

human_size() {
  # Docker reports uncompressed bytes. Retain that exact metric and print a
  # binary-unit form for future dependency/image-growth comparisons.
  awk -v bytes="$1" 'BEGIN {
    split("B KiB MiB GiB TiB", units, " ")
    unit_index = 1
    value = bytes + 0
    while (value >= 1024 && unit_index < 5) { value /= 1024; unit_index += 1 }
    printf "%.2f %s", value, units[unit_index]
  }'
}

main() {
  if [[ "$#" -eq 1 && ( "$1" == "-h" || "$1" == "--help" || "$1" == "help" ) ]]; then
    usage
    exit 0
  fi
  if [[ "$#" -ne 0 ]]; then
    usage >&2
    exit 2
  fi

  require_prerequisites

  # Current k3d nodes are Linux/ARM64. Naming the target ensures an Apple Mac
  # cannot push an amd64-only image that its own local K3s nodes cannot run.
  # Disabling generated provenance keeps a source-identical local rebuild's
  # output stable; formal SBOM/provenance is a separate supply-chain task.
  docker build \
    --platform "${TARGET_PLATFORM}" \
    --provenance=false \
    --file "${DOCKERFILE}" \
    --tag "${IMAGE_NAME}" \
    "${CLIENT_DIRECTORY}"

  docker push "${IMAGE_NAME}"

  local local_size_bytes repository_digest
  local_size_bytes="$(docker image inspect --format '{{.Size}}' "${IMAGE_NAME}")"
  repository_digest="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "${IMAGE_NAME}" | awk -v repository="${REGISTRY_HOST}/dispatcher-smoke-client@" 'index($0, repository) == 1 { print; exit }')"

  if [[ -z "${repository_digest}" ]]; then
    printf 'Image push completed but Docker did not report a repository digest for %s\n' "${IMAGE_NAME}" >&2
    exit 1
  fi

  printf 'Dispatcher smoke-client image pushed: %s\n' "${IMAGE_NAME}"
  printf 'Immutable dispatcher smoke-client reference: %s\n' "${repository_digest}"
  printf 'Local Docker image size: %s bytes (%s, uncompressed layers)\n' "${local_size_bytes}" "$(human_size "${local_size_bytes}")"
}

main "$@"
