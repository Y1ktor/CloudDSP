#!/usr/bin/env bash
# Build and publish the disposable MinIO-to-RabbitMQ transport-test client:
#
#   unit-tested source + hashed Pika wheel -> Linux/ARM64 image
#       -> CloudDSP's local k3d registry
#
# The image has no MinIO, RabbitMQ, database, or Kubernetes credential. The
# later smoke Job injects its limited runtime identities only when it runs.

set -euo pipefail

# Resolve these paths from the script location so the command is independent of
# the caller's current directory.
readonly SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly KUBERNETES_DIRECTORY="$(cd -- "${SCRIPT_DIRECTORY}/.." && pwd)"
readonly CLIENT_DIRECTORY="${KUBERNETES_DIRECTORY}/services/rabbitmq/minio-source-intake-smoke-client"
readonly DOCKERFILE="${CLIENT_DIRECTORY}/Dockerfile"
readonly REQUIREMENTS_FILE="${CLIENT_DIRECTORY}/requirements.txt"

# The human-readable tag describes this source milestone only. The later Job
# must use the immutable repository digest printed after the push and recorded
# in images.lock.yaml, never this mutable tag.
readonly REGISTRY_HOST="clouddsp-registry.localhost:5001"
readonly IMAGE_NAME="${REGISTRY_HOST}/minio-source-intake-smoke-client:1.0.0-pika-1.4.4"
readonly TARGET_PLATFORM="linux/arm64"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/build-minio-source-intake-smoke-client-image.sh

Builds the MinIO-to-RabbitMQ transport-test client for linux/arm64, pushes it
to the local k3d registry, and prints its immutable digest and local image size.

It does not create a Kubernetes Job, upload an object, configure MinIO, or
consume a RabbitMQ message.
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
    "${REQUIREMENTS_FILE}" \
    "${CLIENT_DIRECTORY}/minio_source_intake_event.py" \
    "${CLIENT_DIRECTORY}/minio_source_intake_smoke_client.py"; do
    if [[ ! -f "${required_file}" ]]; then
      printf 'Required image build input is missing: %s\n' "${required_file}" >&2
      exit 1
    fi
  done

  # The registry is a k3d-managed Docker container, not a Kubernetes Service.
  # Confirm its declared name before spending time on an image with nowhere to
  # publish it.
  if ! k3d registry list "${REGISTRY_HOST%%:*}" --no-headers | awk -v name="${REGISTRY_HOST%%:*}" '$1 == name { found = 1 } END { exit !found }'; then
    printf 'Local registry %q does not exist. Create the clouddsp-local cluster first.\n' "${REGISTRY_HOST%%:*}" >&2
    exit 1
  fi
}

human_size() {
  # Docker reports bytes. Print a readable binary-unit value alongside it.
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

  # The current k3d nodes are ARM64. State the platform explicitly so a Mac
  # never pushes an x86_64-only test image that its own nodes cannot schedule.
  # Disable generated provenance so identical local builds produce one stable
  # runtime digest for images.lock.yaml.
  docker build \
    --platform "${TARGET_PLATFORM}" \
    --provenance=false \
    --file "${DOCKERFILE}" \
    --tag "${IMAGE_NAME}" \
    "${CLIENT_DIRECTORY}"

  docker push "${IMAGE_NAME}"

  local local_size_bytes repository_digest
  local_size_bytes="$(docker image inspect --format '{{.Size}}' "${IMAGE_NAME}")"
  repository_digest="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "${IMAGE_NAME}" | awk -v repository="${REGISTRY_HOST}/minio-source-intake-smoke-client@" 'index($0, repository) == 1 { print; exit }')"

  if [[ -z "${repository_digest}" ]]; then
    printf 'Image push completed but Docker did not report a repository digest for %s\n' "${IMAGE_NAME}" >&2
    exit 1
  fi

  printf 'MinIO source-intake smoke-client image pushed: %s\n' "${IMAGE_NAME}"
  printf 'Immutable image reference: %s\n' "${repository_digest}"
  printf 'Local Docker image size: %s bytes (%s, uncompressed layers)\n' "${local_size_bytes}" "$(human_size "${local_size_bytes}")"
}

main "$@"
