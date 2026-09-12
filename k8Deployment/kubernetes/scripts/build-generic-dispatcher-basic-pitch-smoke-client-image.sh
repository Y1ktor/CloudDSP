#!/usr/bin/env bash
# Build and publish the restricted generic-dispatcher Basic Pitch smoke client:
#
#   function-only PostgreSQL client + read/ack-only RabbitMQ client
#       -> Linux/ARM64 OCI image -> CloudDSP's local k3d registry
#
# This script creates and pushes an image only. It does not create a Kubernetes
# Job, synthetic PostgreSQL event, queue delivery, Keycloak identity, MinIO
# object, RabbitMQ topology change, or running Deployment.

set -euo pipefail

# Resolve paths from this script's location so the same command works from the
# workspace root or elsewhere. These values identify repository files and the
# local registry, never runtime Secret values.
readonly SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly KUBERNETES_DIRECTORY="$(cd -- "${SCRIPT_DIRECTORY}/.." && pwd)"
readonly CLIENT_DIRECTORY="${KUBERNETES_DIRECTORY}/tests/generic-dispatcher-smoke/client"
readonly DOCKERFILE="${CLIENT_DIRECTORY}/Dockerfile"
readonly REQUIREMENTS_LOCK="${CLIENT_DIRECTORY}/requirements.lock"

# This readable tag is build provenance only. A later one-shot Job must use
# the immutable digest printed after push and recorded in images.lock.yaml.
readonly REGISTRY_HOST="clouddsp-registry.localhost:5001"
readonly IMAGE_REPOSITORY="${REGISTRY_HOST}/generic-dispatcher-basic-pitch-smoke-client"
readonly IMAGE_NAME="${IMAGE_REPOSITORY}:0.1.0-restricted-routing"
readonly TARGET_PLATFORM="linux/arm64"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/build-generic-dispatcher-basic-pitch-smoke-client-image.sh

Builds the restricted generic-dispatcher Basic Pitch routing smoke client for
linux/arm64, runs its isolated Python tests during the Docker build, pushes it
to the local k3d registry, and prints its immutable digest and Docker size.

It does not create a Kubernetes Job, create a synthetic database event,
publish/consume a RabbitMQ message, or modify a running cluster workload.
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

  # Validate the intentionally narrow build context before Docker starts. This
  # makes missing source actionable rather than producing a vague COPY error.
  local required_file
  for required_file in \
    "${DOCKERFILE}" \
    "${REQUIREMENTS_LOCK}" \
    "${CLIENT_DIRECTORY}/generic_dispatcher_basic_pitch_smoke.py" \
    "${CLIENT_DIRECTORY}/test_generic_dispatcher_basic_pitch_smoke.py"; do
    if [[ ! -f "${required_file}" ]]; then
      printf 'Required generic dispatcher smoke build input is missing: %s\n' "${required_file}" >&2
      exit 1
    fi
  done

  # k3d's registry is a Docker container, not a Kubernetes Service. Checking
  # it here prevents an accidental push to an arbitrary configured registry.
  if ! k3d registry list "${REGISTRY_HOST%%:*}" --no-headers | awk -v name="${REGISTRY_HOST%%:*}" '$1 == name { found = 1 } END { exit !found }'; then
    printf 'Local registry %q does not exist. Create the clouddsp-local cluster first.\n' "${REGISTRY_HOST%%:*}" >&2
    exit 1
  fi
}

human_size() {
  # Docker reports uncompressed layer bytes. Keep that exact figure while also
  # printing binary units for later image-growth comparisons.
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

  # Current k3d nodes are Linux/ARM64. Naming the platform prevents an
  # amd64-only artifact from reaching a local node that cannot schedule it.
  # Generated provenance is disabled so source-identical local rebuilds remain
  # comparable; formal SBOM/provenance is a separate supply-chain task.
  docker build \
    --platform "${TARGET_PLATFORM}" \
    --provenance=false \
    --file "${DOCKERFILE}" \
    --tag "${IMAGE_NAME}" \
    "${CLIENT_DIRECTORY}"

  docker push "${IMAGE_NAME}"

  local local_size_bytes repository_digest
  local_size_bytes="$(docker image inspect --format '{{.Size}}' "${IMAGE_NAME}")"
  repository_digest="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "${IMAGE_NAME}" | awk -v repository="${IMAGE_REPOSITORY}@" 'index($0, repository) == 1 { print; exit }')"

  if [[ -z "${repository_digest}" ]]; then
    printf 'Image push completed but Docker did not report a repository digest for %s\n' "${IMAGE_NAME}" >&2
    exit 1
  fi

  printf 'Generic dispatcher Basic Pitch smoke image pushed: %s\n' "${IMAGE_NAME}"
  printf 'Immutable generic dispatcher Basic Pitch smoke reference: %s\n' "${repository_digest}"
  printf 'Local Docker image size: %s bytes (%s, uncompressed layers)\n' "${local_size_bytes}" "$(human_size "${local_size_bytes}")"
}

main "$@"
