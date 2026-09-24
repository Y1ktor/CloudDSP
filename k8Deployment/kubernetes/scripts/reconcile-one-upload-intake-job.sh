#!/usr/bin/env bash
# Repair one retained direct-upload job whose MinIO event was lost or safely
# acknowledged before the source could be matched. This is an operator action,
# not a Kubernetes controller: it uses the already-running upload-intake Pod's
# restricted PostgreSQL and MinIO identities and creates no new Secret/Job.
set -euo pipefail

readonly CONTEXT="k3d-clouddsp-local"
readonly NAMESPACE="clouddsp-app"
readonly DEPLOYMENT="clouddsp-upload-intake"

if [[ "$#" -ne 1 || ! "$1" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
  printf 'Usage: %s CANONICAL_JOB_UUID\n' "$0" >&2
  exit 2
fi

# Wait for the reviewed digest-pinned consumer Deployment, then execute the
# one-shot module inside its existing Pod. The module fetches the exact key
# from PostgreSQL and uses the normal HeadObject/atomic-outbox handler; this
# script never accepts an object path, credential, or synthesized event body.
kubectl --context "$CONTEXT" --namespace "$NAMESPACE" rollout status \
  "deployment/$DEPLOYMENT" --timeout=120s
kubectl --context "$CONTEXT" --namespace "$NAMESPACE" exec \
  "deployment/$DEPLOYMENT" -- python -m app.reconcile_one "$1"
