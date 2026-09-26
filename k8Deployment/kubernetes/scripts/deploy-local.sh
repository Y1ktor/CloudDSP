#!/usr/bin/env bash
# Entry point for CloudDSP's eventual local deployment orchestrator.
#
# Only `plan` exists today. It runs the read-only preflight in Ruby, which uses
# the Ruby YAML/JSON standard libraries to inspect versioned manifests and
# sanitized Kubernetes metadata. This wrapper intentionally contains no
# install, apply, upgrade, delete, or bootstrap path: adding any mutating mode
# requires a separate review after the plan's ownership gates are reliable.
set -euo pipefail

readonly SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/deploy-local.sh plan

Read-only preflight for the explicit k3d-clouddsp-local context. Reports
manifest ownership, required Secret names, image-lock consistency, and
StatefulSet/PVC identity. No Kubernetes or Helm resource is changed.
USAGE
}

if [[ "$#" -eq 1 && ( "$1" == "-h" || "$1" == "--help" || "$1" == "help" ) ]]; then
  usage
  exit 0
fi

if [[ "$#" -ne 1 || "$1" != "plan" ]]; then
  usage >&2
  exit 2
fi

if ! command -v ruby >/dev/null 2>&1; then
  printf 'Ruby is required for read-only YAML and JSON parsing.\n' >&2
  exit 1
fi

exec ruby "${SCRIPT_DIRECTORY}/deploy-local-plan.rb"
