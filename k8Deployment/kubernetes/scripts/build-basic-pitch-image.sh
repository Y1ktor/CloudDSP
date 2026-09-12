#!/usr/bin/env bash
# Build and publish the local Basic Pitch CPU worker through this path:
#
#   reviewed worker source + hash-locked Python dependencies -> Linux/ARM64 image
#       -> CloudDSP's dedicated k3d registry
#
# This script deliberately publishes an OCI image only. It never invokes
# kubectl, so a separate reviewed Deployment task must choose the digest it
# prints below before any Basic Pitch Pod can run.

set -euo pipefail

# Resolve repository locations from this script rather than the shell's current
# directory. These values identify build inputs only; runtime credentials stay
# in namespace-local Kubernetes Secrets and are never accepted by this script.
readonly SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly KUBERNETES_DIRECTORY="$(cd -- "${SCRIPT_DIRECTORY}/.." && pwd)"
readonly BASIC_PITCH_DIRECTORY="${KUBERNETES_DIRECTORY}/services/basic-pitch"
readonly DOCKERFILE="${BASIC_PITCH_DIRECTORY}/Dockerfile"
readonly REQUIREMENTS_LOCK="${BASIC_PITCH_DIRECTORY}/requirements.lock"

# k3d owns this registry as a Docker container beside the cluster, not as a
# Kubernetes Service. The readable tag explains the build's capability and is
# mutable; a future workload must use only the immutable digest printed after
# push and later recorded in images.lock.yaml.
readonly REGISTRY_HOST="clouddsp-registry.localhost:5001"
readonly IMAGE_REPOSITORY="${REGISTRY_HOST}/basic-pitch"
readonly IMAGE_NAME="${IMAGE_REPOSITORY}:0.1.0-cpu-worker-runtime"
readonly TARGET_PLATFORM="linux/arm64"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/build-basic-pitch-image.sh

Builds the Basic Pitch CPU worker for linux/arm64, runs its isolated Python
tests and model-presence checks during the Docker build, pushes it to the local
k3d registry, and prints its immutable digest and Docker image size.

It creates/pushes an OCI image only. It does not apply a Kubernetes Deployment,
Pod, Service, Ingress, Secret, database change, MinIO object, or RabbitMQ
message.
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

  # Fail before the potentially expensive TensorFlow build when a source input
  # was moved or omitted. Docker still receives the whole Basic Pitch directory
  # as its build context, allowing its validation stage to test every module.
  local required_path
  for required_path in \
    "${DOCKERFILE}" \
    "${REQUIREMENTS_LOCK}" \
    "${BASIC_PITCH_DIRECTORY}/app" \
    "${BASIC_PITCH_DIRECTORY}/app/worker_main.py" \
    "${BASIC_PITCH_DIRECTORY}/app/worker_entrypoint.py" \
    "${BASIC_PITCH_DIRECTORY}/app/worker_runtime.py" \
    "${BASIC_PITCH_DIRECTORY}/tests" \
    "${BASIC_PITCH_DIRECTORY}/tests/test_worker_main.py" \
    "${BASIC_PITCH_DIRECTORY}/tests/test_worker_entrypoint.py" \
    "${BASIC_PITCH_DIRECTORY}/tests/test_worker_runtime.py"; do
    if [[ ! -e "${required_path}" ]]; then
      printf 'Required Basic Pitch build input is missing: %s\n' "${required_path}" >&2
      exit 1
    fi
  done

  # Checking this exact local registry prevents a typo or unrelated Docker
  # configuration from sending a teaching-project image to another registry.
  if ! k3d registry list "${REGISTRY_HOST%%:*}" --no-headers | awk -v name="${REGISTRY_HOST%%:*}" '$1 == name { found = 1 } END { exit !found }'; then
    printf 'Local registry %q does not exist. Create the clouddsp-local cluster first.\n' "${REGISTRY_HOST%%:*}" >&2
    exit 1
  fi
}

human_size() {
  # Docker exposes uncompressed layer bytes. Keep that exact audit value while
  # also rendering binary units for readable future image-size comparisons.
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

  # The current k3d cluster runs Linux/ARM64 nodes. Declaring the target stops
  # Docker from publishing an amd64-only image that those nodes cannot start.
  # Build provenance is disabled so identical local rebuilds remain comparable;
  # an SBOM/provenance policy is intentionally a separate supply-chain task.
  docker build \
    --platform "${TARGET_PLATFORM}" \
    --provenance=false \
    --file "${DOCKERFILE}" \
    --tag "${IMAGE_NAME}" \
    "${BASIC_PITCH_DIRECTORY}"

  docker push "${IMAGE_NAME}"

  local local_size_bytes repository_digest
  local_size_bytes="$(docker image inspect --format '{{.Size}}' "${IMAGE_NAME}")"
  repository_digest="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "${IMAGE_NAME}" | awk -v repository="${IMAGE_REPOSITORY}@" 'index($0, repository) == 1 { print; exit }')"

  if [[ -z "${repository_digest}" ]]; then
    printf 'Image push completed but Docker did not report a repository digest for %s\n' "${IMAGE_NAME}" >&2
    exit 1
  fi

  printf 'Basic Pitch image pushed: %s\n' "${IMAGE_NAME}"
  printf 'Immutable Basic Pitch reference: %s\n' "${repository_digest}"
  printf 'Local Docker image size: %s bytes (%s, uncompressed layers)\n' "${local_size_bytes}" "$(human_size "${local_size_bytes}")"
}

main "$@"
