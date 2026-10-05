#!/usr/bin/env bash
# Build and publish the local upload-intake worker through this exact path:
#
#   reviewed source + hash-locked Python dependencies -> Linux/ARM64 image
#       -> CloudDSP's dedicated k3d registry
#
# The script deliberately creates no Kubernetes workload. A later, separate
# Deployment task will use the immutable digest printed below, never the
# readable build-provenance tag.

set -euo pipefail

# Resolve every path from this script rather than the caller's current
# directory, so the command is reproducible from the repository root or any
# terminal location. These values contain no password, URI, or user data.
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/../lib/paths.sh"
readonly INTAKE_DIRECTORY="${KUBERNETES_DIRECTORY}/services/upload-intake"
readonly DOCKERFILE="${INTAKE_DIRECTORY}/Dockerfile"
readonly REQUIREMENTS_LOCK="${INTAKE_DIRECTORY}/requirements.lock"

# This is a Docker registry container that k3d manages beside the cluster; it
# is not a Kubernetes Service. The tag is readable provenance only. A future
# manifest must use the post-push `repository@sha256:...` immutable reference.
readonly REGISTRY_HOST="clouddsp-registry.localhost:5001"
# The readable tag records this reviewed source milestone for humans and local
# registry browsing. Kubernetes still receives only the digest printed after
# push, so later retagging cannot alter a running or reviewed Pod.
readonly IMAGE_NAME="${REGISTRY_HOST}/upload-intake:0.2.1-form-encoded-keys"
readonly TARGET_PLATFORM="linux/arm64"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/images/build-upload-intake-image.sh

Builds the local upload-intake worker for linux/arm64, runs its isolated Python
tests while building, pushes it to the CloudDSP local k3d registry, and prints
the immutable registry digest plus Docker's local uncompressed image size.

It creates/pushes an OCI image only. It does not apply a Deployment, Pod,
Service, Ingress, Secret, RabbitMQ message, MinIO object, or database change.
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

  # Listing explicit files documents the image's small source boundary and
  # gives an understandable error before Docker starts an expensive build.
  local required_file
  for required_file in \
    "${DOCKERFILE}" \
    "${REQUIREMENTS_LOCK}" \
    "${INTAKE_DIRECTORY}/app/__init__.py" \
    "${INTAKE_DIRECTORY}/app/amqp_consumer.py" \
    "${INTAKE_DIRECTORY}/app/consumer_runtime.py" \
    "${INTAKE_DIRECTORY}/app/database_transition.py" \
    "${INTAKE_DIRECTORY}/app/message_handler.py" \
    "${INTAKE_DIRECTORY}/app/minio_event.py" \
    "${INTAKE_DIRECTORY}/app/object_storage.py" \
    "${INTAKE_DIRECTORY}/app/postgresql.py" \
    "${INTAKE_DIRECTORY}/app/reconcile_one.py" \
    "${INTAKE_DIRECTORY}/tests/test_amqp_consumer.py" \
    "${INTAKE_DIRECTORY}/tests/test_consumer_runtime.py" \
    "${INTAKE_DIRECTORY}/tests/test_database_transition.py" \
    "${INTAKE_DIRECTORY}/tests/test_message_handler.py" \
    "${INTAKE_DIRECTORY}/tests/test_minio_event.py" \
    "${INTAKE_DIRECTORY}/tests/test_object_storage.py" \
    "${INTAKE_DIRECTORY}/tests/test_postgresql.py" \
    "${INTAKE_DIRECTORY}/tests/test_reconcile_one.py"; do
    if [[ ! -f "${required_file}" ]]; then
      printf 'Required upload-intake build input is missing: %s\n' "${required_file}" >&2
      exit 1
    fi
  done

  # Avoid pushing a costly local image where K3s cannot later pull it. The
  # exact registry name comes from cluster/k3d.yaml, not user shell defaults.
  if ! k3d registry list "${REGISTRY_HOST%%:*}" --no-headers | awk -v name="${REGISTRY_HOST%%:*}" '$1 == name { found = 1 } END { exit !found }'; then
    printf 'Local registry %q does not exist. Create the clouddsp-local cluster first.\n' "${REGISTRY_HOST%%:*}" >&2
    exit 1
  fi
}

human_size() {
  # Docker returns uncompressed layer bytes. Show both exact bytes and a
  # binary-unit rendering so later dependency/image-growth comparisons are easy.
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

  # Current k3d nodes are ARM64. Declaring the platform prevents a Mac from
  # accidentally publishing an amd64-only worker that its own K3s nodes cannot
  # schedule. Disabling generated provenance makes repeat local source builds
  # yield stable OCI output; SBOM/provenance is a separate supply-chain task.
  docker build \
    --platform "${TARGET_PLATFORM}" \
    --provenance=false \
    --file "${DOCKERFILE}" \
    --tag "${IMAGE_NAME}" \
    "${INTAKE_DIRECTORY}"

  docker push "${IMAGE_NAME}"

  local local_size_bytes repository_digest
  local_size_bytes="$(docker image inspect --format '{{.Size}}' "${IMAGE_NAME}")"
  repository_digest="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "${IMAGE_NAME}" | awk -v repository="${REGISTRY_HOST}/upload-intake@" 'index($0, repository) == 1 { print; exit }')"

  if [[ -z "${repository_digest}" ]]; then
    printf 'Image push completed but Docker did not report a repository digest for %s\n' "${IMAGE_NAME}" >&2
    exit 1
  fi

  printf 'Upload-intake image pushed: %s\n' "${IMAGE_NAME}"
  printf 'Immutable upload-intake reference: %s\n' "${repository_digest}"
  printf 'Local Docker image size: %s bytes (%s, uncompressed layers)\n' "${local_size_bytes}" "$(human_size "${local_size_bytes}")"
}

main "$@"
