#!/usr/bin/env bash
# Install the reviewed Flux controllers, then connect their versioned Git sync.
# This is an opt-in bootstrap for an existing cluster, separate from host k3d,
# registry, credential, database, and application Helm deployment stages.
set -euo pipefail

if [[ $# -gt 1 ]]; then
  echo "Usage: $0 [kubernetes-context]" >&2
  exit 2
fi
context="${1:-k3d-clouddsp-local}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cluster_dir="$script_dir/../../gitops/clusters/clouddsp-local"
flux_dir="$cluster_dir/flux-system"

for command_name in kubectl flux git ruby helm; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Flux bootstrap stopped: missing $command_name" >&2
    exit 1
  fi
done

# Refuse untracked or modified install input. Publish the committed branch first
# so the cluster can fetch the same reviewed configuration from the Git source.
for manifest in "$cluster_dir/kustomization.yaml" \
  "$flux_dir/kustomization.yaml" "$flux_dir/gotk-components.yaml" \
  "$flux_dir/gotk-sync.yaml"; do
  if ! git -C "$script_dir" ls-files --error-unmatch "$manifest" >/dev/null 2>&1 || \
    ! git -C "$script_dir" diff --quiet HEAD -- "$manifest"; then
    echo "Flux bootstrap stopped: commit and publish the reviewed manifests first" >&2
    exit 1
  fi
done

flux check --pre --context "$context"
kubectl kustomize "$cluster_dir" >/dev/null

# KEDA itself is an adoption of the existing pinned platform release, established
# CRDs, and metrics API. Its prepared identity cannot install arbitrary platform
# resources or namespaces. Authentication also needs existing observer Secrets.
# Neither handoff bootstraps missing credentials. These verifiers
# use the standard profile context, so reject a different bootstrap target.
if [[ "$context" != k3d-clouddsp-local ]]; then
  echo 'Flux bootstrap stopped: selected releases require k3d-clouddsp-local' >&2
  exit 1
fi
# Verify the existing database and bound claim; Flux never initializes data
# or reapplies schema/grant Jobs as part of this delivery handoff.
ruby "$script_dir/../releases/postgresql-release.rb" verify
# Adopt only an existing healthy broker. Its retained claim and native history
# must exist; this optional path never initializes empty durable broker data.
ruby "$script_dir/../releases/rabbitmq-release.rb" verify
ruby "$script_dir/../releases/keda-release-stage.rb" verify
ruby "$script_dir/../releases/scaling-auth-release.rb" verify-prerequisites
# Each selected worker must already have its native Deployment/scaler/HPA
# and configured idle/warm readiness. Its credential bootstrap remains separate.
ruby "$script_dir/../releases/adtof-release.rb" verify
# Basic Pitch retains both RabbitMQ and PostgreSQL trigger authentication.
ruby "$script_dir/../releases/basic-pitch-release.rb" verify
# Demucs is a CPU worker with queue/task triggers and the same shared auth.
ruby "$script_dir/../releases/demucs-release.rb" verify

# Install API definitions and controllers before submitting Flux custom objects.
# Reuse the reconciler's field manager so bootstrap and subsequent Git applies
# do not create separate owners for the same declarative fields.
kubectl --context "$context" apply --server-side \
  --field-manager=kustomize-controller -f "$flux_dir/gotk-components.yaml"
kubectl --context "$context" wait --for=condition=Established \
  crd/gitrepositories.source.toolkit.fluxcd.io \
  crd/kustomizations.kustomize.toolkit.fluxcd.io \
  crd/helmreleases.helm.toolkit.fluxcd.io --timeout=60s

for controller in source-controller kustomize-controller helm-controller notification-controller; do
  kubectl --context "$context" --namespace flux-system rollout status \
    "deployment/$controller" --timeout=3m
done

# Validate against the installed Flux CRD schemas before creating the Git sync.
kubectl --context "$context" apply --server-side --dry-run=server \
  --field-manager=kustomize-controller -f "$flux_dir/gotk-sync.yaml"
kubectl --context "$context" apply --server-side \
  --field-manager=kustomize-controller -f "$flux_dir/gotk-sync.yaml"
flux reconcile kustomization flux-system --with-source \
  --context "$context" --namespace flux-system --timeout=3m
flux check --context "$context"
flux get all --context "$context" --namespace flux-system
