#!/usr/bin/env bash
# Reconcile the Demucs CPU profile and both KEDA work signals in safe order.
# The due database retry will wake immediately after the final ScaledObject
# apply, so the Deployment's reviewed CPU budget must be installed first.
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$script_dir/../../.." && pwd)"
context="k3d-clouddsp-local"
app_secret="$repo_root/k8Deployment/.local/keda-demucs-postgresql-credentials.secret.yaml"
bootstrap_secret="$repo_root/k8Deployment/.local/postgresql-keda-demucs-bootstrap-credentials.secret.yaml"
bootstrap_job="$repo_root/k8Deployment/kubernetes/services/postgresql/postgresql-keda-demucs-bootstrap-job.yaml"
authentication="$repo_root/k8Deployment/kubernetes/helm/keda/keda-demucs-postgresql-trigger-authentication.yaml"
worker="$repo_root/k8Deployment/kubernetes/services/demucs/demucs-deployment.yaml"
scaled_object="$repo_root/k8Deployment/kubernetes/services/demucs/demucs-scaledobject.yaml"

# Fail before applying anything if either ignored local Secret is absent.
# Their committed example files contain placeholders and must never be used
# as live credentials.
test -f "$app_secret"
test -f "$bootstrap_secret"
kubectl --context "$context" cluster-info >/dev/null

# A completed fixed-name Job does not rerun on apply. Refuse to silently bind
# a newly rotated app Secret to an older PostgreSQL role password.
if kubectl --context "$context" --namespace clouddsp-data \
  get job postgresql-keda-demucs-bootstrap >/dev/null 2>&1; then
  printf 'Inspect the existing postgresql-keda-demucs-bootstrap Job before a deliberate rerun.\n' >&2
  exit 1
fi

kubectl --context "$context" apply --filename "$app_secret"
kubectl --context "$context" apply --filename "$bootstrap_secret"
kubectl --context "$context" apply --filename "$bootstrap_job"
kubectl --context "$context" --namespace clouddsp-data wait \
  --for=condition=complete job/postgresql-keda-demucs-bootstrap \
  --timeout=180s
kubectl --context "$context" --namespace clouddsp-data logs \
  job/postgresql-keda-demucs-bootstrap \
  --container=postgresql-bootstrap-client

# The role is now durable in PostgreSQL; remove its temporary copy from the
# data namespace. The ignored local file remains available for a reviewed
# password rotation, while KEDA keeps only its app-namespace Secret.
kubectl --context "$context" --namespace clouddsp-data delete \
  secret clouddsp-keda-demucs-postgresql-bootstrap-credentials

kubectl --context "$context" apply --filename "$authentication"
kubectl --context "$context" apply --filename "$worker"
kubectl --context "$context" apply --filename "$scaled_object"
kubectl --context "$context" --namespace clouddsp-app wait \
  --for=condition=ready scaledobject/clouddsp-demucs-rabbitmq-scaler \
  --timeout=90s
kubectl --context "$context" --namespace clouddsp-app get \
  scaledobject/clouddsp-demucs-rabbitmq-scaler
