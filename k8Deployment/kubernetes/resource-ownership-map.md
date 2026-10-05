# Local Kubernetes resource ownership

This is the current source-defined ownership and lifecycle summary, reviewed
on **2026-10-04**. It does not claim live release revisions, resource UIDs,
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
| KEDA controllers, RBAC, services, and CRDs | Pinned upstream `keda` Helm release; Flux manages delivery after its [handoff](gitops/keda.md) | Exact upstream 2.20.2 and existing history/specs; one duplicate label input moved to explicit patches; named platform RBAC, runtime CA exceptions, six retained CRDs. Shared auth depends on KEDA; workers depend on auth. |
| Optional Flux controllers, CRDs, RBAC, and Git reconciliation | Dedicated `codex/flux-clouddsp-local` GitOps branch | Opt-in bootstrap after ordinary cluster creation; the explicit root selects Flux and the Mailpit/frontend/Job API/upload-intake/generic-dispatcher/dispatcher/scaling-auth/ADTOF/Basic-Pitch/Demucs HelmRelease/RBAC; check their readiness independently. |
| PostgreSQL StatefulSet and two Services | [`clouddsp-postgresql`](helm/postgresql/README.md); Flux manages delivery after its [handoff](gitops/postgresql.md) | Chart owns workload/service definitions; database contents and bootstrap state have separate ownership. |
| MinIO StatefulSet, two Services, S3 Ingress | [`clouddsp-minio`](helm/minio/README.md) | Chart owns delivery/storage workload definitions; buckets, IAM, notification state, and objects are external state. |
| RabbitMQ StatefulSet, three Services, ingress NetworkPolicy | [`clouddsp-rabbitmq`](helm/rabbitmq/README.md); Flux manages delivery after its [handoff](gitops/rabbitmq.md) | Chart owns broker workload/services/network rules; vhosts, queues, users, and messages are broker state. |
| Keycloak Deployment, Service, Ingress | [`clouddsp-keycloak`](helm/keycloak/README.md) | Database, realm/client configuration, SMTP, and credentials use versioned bootstrap stages. |
| Mailpit Deployment, SMTP/web Services, Ingress | Native [`clouddsp-mailpit`](helm/mailpit/README.md) Helm release; Flux manages its lifecycle after handoff | Existing release and storage remain in `clouddsp-data`; changes are committed to the GitOps branch. SMTP remains internal and only its inbox has a browser route. |
| Frontend Deployment, Service, Ingress | Native [`clouddsp-frontend`](helm/frontend/README.md) Helm release; Flux configuration selects its lifecycle after handoff | Existing release and storage remain in `clouddsp-app`; serves the locked shared-frontend local build through NGINX and the reviewed browser origin. |
| Job API Deployment, Service, Ingress | Native [`clouddsp-job-api`](helm/job-api/README.md) Helm release; Flux configuration selects its lifecycle after handoff | Release/storage remain in `clouddsp-app`; authenticated HTTP routes, database schema, runtime credentials, and service identities retain their separate boundaries. |
| Upload-intake Deployment | Native [`clouddsp-upload-intake`](helm/upload-intake/README.md) Helm release; Flux configuration selects its lifecycle after handoff | Release/storage remain in `clouddsp-app`; outbound consumer without Service/Ingress, with managed Job API readiness dependency and separate broker/MinIO/database bootstrap. |
| Demucs-only dispatcher Deployment | Native [`clouddsp-dispatcher`](helm/dispatcher/README.md) Helm release; Flux manages its lifecycle after handoff | Target/storage stay `clouddsp-app`; retains the Demucs-only image and source. Smoke pause/restore uses committed Flux valuesFiles. |
| Generic dispatcher Deployment | Native [`clouddsp-generic-dispatcher`](helm/generic-dispatcher/README.md) Helm release; Flux manages its lifecycle after handoff | Route-aware outbound publisher; target/storage remain `clouddsp-app`. Smoke pause/restore is committed in its HelmRelease valuesFiles. |
| Demucs Deployment and ScaledObject | [`clouddsp-demucs`](helm/demucs/README.md); Flux manages delivery after its [handoff](gitops/demucs.md) | Long-running CPU worker; KEDA observes RabbitMQ plus durable PostgreSQL work. |
| Basic Pitch Deployment and ScaledObject | [`clouddsp-basic-pitch`](helm/basic-pitch/README.md); Flux manages delivery after its [handoff](gitops/basic-pitch.md) | Long-running pitched-stem worker; zero idle replicas is valid. |
| ADTOF Deployment and ScaledObject | [`clouddsp-adtof`](helm/adtof/README.md); Flux manages delivery after its [handoff](gitops/adtof.md) | Long-running drum worker; KEDA owns the generated HPA and /scale, with an exact Deployment replica drift exception. |
| Shared TriggerAuthentication resources | [`clouddsp-scaling-auth`](helm/scaling-auth/README.md); Flux manages delivery after its [handoff](gitops/scaling-auth.md) | Keeps release/storage in `clouddsp-app`; fixed observer Secret references, no credential values. KEDA and worker scaling retain separate ownership. |

