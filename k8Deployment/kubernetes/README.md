# CloudDSP local Kubernetes documentation

The local deployment uses k3d/K3s, independent Helm releases, and one ordered
bootstrap command. Run the commands below from the repository root. The standard
profile uses ARM64 CPU workers and local-path storage; it is for local use.

## Start here

1. Read the [repository README](../../README.md#local) for
   prerequisites, Docker registry configuration, and browser URLs.
2. Use the [operator guide](scripts/README.md) for generated or custom credentials,
   deployment, verification, cleanup, and registry removal.
3. Use the [command reference](command.md) to inspect Helm, Pods, scaling, storage,
   routes, and events during troubleshooting.

```bash
# Deploy a fresh cluster; initialize ignored local credentials if absent.
./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-platform

# Verify the deployed resources and reviewed bootstrap configuration.
./k8Deployment/kubernetes/scripts/deploy-local.sh verify

# Remove the cluster and its stored data; retain local registry and credentials.
./k8Deployment/kubernetes/scripts/deploy-local.sh cleanup

# Separately remove the registry and its images after cluster cleanup.
./k8Deployment/kubernetes/scripts/deploy-local.sh purge-registry
```

A failed fresh bootstrap leaves the partial cluster available for inspection.
It does not automatically roll back or resume. After inspecting the failed stage,
use cleanup before another fresh bootstrap. Cleanup removes PostgreSQL, Keycloak,
RabbitMQ, and MinIO PVC data. Deployment creates a new working cluster; it does
not restore earlier data.

## Current design and service contracts

| Document | Use it for |
| --- | --- |
| [Local architecture and status](../plan.md) | Processing flow, security boundaries, implemented scope, and remaining product gaps. |
| [Deployment orchestration](deployment-orchestration-plan.md) | Stage ordering, failure behavior, verification, and limited reconciliation. |
| [Resource ownership](resource-ownership-map.md) | Helm releases, foundation resources, bootstrap state, and credential ownership. |
| [Job API](services/job-api/README.md) | Authentication, direct-upload contract, owner-filtered history, and artifact snapshots. |
| [Upload intake](services/upload-intake/README.md) | MinIO events, input validation, and durable handoff. |
| [Dispatchers](services/dispatcher/README.md) | Transactional outbox publication and routing. |
| [Demucs](services/demucs/README.md) | Stem processing, task leases, and downstream handoff. |
| [Basic Pitch](services/basic-pitch/README.md) and [ADTOF](services/adtof/README.md) | Pitched-stem and drum MIDI processing. |
| [Frontend container](services/frontend/README.md) | Local image recipe and browser configuration. |
| [Shared frontend](../../frontend/README.md) | Canonical React source and cloud/local build profiles. |

Helm charts live in `helm/`; each chart has its own README. `services/` contains
local backend source, container recipes, bootstrap manifests, and retained
workload comparison baselines. The shared React source lives at repository-root
`frontend/`. Do not apply the baseline workload manifests over Helm-owned objects.

## Tests and historical evidence

`verify` checks deployment gates; it does not submit audio or prove the complete
user workflow. Test commands and their disposable credentials are documented in
`tests/`, including the [source-intake smoke](tests/source-intake-smoke/README.md),
[Demucs smoke](tests/demucs-worker-smoke/README.md), and
[three-job six-stem load test](tests/six-stem-load/README.md).

[Trial records](docs/trials/) preserve dated VM evidence. The
[history index](docs/history/README.md) preserves development notes and the
pre-adoption ownership inventory. Historical commands are contextual records;
use the current guides above to operate the cluster.

Contributors should also read [the Kubernetes instructions](../AGENTS.md).
