#!/usr/bin/env bash
# This script proves the complete local HTTP path on the host's port 8080:
#
#   curl → k3d load balancer → K3s ServiceLB → Traefik → Service → Pod
#
# It pushes a harmless BusyBox HTTP image to the dedicated local registry,
# applies the versioned routing smoke resources, waits for the Deployment, and
# verifies the exact response through routing-smoke.localhost:8080. Resources
# remain deployed afterward for learning and inspection; the documented
# manifest-based cleanup command removes only these test resources.

# Fail on command errors, unset variables, and failed pipeline stages so an
# unavailable registry, failed rollout, or incorrect HTTP response is visible.
set -euo pipefail

# Resolve paths from the script location so invocation works from any directory.
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/../lib/paths.sh"
readonly ROUTING_MANIFEST="${KUBERNETES_DIRECTORY}/tests/routing-smoke/http-routing-smoke.yaml"

# These constants mirror the k3d configuration and versioned manifest. Fixed
# values keep the smoke test restricted to CloudDSP's local cluster and avoid
# accidentally deploying to another context or pushing to another registry.
readonly CLUSTER_NAME="clouddsp-local"
readonly KUBECTL_CONTEXT="k3d-${CLUSTER_NAME}"
readonly NAMESPACE="clouddsp-app"
readonly DEPLOYMENT_NAME="routing-smoke"
readonly REGISTRY_HOST="clouddsp-registry.localhost:5001"
readonly SOURCE_IMAGE="busybox:1.37.0"
readonly TEST_IMAGE="${REGISTRY_HOST}/routing-smoke:1.37.0"
readonly TEST_HOST="routing-smoke.localhost"
readonly EXPECTED_RESPONSE="CloudDSP HTTP routing smoke test"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/maintenance/verify-http-routing.sh

Pushes a versioned BusyBox test image, deploys the routing-smoke resources, and
verifies the response through http://routing-smoke.localhost:8080/.
USAGE
}

require_command() {
  # Clear prerequisite checks make a stopped Docker daemon or absent local tool
  # distinguishable from an Ingress, Service, or Pod configuration failure.
  local command_name="$1"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    printf 'Required command is unavailable: %s\n' "${command_name}" >&2
    exit 1
  fi
}

cluster_exists() {
  k3d cluster list "${CLUSTER_NAME}" --no-headers | awk -v name="${CLUSTER_NAME}" '$1 == name { found = 1 } END { exit !found }'
}

registry_exists() {
  k3d registry list "${REGISTRY_HOST%%:*}" --no-headers | awk -v name="${REGISTRY_HOST%%:*}" '$1 == name { found = 1 } END { exit !found }'
}

require_prerequisites() {
  require_command curl
  require_command docker
  require_command k3d
  require_command kubectl

  if ! docker info >/dev/null 2>&1; then
    printf 'Docker is installed but its daemon is not reachable. Start Docker Desktop and retry.\n' >&2
    exit 1
  fi

  if [[ ! -f "${ROUTING_MANIFEST}" ]]; then
    printf 'The HTTP routing manifest is missing: %s\n' "${ROUTING_MANIFEST}" >&2
    exit 1
  fi

  if ! cluster_exists; then
    printf 'Cluster %q does not exist. Create it before running this smoke test.\n' "${CLUSTER_NAME}" >&2
    exit 1
  fi

  if ! registry_exists; then
    printf 'Registry %q does not exist. Recreate the local cluster profile first.\n' "${REGISTRY_HOST%%:*}" >&2
    exit 1
  fi

  if ! kubectl --context "${KUBECTL_CONTEXT}" get namespace "${NAMESPACE}" >/dev/null; then
    printf 'Namespace %q does not exist. Apply cluster/namespaces.yaml first.\n' "${NAMESPACE}" >&2
    exit 1
  fi
}

verify_http_response() {
  # Traefik observes Ingress changes asynchronously. Retry for at most twenty
  # seconds after the Pod is ready so a brief router-reconciliation delay is
  # not misdiagnosed as a failed routing design.
  local attempt response
  for attempt in {1..20}; do
    response="$(curl --fail --silent --show-error --resolve "${TEST_HOST}:8080:127.0.0.1" "http://${TEST_HOST}:8080/" 2>/dev/null || true)"
    if [[ "${response}" == "${EXPECTED_RESPONSE}" ]]; then
      return 0
    fi
    sleep 1
  done

  printf 'HTTP routing did not return the expected response after 20 seconds.\n' >&2
  printf 'Inspect with: kubectl --context %s --namespace %s get ingress,service,pods -o wide\n' "${KUBECTL_CONTEXT}" "${NAMESPACE}" >&2
  return 1
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

  # Docker obtains the exact BusyBox release, gives it a local-registry name,
  # and pushes it before Kubernetes creates the Deployment that references it.
  docker pull "${SOURCE_IMAGE}"
  docker tag "${SOURCE_IMAGE}" "${TEST_IMAGE}"
  docker push "${TEST_IMAGE}"

  kubectl --context "${KUBECTL_CONTEXT}" apply --filename "${ROUTING_MANIFEST}"
  kubectl --context "${KUBECTL_CONTEXT}" --namespace "${NAMESPACE}" rollout status "deployment/${DEPLOYMENT_NAME}" --timeout=120s
  verify_http_response

  # Show the objects that participated in the verified request. The Deployment,
  # Service, and Ingress remain in place intentionally for user inspection.
  kubectl --context "${KUBECTL_CONTEXT}" --namespace "${NAMESPACE}" get deployment,service,ingress --selector "app.kubernetes.io/name=${DEPLOYMENT_NAME}" -o wide
  kubectl --context "${KUBECTL_CONTEXT}" --namespace "${NAMESPACE}" get pods --selector "app.kubernetes.io/name=${DEPLOYMENT_NAME}" -o wide
  printf 'HTTP routing smoke test succeeded: http://%s:8080/\n' "${TEST_HOST}"
  printf 'Remove only these test resources with: kubectl --context %s delete --filename %s\n' "${KUBECTL_CONTEXT}" "${ROUTING_MANIFEST}"
}

main "$@"