The current source has **14 CloudDSP component charts** plus the upstream KEDA
chart installation. Helm ownership applies to the chart's rendered objects;
it does not make a release the owner of every row, object, identity, or
controller-created resource associated with a service.

## Bootstrap, data, and generated resources

| State or resource | Authority | Verification and lifecycle |
| --- | --- | --- |
| Runtime and bootstrap Secrets | Ignored `.local/` sources generated from the [credential catalog](credentials/catalog.yaml) | Charts reference names; initialization validates shared mappings. Populated values are not committed or kept in Helm values. |
| Database roles/databases and grants | Service-specific bootstrap runners and versioned Jobs | Verify actual service identities and permissions; do not infer success from a completed/absent Job alone. |
| PostgreSQL schema | Immutable migration ConfigMaps/Jobs and `schema_migrations` ledger | [Migration runner](scripts/stages/database/job-api-migrations.rb) checks the exact `v001`–`v009` sequence; fresh install pauses after `v006` for worker roles. |
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
owner. RabbitMQ, Mailpit, frontend, Job API, upload-intake, generic-dispatcher, legacy dispatcher, scaling-auth, ADTOF, Basic Pitch, and Demucs changes use the dedicated GitOps branch after
their Flux handoffs; their current release helpers retain verification and
block direct install/adopt while the matching HelmRelease exists. Mailpit
retains its SMTP smoke command; frontend verifies health, app shell, CSP,
JavaScript/CSS, and direct SPA routes. Job API retains running-digest and
protected-route verification; its disposable authenticated smoke tests real
Keycloak tokens and the PostgreSQL-backed owner job list. Upload-intake keeps
running-digest/Pod verification and the separate source-to-outbox duplicate
notification smoke, with a Git-managed pause/restore for both dispatcher releases after their Flux handoffs.
Other components retain their release
runners. The ordinary fresh bootstrap still does not install Flux automatically.
Do not reapply a raw Deployment/StatefulSet/Service to bypass that
owner. Historic adoption backups and UID-preservation evidence remain in the
component/history guides; they are not fresh-install prerequisites.

Normal `cleanup` removes the fixed cluster and its Kubernetes resources,
including PVC data. It retains the image registry and `.local/` configuration.
`purge-registry` removes the retained registry after cleanup. Fresh deployment
creates empty databases and user-artifact storage rather than restoring an old
installation. See the [current architecture/status](../plan.md) and
[operator guide](scripts/README.md) before changing these lifecycle boundaries.

RabbitMQ delivery now uses its [Flux handoff](gitops/rabbitmq.md). Its native
release retains `clouddsp-data` storage/history and the retained broker claim.
Flux manages the broker's five chart objects; messages, identities, topology,
Secrets and generated PVC/PV stay with their existing authorities. Publishers
and intake depend on RabbitMQ; scaling-auth depends on KEDA and RabbitMQ and
workers depend on authentication. The release helper keeps verify/AMQP smoke,
checks local alarms explicitly, and blocks competing native install/adopt.

PostgreSQL delivery uses its [Flux handoff](gitops/postgresql.md). It reuses
`clouddsp-data` native history and owns only the existing StatefulSet and two
Services. Generated claims/PVs, database contents, roles, grants, migrations,
bootstrap Jobs and credential values remain external. Job API and database
clients wait for its readiness; scaling-auth includes PostgreSQL so workers
inherit that gate. Native install/adopt are reserved once Flux ownership exists.
