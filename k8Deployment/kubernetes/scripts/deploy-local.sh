#!/usr/bin/env bash
# Entry point for CloudDSP's incremental local deployment orchestrator.
#
# `plan` reports the source/live preflight. `prepare` creates only the fresh
# foundation and mirrors its locked images. `bootstrap-mailpit` adds the first
# fresh Helm release. `bootstrap-postgresql` and `bootstrap-rabbitmq` prepare
# individual stateful services; `bootstrap-minio` composes broker and object
# storage prerequisites, buckets, locked samples, and restricted IAM users.
# `bootstrap-platform` reuses those guarded children once, adding PostgreSQL,
# Keycloak's database and release, Mailpit, Job API schema, and processing
# topology in dependency order.
# `verify` checks each reviewed component and bootstrap stage. `reconcile`
# changes only audited external state through versioned runners; existing Helm
# releases are verified and must already match their charts. `cleanup` removes the k3d cluster while
# retaining images; `purge-registry` is the separate explicit image deletion.
set -euo pipefail

readonly SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/deploy-local.sh stages|plan|prepare|bootstrap-mailpit|bootstrap-postgresql|bootstrap-rabbitmq|bootstrap-minio|bootstrap-platform|verify|reconcile|cleanup|purge-registry

stages: List the ordered stages currently wired into bootstrap-platform;
        read-only and does not contact the cluster.
plan: Read-only preflight for the explicit k3d-clouddsp-local context.
prepare: On an absent cluster, create the foundation and mirror the locked
         public images. Helm releases and external state are not installed.
bootstrap-mailpit: On an absent cluster, run prepare, install Mailpit, then
         verify that release. This is a partial application bootstrap.
bootstrap-postgresql: On an absent cluster, run prepare, create the PostgreSQL
         credential Secret, install the StatefulSet, and verify the bound PVC.
         This is a partial application bootstrap.
bootstrap-rabbitmq: On an absent cluster, run prepare, create the RabbitMQ
         credential Secret, install the StatefulSet, and verify the bound PVC.
         This is a partial application bootstrap.
bootstrap-minio: On an absent cluster, prepare and install RabbitMQ, create
         MinIO and upload-intake broker Secrets, bootstrap restricted broker
         source-intake state, install MinIO, and stage Job API, upload-intake,
         Demucs, Basic Pitch, and ADTOF MinIO runtime credentials. It creates buckets,
         mirrors 461 locked MIDI samples, and provisions Job API, upload-intake,
         Demucs, Basic Pitch, and ADTOF MinIO users.
bootstrap-platform: On an absent cluster, compose the guarded PostgreSQL,
         RabbitMQ, and MinIO children once, create Keycloak's database and
         credential Secret, install Mailpit, then stage Keycloak's admin Secret
         and install its Helm release. Bootstrap Job API PostgreSQL migrations
         and RabbitMQ processing topology afterward. Keycloak's realm,
         worker state, KEDA, and app releases remain.
verify: Run that preflight, then the reviewed read-only Helm, KEDA, and
        bootstrap gates in dependency order. Stop at the first failed gate.
reconcile: On an existing cluster, run the same ordered gates and reconcile
           Job API PostgreSQL, RabbitMQ, narrow MinIO bucket and notification
           state, plus matching leftover Job API/upload-intake/Demucs/Basic Pitch/
           ADTOF IAM Secrets.
           Existing Helm releases must verify; this mode does not upgrade them.
cleanup: Delete the fixed CloudDSP k3d cluster and its Kubernetes resources,
         including PVC data. Keep the dedicated local image registry.
purge-registry: Delete that registry and its images after cluster cleanup.
plan and verify do not change Kubernetes or Helm resources.
USAGE
}

if [[ "$#" -eq 1 && ( "$1" == "-h" || "$1" == "--help" || "$1" == "help" ) ]]; then
  usage
  exit 0
fi

if [[ "$#" -ne 1 || ( "$1" != "stages" && "$1" != "plan" && "$1" != "prepare" && "$1" != "bootstrap-mailpit" && "$1" != "bootstrap-postgresql" && "$1" != "bootstrap-rabbitmq" && "$1" != "bootstrap-minio" && "$1" != "bootstrap-platform" && "$1" != "verify" && "$1" != "reconcile" && "$1" != "cleanup" && "$1" != "purge-registry" ) ]]; then
  usage >&2
  exit 2
fi

# The root mode itself is the explicit destructive command. The underlying
# script's --confirm remains its direct-call guard; no Ruby runtime or cluster
# API connection is required to tear down this fixed local k3d profile.
if [[ "$1" == "cleanup" ]]; then
  exec bash "${SCRIPT_DIRECTORY}/cleanup-cluster.sh" --confirm
fi
if [[ "$1" == "purge-registry" ]]; then
  exec bash "${SCRIPT_DIRECTORY}/purge-registry.sh" --confirm
fi

if ! command -v ruby >/dev/null 2>&1; then
  printf 'Ruby is required for local deployment planning and verification.\n' >&2
  exit 1
fi

case "$1" in
  stages) exec ruby "${SCRIPT_DIRECTORY}/deploy-local-platform.rb" list ;;
  plan) exec ruby "${SCRIPT_DIRECTORY}/deploy-local-plan.rb" ;;
  prepare) exec ruby "${SCRIPT_DIRECTORY}/deploy-local-prepare.rb" ;;
  bootstrap-mailpit) exec ruby "${SCRIPT_DIRECTORY}/deploy-local-mailpit.rb" ;;
  bootstrap-postgresql) exec ruby "${SCRIPT_DIRECTORY}/deploy-local-postgresql.rb" ;;
  bootstrap-rabbitmq) exec ruby "${SCRIPT_DIRECTORY}/deploy-local-rabbitmq.rb" ;;
  bootstrap-minio) exec ruby "${SCRIPT_DIRECTORY}/deploy-local-minio.rb" ;;
  bootstrap-platform) exec ruby "${SCRIPT_DIRECTORY}/deploy-local-platform.rb" ;;
  verify) exec ruby "${SCRIPT_DIRECTORY}/deploy-local-verify.rb" ;;
  reconcile) exec ruby "${SCRIPT_DIRECTORY}/deploy-local-reconcile.rb" ;;
esac
