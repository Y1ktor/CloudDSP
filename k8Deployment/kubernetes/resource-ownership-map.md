# Local Kubernetes resource ownership

This is the current source-defined ownership and lifecycle summary, reviewed
on **2026-10-03**. It does not claim live release revisions, resource UIDs,
Pod identities, or bound volume IDs. Use the [operator guide](scripts/README.md)
and `deploy-local.sh verify` to inspect the running installation.

The detailed original inventory, adoption evidence, YAML catalog, and
2026-09-26 resource counts are preserved in the
[historical ownership record](docs/history/2026-10-03-resource-ownership-record.md).
Those observations describe the cluster at their recorded stages, not a new
installation or the current state of your machine.

## Long-lived owners

| Resource group | Owner | Lifecycle and boundary |
| --- | --- | --- |
| k3d nodes, Docker network, load balancer, API endpoint | Versioned [cluster configuration](cluster/k3d.yaml) and cluster scripts | Created by fresh foundation; removed by cluster cleanup. |
| Dedicated local image registry | Registry scripts, outside Kubernetes | Mirrors reviewed Docker Hub images; retained by cleanup and removed only by `purge-registry`. |
| `clouddsp-system`, `clouddsp-app`, `clouddsp-data` | [Namespace manifest](cluster/namespaces.yaml) and foundation runner | Outside application Helm releases; deleted with the cluster. |
| CoreDNS, Traefik/CRDs, networking, metrics, local-path provisioner | K3s distribution in `kube-system` | Foundation verifies these components; CloudDSP charts do not take them over. |
| KEDA controllers, RBAC, services, and CRDs | Pinned upstream `keda` Helm release | Locked by [release.lock.yaml](helm/keda/release.lock.yaml); installed before worker scaling resources. |
| PostgreSQL StatefulSet and two Services | [`clouddsp-postgresql`](helm/postgresql/README.md) | Chart owns workload/service definitions; database contents and bootstrap state have separate ownership. |
| MinIO StatefulSet, two Services, S3 Ingress | [`clouddsp-minio`](helm/minio/README.md) | Chart owns delivery/storage workload definitions; buckets, IAM, notification state, and objects are external state. |
| RabbitMQ StatefulSet, three Services, ingress NetworkPolicy | [`clouddsp-rabbitmq`](helm/rabbitmq/README.md) | Chart owns broker workload/services/network rules; vhosts, queues, users, and messages are broker state. |
| Keycloak Deployment, Service, Ingress | [`clouddsp-keycloak`](helm/keycloak/README.md) | Database, realm/client configuration, SMTP, and credentials use versioned bootstrap stages. |
| Mailpit Deployment, SMTP/web Services, Ingress | [`clouddsp-mailpit`](helm/mailpit/README.md) | Local email capture; SMTP remains internal and only its inbox has a browser route. |
| Frontend Deployment, Service, Ingress | [`clouddsp-frontend`](helm/frontend/README.md) | Serves the locked shared-frontend local build through NGINX and the reviewed browser origin. |
| Job API Deployment, Service, Ingress | [`clouddsp-job-api`](helm/job-api/README.md) | Authenticated HTTP routes; database schema and service identities are separate stages. |
| Upload-intake Deployment | [`clouddsp-upload-intake`](helm/upload-intake/README.md) | Outbound consumer without a Service or Ingress. |
| Demucs-only dispatcher Deployment | [`clouddsp-dispatcher`](helm/dispatcher/README.md) | Legacy internal publisher retained in the current bootstrap. |
| Generic dispatcher Deployment | [`clouddsp-generic-dispatcher`](helm/generic-dispatcher/README.md) | Separate internal publisher with explicit generic command and image. |
| Demucs Deployment and ScaledObject | [`clouddsp-demucs`](helm/demucs/README.md) | Long-running CPU worker; KEDA observes RabbitMQ plus durable PostgreSQL work. |
| Basic Pitch Deployment and ScaledObject | [`clouddsp-basic-pitch`](helm/basic-pitch/README.md) | Long-running pitched-stem worker; zero idle replicas is valid. |
| ADTOF Deployment and ScaledObject | [`clouddsp-adtof`](helm/adtof/README.md) | Long-running drum worker; zero idle replicas is valid. |
| Shared TriggerAuthentication resources | [`clouddsp-scaling-auth`](helm/scaling-auth/README.md) | Names runtime observer Secrets; does not own their values. |

