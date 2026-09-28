#!/usr/bin/env bash
# Entry point for CloudDSP's incremental local deployment orchestrator.
#
# `plan` reports the source/live preflight. `verify` checks each reviewed
# component and bootstrap stage. `reconcile` changes only the five bootstrap
# stages with audited idempotent runners; existing Helm releases are verified
# and must already match their charts. `cleanup` delegates to the fixed-scope
# teardown script, which removes the dedicated k3d cluster and registry.
set -euo pipefail

readonly SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/deploy-local.sh plan|verify|reconcile|cleanup

plan: Read-only preflight for the explicit k3d-clouddsp-local context.
verify: Run that preflight, then the reviewed read-only Helm, KEDA, and
        bootstrap gates in dependency order. Stop at the first failed gate.
reconcile: On an existing cluster, run the same ordered gates and reconcile
           the Job API PostgreSQL, RabbitMQ, and narrow MinIO bucket and
           notification stages. Existing Helm releases must verify; this mode
           does not install/upgrade them.
cleanup: Delete the fixed CloudDSP k3d cluster and dedicated registry, including
         their Kubernetes resources, PVC data, and locally stored registry images.
plan and verify do not change Kubernetes or Helm resources.
USAGE
}

if [[ "$#" -eq 1 && ( "$1" == "-h" || "$1" == "--help" || "$1" == "help" ) ]]; then
  usage
  exit 0
fi

if [[ "$#" -ne 1 || ( "$1" != "plan" && "$1" != "verify" && "$1" != "reconcile" && "$1" != "cleanup" ) ]]; then
  usage >&2
  exit 2
fi

# The root mode itself is the explicit destructive command. The underlying
# script's --confirm remains its direct-call guard; no Ruby runtime or cluster
# API connection is required to tear down this fixed local k3d profile.
if [[ "$1" == "cleanup" ]]; then
  exec bash "${SCRIPT_DIRECTORY}/cleanup-cluster.sh" --confirm
fi

if ! command -v ruby >/dev/null 2>&1; then
  printf 'Ruby is required for local deployment planning and verification.\n' >&2
  exit 1
fi

case "$1" in
  plan) exec ruby "${SCRIPT_DIRECTORY}/deploy-local-plan.rb" ;;
  verify) exec ruby "${SCRIPT_DIRECTORY}/deploy-local-verify.rb" ;;
  reconcile) exec ruby "${SCRIPT_DIRECTORY}/deploy-local-reconcile.rb" ;;
esac
