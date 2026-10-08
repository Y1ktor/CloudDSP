#!/usr/bin/env bash
# Build the CPU-only ARM64 consumer, including its offline homr checkpoints.
# This publishes an image; the versioned Helm digest controls the rollout.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/../lib/paths.sh"
readonly SCORE_DIRECTORY="${KUBERNETES_DIRECTORY}/services/score-omr"
readonly IMAGE="clouddsp-registry.localhost:5001/score-omr:0.1.4-benchmark-parity"

if [[ "$#" -ne 0 ]]; then
  printf 'Usage: %s\n' "$0" >&2
  exit 2
fi
docker build --platform linux/arm64 --provenance=false \
  --file "${SCORE_DIRECTORY}/Dockerfile" --tag "${IMAGE}" "${SCORE_DIRECTORY}"
docker push "${IMAGE}"
# The readable tag is build provenance. Copy the resulting repository digest
# and exact size into images.lock.yaml and the score-omr Helm values.
docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}Size: {{.Size}} bytes' "${IMAGE}"
