#!/usr/bin/env bash
# Build and publish the isolated Basic Pitch worker smoke-test client:
#
#   fixed PostgreSQL functions + exact-key MinIO identity
#       -> Linux/ARM64 OCI image -> CloudDSP's local k3d registry
#
# The script builds/pushes only an image. It does not create the smoke Job,
# upload the controlled WAV, create a PostgreSQL event, publish/consume an AMQP
# message, run a model, or change any running Kubernetes workload.

set -euo pipefail

# Resolve repository paths from this script's own directory, making the command
# safe to run from the workspace root or another working directory. None of
# these values is a runtime credential or a mutable test coordinate.
readonly SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly KUBERNETES_DIRECTORY="$(cd -- "${SCRIPT_DIRECTORY}/.." && pwd)"
readonly CLIENT_DIRECTORY="${KUBERNETES_DIRECTORY}/tests/basic-pitch-worker-smoke/client"
readonly DOCKERFILE="${CLIENT_DIRECTORY}/Dockerfile"
readonly REQUIREMENTS_LOCK="${CLIENT_DIRECTORY}/requirements.lock"

# This readable tag is only build provenance. A later Job must copy the
# immutable digest printed after push into images.lock.yaml; Kubernetes must
# never execute this mutable tag directly.
readonly REGISTRY_HOST="clouddsp-registry.localhost:5001"
readonly IMAGE_REPOSITORY="${REGISTRY_HOST}/basic-pitch-worker-smoke-client"
readonly IMAGE_NAME="${IMAGE_REPOSITORY}:0.1.1-parent-finalizer-fixture"
readonly TARGET_PLATFORM="linux/arm64"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/build-basic-pitch-worker-smoke-client-image.sh

Builds the isolated Basic Pitch worker smoke client for linux/arm64, runs its
nine fake-client unit tests during the Docker build, pushes it to the local
k3d registry, and prints its immutable digest and Docker image size.

It does not create a Kubernetes Job, upload a MinIO object, create a database
event, publish/consume RabbitMQ messages, or alter a running workload.
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

  # Check the deliberately small Docker build context before Docker begins.
  # This produces an actionable missing-file message instead of a COPY error.
  local required_file
  for required_file in \
    "${DOCKERFILE}" \
    "${REQUIREMENTS_LOCK}" \
    "${CLIENT_DIRECTORY}/basic_pitch_worker_smoke.py" \
    "${CLIENT_DIRECTORY}/test_basic_pitch_worker_smoke.py"; do
    if [[ ! -f "${required_file}" ]]; then
      printf 'Required Basic Pitch worker smoke build input is missing: %s\n' "${required_file}" >&2
      exit 1
    fi
  done

  # k3d's registry is a Docker container instead of a Kubernetes Service.
  # Confirming the exact registry name keeps a build from pushing elsewhere.
  if ! k3d registry list "${REGISTRY_HOST%%:*}" --no-headers | awk -v name="${REGISTRY_HOST%%:*}" '$1 == name { found = 1 } END { exit !found }'; then
    printf 'Local registry %q does not exist. Create the clouddsp-local cluster first.\n' "${REGISTRY_HOST%%:*}" >&2
    exit 1
  fi
}

human_size() {
  # Docker reports uncompressed layer bytes. Print the exact value plus a
  # binary-unit form so later builds can be compared without rounding away data.
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

  # The active k3d nodes are Linux/ARM64. Naming the target prevents a local
  # amd64-only build from being pushed for nodes that cannot schedule it. Docker
  # provenance is disabled to keep source-identical local builds comparable;
  # formal provenance/SBOM generation is a separate supply-chain task.
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

  printf 'Basic Pitch worker smoke image pushed: %s\n' "${IMAGE_NAME}"
  printf 'Immutable Basic Pitch worker smoke reference: %s\n' "${repository_digest}"
  printf 'Local Docker image size: %s bytes (%s, uncompressed layers)\n' "${local_size_bytes}" "$(human_size "${local_size_bytes}")"
}

main "$@"
