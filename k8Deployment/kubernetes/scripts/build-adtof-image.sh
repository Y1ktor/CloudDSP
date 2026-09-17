#!/usr/bin/env bash
# Build and publish the local ADTOF CPU worker through this narrow path:
#
#   reviewed worker source + hash-locked Python dependencies -> Linux/ARM64 image
#       -> CloudDSP's dedicated k3d registry
#
# This script publishes an OCI image only. A separate, explicit manifest update
# must copy the printed immutable digest into the Deployment before Kubernetes
# can run the new worker bytes.

set -euo pipefail

# Resolve inputs from this script rather than the caller's working directory.
# These paths identify public source and build files only; Secret values are
# never read, accepted, printed, or placed in the image build context.
readonly SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly KUBERNETES_DIRECTORY="$(cd -- "${SCRIPT_DIRECTORY}/.." && pwd)"
readonly ADTOF_DIRECTORY="${KUBERNETES_DIRECTORY}/services/adtof"
readonly DOCKERFILE="${ADTOF_DIRECTORY}/Dockerfile"
readonly REQUIREMENTS_LOCK="${ADTOF_DIRECTORY}/requirements.lock"

# The registry container belongs to k3d beside the cluster, rather than being
# a Kubernetes Service. The tag is readable build provenance only; workloads
# must use the immutable registry digest printed after the push.
readonly REGISTRY_HOST="clouddsp-registry.localhost:5001"
readonly IMAGE_REPOSITORY="${REGISTRY_HOST}/adtof"
# This tag documents the source milestone included in this build: the previous
# fsGroup/CWD/libsndfile runtime fixes plus the durable exhausted-third-lease
# terminal transition. It remains mutable convenience text; the printed OCI
# digest, not this label, is the only reference a Deployment may use.
readonly IMAGE_NAME="${IMAGE_REPOSITORY}:0.1.4-exhausted-lease-recovery"
readonly TARGET_PLATFORM="linux/arm64"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/build-adtof-image.sh

Builds the ADTOF CPU worker for linux/arm64, runs its isolated Python tests and
model-presence checks during the Docker build, pushes it to the local k3d
registry, and prints its immutable digest and Docker image size.

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

  # Verify all files copied by the Dockerfile or checked by its validation
  # tests before the potentially expensive inference dependency build starts.
  local required_path
  for required_path in \
    "${DOCKERFILE}" \
    "${REQUIREMENTS_LOCK}" \
    "${ADTOF_DIRECTORY}/app" \
    "${ADTOF_DIRECTORY}/app/adtof_cpu_process.py" \
    "${ADTOF_DIRECTORY}/app/worker_main.py" \
    "${ADTOF_DIRECTORY}/app/worker_entrypoint.py" \
    "${ADTOF_DIRECTORY}/tests" \
    "${ADTOF_DIRECTORY}/tests/test_adtof_cpu_process.py" \
    "${ADTOF_DIRECTORY}/adtof-database-bootstrap-job.yaml" \
    "${ADTOF_DIRECTORY}/adtof-deployment.yaml" \
    "${ADTOF_DIRECTORY}/adtof-minio-runtime-policy-smoke-job.yaml"; do
    if [[ ! -e "${required_path}" ]]; then
      printf 'Required ADTOF build input is missing: %s\n' "${required_path}" >&2
      exit 1
    fi
  done

  # A local training project must not accidentally publish to an arbitrary
  # Docker registry. Verify that this exact k3d-owned registry exists first.
  if ! k3d registry list "${REGISTRY_HOST%%:*}" --no-headers | awk -v name="${REGISTRY_HOST%%:*}" '$1 == name { found = 1 } END { exit !found }'; then
    printf 'Local registry %q does not exist. Create the clouddsp-local cluster first.\n' "${REGISTRY_HOST%%:*}" >&2
    exit 1
  fi
}

human_size() {
  # Docker reports uncompressed layer bytes; retain that exact audit number and
  # also show a binary-unit form for easier local image-size comparisons.
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

  # The current k3d nodes are Linux/ARM64. Declaring the platform prevents an
  # Apple host from publishing incompatible AMD64-only Python native wheels.
  # Docker provenance is disabled so equivalent local rebuilds remain
  # comparable; an SBOM/provenance policy is a future supply-chain task.
  docker build \
    --platform "${TARGET_PLATFORM}" \
    --provenance=false \
    --file "${DOCKERFILE}" \
    --tag "${IMAGE_NAME}" \
    "${ADTOF_DIRECTORY}"

  docker push "${IMAGE_NAME}"

  local local_size_bytes repository_digest
  local_size_bytes="$(docker image inspect --format '{{.Size}}' "${IMAGE_NAME}")"
  repository_digest="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "${IMAGE_NAME}" | awk -v repository="${IMAGE_REPOSITORY}@" 'index($0, repository) == 1 { print; exit }')"

  if [[ -z "${repository_digest}" ]]; then
    printf 'Image push completed but Docker did not report a repository digest for %s\n' "${IMAGE_NAME}" >&2
    exit 1
  fi

  printf 'ADTOF image pushed: %s\n' "${IMAGE_NAME}"
  printf 'Immutable ADTOF reference: %s\n' "${repository_digest}"
  printf 'Local Docker image size: %s bytes (%s, uncompressed layers)\n' "${local_size_bytes}" "$(human_size "${local_size_bytes}")"
}

main "$@"
