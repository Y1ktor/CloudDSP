#!/usr/bin/env bash
# Reconcile the reviewed KEDA Helm release into CloudDSP's local k3d cluster.
#
# Helm renders the upstream chart with the versioned local values, then sends
# the resulting Kubernetes resources to the explicit k3d context. The running
# KEDA operator, metrics API server, and admission webhook are Kubernetes Pods;
# this script is only a short-lived host-side client and is not a cluster Pod.
#
# This script intentionally installs no ScaledObject, worker Deployment,
# TriggerAuthentication, Secret, or RabbitMQ credential. Those are separate
# small tasks because each worker needs an independently reviewed queue policy.

set -euo pipefail

# Resolve all project paths from the script instead of the caller's current
# directory. This makes the command reproducible from the repository root,
# the scripts directory, or a CI shell without hidden relative-path behavior.
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/../lib/paths.sh"
readonly KEDA_DIRECTORY="${KUBERNETES_DIRECTORY}/helm/keda"
readonly KEDA_VALUES_FILE="${KEDA_DIRECTORY}/values.yaml"
readonly KEDA_LOCK_FILE="${KEDA_DIRECTORY}/release.lock.yaml"

# These constants duplicate only the reviewed lock's executable fields. Bash
# has no safe built-in YAML parser, so the script also verifies that the lock
# still contains each value before it contacts the cluster. Changing a version
# therefore requires a deliberate paired review of this script and the lock.
readonly KUBECTL_CONTEXT="k3d-clouddsp-local"
readonly CHART_REPOSITORY_NAME="kedacore"
readonly CHART_REPOSITORY_URL="https://kedacore.github.io/charts"
readonly CHART_NAME="keda"
readonly CHART_VERSION="2.20.2"
readonly RELEASE_NAME="keda"
readonly RELEASE_NAMESPACE="keda"
readonly RELEASE_TIMEOUT="5m"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/releases/install-keda.sh

Installs or upgrades the pinned KEDA 2.20.2 Helm release in the explicit
k3d-clouddsp-local context, waits for controller readiness, and verifies the
KEDA CustomResourceDefinitions.

It does not create a ScaledObject, scale an application, or add credentials.
USAGE
}

require_command() {
  # Fail before a partial Helm action with a clear prerequisite message.
  local command_name="$1"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    printf 'Required command is unavailable: %s\n' "${command_name}" >&2
    exit 1
  fi
}

require_file() {
  # A missing committed input is a repository/configuration error, not a Helm
  # chart error. Report the exact path before attempting a cluster mutation.
  local file_path="$1"
  if [[ ! -f "${file_path}" ]]; then
    printf 'Required KEDA configuration file is missing: %s\n' "${file_path}" >&2
    exit 1
  fi
}

lock_contains() {
  # Fixed-string matching avoids interpreting dots in URLs or chart versions
  # as regular-expression syntax. The lock is a lightweight drift tripwire,
  # not a replacement for reviewing the complete versioned YAML file.
  local expected_value="$1"
  grep --fixed-strings --quiet "${expected_value}" "${KEDA_LOCK_FILE}"
}

require_prerequisites() {
  require_command helm
  require_command kubectl
  require_command grep
  require_command ruby
  require_file "${KEDA_VALUES_FILE}"
  require_file "${KEDA_LOCK_FILE}"

  local expected_lock_value
  for expected_lock_value in \
    "repositoryName: ${CHART_REPOSITORY_NAME}" \
    "repositoryURL: ${CHART_REPOSITORY_URL}" \
    "name: ${CHART_NAME}" \
    "version: \"${CHART_VERSION}\"" \
    "releaseName: ${RELEASE_NAME}" \
    "namespace: ${RELEASE_NAMESPACE}"; do
    if ! lock_contains "${expected_lock_value}"; then
      printf 'KEDA release lock does not contain expected value: %s\n' "${expected_lock_value}" >&2
      exit 1
    fi
  done

  # Use an explicit context on every cluster command below. Checking that the
  # configured context exists catches an expired/deleted k3d cluster before
  # Helm can accidentally target another Kubernetes environment.
  if ! kubectl config get-contexts --output=name | grep --fixed-strings --quiet --line-regexp "${KUBECTL_CONTEXT}"; then
    printf 'Required Kubernetes context is unavailable: %s\n' "${KUBECTL_CONTEXT}" >&2
    exit 1
  fi

  # Fail closed before repo or release writes whenever Flux reserves KEDA,
  # including failed/suspended/deleting records and API lookup failures.
  ruby "${CLOUDDSP_SCRIPTS_DIRECTORY}/gitops/keda-flux-ownership.rb" guard-native
}

verify_release() {
  # A successful Helm process alone does not prove that Pods started or that
  # the extension API is usable. Check each chart-owned controller Deployment
  # and the two CRDs needed by later CloudDSP scaling resources.
  kubectl --context "${KUBECTL_CONTEXT}" rollout status \
    --namespace "${RELEASE_NAMESPACE}" \
    deployment/keda-operator \
    --timeout="${RELEASE_TIMEOUT}"
  kubectl --context "${KUBECTL_CONTEXT}" rollout status \
    --namespace "${RELEASE_NAMESPACE}" \
    deployment/keda-operator-metrics-apiserver \
    --timeout="${RELEASE_TIMEOUT}"
  kubectl --context "${KUBECTL_CONTEXT}" rollout status \
    --namespace "${RELEASE_NAMESPACE}" \
    deployment/keda-admission-webhooks \
    --timeout="${RELEASE_TIMEOUT}"
  kubectl --context "${KUBECTL_CONTEXT}" get crd scaledobjects.keda.sh
  kubectl --context "${KUBECTL_CONTEXT}" get crd triggerauthentications.keda.sh
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

  # --force-update permits a repeatable run after the repository already
  # exists. Helm fetches only the named official repository, then the pinned
  # chart is selected explicitly rather than accepting the repository's latest.
  helm repo add "${CHART_REPOSITORY_NAME}" "${CHART_REPOSITORY_URL}" --force-update
  helm repo update "${CHART_REPOSITORY_NAME}"

  # This historical native path is reserved for pre-Flux clusters. --atomic
  # implies waiting and failure remediation. KEDA's CRDs are normal templates;
  # their deletion can delete stored resources. The optional Flux handoff adds
  # explicit retention and disables automatic rollback/uninstall instead.
  helm upgrade --install "${RELEASE_NAME}" "${CHART_REPOSITORY_NAME}/${CHART_NAME}" \
    --kube-context "${KUBECTL_CONTEXT}" \
    --namespace "${RELEASE_NAMESPACE}" \
    --create-namespace \
    --version "${CHART_VERSION}" \
    --values "${KEDA_VALUES_FILE}" \
    --wait \
    --timeout "${RELEASE_TIMEOUT}" \
    --atomic

  verify_release
  printf 'KEDA release %q is ready in namespace %q on context %q.\n' \
    "${RELEASE_NAME}" "${RELEASE_NAMESPACE}" "${KUBECTL_CONTEXT}"
}

main "$@"
