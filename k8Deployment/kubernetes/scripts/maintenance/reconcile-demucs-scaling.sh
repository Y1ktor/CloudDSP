#!/usr/bin/env bash
# Reassert the reviewed, already-installed Demucs CPU and KEDA configuration.
# Helm owns the worker Deployment/ScaledObject and both shared scaler
# authentications. Flux may manage authentication delivery. Reuse the release verifiers so
# missing releases, ownership drift, changed profiles, or active work stop here.
# Fresh bootstrap and credential rotation belong to their separate stages.
set -euo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/../lib/paths.sh"

if [ "$#" -ne 0 ]; then
  printf 'Usage: ./k8Deployment/kubernetes/scripts/maintenance/reconcile-demucs-scaling.sh\n' >&2
  exit 64
fi

repo_root="$REPOSITORY_DIRECTORY"
context="k3d-clouddsp-local"
namespace="clouddsp-app"
chart_dir="$repo_root/k8Deployment/kubernetes/helm"

run_stage() {
  local stage="$1"
  shift
  printf 'Demucs scaling reconcile: %s\n' "$stage"
  if ! "$@"; then
    printf 'Demucs scaling reconcile stopped at %s; inspect the identities and Helm releases before retrying.\n' "$stage" >&2
    exit 1
  fi
}

# Both authentications are shared: Basic Pitch uses the PostgreSQL observer,
# and all three workers use the RabbitMQ observer. Verify their existing
# restricted identities instead of recreating passwords or bootstrap Jobs.
run_stage 'PostgreSQL scaler identity verification' \
  ruby "$CLOUDDSP_SCRIPTS_DIRECTORY/stages/credentials/application-identity-stage.rb" database keda-demucs verify
run_stage 'RabbitMQ scaler identity verification' \
  ruby "$CLOUDDSP_SCRIPTS_DIRECTORY/stages/credentials/application-identity-stage.rb" rabbitmq keda-scaler verify
run_stage 'scaling-auth Helm preflight' \
  ruby "$CLOUDDSP_SCRIPTS_DIRECTORY/releases/scaling-auth-release.rb" verify-prerequisites
run_stage 'idle Demucs Helm preflight' \
  ruby "$CLOUDDSP_SCRIPTS_DIRECTORY/releases/demucs-release.rb" verify-idle

# Upgrade only existing releases, using their checked-in chart defaults.
# scaling-auth owns both TriggerAuthentications; demucs owns the Deployment
# and dual-trigger ScaledObject together. KEDA retains its controller, HPA,
# and /scale ownership. A failed write remains inspectable in place.
run_stage 'scaling-auth delivery reconciliation' \
  ruby "$CLOUDDSP_SCRIPTS_DIRECTORY/releases/scaling-auth-release.rb" reconcile
run_stage 'Demucs Helm upgrade' \
  helm upgrade clouddsp-demucs "$chart_dir/demucs" \
    --kube-context "$context" --namespace "$namespace" --wait --timeout 3m

# These checks cover stored Helm manifests, resource ownership, both Secret
# references, scaler readiness, the generated HPA, and the idle worker state.
run_stage 'scaling-auth Helm verification' \
  ruby "$CLOUDDSP_SCRIPTS_DIRECTORY/releases/scaling-auth-release.rb" verify-prerequisites
run_stage 'Demucs Helm verification' \
  ruby "$CLOUDDSP_SCRIPTS_DIRECTORY/releases/demucs-release.rb" verify-idle
printf 'Demucs scaling reconcile completed through Helm.\n'
