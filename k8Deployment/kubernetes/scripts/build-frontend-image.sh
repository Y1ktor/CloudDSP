#!/usr/bin/env bash
# Build and publish the Kubernetes-local React image through this exact path:
#
#   local app/.env.production -> Docker build arguments -> Vite static files
#       -> non-root NGINX image -> CloudDSP's local k3d registry
#
# The configuration values supplied to Vite are public browser settings. This
# script deliberately does not accept a database password, Keycloak admin
# credential, client secret, or Kubernetes credential as an argument or file.

set -euo pipefail

# Resolve project paths from the script location so the command works from the
# repository root or any other current directory.
readonly SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly KUBERNETES_DIRECTORY="$(cd -- "${SCRIPT_DIRECTORY}/.." && pwd)"
readonly FRONTEND_DIRECTORY="${KUBERNETES_DIRECTORY}/services/frontend"
readonly ENVIRONMENT_FILE="${FRONTEND_DIRECTORY}/app/.env.production"
readonly DOCKERFILE="${FRONTEND_DIRECTORY}/Dockerfile"

# Keep the registry name, image path, and tag scoped to this existing k3d
# learning cluster. The immutable repository digest printed after push is the
# value a future Deployment will use; this descriptive tag is build traceability.
readonly REGISTRY_HOST="clouddsp-registry.localhost:5001"
readonly IMAGE_NAME="${REGISTRY_HOST}/frontend:0.5.2-local-midi-samples"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/build-frontend-image.sh

Builds the local CloudDSP React + Keycloak OIDC image for linux/arm64, pushes it
to the k3d local registry, and prints both its immutable registry digest and
Docker's local uncompressed image size.
USAGE
}

require_command() {
  local command_name="$1"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    printf 'Required command is unavailable: %s\n' "${command_name}" >&2
    exit 1
  fi
}

require_prerequisites() {
  require_command docker
  require_command k3d

  if ! docker info >/dev/null 2>&1; then
    printf 'Docker is installed but its daemon is not reachable. Start Docker Desktop and retry.\n' >&2
    exit 1
  fi

  if [[ ! -f "${DOCKERFILE}" ]]; then
    printf 'Frontend Dockerfile is missing: %s\n' "${DOCKERFILE}" >&2
    exit 1
  fi
  if [[ ! -f "${ENVIRONMENT_FILE}" ]]; then
    printf 'Local public frontend configuration is missing: %s\n' "${ENVIRONMENT_FILE}" >&2
    exit 1
  fi

  # The registry is a Docker container managed by k3d, not a Kubernetes
  # Service. Verify its exact declared name before a push can happen.
  if ! k3d registry list "${REGISTRY_HOST%%:*}" --no-headers | awk -v name="${REGISTRY_HOST%%:*}" '$1 == name { found = 1 } END { exit !found }'; then
    printf 'Local registry %q does not exist. Create the clouddsp-local cluster first.\n' "${REGISTRY_HOST%%:*}" >&2
    exit 1
  fi
}

environment_value() {
  # Extract a simple KEY=value entry without `source`-ing the file. This makes
  # the file data rather than executable shell and rejects missing/duplicate
  # keys. Vite public URLs/client IDs contain no multiline syntax in this local
  # profile, so a strict one-line parser is intentional.
  local key="$1"
  local value
  if ! value="$(awk -v wanted_key="${key}" '
    index($0, wanted_key "=") == 1 {
      count += 1
      value = substr($0, length(wanted_key) + 2)
    }
    END {
      if (count != 1) exit 1
      print value
    }
  ' "${ENVIRONMENT_FILE}")"; then
    printf 'Expected exactly one %s entry in %s\n' "${key}" "${ENVIRONMENT_FILE}" >&2
    exit 1
  fi
  printf '%s' "${value}"
}

require_nonempty_environment_value() {
  local key="$1"
  local value
  value="$(environment_value "${key}")"
  if [[ -z "${value}" ]]; then
    printf 'Required local browser setting is empty: %s\n' "${key}" >&2
    exit 1
  fi
  printf '%s' "${value}"
}

