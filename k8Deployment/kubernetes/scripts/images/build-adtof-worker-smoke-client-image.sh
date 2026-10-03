#!/usr/bin/env bash
# Build and publish the isolated ADTOF worker smoke-test client:
#
#   fixed PostgreSQL functions + exact-key MinIO identity
#       -> Linux/ARM64 OCI image -> CloudDSP's local k3d registry
#
# The script builds/pushes only an image. It does not create the smoke Job,
# upload the controlled WAV, create a PostgreSQL event, publish/consume an AMQP
# message, run an ADTOF model, or change any running Kubernetes workload.

set -euo pipefail

# Resolve every path from this script's own directory. This lets a learner run
# the documented command from the repository root or another directory without
# accidentally building a larger, credential-bearing Docker context.
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/../lib/paths.sh"
readonly CLIENT_DIRECTORY="${KUBERNETES_DIRECTORY}/tests/adtof-worker-smoke/client"
readonly DOCKERFILE="${CLIENT_DIRECTORY}/Dockerfile"
readonly REQUIREMENTS_LOCK="${CLIENT_DIRECTORY}/requirements.lock"

# This readable tag is build provenance only. A future Kubernetes Job must use
# the immutable digest printed after push and recorded in images.lock.yaml; it
# must never execute a tag because tags can be repointed to different bytes.
readonly REGISTRY_HOST="clouddsp-registry.localhost:5001"
readonly IMAGE_REPOSITORY="${REGISTRY_HOST}/adtof-worker-smoke-client"
# This revision recognizes only the database observer's exact marker for the
# intentionally incomplete parent fixture after a real ADTOF task succeeds.
# Kubernetes Jobs still use only the immutable digest printed after push.
readonly IMAGE_NAME="${IMAGE_REPOSITORY}:0.1.2-parent-finalizer-fixture"
readonly TARGET_PLATFORM="linux/arm64"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/images/build-adtof-worker-smoke-client-image.sh

Builds the isolated ADTOF worker smoke client for linux/arm64, runs its
fake-client unit tests during the Docker build, pushes it to the local k3d
registry, and prints the immutable digest and Docker image size.

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

  # Validate the deliberately narrow build context before Docker starts. The
  # loops include every runtime module and every offline test copied by the
  # Dockerfile, producing an actionable missing-file error rather than a later
  # opaque COPY failure.
  local required_file
  for required_file in "${DOCKERFILE}" "${REQUIREMENTS_LOCK}"; do
    if [[ ! -f "${required_file}" ]]; then
      printf 'Required ADTOF worker smoke build input is missing: %s\n' "${required_file}" >&2
      exit 1
    fi
  done
  for required_file in \
    "${CLIENT_DIRECTORY}"/adtof_worker_smoke_*.py \
    "${CLIENT_DIRECTORY}"/test_*.py; do
    if [[ ! -f "${required_file}" ]]; then
      printf 'Required ADTOF worker smoke build input is missing: %s\n' "${required_file}" >&2
      exit 1
    fi
  done

  # k3d's registry is a Docker container rather than a Kubernetes Service.
  # Confirming its exact name keeps an image push from silently targeting a
  # remote registry or an unrelated local project registry.
  if ! k3d registry list "${REGISTRY_HOST%%:*}" --no-headers | awk -v name="${REGISTRY_HOST%%:*}" '$1 == name { found = 1 } END { exit !found }'; then
    printf 'Local registry %q does not exist. Create the clouddsp-local cluster first.\n' "${REGISTRY_HOST%%:*}" >&2
    exit 1
  fi
}

human_size() {
  # Docker reports uncompressed layer bytes. Print the exact value plus a
  # binary-unit form so future local rebuilds are comparable without discarding
  # a meaningful size difference through premature rounding.
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

  # Current k3d nodes are Linux/ARM64. Explicitly naming the platform prevents
  # a Mac from pushing an amd64-only image that these nodes cannot schedule.
  # Build provenance is disabled solely to keep source-identical local rebuilds
  # comparable; formal SBOM/provenance generation is a separate supply-chain
  # task and must not be implied by this teaching-profile helper.
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

  printf 'ADTOF worker smoke image pushed: %s\n' "${IMAGE_NAME}"
  printf 'Immutable ADTOF worker smoke reference: %s\n' "${repository_digest}"
  printf 'Local Docker image size: %s bytes (%s, uncompressed layers)\n' "${local_size_bytes}" "$(human_size "${local_size_bytes}")"
}

main "$@"
