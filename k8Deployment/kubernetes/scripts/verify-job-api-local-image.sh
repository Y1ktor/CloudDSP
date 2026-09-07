#!/usr/bin/env bash
# Verify the Job API image before Kubernetes creates a Pod from it:
#
#   local Docker container (no network, no Secret) -> /healthz is 200
#                                               -> /readyz is 503 configuration
#   `docker exec` requests its loopback-only HTTP server; no Mac port is opened.
#
# This is intentionally not a PostgreSQL integration test. A Docker container
# outside Kubernetes cannot use the cluster's Service DNS or mount its Secret.
# The later Deployment task will verify `/readyz` against the actual database.

set -euo pipefail

readonly REGISTRY_HOST="clouddsp-registry.localhost:5001"
readonly IMAGE_REFERENCE="${REGISTRY_HOST}/job-api@sha256:fc2c3b5d42760193eb10ed7f693300567eb9227125c0f86d1d9072b68694ce3c"
# Checking this non-sensitive build milestone prevents a local tag or stale
# digest from passing the liveness test while actually running older API code.
readonly EXPECTED_SERVICE_VERSION="0.0.6-create-direct-upload"
readonly CONTAINER_NAME="clouddsp-job-api-local-image-smoke"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/verify-job-api-local-image.sh

Runs the immutable local Job API image as a short-lived Docker container without
network access or database credentials. It verifies:

  GET /healthz -> HTTP 200
  GET /readyz  -> HTTP 503 with reason database_configuration

The temporary container is always stopped and removed when the script exits.
No Kubernetes resource, Secret, database row, or registry image is changed.
USAGE
}

require_command() {
  local command_name="$1"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    printf 'Required command is unavailable: %s\n' "${command_name}" >&2
    exit 1
  fi
}

cleanup() {
  # `--rm` removes the stopped container. Stopping by exact dedicated name is
  # safe even after a failed curl assertion, and its error is ignored if Docker
  # already removed an unexpectedly exited container.
  docker stop "${CONTAINER_NAME}" >/dev/null 2>&1 || true
}

require_prerequisites() {
  require_command docker
  if ! docker info >/dev/null 2>&1; then
    printf 'Docker is installed but its daemon is not reachable. Start Docker Desktop and retry.\n' >&2
    exit 1
  fi
  if ! docker image inspect "${IMAGE_REFERENCE}" >/dev/null 2>&1; then
    printf 'The immutable Job API image is unavailable locally: %s\n' "${IMAGE_REFERENCE}" >&2
    printf 'Run build-job-api-image.sh first.\n' >&2
    exit 1
  fi
  if docker container inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
    printf 'A prior smoke-test container still exists: %s\n' "${CONTAINER_NAME}" >&2
    printf 'Inspect or remove it deliberately before starting another test.\n' >&2
    exit 1
  fi
}

wait_for_health_endpoint() {
  local attempt
  # Uvicorn normally starts immediately. This short bounded retry accommodates
  # Docker Desktop scheduling without turning a startup failure into a hang.
  for attempt in {1..40}; do
    if docker exec "${CONTAINER_NAME}" python -c \
      'from urllib.request import urlopen; urlopen("http://127.0.0.1:8080/healthz", timeout=1).read()' \
      >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

http_response() {
  # Execute a request inside the container's network namespace. `HTTPError` is
  # treated as an ordinary response because `/readyz` deliberately returns 503
  # in this no-Secret test. The first output line is the status; the second is
  # FastAPI's single-line JSON body, making shell assertions straightforward.
  docker exec "${CONTAINER_NAME}" python -c '
from urllib.error import HTTPError
from urllib.request import urlopen
import sys

try:
    response = urlopen("http://127.0.0.1:8080" + sys.argv[1], timeout=2)
except HTTPError as error:
    response = error

print(response.status)
print(response.read().decode("utf-8"))
' "$1"
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
  trap cleanup EXIT

  # `--network none` proves `/healthz` has no hidden dependency and makes a
  # database connection impossible. Requests are executed inside the container
  # through its own loopback interface, so no host/LAN port needs publishing. A
  # read-only root verifies the runtime image does not write app files at start.
  docker run \
    --detach \
    --rm \
    --name "${CONTAINER_NAME}" \
    --network none \
    --read-only \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=16m \
    "${IMAGE_REFERENCE}" >/dev/null

  if ! wait_for_health_endpoint; then
    printf 'Job API /healthz did not become reachable within 10 seconds.\n' >&2
    docker logs "${CONTAINER_NAME}" >&2 || true
    exit 1
  fi

  local health_body_and_status health_body health_status
  health_body_and_status="$(http_response /healthz)"
  health_status="${health_body_and_status%%$'\n'*}"
  health_body="${health_body_and_status#*$'\n'}"
  if [[ "${health_status}" != "200" || "${health_body}" != *'"status":"ok"'* || "${health_body}" != *"\"version\":\"${EXPECTED_SERVICE_VERSION}\""* ]]; then
    printf 'Job API /healthz returned an unexpected result, HTTP %s: %s\n' "${health_status}" "${health_body}" >&2
    exit 1
  fi

  # HTTP 503 is the successful expected result when this intentionally isolated
  # container has no Kubernetes Secret.
  local readiness_body_and_status readiness_body readiness_status
  readiness_body_and_status="$(http_response /readyz)"
  readiness_status="${readiness_body_and_status%%$'\n'*}"
  readiness_body="${readiness_body_and_status#*$'\n'}"
  if [[ "${readiness_status}" != "503" || "${readiness_body}" != *'"reason":"database_configuration"'* ]]; then
    printf 'Job API /readyz should report an unconfigured database, got HTTP %s: %s\n' "${readiness_status}" "${readiness_body}" >&2
    exit 1
  fi

  printf 'Job API image smoke test passed\n'
  printf '  /healthz: HTTP 200 (process is alive)\n'
  printf '  /readyz:  HTTP 503 database_configuration (expected without a Secret)\n'
}

main "$@"
