# CloudDSP local deployment orchestration

## Current status and scope

The one-command fresh deployment is implemented. From the repository root:

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-platform
./k8Deployment/kubernetes/scripts/deploy-local.sh verify
```

`bootstrap-platform` currently composes **93 ordered stages**. Obtain the exact
names from `deploy-local.sh stages`; the source of truth is
[`deploy-local-platform.rb`](scripts/deploy-local-platform.rb). The completed
fresh and retained-registry paths have [dated VM trial records](docs/trials/).
Those records describe the tested revisions and timings, not a guarantee for
another machine.

The goal is a fresh working local cluster. This workflow does not restore
previous PostgreSQL, Keycloak, RabbitMQ, or MinIO data. It uses independent
Helm releases, versioned bootstrap runners, and explicit verification gates.
AWS deployment remains independent; both deployments build the shared
[React frontend](../../frontend/README.md) with their own platform adapters.

## Dependency order

| Phase | Resources or state established | Required gate |
| --- | --- | --- |
| Fresh preflight | Guard absent cluster; generate or validate ignored credential sources. | Valid input and no existing target cluster. |
| Foundation and images | Fixed k3d topology, namespaces, registry, locked public images mirrored by digest. | Nodes/platform Ready and required immutable images available. |
| Data services | PostgreSQL, RabbitMQ, MinIO Helm releases and runtime Secrets. | Service health, Helm specs, and Bound PVCs. |
| Object storage | Private user buckets, public read-only instrument samples, restricted identities, upload notification. | Policies, sample manifest, IAM boundaries, and notification match versioned inputs. |
| Authentication | Keycloak database, Mailpit, Keycloak release, realm and clients. | Database identity, OIDC settings, redirect URIs, and audience configuration. |
| Application state | Job API database, v001–v006 migrations, worker roles, remaining migrations, restricted database/broker identities and processing topology. | Immutable migration ledger, grants, topology, and runtime Secret consistency. |
| API and scaling | Job API, KEDA, scaling authentication. | Protected routes, controller readiness, and scaler prerequisites. |
| Processing and browser | Upload-intake, legacy and generic dispatchers, Demucs, Basic Pitch, ADTOF, frontend. | Release specs, image identity, readiness, scaling, and frontend route/assets. |

The first six migrations establish worker task coordinates before Basic Pitch
and ADTOF roles are provisioned. Later migrations install completion/finalization
and tempo logic that depends on those roles. KEDA and its authentication resources
precede worker `ScaledObject` resources. Secrets are staged before workloads
reference them. The source composition captures these dependencies, including
checks between writes.

## Helm and bootstrap ownership

Each component has an independent chart and release. This provides separately
reviewable manifests and lifecycle checks, similar to sections of a nested
CloudFormation deployment. Helm tracks Kubernetes workload ownership; the root
script orders releases and state bootstrap across service boundaries.

Charts do not embed passwords. Credential runners create namespaced Secrets
from ignored local files. PostgreSQL roles/schema, RabbitMQ topology/users,
MinIO policies/notifications/samples, and Keycloak realm/clients require
service-specific bootstrap and verification beyond a Ready Pod. See the
[current ownership map](resource-ownership-map.md) and
[operator guide](scripts/README.md).

Retained raw workload manifests are adoption comparison baselines. Do not
reapply them over Helm-owned objects. The earlier adoption process and UID
preservation evidence are in the [historical notes](docs/history/README.md).

## Modes and failure handling

| Mode | Behavior |
| --- | --- |
| `secrets-init` | Generate or validate local credential files without contacting the cluster; optional owner-only override input. |
| `stages` | List the current fresh bootstrap composition without contacting the cluster. |
| `plan` | Read-only preflight of source and target context. |
| `bootstrap-platform` | Require an absent cluster and deploy the complete current local platform. |
| `verify` | Check reviewed resources and service bootstrap state in order; stop at first failed gate. |
| `reconcile` | On an existing cluster, verify releases and repair only supported, audited external state. It does not upgrade Helm releases. |
| `cleanup` | Delete the fixed cluster and its Kubernetes resources, including PVC data. Retain local registry and credential files. |
| `purge-registry` | Separately delete the dedicated registry and images after the cluster is absent. |

`prepare` and the individual `bootstrap-*` commands are partial fresh-cluster
trials. Each requires an absent cluster; they are alternatives, not sequential
steps to run before `bootstrap-platform`.

A failed stage exits nonzero and names its boundary. The partial cluster is
left available for diagnosis. Fresh bootstrap is not a resumable installer and
will refuse an existing cluster. Inspect the failed stage, fix its cause, then
clean up before another fresh run. Cleanup destroys that cluster's stored data.
There is no automatic rollback or data recovery.

`reconcile` has a deliberately narrower scope: it covers the reviewed Job API
PostgreSQL and RabbitMQ state plus supported MinIO bucket, notification, and
matching leftover IAM bootstrap state. It verifies other boundaries and stops
on drift. It cannot complete an arbitrary partial installation, rotate
credentials, restore missing data, or perform general chart upgrades.

## Verification and remaining limits

Deployment verification checks reviewed configuration and health boundaries.
It does not create a user job or run all audio smokes. Authentication/upload,
worker completion, duplicate delivery, lease recovery, and load tests have
separate contracts under [`tests/`](tests/).

The standard profile uses ARM64 CPU images and local-path storage. It does not
validate NVIDIA GPU throughput or production high availability. Local linked
media ingestion, user-facing job deletion, UTC quota enforcement, retention
cleanup, and a realtime notification service are outside the current implemented
local API scope; see [current architecture/status](../plan.md) and the
[Job API contract](services/api/README.md).

The original design, adoption sequence, and incremental implementation milestones
are preserved in [orchestration history](docs/history/deployment-orchestration-implementation-notes.md).