human_size() {
  # Docker exposes local image size in bytes. Format it with binary units so the
  # reported number is understandable while retaining the exact byte count too.
  awk -v bytes="$1" 'BEGIN {
    split("B KiB MiB GiB TiB", units, " ")
    unit_index = 1
    value = bytes + 0
    while (value >= 1024 && unit_index < 5) { value /= 1024; unit_index += 1 }
    printf "%.2f %s", value, units[unit_index]
  }'
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

  local oidc_issuer oidc_client_id redirect_uri post_logout_redirect_uri
  local job_api_url object_storage_url websocket_url demo_asset_origin
  oidc_issuer="$(require_nonempty_environment_value VITE_OIDC_ISSUER)"
  oidc_client_id="$(require_nonempty_environment_value VITE_OIDC_CLIENT_ID)"
  redirect_uri="$(require_nonempty_environment_value VITE_OIDC_REDIRECT_URI)"
  post_logout_redirect_uri="$(require_nonempty_environment_value VITE_OIDC_POST_LOGOUT_REDIRECT_URI)"
  job_api_url="$(environment_value VITE_JOB_API_URL)"
  # This is a public browser origin, not an S3 credential. It becomes an exact
  # CSP connect-src entry so the browser may use a server-issued presigned POST.
  object_storage_url="$(require_nonempty_environment_value VITE_OBJECT_STORAGE_URL)"
  websocket_url="$(environment_value VITE_WEBSOCKET_URL)"
  demo_asset_origin="$(environment_value VITE_DEMO_ASSET_ORIGIN)"

  # k3d nodes in this local profile are arm64. State the platform explicitly so
  # an accidental x86 build cannot be pushed and fail later at Pod scheduling.
  # Docker Desktop otherwise appends a generated provenance attestation whose
  # timestamp changes the *index* digest on every identical local rebuild. This
  # project records one stable runtime digest in images.lock.yaml instead; an
  # SBOM/provenance-attestation policy can be added deliberately in a later task.
  docker build \
    --platform linux/arm64 \
    --provenance=false \
    --file "${DOCKERFILE}" \
    --tag "${IMAGE_NAME}" \
    --build-arg "VITE_OIDC_ISSUER=${oidc_issuer}" \
    --build-arg "VITE_OIDC_CLIENT_ID=${oidc_client_id}" \
    --build-arg "VITE_OIDC_REDIRECT_URI=${redirect_uri}" \
    --build-arg "VITE_OIDC_POST_LOGOUT_REDIRECT_URI=${post_logout_redirect_uri}" \
    --build-arg "VITE_JOB_API_URL=${job_api_url}" \
    --build-arg "VITE_OBJECT_STORAGE_URL=${object_storage_url}" \
    --build-arg "VITE_WEBSOCKET_URL=${websocket_url}" \
    --build-arg "VITE_DEMO_ASSET_ORIGIN=${demo_asset_origin}" \
    "${FRONTEND_DIRECTORY}"

  docker push "${IMAGE_NAME}"

  local local_size_bytes repository_digest
  local_size_bytes="$(docker image inspect --format '{{.Size}}' "${IMAGE_NAME}")"
  repository_digest="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "${IMAGE_NAME}" | awk -v repository="${REGISTRY_HOST}/frontend@" 'index($0, repository) == 1 { print; exit }')"

  if [[ -z "${repository_digest}" ]]; then
    printf 'Image push completed but Docker did not report a repository digest for %s\n' "${IMAGE_NAME}" >&2
    exit 1
  fi

  printf 'Frontend image pushed: %s\n' "${IMAGE_NAME}"
  printf 'Immutable frontend reference: %s\n' "${repository_digest}"
  printf 'Local Docker image size: %s bytes (%s, uncompressed layers)\n' "${local_size_bytes}" "$(human_size "${local_size_bytes}")"
}

main "$@"
