# Shared shell path adapter. Source this file; do not execute it.
# Keeping teardown independent of Ruby allows cleanup when deployment tooling
# is unavailable. All shell clients anchor paths to this versioned library.
readonly CLOUDDSP_SCRIPTS_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly KUBERNETES_DIRECTORY="$(cd -- "${CLOUDDSP_SCRIPTS_DIRECTORY}/.." && pwd)"
readonly REPOSITORY_DIRECTORY="$(cd -- "${KUBERNETES_DIRECTORY}/../.." && pwd)"
