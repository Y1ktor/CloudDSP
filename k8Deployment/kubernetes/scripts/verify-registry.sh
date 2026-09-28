#!/usr/bin/env bash
# This script verifies the complete local image-delivery path:
#
#   Docker Hub → Docker Desktop → CloudDSP local registry → K3s node → Job Pod
#
# It pushes a harmless, exact BusyBox release under a CloudDSP-only test tag,
# creates the versioned registry-smoke Job, then prints the Job's Pod events
# and logs.  The completed Job and Pod remain visible until Kubernetes's
# five-minute TTL cleanup. The pushed test image also remains in the dedicated
# local registry; the explicit purge-registry.sh removes it when desired.

# Exit on command errors, unset variables, and failures inside pipelines so a
# failed push, pull, or Job cannot be reported as a successful verification.
set -euo pipefail

# Resolve repository paths from this script rather than the caller's directory.
readonly SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly KUBERNETES_DIRECTORY="$(cd -- "${SCRIPT_DIRECTORY}/.." && pwd)"
readonly JOB_MANIFEST="${KUBERNETES_DIRECTORY}/tests/registry-smoke/registry-smoke-job.yaml"

# These constants mirror the cluster configuration and Job manifest.  Keeping
# the scope fixed prevents this test from pushing to or deleting from a
# developer's unrelated registry, cluster, namespace, or Job.
readonly CLUSTER_NAME="clouddsp-local"
readonly KUBECTL_CONTEXT="k3d-${CLUSTER_NAME}"
readonly NAMESPACE="clouddsp-app"
readonly JOB_NAME="registry-smoke"
readonly REGISTRY_HOST="clouddsp-registry.localhost:5001"
readonly SOURCE_IMAGE="busybox:1.37.0"
readonly TEST_IMAGE="${REGISTRY_HOST}/registry-smoke:1.37.0"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/verify-registry.sh

Pushes a pinned BusyBox release to the CloudDSP local registry, then runs and
leaves the versioned registry-smoke Kubernetes Job in clouddsp-app for
inspection until its Kubernetes TTL expires.
USAGE
}

require_command() {
  # Fail with a direct prerequisite message instead of a later shell-specific
  # error that obscures whether Docker, k3d, or Kubernetes was the problem.
  local command_name="$1"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    printf 'Required command is unavailable: %s\n' "${command_name}" >&2
    exit 1
  fi
}

cluster_exists() {
  # Exact matching keeps the test tied to its declared cluster and avoids a
  # similarly named k3d cluster being accepted as a valid test target.
  k3d cluster list "${CLUSTER_NAME}" --no-headers | awk -v name="${CLUSTER_NAME}" '$1 == name { found = 1 } END { exit !found }'
}

registry_exists() {
  # The registry name is declared in cluster/k3d.yaml and is a Docker resource,
  # not a Kubernetes Service, so k3d is the authoritative discovery tool here.
  k3d registry list "${REGISTRY_HOST%%:*}" --no-headers | awk -v name="${REGISTRY_HOST%%:*}" '$1 == name { found = 1 } END { exit !found }'
}

require_prerequisites() {
  require_command docker
  require_command k3d
  require_command kubectl

  if ! docker info >/dev/null 2>&1; then
    printf 'Docker is installed but its daemon is not reachable. Start Docker Desktop and retry.\n' >&2
    exit 1
  fi

  if [[ ! -f "${JOB_MANIFEST}" ]]; then
    printf 'The registry smoke Job manifest is missing: %s\n' "${JOB_MANIFEST}" >&2
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

remove_previous_job() {
  # A completed Job's Pod template is immutable. Before a *new* smoke test,
  # remove only the fixed prior test Job so Kubernetes creates a fresh Pod and
  # performs another image-pull check. This is not post-test cleanup: the newly
  # completed Job intentionally remains available for inspection.
  kubectl --context "${KUBECTL_CONTEXT}" --namespace "${NAMESPACE}" delete job "${JOB_NAME}" --ignore-not-found --wait=true >/dev/null 2>&1 || true
}

show_failure_diagnostics() {
  # If the Job fails, events normally distinguish registry reachability,
  # authentication, architecture, scheduling, and container-command problems.
  # Do not use this output as a success criterion; it is diagnostic evidence.
  local pod_name
  pod_name="$(kubectl --context "${KUBECTL_CONTEXT}" --namespace "${NAMESPACE}" get pods --selector "job-name=${JOB_NAME}" --output jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  if [[ -n "${pod_name}" ]]; then
    kubectl --context "${KUBECTL_CONTEXT}" --namespace "${NAMESPACE}" describe pod "${pod_name}" >&2 || true
  fi
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

  # Pull the fixed official BusyBox version for this computer's architecture,
  # tag that exact local image for the loopback registry, and push it.  The
  # resulting test image is distinct from all CloudDSP application images.
  docker pull "${SOURCE_IMAGE}"
  docker tag "${SOURCE_IMAGE}" "${TEST_IMAGE}"
  docker push "${TEST_IMAGE}"

  # A previous smoke-test Job remains visible for up to five minutes by design.
  # Remove it before applying the manifest because completed Jobs are immutable
  # with respect to their Pod template and cannot verify a fresh image pull.
  remove_previous_job

  kubectl --context "${KUBECTL_CONTEXT}" apply --filename "${JOB_MANIFEST}"

  if ! kubectl --context "${KUBECTL_CONTEXT}" --namespace "${NAMESPACE}" wait --for=condition=complete "job/${JOB_NAME}" --timeout=120s; then
    show_failure_diagnostics
    exit 1
  fi

  # The Job's own output proves the pulled image started.  The Pod events show
  # the runtime's image-pull sequence, including a useful pull source/error.
  local pod_name
  pod_name="$(kubectl --context "${KUBECTL_CONTEXT}" --namespace "${NAMESPACE}" get pods --selector "job-name=${JOB_NAME}" --output jsonpath='{.items[0].metadata.name}')"
  kubectl --context "${KUBECTL_CONTEXT}" --namespace "${NAMESPACE}" logs "job/${JOB_NAME}"
  kubectl --context "${KUBECTL_CONTEXT}" --namespace "${NAMESPACE}" get pod "${pod_name}" -o wide
  kubectl --context "${KUBECTL_CONTEXT}" --namespace "${NAMESPACE}" get events --field-selector "involvedObject.name=${pod_name}" --sort-by=.lastTimestamp

  printf 'Local registry smoke test succeeded: %s was pulled by Kubernetes.\n' "${TEST_IMAGE}"
  printf 'The completed Job and Pod remain inspectable for up to five minutes.\n'
}

main "$@"
