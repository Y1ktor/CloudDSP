#!/usr/bin/env bash
# Build and publish the local PostgreSQL-outbox dispatcher through this path:
#
#   reviewed source + hash-locked Python dependencies -> Linux/ARM64 image
#       -> CloudDSP's dedicated k3d registry
#
# This script deliberately does not deploy the image. A later Deployment task
# will copy its immutable digest into a manifest, never the readable tag.

set -euo pipefail

# Resolve source locations from the script instead of the caller's current
# directory. The variables contain only repository paths and public registry
# names; no Secret, database URI, or RabbitMQ credential is accepted here.
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/../lib/paths.sh"
readonly DISPATCHER_DIRECTORY="${KUBERNETES_DIRECTORY}/services/dispatcher"
readonly DOCKERFILE="${DISPATCHER_DIRECTORY}/Dockerfile"
readonly REQUIREMENTS_LOCK="${DISPATCHER_DIRECTORY}/requirements.lock"

# k3d owns this registry as a Docker container alongside the local cluster; it
# is not a Kubernetes Service. The tag is human-readable build provenance only.
# A future workload must use the digest printed after `docker push` instead.
readonly REGISTRY_HOST="clouddsp-registry.localhost:5001"
# This tag identifies the source capability packaged by this build. The image
# still starts the existing Demucs-only runtime by default; a future generic
# Deployment will deliberately override that entrypoint. Keeping the runtime
# choice in its manifest prevents this image refresh from widening the current
# dispatcher unexpectedly.
readonly IMAGE_NAME="${REGISTRY_HOST}/dispatcher:0.2.0-generic-outbox-dispatcher"
readonly TARGET_PLATFORM="linux/arm64"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/images/build-dispatcher-image.sh

Builds the local PostgreSQL-outbox dispatcher for linux/arm64, runs its isolated
Python tests while building, pushes it to the CloudDSP k3d registry, and prints
the immutable registry digest and Docker's local uncompressed image size.

It creates/pushes an OCI image only. It does not apply a Deployment, Pod,
Service, Ingress, Secret, RabbitMQ topology/message, or PostgreSQL change.
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

  # Listing the exact files makes the image boundary visible and produces an
  # understandable local error before Docker begins an expensive build.
  local required_file
  for required_file in \
    "${DOCKERFILE}" \
    "${REQUIREMENTS_LOCK}" \
    "${DISPATCHER_DIRECTORY}/app/__init__.py" \
    "${DISPATCHER_DIRECTORY}/app/amqp_publisher.py" \
    "${DISPATCHER_DIRECTORY}/app/dispatch_dispatchable_once.py" \
    "${DISPATCHER_DIRECTORY}/app/dispatch_once.py" \
    "${DISPATCHER_DIRECTORY}/app/dispatchable_outbox_request.py" \
    "${DISPATCHER_DIRECTORY}/app/downstream_outbox_request.py" \
    "${DISPATCHER_DIRECTORY}/app/dispatcher_generic_runtime.py" \
    "${DISPATCHER_DIRECTORY}/app/dispatcher_runtime.py" \
    "${DISPATCHER_DIRECTORY}/app/outbox_lease.py" \
    "${DISPATCHER_DIRECTORY}/app/postgresql.py" \
    "${DISPATCHER_DIRECTORY}/tests/test_amqp_publisher.py" \
    "${DISPATCHER_DIRECTORY}/tests/test_dispatch_dispatchable_once.py" \
    "${DISPATCHER_DIRECTORY}/tests/test_dispatch_once.py" \
    "${DISPATCHER_DIRECTORY}/tests/test_dispatchable_outbox_request.py" \
    "${DISPATCHER_DIRECTORY}/tests/test_downstream_outbox_request.py" \
    "${DISPATCHER_DIRECTORY}/tests/test_dispatcher_generic_runtime.py" \
    "${DISPATCHER_DIRECTORY}/tests/test_dispatcher_runtime.py" \
    "${DISPATCHER_DIRECTORY}/tests/test_outbox_lease.py" \
    "${DISPATCHER_DIRECTORY}/tests/test_postgresql.py"; do
    if [[ ! -f "${required_file}" ]]; then
      printf 'Required dispatcher build input is missing: %s\n' "${required_file}" >&2
      exit 1
    fi
  done

  # Building is useful only if the current k3d profile has the declared
  # registry to receive the image. This read-only check protects against
  # accidentally pushing a teaching-project image to another registry.
  if ! k3d registry list "${REGISTRY_HOST%%:*}" --no-headers | awk -v name="${REGISTRY_HOST%%:*}" '$1 == name { found = 1 } END { exit !found }'; then
    printf 'Local registry %q does not exist. Create the clouddsp-local cluster first.\n' "${REGISTRY_HOST%%:*}" >&2
    exit 1
  fi
}

human_size() {
  # Docker exposes uncompressed layer bytes. Show both that exact value and a
  # binary-unit rendering so future dependency/image-growth comparisons work.
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

  # The present k3d nodes are ARM64. Naming the platform prevents an Apple Mac
  # from publishing an amd64-only image its local K3s nodes cannot execute.
  # Generated provenance is disabled so identical local builds yield stable
  # OCI output; SBOM/provenance policy remains a separate supply-chain task.
  docker build \
    --platform "${TARGET_PLATFORM}" \
    --provenance=false \
    --file "${DOCKERFILE}" \
    --tag "${IMAGE_NAME}" \
    "${DISPATCHER_DIRECTORY}"

  docker push "${IMAGE_NAME}"

  local local_size_bytes repository_digest
  local_size_bytes="$(docker image inspect --format '{{.Size}}' "${IMAGE_NAME}")"
  repository_digest="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "${IMAGE_NAME}" | awk -v repository="${REGISTRY_HOST}/dispatcher@" 'index($0, repository) == 1 { print; exit }')"

  if [[ -z "${repository_digest}" ]]; then
    printf 'Image push completed but Docker did not report a repository digest for %s\n' "${IMAGE_NAME}" >&2
    exit 1
  fi

  printf 'Dispatcher image pushed: %s\n' "${IMAGE_NAME}"
  printf 'Immutable dispatcher reference: %s\n' "${repository_digest}"
  printf 'Local Docker image size: %s bytes (%s, uncompressed layers)\n' "${local_size_bytes}" "$(human_size "${local_size_bytes}")"
}

main "$@"
