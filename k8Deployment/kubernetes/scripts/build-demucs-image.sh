#!/usr/bin/env bash
# Build and publish the local Demucs CPU worker through one reproducible path:
#
#   reviewed worker source + locked Python/model inputs -> Linux/ARM64 OCI image
#       -> CloudDSP's dedicated k3d registry
#
# The script never calls kubectl. A separate reviewed Deployment manifest must
# copy the immutable digest printed below before a running Pod can receive the
# repaired worker code.

set -euo pipefail

# Resolve repository paths from the script location rather than the caller's
# current directory. These are public build inputs; no Kubernetes Secret is
# read, accepted, printed, or included intentionally by this helper.
readonly SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly KUBERNETES_DIRECTORY="$(cd -- "${SCRIPT_DIRECTORY}/.." && pwd)"
readonly DEMUCS_DIRECTORY="${KUBERNETES_DIRECTORY}/services/demucs"
readonly DOCKERFILE="${DEMUCS_DIRECTORY}/Dockerfile"
readonly REQUIREMENTS_LOCK="${DEMUCS_DIRECTORY}/requirements.lock"
readonly MODEL_ARTIFACTS_LOCK="${DEMUCS_DIRECTORY}/model-artifacts.lock.yaml"

# k3d owns this registry as a Docker container adjacent to the cluster. The
# readable tag identifies this source milestone only; every workload must use
# the immutable repository digest printed after `docker push`.
readonly REGISTRY_HOST="clouddsp-registry.localhost:5001"
readonly IMAGE_REPOSITORY="${REGISTRY_HOST}/demucs"
# This readable tag identifies the terminal 12-minute deadline policy.
# Deployments still use the immutable digest printed after `docker push`.
readonly IMAGE_NAME="${IMAGE_REPOSITORY}:0.1.9-terminal-deadline"
readonly TARGET_PLATFORM="linux/arm64"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/build-demucs-image.sh

Builds the Demucs CPU worker for linux/arm64, runs its isolated Python tests,
FFprobe check, locked ML-runtime import check, and a real two-stem local CPU
inference check during the Docker build, pushes it to the local k3d registry,
and prints its immutable digest and Docker image size.

It creates/pushes an OCI image only. It does not apply a Deployment, Pod,
Service, Ingress, Secret, database change, MinIO object, or RabbitMQ message.
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

  # Check the files that the multi-stage Dockerfile needs before the expensive
  # CPU Torch/Demucs/model validation begins. Docker still receives the whole
  # service directory as context so tests can exercise all worker modules.
  local required_path
  for required_path in \
    "${DOCKERFILE}" \
    "${REQUIREMENTS_LOCK}" \
    "${MODEL_ARTIFACTS_LOCK}" \
    "${DEMUCS_DIRECTORY}/app" \
    "${DEMUCS_DIRECTORY}/app/worker_main.py" \
    "${DEMUCS_DIRECTORY}/app/worker_entrypoint.py" \
    "${DEMUCS_DIRECTORY}/app/task_lease.py" \
    "${DEMUCS_DIRECTORY}/tests" \
    "${DEMUCS_DIRECTORY}/tests/test_task_lease.py" \
    "${DEMUCS_DIRECTORY}/demucs-deployment.yaml"; do
    if [[ ! -e "${required_path}" ]]; then
      printf 'Required Demucs build input is missing: %s\n' "${required_path}" >&2
      exit 1
    fi
  done

  # A typo must never publish the learning-project worker to another registry.
  if ! k3d registry list "${REGISTRY_HOST%%:*}" --no-headers | awk -v name="${REGISTRY_HOST%%:*}" '$1 == name { found = 1 } END { exit !found }'; then
    printf 'Local registry %q does not exist. Create the clouddsp-local cluster first.\n' "${REGISTRY_HOST%%:*}" >&2
    exit 1
  fi
}

human_size() {
  # Docker's image size is uncompressed local layer bytes; retain both the
  # exact audit value and a readable binary-unit rendering.
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

  # The local k3d nodes are Linux/ARM64. Naming that platform prevents an
  # Apple-host build from publishing an incompatible AMD64-only worker. Docker
  # provenance is disabled so equal local source builds remain comparable;
  # formal provenance/SBOM generation remains a separate supply-chain task.
  docker build \
    --platform "${TARGET_PLATFORM}" \
    --provenance=false \
    --file "${DOCKERFILE}" \
    --tag "${IMAGE_NAME}" \
    "${DEMUCS_DIRECTORY}"

  docker push "${IMAGE_NAME}"

  local local_size_bytes repository_digest
  local_size_bytes="$(docker image inspect --format '{{.Size}}' "${IMAGE_NAME}")"
  repository_digest="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "${IMAGE_NAME}" | awk -v repository="${IMAGE_REPOSITORY}@" 'index($0, repository) == 1 { print; exit }')"

  if [[ -z "${repository_digest}" ]]; then
    printf 'Image push completed but Docker did not report a repository digest for %s\n' "${IMAGE_NAME}" >&2
    exit 1
  fi

  printf 'Demucs image pushed: %s\n' "${IMAGE_NAME}"
  printf 'Immutable Demucs reference: %s\n' "${repository_digest}"
  printf 'Local Docker image size: %s bytes (%s, uncompressed layers)\n' "${local_size_bytes}" "$(human_size "${local_size_bytes}")"
}

main "$@"