The current source has **14 CloudDSP component charts** plus the upstream KEDA
chart installation. Helm ownership applies to the chart's rendered objects;
it does not make a release the owner of every row, object, identity, or
controller-created resource associated with a service.

## Bootstrap, data, and generated resources

| State or resource | Authority | Verification and lifecycle |
| --- | --- | --- |
| Runtime and bootstrap Secrets | Ignored `.local/` sources generated from the [credential catalog](credentials/catalog.yaml) | Charts reference names; initialization validates shared mappings. Populated values are not committed or kept in Helm values. |
| Database roles/databases and grants | Service-specific bootstrap runners and versioned Jobs | Verify actual service identities and permissions; do not infer success from a completed/absent Job alone. |
| PostgreSQL schema | Immutable migration ConfigMaps/Jobs and `schema_migrations` ledger | [Migration runner](scripts/job-api-migrations.rb) checks the exact `v001`–`v009` sequence; fresh install pauses after `v006` for worker roles. |
| Job, task, lease, retry, outbox, and artifact-key rows | PostgreSQL application state | Workers use guarded transactions; neither Helm nor broker queue depth is its source of truth. |
| Keycloak realm, clients, SMTP, and registration policies | Keycloak bootstrap/verification stages | Verify provider configuration independently of Pod readiness or Helm status. |
| RabbitMQ vhosts, users, topology, source and processing queues | RabbitMQ bootstrap/verification stages | Verify durable routes, identities, and restricted permissions; messages are data. |
| MinIO private uploads bucket, IAM, and source notifications | MinIO bootstrap/verification stages | Verify bucket boundaries and external configuration; absence of a private bucket is not repaired by creating an empty replacement during reconcile. |
| Shared MIDI sample bucket and objects | Locked sample catalog/mirror plus narrow public-read policy | Only reviewed non-user samples permit anonymous object reads; user artifacts stay private. |
| PVCs/PVs | StatefulSet claim templates and local-path storage provisioner | Preserve claim names, selectors, service linkage, class, access mode, and capacity during adoption/upgrades. Cleanup of the entire cluster deletes their data. |
| Pods, ReplicaSets, EndpointSlices | Kubernetes workload/service controllers | Generated children; inspect the owning workload rather than treating Pod UIDs as permanent contracts. |
| Worker HPAs and desired replica counts | KEDA controller and Kubernetes HPA | Generated from ScaledObjects; scaling to zero is normal idle behavior. |
| Smoke/load Jobs, RBAC, test identities and data | Explicit test runners | Opt-in checks with their own cleanup; not normal bootstrap ownership. |

## Operating rules

Fresh `bootstrap-platform` runs the versioned 93-stage sequence on an absent
cluster. It initializes credentials, installs charts, and provisions external
state in dependency order. Use `deploy-local.sh stages` for the current exact
list; use [scripts/README.md](scripts/README.md) for prerequisites and commands.

`verify` checks existing resources and external state without changing them.
`reconcile` only runs the audited bootstrap repair paths: it does not adopt
unknown resources, upgrade chart releases, recreate missing private buckets,
rotate credentials, or restore application data. Each release must pass its
chart-specific source/live verification.

The raw service manifests remain useful comparison/bootstrap inputs, but
long-lived Helm-owned objects must be changed through their chart and release
runner. Do not reapply a raw Deployment/StatefulSet/Service to bypass that
owner. Historic adoption backups and UID-preservation evidence remain in the
component/history guides; they are not fresh-install prerequisites.

Normal `cleanup` removes the fixed cluster and its Kubernetes resources,
including PVC data. It retains the image registry and `.local/` configuration.
`purge-registry` removes the retained registry after cleanup. Fresh deployment
creates empty databases and user-artifact storage rather than restoring an old
installation. See the [current architecture/status](../plan.md) and
[operator guide](scripts/README.md) before changing these lifecycle boundaries.
