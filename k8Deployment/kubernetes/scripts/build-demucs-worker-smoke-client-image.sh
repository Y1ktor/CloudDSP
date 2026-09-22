#!/usr/bin/env bash
# Build and publish the finite Demucs worker smoke-test client:
#
#   fixed PostgreSQL functions + exact-key MinIO identity
#       -> Linux/ARM64 OCI image -> CloudDSP local k3d registry
#
# This helper does not apply a Kubernetes Secret/Job, create a database event,
# upload audio, publish/consume RabbitMQ, or alter any running worker. It only
# produces the immutable image that the separate smoke Job will reference.

set -euo pipefail

# Resolve all files from this script's own location. A learner may run the
# command from any current directory without expanding Docker's build context
# to a Secret-bearing workspace or unrelated cloud source tree.
readonly SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly KUBERNETES_DIRECTORY="$(cd -- "${SCRIPT_DIRECTORY}/.." && pwd)"
readonly CLIENT_DIRECTORY="${KUBERNETES_DIRECTORY}/tests/demucs-worker-smoke/client"
readonly DOCKERFILE="${CLIENT_DIRECTORY}/Dockerfile"
readonly REQUIREMENTS_LOCK="${CLIENT_DIRECTORY}/requirements.lock"

# A readable tag documents what was built, but it is mutable. The script prints
# the OCI digest after push; only that digest may be placed in a Job manifest.
readonly REGISTRY_HOST="clouddsp-registry.localhost:5001"
readonly IMAGE_REPOSITORY="${REGISTRY_HOST}/demucs-worker-smoke-client"
readonly IMAGE_NAME="${IMAGE_REPOSITORY}:0.1.0-durable-demucs-stage"
readonly TARGET_PLATFORM="linux/arm64"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/build-demucs-worker-smoke-client-image.sh

Builds the isolated Demucs worker smoke client for linux/arm64, runs its
fake-client unit tests during the Docker build, pushes it to the local k3d
registry, and prints the immutable image reference and Docker image size.

It does not create a Kubernetes Job, upload a MinIO object, create a database
event, publish/consume RabbitMQ, run Demucs, or change a running workload.
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

  # Fail clearly before Docker starts if the intentionally small build context
  # is incomplete. No credentials belong in any of these build inputs.
  local required_file
  for required_file in \
    "${DOCKERFILE}" \
    "${REQUIREMENTS_LOCK}" \
    "${CLIENT_DIRECTORY}/demucs_worker_smoke.py" \
    "${CLIENT_DIRECTORY}/test_demucs_worker_smoke.py"; do
    if [[ ! -f "${required_file}" ]]; then
      printf 'Required Demucs worker smoke build input is missing: %s\n' "${required_file}" >&2
      exit 1
    fi
  done

  # k3d's registry is a Docker container, not a Kubernetes Service. Verify its
  # exact local name so this helper cannot silently push to an unrelated host.
  if ! k3d registry list "${REGISTRY_HOST%%:*}" --no-headers | awk -v name="${REGISTRY_HOST%%:*}" '$1 == name { found = 1 } END { exit !found }'; then
    printf 'Local registry %q does not exist. Create the clouddsp-local cluster first.\n' "${REGISTRY_HOST%%:*}" >&2
    exit 1
  fi
}

human_size() {
  # Docker reports uncompressed layer bytes. Preserve exact bytes plus a
  # binary-unit display so later local builds can be compared meaningfully.
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

  # The current local K3d nodes are Linux/ARM64. Targeting it explicitly
  # prevents an amd64-only Mac image from entering the local registry. Formal
  # SBOM/provenance generation is a separate supply-chain concern; disabling
  # Docker's mutable local provenance keeps comparable builds deterministic.
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

  printf 'Demucs worker smoke image pushed: %s\n' "${IMAGE_NAME}"
  printf 'Immutable Demucs worker smoke reference: %s\n' "${repository_digest}"
  printf 'Local Docker image size: %s bytes (%s, uncompressed layers)\n' "${local_size_bytes}" "$(human_size "${local_size_bytes}")"
}

main "$@"
