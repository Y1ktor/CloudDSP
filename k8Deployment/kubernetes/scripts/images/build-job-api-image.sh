#!/usr/bin/env bash
# Build and publish the Kubernetes-local Job API image through this exact path:
#
#   API source + hashed Python lock -> Linux/ARM64 container image
#       -> CloudDSP's local k3d registry
#
# This script intentionally accepts no database password, Keycloak token, or
# other secret. The image is generic; Kubernetes will inject the Job API's
# database Secret only when a future Deployment creates a Pod.

set -euo pipefail

# Resolve paths from this script's own directory so the command behaves the
# same from the repository root, this directory, or another terminal location.
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/../lib/paths.sh"
readonly API_DIRECTORY="${KUBERNETES_DIRECTORY}/services/job-api"
readonly DOCKERFILE="${API_DIRECTORY}/Dockerfile"
readonly REQUIREMENTS_LOCK="${API_DIRECTORY}/requirements.lock"

# This registry is the dedicated Docker registry created by the clouddsp-local
# k3d profile. The tag is descriptive build provenance only; a later Deployment
# must use the immutable `repository@sha256:...` output recorded after push.
readonly REGISTRY_HOST="clouddsp-registry.localhost:5001"
# This readable tag records the source milestone being built. It is not a
# deployment input: after push, the script prints a sha256 digest and the image
# lock records that immutable reference for a later, separately approved
# rollout task.
readonly IMAGE_NAME="${REGISTRY_HOST}/job-api:0.0.10-score-upload"
readonly TARGET_PLATFORM="linux/arm64"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/images/build-job-api-image.sh

Builds the local CloudDSP Job API for linux/arm64, pushes it to the k3d local
registry, and prints its immutable registry digest and Docker image size.

It builds and pushes an image only. It does not create or update a Kubernetes
Deployment, Pod, Service, Ingress, database table, or browser-reachable API.
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

  local required_file
  for required_file in \
    "${DOCKERFILE}" \
    "${REQUIREMENTS_LOCK}" \
    "${API_DIRECTORY}/app/main.py" \
    "${API_DIRECTORY}/app/database.py" \
    "${API_DIRECTORY}/app/authentication.py" \
    "${API_DIRECTORY}/app/direct_upload_contract.py" \
    "${API_DIRECTORY}/app/object_storage.py" \
    "${API_DIRECTORY}/app/presigned_download.py" \
    "${API_DIRECTORY}/app/presigned_upload.py" \
    "${API_DIRECTORY}/app/score_upload_contract.py" \
    "${API_DIRECTORY}/tests/test_authentication.py" \
    "${API_DIRECTORY}/tests/test_jobs.py" \
    "${API_DIRECTORY}/tests/test_presigned_download.py" \
    "${API_DIRECTORY}/tests/test_job_finalization_migration.py" \
    "${API_DIRECTORY}/job-api-schema-migration-v007-job-finalization-configmap.yaml" \
    "${API_DIRECTORY}/job-api-schema-migration-v007-job-finalization-job.yaml" \
    "${API_DIRECTORY}/job-api-schema-migration-v008-partial-task-finalization-configmap.yaml" \
    "${API_DIRECTORY}/job-api-schema-migration-v008-partial-task-finalization-job.yaml" \
    "${API_DIRECTORY}/tests/test_direct_upload_contract.py" \
    "${API_DIRECTORY}/tests/test_job_creation.py" \
    "${API_DIRECTORY}/tests/test_job_creation_route.py" \
    "${API_DIRECTORY}/tests/test_presigned_upload.py" \
    "${API_DIRECTORY}/tests/test_score_upload.py"; do
    if [[ ! -f "${required_file}" ]]; then
      printf 'Required Job API build input is missing: %s\n' "${required_file}" >&2
      exit 1
    fi
  done

  # The registry is a Docker container managed by k3d, not a Kubernetes
  # Service. Confirm this exact declared registry before the script starts an
  # expensive build whose resulting image could otherwise have nowhere to go.
  if ! k3d registry list "${REGISTRY_HOST%%:*}" --no-headers | awk -v name="${REGISTRY_HOST%%:*}" '$1 == name { found = 1 } END { exit !found }'; then
    printf 'Local registry %q does not exist. Create the clouddsp-local cluster first.\n' "${REGISTRY_HOST%%:*}" >&2
    exit 1
  fi
}

human_size() {
  # Docker reports local image size in bytes. Display binary units alongside
  # the exact byte count so later image-size changes are easy to compare.
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

  # k3d nodes in this local profile are ARM64. Pinning the platform prevents an
  # accidental x86_64 image that the current nodes cannot schedule. Disabling
  # generated provenance keeps the pushed OCI image digest stable across
  # identical local builds; provenance/SBOM policy is a later deliberate task.
  docker build \
    --platform "${TARGET_PLATFORM}" \
    --provenance=false \
    --file "${DOCKERFILE}" \
    --tag "${IMAGE_NAME}" \
    "${API_DIRECTORY}"

  docker push "${IMAGE_NAME}"

  local local_size_bytes repository_digest
  local_size_bytes="$(docker image inspect --format '{{.Size}}' "${IMAGE_NAME}")"
  repository_digest="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "${IMAGE_NAME}" | awk -v repository="${REGISTRY_HOST}/job-api@" 'index($0, repository) == 1 { print; exit }')"

  if [[ -z "${repository_digest}" ]]; then
    printf 'Image push completed but Docker did not report a repository digest for %s\n' "${IMAGE_NAME}" >&2
    exit 1
  fi

  printf 'Job API image pushed: %s\n' "${IMAGE_NAME}"
  printf 'Immutable Job API reference: %s\n' "${repository_digest}"
  printf 'Local Docker image size: %s bytes (%s, uncompressed layers)\n' "${local_size_bytes}" "$(human_size "${local_size_bytes}")"
}

main "$@"
