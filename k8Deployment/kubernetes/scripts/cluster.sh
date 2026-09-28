#!/usr/bin/env bash
# This is the imperative companion to ../cluster/k3d.yaml.  The YAML declares
# the desired k3d cluster topology, while this wrapper performs host actions a
# YAML file cannot perform: verify local prerequisites, create the Docker-based
# cluster once, and inspect its Kubernetes state.  It intentionally does not
# apply application manifests, install Helm charts, build images, or delete
# data; those are separate, small learning tasks.

# Exit on a failed command, on an unset variable, and on a failed command in a
# pipeline.  This prevents a partial cluster setup from being mistaken for a
# successful one.
set -euo pipefail

# Resolve paths from this script's location instead of the caller's working
# directory.  The script therefore works whether it is invoked from the
# repository root or another shell directory.
readonly SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly KUBERNETES_DIRECTORY="$(cd -- "${SCRIPT_DIRECTORY}/.." && pwd)"
readonly CLUSTER_CONFIG="${KUBERNETES_DIRECTORY}/cluster/k3d.yaml"

# This value must match metadata.name in the declarative k3d configuration.
# k3d derives the kubeconfig context name by prepending `k3d-`, which lets this
# script inspect the intended cluster without relying on the user's current
# kubectl context.
readonly CLUSTER_NAME="clouddsp-local"
readonly KUBECTL_CONTEXT="k3d-${CLUSTER_NAME}"
readonly REGISTRY_NAME="clouddsp-registry.localhost"

usage() {
  cat <<'USAGE'
Usage: ./k8Deployment/kubernetes/scripts/cluster.sh [create|status]

  create  Create the declared k3d cluster when it does not already exist,
          then display its nodes and system Pods. This is the default.
  status  Display the nodes and system Pods of the existing cluster only.
USAGE
}

require_command() {
  # A clear prerequisite error is more useful than letting a later command
  # fail with a shell-specific "command not found" message.
  local command_name="$1"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    printf 'Required command is unavailable: %s\n' "${command_name}" >&2
    exit 1
  fi
}

require_prerequisites() {
  require_command docker
  require_command k3d
  require_command kubectl
  require_command ruby

  # k3d creates K3s server/agent containers through Docker.  Checking the
  # daemon before creation distinguishes a stopped Docker Desktop instance
  # from a k3d or Kubernetes configuration problem.
  if ! docker info >/dev/null 2>&1; then
    printf 'Docker is installed but its daemon is not reachable. Start Docker Desktop and retry.\n' >&2
    exit 1
  fi

  if [[ ! -f "${CLUSTER_CONFIG}" ]]; then
    printf 'The cluster configuration is missing: %s\n' "${CLUSTER_CONFIG}" >&2
    exit 1
  fi
}

cluster_exists() {
  # `k3d cluster list NAME` limits output to the named cluster.  awk checks
  # the first column rather than matching a substring, so a future cluster such
  # as `clouddsp-local-test` is never treated as this project cluster.
  k3d cluster list "${CLUSTER_NAME}" --no-headers | awk -v name="${CLUSTER_NAME}" '$1 == name { found = 1 } END { exit !found }'
}

registry_exists() {
  # The registry is independent of the cluster after normal cleanup. Match
  # only this project's exact name when deciding whether to attach or create.
  k3d registry list "${REGISTRY_NAME}" --no-headers |
    awk -v name="${REGISTRY_NAME}" '$1 == name { found = 1 } END { exit !found }'
}

create_with_retained_registry() (
  # k3d's checked-in config creates a registry on a completely clean machine.
  # For a second cluster, generate a temporary copy replacing only that
  # registry action with `use`. This keeps the reviewed node, port, and label
  # settings identical while attaching the surviving image registry.
  local temporary_config
  temporary_config="$(mktemp "${TMPDIR:-/tmp}/clouddsp-k3d.XXXXXXXX")"
  trap 'rm -f -- "${temporary_config}"' EXIT

  ruby -ryaml -e '
    config = YAML.load_file(ARGV.fetch(0))
    registry = config.fetch("registries")
    expected = {"name" => "clouddsp-registry.localhost", "host" => "127.0.0.1", "hostPort" => "5001"}
    abort "Reviewed registry configuration changed" unless registry.fetch("create") == expected
    registry.delete("create")
    registry["use"] = ["clouddsp-registry.localhost:5001"]
    File.write(ARGV.fetch(1), YAML.dump(config))
  ' "${CLUSTER_CONFIG}" "${temporary_config}"

  k3d cluster create --config "${temporary_config}"
)

show_status() {
  if ! cluster_exists; then
    printf 'Cluster %q does not exist. Run this script with create first.\n' "${CLUSTER_NAME}" >&2
    exit 1
  fi

  # A node is ready when the K3s server or agent has registered with the
  # control plane and can accept Pods.  `-o wide` also shows the internal node
  # addresses, which is useful when learning that host port mappings are not
  # the same thing as Pod or Service networking.
  printf '\nKubernetes nodes in %s:\n' "${KUBECTL_CONTEXT}"
  kubectl --context "${KUBECTL_CONTEXT}" get nodes -o wide

  # K3s packages foundational system Pods such as CoreDNS, Traefik, the local
  # storage provisioner, and the metrics server.  Showing all namespaces makes
  # these platform components visible before CloudDSP workloads are added.
  printf '\nCluster system Pods:\n'
  kubectl --context "${KUBECTL_CONTEXT}" get pods --all-namespaces -o wide
}

main() {
  local action="${1:-create}"

  if [[ "$#" -gt 1 ]]; then
    usage >&2
    exit 2
  fi

  case "${action}" in
    create)
      require_prerequisites
      if cluster_exists; then
        # Creation is idempotent: never recreate a local cluster implicitly,
        # because that could discard the PVC-backed development data we add in
        # later tasks.
        printf 'Cluster %q already exists; leaving it unchanged.\n' "${CLUSTER_NAME}"
      else
        # A first install lets the versioned config create the registry. After
        # ordinary cleanup, reuse its surviving Docker container so every
        # pinned image remains available to the replacement K3s nodes.
        if registry_exists; then
          create_with_retained_registry
        else
          k3d cluster create --config "${CLUSTER_CONFIG}"
        fi
      fi
      show_status
      ;;
    status)
      require_prerequisites
      show_status
      ;;
    -h|--help|help)
      usage
      ;;
    *)
      printf 'Unknown action: %s\n' "${action}" >&2
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
