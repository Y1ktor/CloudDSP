# Resource ownership and adoption history

Archived on **2026-10-03** from `k8Deployment/kubernetes/resource-ownership-map.md` before separating current
instructions from the incremental implementation record. This is historical
evidence: statements about future work, resource counts, release revisions,
Pod/PVC identities, or live state describe the stage at which they were
written and are not current deployment instructions. The ownership inventory
originated on 2026-09-26 and later adoption entries record subsequent work.

Use the [current document](../../resource-ownership-map.md) and the
[operator guide](../../scripts/README.md) for the supported commands.
The original text is preserved below; relative Markdown links have been
adjusted for this history directory.

---

# Local Kubernetes resource and ownership map

This began as the first implementation task in the
[deployment orchestration plan](../../deployment-orchestration-plan.md): an inventory
and proposed ownership map. The counts and source catalog below are the
pre-adoption snapshot from context `k3d-clouddsp-local` on **2026-09-26**;
they exclude subsequently added Helm chart files. Re-run the inventory
and a full spec diff before each further Helm adoption. A matching name in
this document does not prove that live spec and source spec match.

## Scope and counting

- Source: 212 source YAML files under `kubernetes/`, containing 221 YAML
  documents. That includes 216 Kubernetes objects, one k3d `Simple` object,
  and four non-Kubernetes lock/values documents. Example Secret manifests are
  included as *templates only*; they are never deployment inputs.
- Live: 135 named resources in the CloudDSP and KEDA namespaces across the
  selected workload, service, configuration, identity, storage, scaling, and
  Secret kinds, plus three CloudDSP namespaces. Of those 135 namespaced
  resources, 105 match source object identities and 30 have no corresponding
  source object. No Secret data was read. Cluster-scoped KEDA resources are
  listed separately below.
- This map inventories project-authored top-level resources and important
  generated PVCs/HPAs. It deliberately does not list each controller-created
  Pod, ReplicaSet, EndpointSlice, default service token, or unrelated
  `kube-system` object. The packaged Traefik releases and k3s components keep
  their existing owners.
- “Present” in the catalog means that kind + namespace + name exist. It does
  not mean the source file is byte-for-byte deployed. “Absent” is expected for
  completed Jobs with TTL, unapplied examples, and opt-in tests.

## Current versus proposed ownership

| Resource group | Current observation | Proposed owner | Prerequisites and preservation rule |
| --- | --- | --- | --- |
| k3d topology and local registry | Created by k3d from [`cluster/k3d.yaml`](../../cluster/k3d.yaml) | Existing `cluster.sh` | Never recreate the existing cluster during reconcile; preserve API endpoint, registry, ingress ports, and local data. |
| Three CloudDSP namespaces | Live, `managed-by=kubectl` | Cluster manifest/script | Keep outside app Helm releases; preserve names and existing namespace labels. |
| KEDA controller, services, RBAC, six CRDs | Helm release `keda` | Existing pinned KEDA release | Do not adopt into CloudDSP charts; install and verify before `ScaledObject` resources. CRD lifecycle stays separate. |
| Traefik and Traefik CRDs | k3s Helm releases in `kube-system` | k3s | Keep outside CloudDSP orchestration and ownership changes. |
| PostgreSQL StatefulSet and Services | Helm release `clouddsp-postgresql`, revision 1 | Data release [`helm/postgresql`](../../helm/postgresql/README.md) | Protected backup/isolated restore passed; three object UIDs, Pod UID, Service IPs, and bound PVC/PV identity unchanged. Read/write smoke passed. |
| MinIO StatefulSet, Services, and S3 Ingress | Helm release `clouddsp-minio`, revision 1 | Data release [`helm/minio`](../../helm/minio/README.md) | Stopped-volume backup and isolated restore passed; four resource UIDs, post-backup Pod UID, Service IPs, and PVC/PV identity preserved. S3 and restricted-access smokes passed. |
| RabbitMQ StatefulSet, three Services, and ingress NetworkPolicy | Helm release `clouddsp-rabbitmq`, revision 1 | Data release [`helm/rabbitmq`](../../helm/rabbitmq/README.md) | Stopped-volume backup and isolated restore passed; five resource UIDs, post-backup Pod UID, Service IPs, and bound PVC/PV identity preserved. AMQP smoke passed. |
| Keycloak | Helm release `clouddsp-keycloak`, revision 1 | Identity release [`helm/keycloak`](../../helm/keycloak/README.md) | Three resource UIDs, Service IP, Pod UID, issuer route, and PostgreSQL Secret references preserved. OIDC, PKCE authorization, verification email, and authenticated-read smokes passed. |
| Mailpit | Helm release `clouddsp-mailpit`, revision 2 | Support release [`helm/mailpit`](../../helm/mailpit/README.md) | Four existing objects adopted without changing UIDs, Service IPs, Pod UID, or route; use Helm for subsequent changes. |
| Frontend | Helm release `clouddsp-frontend`, revision 1 | App release [`helm/frontend`](../../helm/frontend/README.md) | Three existing objects adopted without changing UIDs, Service IP, Pod UID, or route; preserve the OIDC redirect origin. |
| Legacy dispatcher | Helm release `clouddsp-dispatcher`, revision 3 | App release [`helm/dispatcher`](../../helm/dispatcher/README.md) | Adoption preserved the Deployment and Pod UIDs; the later smoke pause/restore replaced the Pod. One Ready replica uses the Demucs-only digest. |
| Generic dispatcher | Helm release `clouddsp-generic-dispatcher`, revision 3 | App release [`helm/generic-dispatcher`](../../helm/generic-dispatcher/README.md) | Adoption preserved the Deployment and Pod UIDs; the later smoke pause/restore replaced the Pod. Explicit generic command and digest remain. |
| Job API | Helm release `clouddsp-job-api`, revision 1 | App release [`helm/job-api`](../../helm/job-api/README.md) | Three resource UIDs, Service IP, Pod UID, image digest, and protected `/auth` and `/jobs` routes preserved. |
| Upload-intake | Helm release `clouddsp-upload-intake`, revision 1 | App release [`helm/upload-intake`](../../helm/upload-intake/README.md) | Existing Deployment and Pod UIDs, Secret references, and running image digest unchanged; no Service or route. |
| Demucs Deployment and ScaledObject | Helm release `clouddsp-demucs`, revision 1 | Worker release [`helm/demucs`](../../helm/demucs/README.md) | Both resource UIDs, scaler generation, generated HPA UID, and zero idle Pods preserved. The chart retains the RabbitMQ and PostgreSQL triggers and KEDA's `/scale` ownership. |
| ADTOF Deployment and ScaledObject | Helm release `clouddsp-adtof`, revision 1 | Worker release [`helm/adtof`](../../helm/adtof/README.md) | Existing UIDs, scaler generation, generated HPA UID, and zero idle Pods preserved. Repaired one-drum smoke passed with verified MIDI/tempo and scoped cleanup. |
| Basic Pitch Deployment and ScaledObject | Helm release `clouddsp-basic-pitch`, revision 5 | Worker release [`helm/basic-pitch`](../../helm/basic-pitch/README.md) | Existing Deployment, ScaledObject, and generated HPA UIDs preserved. The scaler combines RabbitMQ backlog with the read-only PostgreSQL task count and holds scale-in for six minutes; the worker Pod sets the portable Numba CPU target. |
| Two shared `TriggerAuthentication` resources | Helm release `clouddsp-scaling-auth`, revision 1 | Shared release [`helm/scaling-auth`](../../helm/scaling-auth/README.md) | Both UIDs and spec generations, three Ready ScaledObject UIDs, generated HPA UIDs, and worker Deployment UIDs were preserved. Secret values remain outside Helm. |
| Database migrations, service bootstrap Jobs and their ConfigMaps | ConfigMaps live; Jobs currently absent after completion/TTL | Versioned one-shot scripts, outside long-lived releases | Verify the schema ledger or external service state; never infer completion solely from a missing/present Job. Preserve migration IDs and existing grants/policies. |
| Runtime and bootstrap Secrets | Live names mostly match ignored local files; examples are committed | Ignored local Secret inputs and narrow scripts | Charts only refer to Secret names. Avoid Helm ownership of values; rotation is separate. |
| Smoke/load resources | Some test RBAC, ConfigMaps, and Secrets remain; Jobs absent | Explicit test commands only | Exclude from normal bootstrap/reconcile. Review retained credentials and test data separately. |

The source-to-outbox smoke now has one fixed-name, label-constrained
RabbitMQ-management ingress exception in the versioned NetworkPolicy. It is
limited to `source-to-outbox-smoke` in `clouddsp-data` on TCP 15672; normal
application Pods and other integration Jobs remain excluded. The test passed
after that rule was applied, and its disposable Job was deleted.

## Stateful identity that Helm must preserve

The live StatefulSet selectors, headless Service names, claim template names,
storage classes, access modes, and requested capacities matched the current
source manifests when inspected. Record these values as adoption invariants;
matching them today does not waive a fresh diff at adoption time.

| StatefulSet in `clouddsp-data` | Headless Service | Claim template | Current bound PVC → PV | Class / access / requested size |
| --- | --- | --- | --- | --- |
| `clouddsp-postgresql` | `clouddsp-postgresql-headless` | `postgres-data` | `postgres-data-clouddsp-postgresql-0` → `pvc-58ab825e-907b-43f7-9178-bf2940cca9f5` | `local-path` / `ReadWriteOnce` / 5Gi |
| `clouddsp-minio` | `clouddsp-minio-headless` | `minio-data` | `minio-data-clouddsp-minio-0` → `pvc-a98e0c0c-26a6-4377-b24d-86be63b18b8f` | `local-path` / `ReadWriteOnce` / 10Gi |
| `clouddsp-rabbitmq` | `clouddsp-rabbitmq-headless` | `rabbitmq-data` | `rabbitmq-data-clouddsp-rabbitmq-0` → `pvc-180f50b2-cc60-4af3-b5f4-2e828437ca21` | `local-path` / `ReadWriteOnce` / 5Gi |

All three PVCs were Bound. A chart render that changes any of these names,
selectors, claims, or Service linkage must stop before adoption. Existing
PVCs are data, not resources to delete and recreate to satisfy Helm.

## Dependency and preservation codes used in the catalog

The source catalog below uses a short owner code. These codes define the
prerequisites and preservation rules for **every row** assigned to the code.
An individual Job can have additional requirements in its manifest or README.

| Code | Future owner / stage | Prerequisites | Preserve / verify |
| --- | --- | --- | --- |
| `cluster` | k3d/namespace script | Docker, k3d, explicit local context | Keep cluster, namespace identity, registry, and data. |
| `keda` | Existing KEDA Helm release | Cluster and pinned chart/values | Keep release/CRD ownership with KEDA; no CloudDSP takeover. |
| `data:postgresql` | PostgreSQL Helm release | Data namespace, ignored DB Secret, locked image | Preserve StatefulSet/Service selectors and PVC identity. |
| `data:rabbitmq` | RabbitMQ Helm release | Data namespace, ignored broker Secret, locked image | Preserve broker StatefulSet/PVC, Service names, and network policy behavior. |
| `data:minio` | MinIO Helm release | Data namespace, ignored root/notification Secrets, locked image | Preserve storage StatefulSet/PVC, S3 route, buckets and private policies. |
| `support:mailpit` | Mailpit Helm release | Data namespace, locked image | Preserve SMTP and browser Service names/routes. |
| `identity:keycloak` | Keycloak Helm release | PostgreSQL and Keycloak DB bootstrap/Secret | Preserve realm issuer, Service/Ingress route, and existing client state. |
| `app:api` | Job API Helm release | Schema ledger current, DB/MinIO access, OIDC issuer/audience, runtime Secrets | Preserve API route, Service selector, job contract, and image digest. |
| `app:intake` | Upload-intake Helm release | Outbox schema/role, source queue/identity, MinIO source policy/notification | Preserve intake consumer identity and durable transition rules. |
| `app:dispatcher` | Dispatcher Helm release(s) | Outbox schema/role and RabbitMQ publisher identity | Preserve both Deployment names and message/lease behavior. |
| `app:frontend` | Frontend Helm release | Keycloak browser client and public API/MinIO URLs | Preserve ingress route, CSP/public build config, and image digest. |
| `worker:demucs`, `worker:basic-pitch`, `worker:adtof` | Worker Helm releases | DB role, RabbitMQ queue/user, MinIO policy/user, locked image; KEDA/observer auth before scaler | Preserve worker Deployment name and `scaleTargetRef`; zero idle replicas valid. |
| `scaling-auth` | Shared TriggerAuthentication Helm release | KEDA CRDs, observer users, app-namespace Secrets, network path | Preserve exact observer Secret refs; never place credentials in values. |
| `migration` | Versioned migration runner | PostgreSQL, schema-owner Secret, preceding migration | Keep immutable SQL/IDs and `schema_migrations` ledger; verify before skip/rerun. |
| `bootstrap` | Versioned service-specific one-shot runner | Named backing service, admin/bootstrap Secret, required prior schema/topology/policy | Verify external state and remove only temporary credentials after success. No automatic rerun from Job absence. |
| `secret` | Ignored local configuration | Secret template only; actual file outside Git | Template never applied; live name only proves existence, not content or rotation. |
| `test` | Explicit smoke/load command | Relevant deployed services and test-only credentials | Exclude from normal deploy; test data/credentials have separate cleanup. |
| `lock` | Reviewed build/deploy input | Versioned source, image/model/chart review | Never auto-update during reconciliation. |

## Source manifest catalog

The next table lists every YAML document in the pre-adoption source inventory;
it does not include charts and values files added after that snapshot.
For multi-document files, `#doc N` identifies the document. Namespace is
shown with each Kubernetes object; `—` means cluster-scoped or non-Kubernetes
configuration. Live status is identity-only. Secret rows expose names only.

| Source file | Kind / name | Namespace | Live | Proposed owner |
| --- | --- | --- | --- | --- |
| [cluster/k3d.yaml](../../cluster/k3d.yaml) | `Simple/clouddsp-local` | — | n/a | `cluster` |
| [cluster/namespaces.yaml](../../cluster/namespaces.yaml) #doc 1 | `Namespace/clouddsp-system` | — | Present | `cluster` |
| [cluster/namespaces.yaml](../../cluster/namespaces.yaml) #doc 2 | `Namespace/clouddsp-app` | — | Present | `cluster` |
| [cluster/namespaces.yaml](../../cluster/namespaces.yaml) #doc 3 | `Namespace/clouddsp-data` | — | Present | `cluster` |
| [helm/keda/keda-demucs-postgresql-credentials.secret.example.yaml](../../helm/keda/keda-demucs-postgresql-credentials.secret.example.yaml) | `Secret/clouddsp-keda-demucs-postgresql-credentials` | `clouddsp-app` | Present | `secret` |
| [helm/keda/keda-demucs-postgresql-trigger-authentication.yaml](../../helm/keda/keda-demucs-postgresql-trigger-authentication.yaml) | `TriggerAuthentication/clouddsp-demucs-postgresql-scaler-authentication` | `clouddsp-app` | Present | `scaling-auth` |
| [helm/keda/keda-rabbitmq-scaler-credentials.secret.example.yaml](../../helm/keda/keda-rabbitmq-scaler-credentials.secret.example.yaml) | `Secret/clouddsp-keda-rabbitmq-scaler-credentials` | `clouddsp-app` | Present | `secret` |
| [helm/keda/keda-rabbitmq-scaler-trigger-authentication.yaml](../../helm/keda/keda-rabbitmq-scaler-trigger-authentication.yaml) | `TriggerAuthentication/clouddsp-rabbitmq-scaler-authentication` | `clouddsp-app` | Present | `scaling-auth` |
| [helm/keda/release.lock.yaml](../../helm/keda/release.lock.yaml) | `configuration` | — | n/a | `keda` |
| [helm/keda/values.yaml](../../helm/keda/values.yaml) | `configuration` | — | n/a | `keda` |
| [images.lock.yaml](../../images.lock.yaml) | `configuration` | — | n/a | `lock` |
| [services/adtof/adtof-database-bootstrap-credentials.secret.example.yaml](../../services/adtof/adtof-database-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-adtof-database-bootstrap-credentials` | `clouddsp-data` | Absent | `secret` |
| [services/adtof/adtof-database-bootstrap-job.yaml](../../services/adtof/adtof-database-bootstrap-job.yaml) | `Job/adtof-database-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/adtof/adtof-database-credentials.secret.example.yaml](../../services/adtof/adtof-database-credentials.secret.example.yaml) | `Secret/clouddsp-adtof-database-credentials` | `clouddsp-app` | Present | `secret` |
| [services/adtof/adtof-deployment.yaml](../../services/adtof/adtof-deployment.yaml) | `Deployment/clouddsp-adtof` | `clouddsp-app` | Present | `worker:adtof` |
| [services/adtof/adtof-minio-bootstrap-credentials.secret.example.yaml](../../services/adtof/adtof-minio-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-adtof-minio-bootstrap-credentials` | `clouddsp-data` | Absent | `secret` |
| [services/adtof/adtof-minio-credentials.secret.example.yaml](../../services/adtof/adtof-minio-credentials.secret.example.yaml) | `Secret/clouddsp-adtof-minio-credentials` | `clouddsp-app` | Present | `secret` |
| [services/adtof/adtof-minio-runtime-policy-smoke-job.yaml](../../services/adtof/adtof-minio-runtime-policy-smoke-job.yaml) | `Job/adtof-minio-runtime-policy-smoke` | `clouddsp-app` | Absent | `test` |
| [services/adtof/adtof-rabbitmq-credentials.secret.example.yaml](../../services/adtof/adtof-rabbitmq-credentials.secret.example.yaml) | `Secret/clouddsp-adtof-rabbitmq-credentials` | `clouddsp-app` | Present | `secret` |
| [services/adtof/adtof-scaledobject.yaml](../../services/adtof/adtof-scaledobject.yaml) | `ScaledObject/clouddsp-adtof-rabbitmq-scaler` | `clouddsp-app` | Present | `worker:adtof` |
| [services/api/job-api-database-bootstrap-credentials.secret.example.yaml](../../services/api/job-api-database-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-job-api-database-bootstrap-credentials` | `clouddsp-data` | Absent | `secret` |
| [services/api/job-api-database-bootstrap-job.yaml](../../services/api/job-api-database-bootstrap-job.yaml) | `Job/job-api-database-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/api/job-api-database-credentials.secret.example.yaml](../../services/api/job-api-database-credentials.secret.example.yaml) | `Secret/clouddsp-job-api-database-credentials` | `clouddsp-app` | Present | `secret` |
| [services/api/job-api-deployment.yaml](../../services/api/job-api-deployment.yaml) | `Deployment/clouddsp-job-api` | `clouddsp-app` | Present | `app:api` |
| [services/api/job-api-ingress.yaml](../../services/api/job-api-ingress.yaml) | `Ingress/clouddsp-job-api` | `clouddsp-app` | Present | `app:api` |
| [services/api/job-api-minio-credentials.secret.example.yaml](../../services/api/job-api-minio-credentials.secret.example.yaml) | `Secret/clouddsp-job-api-minio-credentials` | `clouddsp-app` | Present | `secret` |
| [services/api/job-api-schema-migration-v001-configmap.yaml](../../services/api/job-api-schema-migration-v001-configmap.yaml) | `ConfigMap/job-api-schema-migration-v001` | `clouddsp-app` | Present | `migration` |
| [services/api/job-api-schema-migration-v001-job.yaml](../../services/api/job-api-schema-migration-v001-job.yaml) | `Job/job-api-schema-migration-v001` | `clouddsp-app` | Absent | `migration` |
| [services/api/job-api-schema-migration-v002-outbox-configmap.yaml](../../services/api/job-api-schema-migration-v002-outbox-configmap.yaml) | `ConfigMap/job-api-schema-migration-v002-outbox` | `clouddsp-app` | Present | `migration` |
| [services/api/job-api-schema-migration-v002-outbox-job.yaml](../../services/api/job-api-schema-migration-v002-outbox-job.yaml) | `Job/job-api-schema-migration-v002-outbox` | `clouddsp-app` | Absent | `migration` |
| [services/api/job-api-schema-migration-v003-processing-tasks-configmap.yaml](../../services/api/job-api-schema-migration-v003-processing-tasks-configmap.yaml) | `ConfigMap/job-api-schema-migration-v003-processing-tasks` | `clouddsp-app` | Present | `migration` |
| [services/api/job-api-schema-migration-v003-processing-tasks-job.yaml](../../services/api/job-api-schema-migration-v003-processing-tasks-job.yaml) | `Job/job-api-schema-migration-v003-processing-tasks` | `clouddsp-app` | Absent | `migration` |
| [services/api/job-api-schema-migration-v004-downstream-outbox-configmap.yaml](../../services/api/job-api-schema-migration-v004-downstream-outbox-configmap.yaml) | `ConfigMap/job-api-schema-migration-v004-downstream-outbox` | `clouddsp-app` | Present | `migration` |
| [services/api/job-api-schema-migration-v004-downstream-outbox-job.yaml](../../services/api/job-api-schema-migration-v004-downstream-outbox-job.yaml) | `Job/job-api-schema-migration-v004-downstream-outbox` | `clouddsp-app` | Absent | `migration` |
| [services/api/job-api-schema-migration-v005-basic-pitch-processing-tasks-configmap.yaml](../../services/api/job-api-schema-migration-v005-basic-pitch-processing-tasks-configmap.yaml) | `ConfigMap/job-api-schema-migration-v005-basic-pitch-processing-tasks` | `clouddsp-app` | Present | `migration` |
| [services/api/job-api-schema-migration-v005-basic-pitch-processing-tasks-job.yaml](../../services/api/job-api-schema-migration-v005-basic-pitch-processing-tasks-job.yaml) | `Job/job-api-schema-migration-v005-basic-pitch-processing-tasks` | `clouddsp-app` | Absent | `migration` |
| [services/api/job-api-schema-migration-v006-adtof-processing-tasks-configmap.yaml](../../services/api/job-api-schema-migration-v006-adtof-processing-tasks-configmap.yaml) | `ConfigMap/job-api-schema-migration-v006-adtof-processing-tasks` | `clouddsp-app` | Present | `migration` |
| [services/api/job-api-schema-migration-v006-adtof-processing-tasks-job.yaml](../../services/api/job-api-schema-migration-v006-adtof-processing-tasks-job.yaml) | `Job/job-api-schema-migration-v006-adtof-processing-tasks` | `clouddsp-app` | Absent | `migration` |
| [services/api/job-api-schema-migration-v007-job-finalization-configmap.yaml](../../services/api/job-api-schema-migration-v007-job-finalization-configmap.yaml) | `ConfigMap/job-api-schema-migration-v007-job-finalization` | `clouddsp-app` | Present | `migration` |
| [services/api/job-api-schema-migration-v007-job-finalization-job.yaml](../../services/api/job-api-schema-migration-v007-job-finalization-job.yaml) | `Job/job-api-schema-migration-v007-job-finalization` | `clouddsp-app` | Absent | `migration` |
| [services/api/job-api-schema-migration-v008-partial-task-finalization-configmap.yaml](../../services/api/job-api-schema-migration-v008-partial-task-finalization-configmap.yaml) | `ConfigMap/job-api-schema-migration-v008-partial-task-finalization` | `clouddsp-app` | Present | `migration` |
| [services/api/job-api-schema-migration-v008-partial-task-finalization-job.yaml](../../services/api/job-api-schema-migration-v008-partial-task-finalization-job.yaml) | `Job/job-api-schema-migration-v008-partial-task-finalization` | `clouddsp-app` | Absent | `migration` |
| [services/api/job-api-schema-migration-v009-tempo-resolution-configmap.yaml](../../services/api/job-api-schema-migration-v009-tempo-resolution-configmap.yaml) | `ConfigMap/job-api-schema-migration-v009-tempo-resolution` | `clouddsp-app` | Present | `migration` |
| [services/api/job-api-schema-migration-v009-tempo-resolution-job.yaml](../../services/api/job-api-schema-migration-v009-tempo-resolution-job.yaml) | `Job/job-api-schema-migration-v009-tempo-resolution` | `clouddsp-app` | Absent | `migration` |
| [services/api/job-api-service.yaml](../../services/api/job-api-service.yaml) | `Service/clouddsp-job-api` | `clouddsp-app` | Present | `app:api` |
| [services/basic-pitch/basic-pitch-database-bootstrap-credentials.secret.example.yaml](../../services/basic-pitch/basic-pitch-database-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-basic-pitch-database-bootstrap-credentials` | `clouddsp-data` | Absent | `secret` |
| [services/basic-pitch/basic-pitch-database-bootstrap-job.yaml](../../services/basic-pitch/basic-pitch-database-bootstrap-job.yaml) | `Job/basic-pitch-database-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/basic-pitch/basic-pitch-database-credentials.secret.example.yaml](../../services/basic-pitch/basic-pitch-database-credentials.secret.example.yaml) | `Secret/clouddsp-basic-pitch-database-credentials` | `clouddsp-app` | Present | `secret` |
| [services/basic-pitch/basic-pitch-deployment.yaml](../../services/basic-pitch/basic-pitch-deployment.yaml) | `Deployment/clouddsp-basic-pitch` | `clouddsp-app` | Present | `worker:basic-pitch` |
| [services/basic-pitch/basic-pitch-minio-credentials.secret.example.yaml](../../services/basic-pitch/basic-pitch-minio-credentials.secret.example.yaml) | `Secret/clouddsp-basic-pitch-minio-credentials` | `clouddsp-app` | Present | `secret` |
| [services/basic-pitch/basic-pitch-rabbitmq-credentials.secret.example.yaml](../../services/basic-pitch/basic-pitch-rabbitmq-credentials.secret.example.yaml) | `Secret/clouddsp-basic-pitch-rabbitmq-credentials` | `clouddsp-app` | Present | `secret` |
| [services/basic-pitch/basic-pitch-scaledobject.yaml](../../services/basic-pitch/basic-pitch-scaledobject.yaml) | `ScaledObject/clouddsp-basic-pitch-rabbitmq-scaler` | `clouddsp-app` | Present | `worker:basic-pitch` |
| [services/demucs/demucs-database-bootstrap-credentials.secret.example.yaml](../../services/demucs/demucs-database-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-demucs-database-bootstrap-credentials` | `clouddsp-data` | Absent | `secret` |
| [services/demucs/demucs-database-bootstrap-job.yaml](../../services/demucs/demucs-database-bootstrap-job.yaml) | `Job/demucs-database-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/demucs/demucs-database-credentials.secret.example.yaml](../../services/demucs/demucs-database-credentials.secret.example.yaml) | `Secret/clouddsp-demucs-database-credentials` | `clouddsp-app` | Present | `secret` |
| [services/demucs/demucs-deployment.yaml](../../services/demucs/demucs-deployment.yaml) | `Deployment/clouddsp-demucs` | `clouddsp-app` | Present | `worker:demucs` |
| [services/demucs/demucs-downstream-outbox-permissions-bootstrap-job.yaml](../../services/demucs/demucs-downstream-outbox-permissions-bootstrap-job.yaml) | `Job/demucs-downstream-outbox-permissions-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/demucs/demucs-minio-credentials.secret.example.yaml](../../services/demucs/demucs-minio-credentials.secret.example.yaml) | `Secret/clouddsp-demucs-minio-credentials` | `clouddsp-app` | Present | `secret` |
| [services/demucs/demucs-rabbitmq-credentials.secret.example.yaml](../../services/demucs/demucs-rabbitmq-credentials.secret.example.yaml) | `Secret/clouddsp-demucs-rabbitmq-credentials` | `clouddsp-app` | Present | `secret` |
| [services/demucs/demucs-recovery-verifier-bootstrap-job.yaml](../../services/demucs/demucs-recovery-verifier-bootstrap-job.yaml) | `Job/demucs-recovery-verifier-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/demucs/demucs-scaledobject.yaml](../../services/demucs/demucs-scaledobject.yaml) | `ScaledObject/clouddsp-demucs-rabbitmq-scaler` | `clouddsp-app` | Present | `worker:demucs` |
| [services/demucs/model-artifacts.lock.yaml](../../services/demucs/model-artifacts.lock.yaml) | `configuration` | — | n/a | `lock` |
| [services/dispatcher/dispatcher-database-bootstrap-job.yaml](../../services/dispatcher/dispatcher-database-bootstrap-job.yaml) | `Job/dispatcher-database-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/dispatcher/dispatcher-database-credentials.secret.example.yaml](../../services/dispatcher/dispatcher-database-credentials.secret.example.yaml) | `Secret/clouddsp-dispatcher-database-credentials` | `clouddsp-app` | Present | `secret` |
| [services/dispatcher/dispatcher-deployment.yaml](../../services/dispatcher/dispatcher-deployment.yaml) | `Deployment/clouddsp-dispatcher` | `clouddsp-app` | Present | `app:dispatcher` |
| [services/dispatcher/dispatcher-generic-deployment.yaml](../../services/dispatcher/dispatcher-generic-deployment.yaml) | `Deployment/clouddsp-generic-dispatcher` | `clouddsp-app` | Present | `app:dispatcher` |
| [services/dispatcher/dispatcher-rabbitmq-credentials.secret.example.yaml](../../services/dispatcher/dispatcher-rabbitmq-credentials.secret.example.yaml) | `Secret/clouddsp-dispatcher-rabbitmq-credentials` | `clouddsp-app` | Present | `secret` |
| [services/dispatcher/dispatcher-smoke-rabbitmq-credentials.secret.example.yaml](../../services/dispatcher/dispatcher-smoke-rabbitmq-credentials.secret.example.yaml) | `Secret/clouddsp-dispatcher-smoke-rabbitmq-credentials` | `clouddsp-app` | Present | `test` |
| [services/frontend/frontend-deployment.yaml](../../services/frontend/frontend-deployment.yaml) | `Deployment/clouddsp-frontend` | `clouddsp-app` | Present | `app:frontend` |
| [services/frontend/frontend-ingress.yaml](../../services/frontend/frontend-ingress.yaml) | `Ingress/clouddsp-frontend` | `clouddsp-app` | Present | `app:frontend` |
| [services/frontend/frontend-service.yaml](../../services/frontend/frontend-service.yaml) | `Service/clouddsp-frontend` | `clouddsp-app` | Present | `app:frontend` |
| [services/keycloak/keycloak-bootstrap-admin.secret.example.yaml](../../services/keycloak/keycloak-bootstrap-admin.secret.example.yaml) | `Secret/clouddsp-keycloak-bootstrap-admin` | `clouddsp-data` | Present | `secret` |
| [services/keycloak/keycloak-database-bootstrap-job.yaml](../../services/keycloak/keycloak-database-bootstrap-job.yaml) | `Job/keycloak-database-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/keycloak/keycloak-database-credentials.secret.example.yaml](../../services/keycloak/keycloak-database-credentials.secret.example.yaml) | `Secret/clouddsp-keycloak-database-credentials` | `clouddsp-data` | Present | `secret` |
| [services/keycloak/keycloak-deployment.yaml](../../services/keycloak/keycloak-deployment.yaml) | `Deployment/clouddsp-keycloak` | `clouddsp-data` | Present | `identity:keycloak` |
| [services/keycloak/keycloak-frontend-oidc-client-bootstrap-job.yaml](../../services/keycloak/keycloak-frontend-oidc-client-bootstrap-job.yaml) | `Job/keycloak-frontend-oidc-client-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/keycloak/keycloak-ingress.yaml](../../services/keycloak/keycloak-ingress.yaml) | `Ingress/clouddsp-keycloak` | `clouddsp-data` | Present | `identity:keycloak` |
| [services/keycloak/keycloak-job-api-audience-bootstrap-job.yaml](../../services/keycloak/keycloak-job-api-audience-bootstrap-job.yaml) | `Job/keycloak-job-api-audience-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/keycloak/keycloak-realm-bootstrap-job.yaml](../../services/keycloak/keycloak-realm-bootstrap-job.yaml) | `Job/keycloak-realm-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/keycloak/keycloak-realm-registration-policy-job.yaml](../../services/keycloak/keycloak-realm-registration-policy-job.yaml) | `Job/keycloak-realm-registration-policy` | `clouddsp-data` | Absent | `bootstrap` |
| [services/keycloak/keycloak-realm-smtp-config-job.yaml](../../services/keycloak/keycloak-realm-smtp-config-job.yaml) | `Job/keycloak-realm-smtp-config` | `clouddsp-data` | Absent | `bootstrap` |
| [services/keycloak/keycloak-registration-password-form-policy-job.yaml](../../services/keycloak/keycloak-registration-password-form-policy-job.yaml) | `Job/keycloak-registration-password-form-policy` | `clouddsp-data` | Absent | `bootstrap` |
| [services/keycloak/keycloak-service.yaml](../../services/keycloak/keycloak-service.yaml) | `Service/clouddsp-keycloak` | `clouddsp-data` | Present | `identity:keycloak` |
| [services/mailpit/mailpit-deployment.yaml](../../services/mailpit/mailpit-deployment.yaml) | `Deployment/clouddsp-mailpit` | `clouddsp-data` | Present | `support:mailpit` |
| [services/mailpit/mailpit-ingress.yaml](../../services/mailpit/mailpit-ingress.yaml) | `Ingress/clouddsp-mailpit` | `clouddsp-data` | Present | `support:mailpit` |
| [services/mailpit/mailpit-services.yaml](../../services/mailpit/mailpit-services.yaml) #doc 1 | `Service/clouddsp-mailpit-smtp` | `clouddsp-data` | Present | `support:mailpit` |
| [services/mailpit/mailpit-services.yaml](../../services/mailpit/mailpit-services.yaml) #doc 2 | `Service/clouddsp-mailpit` | `clouddsp-data` | Present | `support:mailpit` |
| [services/minio/minio-adtof-artifacts-bootstrap-job.yaml](../../services/minio/minio-adtof-artifacts-bootstrap-job.yaml) | `Job/minio-adtof-artifacts-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/minio/minio-adtof-artifacts-policy-v001-configmap.yaml](../../services/minio/minio-adtof-artifacts-policy-v001-configmap.yaml) | `ConfigMap/clouddsp-adtof-artifacts-policy-v001` | `clouddsp-data` | Present | `bootstrap` |
| [services/minio/minio-basic-pitch-artifacts-bootstrap-credentials.secret.example.yaml](../../services/minio/minio-basic-pitch-artifacts-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-basic-pitch-minio-bootstrap-credentials` | `clouddsp-data` | Absent | `secret` |
| [services/minio/minio-basic-pitch-artifacts-bootstrap-job.yaml](../../services/minio/minio-basic-pitch-artifacts-bootstrap-job.yaml) | `Job/minio-basic-pitch-artifacts-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/minio/minio-basic-pitch-artifacts-policy-v001-configmap.yaml](../../services/minio/minio-basic-pitch-artifacts-policy-v001-configmap.yaml) | `ConfigMap/clouddsp-basic-pitch-artifacts-policy-v001` | `clouddsp-data` | Present | `bootstrap` |
| [services/minio/minio-demucs-artifacts-bootstrap-credentials.secret.example.yaml](../../services/minio/minio-demucs-artifacts-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-demucs-minio-bootstrap-credentials` | `clouddsp-data` | Absent | `secret` |
| [services/minio/minio-demucs-artifacts-bootstrap-job.yaml](../../services/minio/minio-demucs-artifacts-bootstrap-job.yaml) | `Job/minio-demucs-artifacts-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/minio/minio-demucs-artifacts-policy-v001-configmap.yaml](../../services/minio/minio-demucs-artifacts-policy-v001-configmap.yaml) | `ConfigMap/clouddsp-demucs-artifacts-policy-v001` | `clouddsp-data` | Present | `bootstrap` |
| [services/minio/minio-headless-service.yaml](../../services/minio/minio-headless-service.yaml) | `Service/clouddsp-minio-headless` | `clouddsp-data` | Present | `data:minio` |
| [services/minio/minio-job-api-artifact-read-bootstrap-job.yaml](../../services/minio/minio-job-api-artifact-read-bootstrap-job.yaml) | `Job/minio-job-api-artifact-read-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/minio/minio-job-api-artifact-read-policy-v003-configmap.yaml](../../services/minio/minio-job-api-artifact-read-policy-v003-configmap.yaml) | `ConfigMap/clouddsp-job-api-artifact-read-policy-v003` | `clouddsp-data` | Present | `bootstrap` |
| [services/minio/minio-job-api-uploads-bootstrap-credentials.secret.example.yaml](../../services/minio/minio-job-api-uploads-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-job-api-minio-bootstrap-credentials` | `clouddsp-data` | Present | `secret` |
| [services/minio/minio-job-api-uploads-bootstrap-job.yaml](../../services/minio/minio-job-api-uploads-bootstrap-job.yaml) | `Job/minio-job-api-uploads-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/minio/minio-job-api-uploads-policy-v002-configmap.yaml](../../services/minio/minio-job-api-uploads-policy-v002-configmap.yaml) | `ConfigMap/clouddsp-job-api-uploads-policy-v002` | `clouddsp-data` | Present | `bootstrap` |
| [services/minio/minio-root-credentials.secret.example.yaml](../../services/minio/minio-root-credentials.secret.example.yaml) | `Secret/clouddsp-minio-root-credentials` | `clouddsp-data` | Present | `secret` |
| [services/minio/minio-s3-ingress.yaml](../../services/minio/minio-s3-ingress.yaml) | `Ingress/clouddsp-minio-s3` | `clouddsp-data` | Present | `data:minio` |
| [services/minio/minio-service.yaml](../../services/minio/minio-service.yaml) | `Service/clouddsp-minio` | `clouddsp-data` | Present | `data:minio` |
| [services/minio/minio-source-intake-notification-bootstrap-job.yaml](../../services/minio/minio-source-intake-notification-bootstrap-job.yaml) | `Job/minio-source-intake-notification-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/minio/minio-source-intake-rabbitmq-credentials.secret.example.yaml](../../services/minio/minio-source-intake-rabbitmq-credentials.secret.example.yaml) | `Secret/clouddsp-minio-source-intake-rabbitmq-credentials` | `clouddsp-data` | Present | `secret` |
| [services/minio/minio-statefulset.yaml](../../services/minio/minio-statefulset.yaml) | `StatefulSet/clouddsp-minio` | `clouddsp-data` | Present | `data:minio` |
| [services/minio/minio-upload-intake-source-read-bootstrap-job.yaml](../../services/minio/minio-upload-intake-source-read-bootstrap-job.yaml) | `Job/minio-upload-intake-source-read-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/minio/minio-upload-intake-source-read-policy-v001-configmap.yaml](../../services/minio/minio-upload-intake-source-read-policy-v001-configmap.yaml) | `ConfigMap/clouddsp-upload-intake-source-read-policy-v001` | `clouddsp-data` | Present | `bootstrap` |
| [services/postgresql/postgresql-credentials.secret.example.yaml](../../services/postgresql/postgresql-credentials.secret.example.yaml) | `Secret/clouddsp-postgresql-credentials` | `clouddsp-data` | Present | `secret` |
| [services/postgresql/postgresql-headless-service.yaml](../../services/postgresql/postgresql-headless-service.yaml) | `Service/clouddsp-postgresql-headless` | `clouddsp-data` | Present | `data:postgresql` |
| [services/postgresql/postgresql-keda-demucs-bootstrap-credentials.secret.example.yaml](../../services/postgresql/postgresql-keda-demucs-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-keda-demucs-postgresql-bootstrap-credentials` | `clouddsp-data` | Absent | `secret` |
| [services/postgresql/postgresql-keda-demucs-bootstrap-job.yaml](../../services/postgresql/postgresql-keda-demucs-bootstrap-job.yaml) | `Job/postgresql-keda-demucs-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/postgresql/postgresql-service.yaml](../../services/postgresql/postgresql-service.yaml) | `Service/clouddsp-postgresql` | `clouddsp-data` | Present | `data:postgresql` |
| [services/postgresql/postgresql-statefulset.yaml](../../services/postgresql/postgresql-statefulset.yaml) | `StatefulSet/clouddsp-postgresql` | `clouddsp-data` | Present | `data:postgresql` |
| [services/rabbitmq/rabbitmq-adtof-consumer-bootstrap-credentials.secret.example.yaml](../../services/rabbitmq/rabbitmq-adtof-consumer-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-adtof-rabbitmq-bootstrap-credentials` | `clouddsp-data` | Absent | `secret` |
| [services/rabbitmq/rabbitmq-adtof-consumer-bootstrap-job.yaml](../../services/rabbitmq/rabbitmq-adtof-consumer-bootstrap-job.yaml) | `Job/rabbitmq-adtof-consumer-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/rabbitmq/rabbitmq-basic-pitch-consumer-bootstrap-credentials.secret.example.yaml](../../services/rabbitmq/rabbitmq-basic-pitch-consumer-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-basic-pitch-rabbitmq-bootstrap-credentials` | `clouddsp-data` | Absent | `secret` |
| [services/rabbitmq/rabbitmq-basic-pitch-consumer-bootstrap-job.yaml](../../services/rabbitmq/rabbitmq-basic-pitch-consumer-bootstrap-job.yaml) | `Job/rabbitmq-basic-pitch-consumer-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/rabbitmq/rabbitmq-basic-pitch-smoke-bootstrap-credentials.secret.example.yaml](../../services/rabbitmq/rabbitmq-basic-pitch-smoke-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-basic-pitch-smoke-rabbitmq-bootstrap-credentials` | `clouddsp-data` | Present | `test` |
| [services/rabbitmq/rabbitmq-basic-pitch-smoke-consumer-bootstrap-job.yaml](../../services/rabbitmq/rabbitmq-basic-pitch-smoke-consumer-bootstrap-job.yaml) | `Job/rabbitmq-basic-pitch-smoke-consumer-bootstrap` | `clouddsp-data` | Absent | `test` |
| [services/rabbitmq/rabbitmq-credentials.secret.example.yaml](../../services/rabbitmq/rabbitmq-credentials.secret.example.yaml) | `Secret/clouddsp-rabbitmq-credentials` | `clouddsp-data` | Present | `secret` |
| [services/rabbitmq/rabbitmq-demucs-consumer-bootstrap-credentials.secret.example.yaml](../../services/rabbitmq/rabbitmq-demucs-consumer-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-demucs-rabbitmq-bootstrap-credentials` | `clouddsp-data` | Absent | `secret` |
| [services/rabbitmq/rabbitmq-demucs-consumer-bootstrap-job.yaml](../../services/rabbitmq/rabbitmq-demucs-consumer-bootstrap-job.yaml) | `Job/rabbitmq-demucs-consumer-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/rabbitmq/rabbitmq-dispatcher-publisher-bootstrap-job.yaml](../../services/rabbitmq/rabbitmq-dispatcher-publisher-bootstrap-job.yaml) | `Job/rabbitmq-dispatcher-publisher-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/rabbitmq/rabbitmq-dispatcher-smoke-bootstrap-credentials.secret.example.yaml](../../services/rabbitmq/rabbitmq-dispatcher-smoke-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-dispatcher-smoke-rabbitmq-bootstrap-credentials` | `clouddsp-data` | Absent | `test` |
| [services/rabbitmq/rabbitmq-dispatcher-smoke-consumer-bootstrap-job.yaml](../../services/rabbitmq/rabbitmq-dispatcher-smoke-consumer-bootstrap-job.yaml) | `Job/rabbitmq-dispatcher-smoke-consumer-bootstrap` | `clouddsp-data` | Absent | `test` |
| [services/rabbitmq/rabbitmq-headless-service.yaml](../../services/rabbitmq/rabbitmq-headless-service.yaml) | `Service/clouddsp-rabbitmq-headless` | `clouddsp-data` | Present | `data:rabbitmq` |
| [services/rabbitmq/rabbitmq-ingress-network-policy.yaml](../../services/rabbitmq/rabbitmq-ingress-network-policy.yaml) | `NetworkPolicy/clouddsp-rabbitmq-ingress` | `clouddsp-data` | Present | `data:rabbitmq` |
| [services/rabbitmq/rabbitmq-keda-scaler-bootstrap-credentials.secret.example.yaml](../../services/rabbitmq/rabbitmq-keda-scaler-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-keda-rabbitmq-scaler-bootstrap-credentials` | `clouddsp-data` | Absent | `secret` |
| [services/rabbitmq/rabbitmq-keda-scaler-bootstrap-job.yaml](../../services/rabbitmq/rabbitmq-keda-scaler-bootstrap-job.yaml) | `Job/rabbitmq-keda-scaler-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/rabbitmq/rabbitmq-management-service.yaml](../../services/rabbitmq/rabbitmq-management-service.yaml) | `Service/clouddsp-rabbitmq-management` | `clouddsp-data` | Present | `data:rabbitmq` |
| [services/rabbitmq/rabbitmq-processing-topology-bootstrap-job.yaml](../../services/rabbitmq/rabbitmq-processing-topology-bootstrap-job.yaml) | `Job/rabbitmq-processing-topology-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/rabbitmq/rabbitmq-processing-topology-v001-configmap.yaml](../../services/rabbitmq/rabbitmq-processing-topology-v001-configmap.yaml) | `ConfigMap/rabbitmq-processing-topology-v001` | `clouddsp-data` | Present | `bootstrap` |
| [services/rabbitmq/rabbitmq-processing-topology-v002-downstream-bootstrap-job.yaml](../../services/rabbitmq/rabbitmq-processing-topology-v002-downstream-bootstrap-job.yaml) | `Job/rabbitmq-processing-topology-v002-downstream-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/rabbitmq/rabbitmq-processing-topology-v002-downstream-configmap.yaml](../../services/rabbitmq/rabbitmq-processing-topology-v002-downstream-configmap.yaml) | `ConfigMap/rabbitmq-processing-topology-v002-downstream` | `clouddsp-data` | Present | `bootstrap` |
| [services/rabbitmq/rabbitmq-service.yaml](../../services/rabbitmq/rabbitmq-service.yaml) | `Service/clouddsp-rabbitmq` | `clouddsp-data` | Present | `data:rabbitmq` |
| [services/rabbitmq/rabbitmq-source-intake-bootstrap-job.yaml](../../services/rabbitmq/rabbitmq-source-intake-bootstrap-job.yaml) | `Job/rabbitmq-source-intake-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/rabbitmq/rabbitmq-source-intake-topology-v001-configmap.yaml](../../services/rabbitmq/rabbitmq-source-intake-topology-v001-configmap.yaml) | `ConfigMap/rabbitmq-source-intake-topology-v001` | `clouddsp-data` | Present | `bootstrap` |
| [services/rabbitmq/rabbitmq-statefulset.yaml](../../services/rabbitmq/rabbitmq-statefulset.yaml) | `StatefulSet/clouddsp-rabbitmq` | `clouddsp-data` | Present | `data:rabbitmq` |
| [services/rabbitmq/rabbitmq-upload-intake-bootstrap-credentials.secret.example.yaml](../../services/rabbitmq/rabbitmq-upload-intake-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-upload-intake-rabbitmq-bootstrap-credentials` | `clouddsp-data` | Absent | `secret` |
| [services/upload-intake/upload-intake-database-bootstrap-credentials.secret.example.yaml](../../services/upload-intake/upload-intake-database-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-upload-intake-database-bootstrap-credentials` | `clouddsp-data` | Absent | `secret` |
| [services/upload-intake/upload-intake-database-bootstrap-job.yaml](../../services/upload-intake/upload-intake-database-bootstrap-job.yaml) | `Job/upload-intake-database-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/upload-intake/upload-intake-database-credentials.secret.example.yaml](../../services/upload-intake/upload-intake-database-credentials.secret.example.yaml) | `Secret/clouddsp-upload-intake-database-credentials` | `clouddsp-app` | Present | `secret` |
| [services/upload-intake/upload-intake-deployment.yaml](../../services/upload-intake/upload-intake-deployment.yaml) | `Deployment/clouddsp-upload-intake` | `clouddsp-app` | Present | `app:intake` |
| [services/upload-intake/upload-intake-minio-credentials.secret.example.yaml](../../services/upload-intake/upload-intake-minio-credentials.secret.example.yaml) | `Secret/clouddsp-upload-intake-minio-credentials` | `clouddsp-app` | Present | `secret` |
| [services/upload-intake/upload-intake-outbox-permissions-bootstrap-job.yaml](../../services/upload-intake/upload-intake-outbox-permissions-bootstrap-job.yaml) | `Job/upload-intake-outbox-permissions-bootstrap` | `clouddsp-data` | Absent | `bootstrap` |
| [services/upload-intake/upload-intake-rabbitmq-credentials.secret.example.yaml](../../services/upload-intake/upload-intake-rabbitmq-credentials.secret.example.yaml) | `Secret/clouddsp-upload-intake-rabbitmq-credentials` | `clouddsp-app` | Present | `secret` |
| [tests/adtof-exhausted-lease-recovery-smoke/adtof-exhausted-lease-recovery-smoke-job.yaml](../../tests/adtof-exhausted-lease-recovery-smoke/adtof-exhausted-lease-recovery-smoke-job.yaml) | `Job/clouddsp-adtof-exhausted-lease-recovery-smoke` | `clouddsp-data` | Absent | `test` |
| [tests/adtof-worker-smoke/adtof-worker-smoke-database-bootstrap-credentials.secret.example.yaml](../../tests/adtof-worker-smoke/adtof-worker-smoke-database-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-adtof-worker-smoke-database-bootstrap-credentials` | `clouddsp-data` | Present | `test` |
| [tests/adtof-worker-smoke/adtof-worker-smoke-database-bootstrap-job.yaml](../../tests/adtof-worker-smoke/adtof-worker-smoke-database-bootstrap-job.yaml) | `Job/adtof-worker-smoke-database-bootstrap` | `clouddsp-data` | Absent | `test` |
| [tests/adtof-worker-smoke/adtof-worker-smoke-database-credentials.secret.example.yaml](../../tests/adtof-worker-smoke/adtof-worker-smoke-database-credentials.secret.example.yaml) | `Secret/clouddsp-adtof-worker-smoke-database-credentials` | `clouddsp-app` | Present | `test` |
| [tests/adtof-worker-smoke/adtof-worker-smoke-job.yaml](../../tests/adtof-worker-smoke/adtof-worker-smoke-job.yaml) | `Job/adtof-worker-smoke` | `clouddsp-app` | Absent | `test` |
| [tests/adtof-worker-smoke/adtof-worker-smoke-minio-bootstrap-credentials.secret.example.yaml](../../tests/adtof-worker-smoke/adtof-worker-smoke-minio-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-adtof-worker-smoke-minio-bootstrap-credentials` | `clouddsp-data` | Present | `test` |
| [tests/adtof-worker-smoke/adtof-worker-smoke-minio-credentials.secret.example.yaml](../../tests/adtof-worker-smoke/adtof-worker-smoke-minio-credentials.secret.example.yaml) | `Secret/clouddsp-adtof-worker-smoke-minio-credentials` | `clouddsp-app` | Present | `test` |
| [tests/adtof-worker-smoke/adtof-worker-smoke-minio-policy-v001-configmap.yaml](../../tests/adtof-worker-smoke/adtof-worker-smoke-minio-policy-v001-configmap.yaml) | `ConfigMap/clouddsp-adtof-worker-smoke-objects-policy-v001` | `clouddsp-data` | Present | `test` |
| [tests/adtof-worker-smoke/minio-adtof-worker-smoke-objects-bootstrap-job.yaml](../../tests/adtof-worker-smoke/minio-adtof-worker-smoke-objects-bootstrap-job.yaml) | `Job/minio-adtof-worker-smoke-objects-bootstrap` | `clouddsp-data` | Absent | `test` |
| [tests/basic-pitch-keda-burst-smoke/basic-pitch-keda-burst-smoke-database-bootstrap-credentials.secret.example.yaml](../../tests/basic-pitch-keda-burst-smoke/basic-pitch-keda-burst-smoke-database-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-basic-pitch-keda-burst-smoke-database-bootstrap-credentials` | `clouddsp-data` | Absent | `test` |
| [tests/basic-pitch-keda-burst-smoke/basic-pitch-keda-burst-smoke-database-bootstrap-job.yaml](../../tests/basic-pitch-keda-burst-smoke/basic-pitch-keda-burst-smoke-database-bootstrap-job.yaml) | `Job/basic-pitch-keda-burst-smoke-database-bootstrap` | `clouddsp-data` | Absent | `test` |
| [tests/basic-pitch-keda-burst-smoke/basic-pitch-keda-burst-smoke-database-credentials.secret.example.yaml](../../tests/basic-pitch-keda-burst-smoke/basic-pitch-keda-burst-smoke-database-credentials.secret.example.yaml) | `Secret/clouddsp-basic-pitch-keda-burst-smoke-database-credentials` | `clouddsp-app` | Present | `test` |
| [tests/basic-pitch-keda-burst-smoke/basic-pitch-keda-burst-smoke-job.yaml](../../tests/basic-pitch-keda-burst-smoke/basic-pitch-keda-burst-smoke-job.yaml) | `Job/basic-pitch-keda-burst-smoke` | `clouddsp-app` | Absent | `test` |
| [tests/basic-pitch-keda-burst-smoke/basic-pitch-keda-burst-smoke-minio-bootstrap-credentials.secret.example.yaml](../../tests/basic-pitch-keda-burst-smoke/basic-pitch-keda-burst-smoke-minio-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-basic-pitch-keda-burst-smoke-minio-bootstrap-credentials` | `clouddsp-data` | Absent | `test` |
| [tests/basic-pitch-keda-burst-smoke/basic-pitch-keda-burst-smoke-minio-credentials.secret.example.yaml](../../tests/basic-pitch-keda-burst-smoke/basic-pitch-keda-burst-smoke-minio-credentials.secret.example.yaml) | `Secret/clouddsp-basic-pitch-keda-burst-smoke-minio-credentials` | `clouddsp-app` | Present | `test` |
| [tests/basic-pitch-keda-burst-smoke/basic-pitch-keda-burst-smoke-minio-policy-v001-configmap.yaml](../../tests/basic-pitch-keda-burst-smoke/basic-pitch-keda-burst-smoke-minio-policy-v001-configmap.yaml) | `ConfigMap/clouddsp-basic-pitch-keda-burst-smoke-objects-policy-v001` | `clouddsp-data` | Present | `test` |
| [tests/basic-pitch-keda-burst-smoke/minio-basic-pitch-keda-burst-smoke-objects-bootstrap-job.yaml](../../tests/basic-pitch-keda-burst-smoke/minio-basic-pitch-keda-burst-smoke-objects-bootstrap-job.yaml) | `Job/minio-basic-pitch-keda-burst-smoke-objects-bootstrap` | `clouddsp-data` | Absent | `test` |
| [tests/basic-pitch-worker-smoke/basic-pitch-worker-smoke-database-bootstrap-credentials.secret.example.yaml](../../tests/basic-pitch-worker-smoke/basic-pitch-worker-smoke-database-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-basic-pitch-worker-smoke-database-bootstrap-credentials` | `clouddsp-data` | Present | `test` |
| [tests/basic-pitch-worker-smoke/basic-pitch-worker-smoke-database-bootstrap-job.yaml](../../tests/basic-pitch-worker-smoke/basic-pitch-worker-smoke-database-bootstrap-job.yaml) | `Job/basic-pitch-worker-smoke-database-bootstrap` | `clouddsp-data` | Absent | `test` |
| [tests/basic-pitch-worker-smoke/basic-pitch-worker-smoke-database-credentials.secret.example.yaml](../../tests/basic-pitch-worker-smoke/basic-pitch-worker-smoke-database-credentials.secret.example.yaml) | `Secret/clouddsp-basic-pitch-worker-smoke-database-credentials` | `clouddsp-app` | Present | `test` |
| [tests/basic-pitch-worker-smoke/basic-pitch-worker-smoke-failed-run-cleanup-job.yaml](../../tests/basic-pitch-worker-smoke/basic-pitch-worker-smoke-failed-run-cleanup-job.yaml) | `Job/basic-pitch-worker-smoke-failed-run-cleanup` | `clouddsp-data` | Absent | `test` |
| [tests/basic-pitch-worker-smoke/basic-pitch-worker-smoke-job.yaml](../../tests/basic-pitch-worker-smoke/basic-pitch-worker-smoke-job.yaml) | `Job/basic-pitch-worker-smoke` | `clouddsp-app` | Absent | `test` |
| [tests/basic-pitch-worker-smoke/basic-pitch-worker-smoke-minio-bootstrap-credentials.secret.example.yaml](../../tests/basic-pitch-worker-smoke/basic-pitch-worker-smoke-minio-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-basic-pitch-worker-smoke-minio-bootstrap-credentials` | `clouddsp-data` | Absent | `test` |
| [tests/basic-pitch-worker-smoke/basic-pitch-worker-smoke-minio-credentials.secret.example.yaml](../../tests/basic-pitch-worker-smoke/basic-pitch-worker-smoke-minio-credentials.secret.example.yaml) | `Secret/clouddsp-basic-pitch-worker-smoke-minio-credentials` | `clouddsp-app` | Present | `test` |
| [tests/basic-pitch-worker-smoke/basic-pitch-worker-smoke-minio-policy-v001-configmap.yaml](../../tests/basic-pitch-worker-smoke/basic-pitch-worker-smoke-minio-policy-v001-configmap.yaml) | `ConfigMap/clouddsp-basic-pitch-worker-smoke-objects-policy-v001` | `clouddsp-data` | Present | `test` |
| [tests/basic-pitch-worker-smoke/minio-basic-pitch-worker-smoke-objects-bootstrap-job.yaml](../../tests/basic-pitch-worker-smoke/minio-basic-pitch-worker-smoke-objects-bootstrap-job.yaml) | `Job/minio-basic-pitch-worker-smoke-objects-bootstrap` | `clouddsp-data` | Absent | `test` |
| [tests/demucs-worker-smoke/demucs-worker-smoke-database-bootstrap-credentials.secret.example.yaml](../../tests/demucs-worker-smoke/demucs-worker-smoke-database-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-demucs-worker-smoke-database-bootstrap-credentials` | `clouddsp-data` | Absent | `test` |
| [tests/demucs-worker-smoke/demucs-worker-smoke-database-bootstrap-job.yaml](../../tests/demucs-worker-smoke/demucs-worker-smoke-database-bootstrap-job.yaml) | `Job/demucs-worker-smoke-database-bootstrap` | `clouddsp-data` | Absent | `test` |
| [tests/demucs-worker-smoke/demucs-worker-smoke-database-credentials.secret.example.yaml](../../tests/demucs-worker-smoke/demucs-worker-smoke-database-credentials.secret.example.yaml) | `Secret/clouddsp-demucs-worker-smoke-database-credentials` | `clouddsp-app` | Present | `test` |
| [tests/demucs-worker-smoke/demucs-worker-smoke-downstream-scale-recovery-v001-job.yaml](../../tests/demucs-worker-smoke/demucs-worker-smoke-downstream-scale-recovery-v001-job.yaml) | `Job/demucs-worker-smoke-downstream-scale-recovery-v001` | `clouddsp-data` | Temporary; TTL after cleanup | `test` |
| [tests/demucs-worker-smoke/demucs-worker-smoke-failed-run-cleanup-job.yaml](../../tests/demucs-worker-smoke/demucs-worker-smoke-failed-run-cleanup-job.yaml) | `Job/demucs-worker-smoke-failed-run-cleanup` | `clouddsp-data` | Absent | `test` |
| [tests/demucs-worker-smoke/demucs-worker-smoke-job.yaml](../../tests/demucs-worker-smoke/demucs-worker-smoke-job.yaml) | `Job/demucs-worker-smoke` | `clouddsp-app` | Absent | `test` |
| [tests/demucs-worker-smoke/demucs-worker-smoke-minio-bootstrap-credentials.secret.example.yaml](../../tests/demucs-worker-smoke/demucs-worker-smoke-minio-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-demucs-worker-smoke-minio-bootstrap-credentials` | `clouddsp-data` | Absent | `test` |
| [tests/demucs-worker-smoke/demucs-worker-smoke-minio-credentials.secret.example.yaml](../../tests/demucs-worker-smoke/demucs-worker-smoke-minio-credentials.secret.example.yaml) | `Secret/clouddsp-demucs-worker-smoke-minio-credentials` | `clouddsp-app` | Present | `test` |
| [tests/demucs-worker-smoke/demucs-worker-smoke-minio-policy-v001-configmap.yaml](../../tests/demucs-worker-smoke/demucs-worker-smoke-minio-policy-v001-configmap.yaml) | `ConfigMap/clouddsp-demucs-worker-smoke-objects-policy-v001` | `clouddsp-data` | Present | `test` |
| [tests/demucs-worker-smoke/minio-demucs-worker-smoke-objects-bootstrap-job.yaml](../../tests/demucs-worker-smoke/minio-demucs-worker-smoke-objects-bootstrap-job.yaml) | `Job/minio-demucs-worker-smoke-objects-bootstrap` | `clouddsp-data` | Absent | `test` |
| [tests/dispatcher-smoke/dispatcher-normal-path-smoke-job.yaml](../../tests/dispatcher-smoke/dispatcher-normal-path-smoke-job.yaml) | `Job/dispatcher-normal-path-smoke` | `clouddsp-data` | Absent | `test` |
| [tests/dispatcher-smoke/dispatcher-smoke-rabbitmq-data-credentials.secret.example.yaml](../../tests/dispatcher-smoke/dispatcher-smoke-rabbitmq-data-credentials.secret.example.yaml) | `Secret/clouddsp-dispatcher-smoke-rabbitmq-data-credentials` | `clouddsp-data` | Absent | `test` |
| [tests/generic-dispatcher-smoke/basic-pitch-smoke-rabbitmq-credentials.secret.example.yaml](../../tests/generic-dispatcher-smoke/basic-pitch-smoke-rabbitmq-credentials.secret.example.yaml) | `Secret/clouddsp-basic-pitch-smoke-rabbitmq-credentials` | `clouddsp-app` | Present | `test` |
| [tests/generic-dispatcher-smoke/generic-dispatcher-basic-pitch-routing-smoke-job.yaml](../../tests/generic-dispatcher-smoke/generic-dispatcher-basic-pitch-routing-smoke-job.yaml) | `Job/generic-dispatcher-basic-pitch-routing-smoke` | `clouddsp-app` | Absent | `test` |
| [tests/generic-dispatcher-smoke/generic-dispatcher-smoke-database-bootstrap-credentials.secret.example.yaml](../../tests/generic-dispatcher-smoke/generic-dispatcher-smoke-database-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-generic-dispatcher-smoke-database-bootstrap-credentials` | `clouddsp-data` | Present | `test` |
| [tests/generic-dispatcher-smoke/generic-dispatcher-smoke-database-bootstrap-job.yaml](../../tests/generic-dispatcher-smoke/generic-dispatcher-smoke-database-bootstrap-job.yaml) | `Job/generic-dispatcher-smoke-database-bootstrap` | `clouddsp-data` | Absent | `test` |
| [tests/generic-dispatcher-smoke/generic-dispatcher-smoke-database-credentials.secret.example.yaml](../../tests/generic-dispatcher-smoke/generic-dispatcher-smoke-database-credentials.secret.example.yaml) | `Secret/clouddsp-generic-dispatcher-smoke-database-credentials` | `clouddsp-app` | Present | `test` |
| [tests/keda/basic-pitch-container-startup-benchmark-jobs.yaml](../../tests/keda/basic-pitch-container-startup-benchmark-jobs.yaml) #doc 1 | `Job/basic-pitch-startup-cached-benchmark` | `clouddsp-app` | Absent | `test` |
| [tests/keda/basic-pitch-container-startup-benchmark-jobs.yaml](../../tests/keda/basic-pitch-container-startup-benchmark-jobs.yaml) #doc 2 | `Job/basic-pitch-startup-image-pull-benchmark` | `clouddsp-app` | Absent | `test` |
| [tests/keda/rabbitmq-management-access-smoke-jobs.yaml](../../tests/keda/rabbitmq-management-access-smoke-jobs.yaml) #doc 1 | `Job/rabbitmq-management-allowed-smoke` | `clouddsp-data` | Absent | `test` |
| [tests/keda/rabbitmq-management-access-smoke-jobs.yaml](../../tests/keda/rabbitmq-management-access-smoke-jobs.yaml) #doc 2 | `Job/rabbitmq-management-denied-smoke` | `clouddsp-app` | Absent | `test` |
| [tests/keycloak-smoke/keycloak-email-verification-mailpit-smoke-job.yaml](../../tests/keycloak-smoke/keycloak-email-verification-mailpit-smoke-job.yaml) | `Job/keycloak-email-verification-mailpit-smoke` | `clouddsp-data` | Absent | `test` |
| [tests/keycloak-smoke/keycloak-frontend-oidc-authorize-smoke-job.yaml](../../tests/keycloak-smoke/keycloak-frontend-oidc-authorize-smoke-job.yaml) | `Job/keycloak-frontend-oidc-authorize-smoke` | `clouddsp-data` | Absent | `test` |
| [tests/keycloak-smoke/keycloak-job-api-auth-me-smoke-job.yaml](../../tests/keycloak-smoke/keycloak-job-api-auth-me-smoke-job.yaml) | `Job/keycloak-job-api-auth-me-smoke` | `clouddsp-data` | Absent | `test` |
| [tests/keycloak-smoke/keycloak-oidc-discovery-smoke-job.yaml](../../tests/keycloak-smoke/keycloak-oidc-discovery-smoke-job.yaml) | `Job/keycloak-oidc-discovery-smoke` | `clouddsp-data` | Absent | `test` |
| [tests/mailpit-smoke/mailpit-smtp-capture-smoke-job.yaml](../../tests/mailpit-smoke/mailpit-smtp-capture-smoke-job.yaml) | `Job/mailpit-smtp-capture-smoke` | `clouddsp-data` | Absent | `test` |
| [tests/minio-smoke/minio-job-api-restricted-access-smoke-job.yaml](../../tests/minio-smoke/minio-job-api-restricted-access-smoke-job.yaml) | `Job/minio-job-api-restricted-access-smoke` | `clouddsp-app` | Absent | `test` |
| [tests/minio-smoke/minio-s3-api-smoke-job.yaml](../../tests/minio-smoke/minio-s3-api-smoke-job.yaml) | `Job/minio-s3-api-smoke` | `clouddsp-data` | Absent | `test` |
| [tests/minio-smoke/minio-source-intake-rabbitmq-smoke-job.yaml](../../tests/minio-smoke/minio-source-intake-rabbitmq-smoke-job.yaml) | `Job/minio-source-intake-rabbitmq-smoke` | `clouddsp-app` | Absent | `test` |
| [tests/postgresql-smoke/postgresql-persistence-reader-job.yaml](../../tests/postgresql-smoke/postgresql-persistence-reader-job.yaml) | `Job/postgresql-persistence-reader` | `clouddsp-data` | Absent | `test` |
| [tests/postgresql-smoke/postgresql-persistence-writer-job.yaml](../../tests/postgresql-smoke/postgresql-persistence-writer-job.yaml) | `Job/postgresql-persistence-writer` | `clouddsp-data` | Absent | `test` |
| [tests/postgresql-smoke/postgresql-read-write-smoke-job.yaml](../../tests/postgresql-smoke/postgresql-read-write-smoke-job.yaml) | `Job/postgresql-read-write-smoke` | `clouddsp-data` | Absent | `test` |
| [tests/rabbitmq-smoke/rabbitmq-amqp-smoke-job.yaml](../../tests/rabbitmq-smoke/rabbitmq-amqp-smoke-job.yaml) | `Job/rabbitmq-amqp-smoke` | `clouddsp-data` | Absent | `test` |
| [tests/registry-smoke/registry-smoke-job.yaml](../../tests/registry-smoke/registry-smoke-job.yaml) | `Job/registry-smoke` | `clouddsp-app` | Absent | `test` |
| [tests/routing-smoke/http-routing-smoke.yaml](../../tests/routing-smoke/http-routing-smoke.yaml) #doc 1 | `Deployment/routing-smoke` | `clouddsp-app` | Absent | `test` |
| [tests/routing-smoke/http-routing-smoke.yaml](../../tests/routing-smoke/http-routing-smoke.yaml) #doc 2 | `Service/routing-smoke` | `clouddsp-app` | Absent | `test` |
| [tests/routing-smoke/http-routing-smoke.yaml](../../tests/routing-smoke/http-routing-smoke.yaml) #doc 3 | `Ingress/routing-smoke` | `clouddsp-app` | Absent | `test` |
| [tests/six-stem-load/six-stem-load-job.yaml](../../tests/six-stem-load/six-stem-load-job.yaml) | `Job/clouddsp-six-stem-load` | `clouddsp-app` | Absent | `test` |
| [tests/six-stem-load/six-stem-load-keycloak-bootstrap-credentials.secret.example.yaml](../../tests/six-stem-load/six-stem-load-keycloak-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-six-stem-load-keycloak-bootstrap-credentials` | `clouddsp-app` | Present | `test` |
| [tests/six-stem-load/six-stem-load-minio-admin-credentials.secret.example.yaml](../../tests/six-stem-load/six-stem-load-minio-admin-credentials.secret.example.yaml) | `Secret/clouddsp-six-stem-load-minio-admin-credentials` | `clouddsp-app` | Present | `test` |
| [tests/six-stem-load/six-stem-load-observer-rbac.yaml](../../tests/six-stem-load/six-stem-load-observer-rbac.yaml) #doc 1 | `ServiceAccount/clouddsp-six-stem-load-observer` | `clouddsp-app` | Present | `test` |
| [tests/six-stem-load/six-stem-load-observer-rbac.yaml](../../tests/six-stem-load/six-stem-load-observer-rbac.yaml) #doc 2 | `Role/clouddsp-six-stem-load-observer` | `clouddsp-app` | Present | `test` |
| [tests/six-stem-load/six-stem-load-observer-rbac.yaml](../../tests/six-stem-load/six-stem-load-observer-rbac.yaml) #doc 3 | `RoleBinding/clouddsp-six-stem-load-observer` | `clouddsp-app` | Present | `test` |
| [tests/six-stem-load/six-stem-load-postgresql-bootstrap-credentials.secret.example.yaml](../../tests/six-stem-load/six-stem-load-postgresql-bootstrap-credentials.secret.example.yaml) | `Secret/clouddsp-six-stem-load-postgresql-bootstrap-credentials` | `clouddsp-app` | Present | `test` |
| [tests/source-intake-smoke/source-to-outbox-smoke-job.yaml](../../tests/source-intake-smoke/source-to-outbox-smoke-job.yaml) | `Job/source-to-outbox-smoke` | `clouddsp-data` | Absent | `test` |
| [tests/storage-smoke/local-path-smoke-pvc.yaml](../../tests/storage-smoke/local-path-smoke-pvc.yaml) | `PersistentVolumeClaim/local-path-smoke` | `clouddsp-data` | Absent | `test` |
| [tests/storage-smoke/local-path-smoke-reader-job.yaml](../../tests/storage-smoke/local-path-smoke-reader-job.yaml) | `Job/local-path-smoke-reader` | `clouddsp-data` | Absent | `test` |
| [tests/storage-smoke/local-path-smoke-writer-job.yaml](../../tests/storage-smoke/local-path-smoke-writer-job.yaml) | `Job/local-path-smoke-writer` | `clouddsp-data` | Absent | `test` |

## Live resources without a matching source object

These 30 resources were found in the selected namespaces but have no
same-kind, same-namespace, same-name source manifest in this tree.
Generated resources and retained local Secrets are expected to appear here.
The three runtime Secret identities added to the source catalog now have
committed, non-secret templates; their live values were not read.

| Namespace | Kind / name | Handling |
| --- | --- | --- |
| `clouddsp-app` | `ConfigMap/kube-root-ca.crt` | Kubernetes-generated namespace trust ConfigMap |
| `clouddsp-app` | `HorizontalPodAutoscaler/keda-hpa-clouddsp-adtof-rabbitmq-scaler` | KEDA-generated HPA; manage the parent `ScaledObject`, not this HPA |
| `clouddsp-app` | `HorizontalPodAutoscaler/keda-hpa-clouddsp-basic-pitch-rabbitmq-scaler` | KEDA-generated HPA; manage the parent `ScaledObject`, not this HPA |
| `clouddsp-app` | `HorizontalPodAutoscaler/keda-hpa-clouddsp-demucs-rabbitmq-scaler` | KEDA-generated HPA; manage the parent `ScaledObject`, not this HPA |
| `clouddsp-app` | `RoleBinding/keda-operator` | KEDA release resource; retain existing owner |
| `clouddsp-app` | `ServiceAccount/default` | Kubernetes-generated default ServiceAccount |
| `clouddsp-data` | `ConfigMap/kube-root-ca.crt` | Kubernetes-generated namespace trust ConfigMap |
| `clouddsp-data` | `PersistentVolumeClaim/minio-data-clouddsp-minio-0` | StatefulSet-generated PVC; preserve data and name |
| `clouddsp-data` | `PersistentVolumeClaim/postgres-data-clouddsp-postgresql-0` | StatefulSet-generated PVC; preserve data and name |
| `clouddsp-data` | `PersistentVolumeClaim/rabbitmq-data-clouddsp-rabbitmq-0` | StatefulSet-generated PVC; preserve data and name |
| `clouddsp-data` | `ServiceAccount/default` | Kubernetes-generated default ServiceAccount |
| `clouddsp-system` | `ConfigMap/kube-root-ca.crt` | Kubernetes-generated namespace trust ConfigMap |
| `clouddsp-system` | `ServiceAccount/default` | Kubernetes-generated default ServiceAccount |
| `keda` | `ConfigMap/kube-root-ca.crt` | Kubernetes-generated namespace trust ConfigMap |
| `keda` | `Deployment/keda-admission-webhooks` | KEDA release resource; retain existing owner |
| `keda` | `Deployment/keda-operator` | KEDA release resource; retain existing owner |
| `keda` | `Deployment/keda-operator-metrics-apiserver` | KEDA release resource; retain existing owner |
| `keda` | `Role/keda-operator-certs` | KEDA release resource; retain existing owner |
| `keda` | `RoleBinding/keda-operator` | KEDA release resource; retain existing owner |
| `keda` | `RoleBinding/keda-operator-certs` | KEDA release resource; retain existing owner |
| `keda` | `Secret/kedaorg-certs` | KEDA release resource; retain existing owner |
| `keda` | `Secret/sh.helm.release.v1.keda.v1` | KEDA release resource; retain existing owner |
| `keda` | `Secret/sh.helm.release.v1.keda.v2` | KEDA release resource; retain existing owner |
| `keda` | `Service/keda-admission-webhooks` | KEDA release resource; retain existing owner |
| `keda` | `Service/keda-operator` | KEDA release resource; retain existing owner |
| `keda` | `Service/keda-operator-metrics-apiserver` | KEDA release resource; retain existing owner |
| `keda` | `ServiceAccount/default` | Kubernetes-generated default ServiceAccount |
| `keda` | `ServiceAccount/keda-metrics-server` | KEDA release resource; retain existing owner |
| `keda` | `ServiceAccount/keda-operator` | KEDA release resource; retain existing owner |
| `keda` | `ServiceAccount/keda-webhook` | KEDA release resource; retain existing owner |

## KEDA cluster-scoped resources

The existing `keda` Helm release also owns six CRDs:
`cloudeventsources.eventing.keda.sh`,
`clustercloudeventsources.eventing.keda.sh`,
`clustertriggerauthentications.keda.sh`, `scaledjobs.keda.sh`,
`scaledobjects.keda.sh`, and `triggerauthentications.keda.sh`. It owns four
ClusterRoles (`keda-operator`, `keda-operator-external-metrics-reader`,
`keda-operator-minimal-cluster-role`, `keda-operator-webhook`) and four
ClusterRoleBindings (`keda-operator-hpa-controller-external-metrics`,
`keda-operator-minimal`, `keda-operator-system-auth-delegator`,
`keda-operator-webhook`). These remain with the pinned vendor release;
CloudDSP charts should own neither the CRDs nor the generated HPAs.

## Findings that gate the next task

1. **Three runtime Secret contracts are documented.** The live Secrets
   `clouddsp-dispatcher-database-credentials`,
   `clouddsp-dispatcher-rabbitmq-credentials`, and
   `clouddsp-upload-intake-minio-credentials` now have committed
   `.secret.example.yaml` counterparts with the names and keys required by
   their Deployments. Runtime values remain in ignored local files; this
   inventory checked Secret identities only and did not read live values.
2. **Persistent data is in use.** All three data PVCs are Bound. PostgreSQL,
   MinIO, and RabbitMQ each completed a protected Helm adoption with a tested
   backup and rendered-versus-live diff. The bound claims and PVs remain
   outside those Helm releases.
3. **Jobs are absent while effects may persist.** All 83 versioned Job
   objects were absent from this snapshot. The nine SQL migration ConfigMaps
   were live. Consult `schema_migrations` and each external service's actual
   state before treating any bootstrap or migration as pending or complete.
4. **Test resources remain live.** The six-stem-load observer ServiceAccount,
   Role, and RoleBinding and several test Secrets/ConfigMaps remain after test
   Jobs have disappeared. They are outside the normal deployment graph. An
   explicit test-resource audit can decide whether to retain or clean them;
   the orchestration script should not adopt or delete them implicitly.
5. **Namespace and vendor boundaries are already clear.** The three project
   namespaces carry `managed-by=kubectl`. KEDA is its own Helm release, and
   Traefik belongs to k3s. The future orchestrator should leave those
   boundaries intact.
6. **The dispatcher controllers intentionally use separate image digests.**
   The legacy `clouddsp-dispatcher` Deployment still uses the Demucs-only
   `dispatcher@sha256:3d04f458...` image, now cataloged as
   `images.dispatcher-demucs-only`. The separate generic controller uses
   `dispatcher@sha256:cc2d36bd...`, cataloged as `images.dispatcher`. The
   local registry, Docker cache, and running Pods confirm these references;
   the historical source tree is not asserted to reproduce the older image.
   The read-only preflight now requires each controller to match its own lock
   entry. Neither Deployment was rolled to reconcile the catalog. The legacy
   controller was subsequently adopted by Helm without replacing its Pod; the
   generic controller was then adopted in its own release with the same
   identity preservation.

The read-only [plan/preflight command](../../scripts/deploy-local.sh) now checks
current ownership, Secret names, image-lock consistency, and StatefulSet/PVC
identity. The [Mailpit](../../scripts/mailpit-release.rb),
[frontend](../../scripts/frontend-release.rb),
[legacy dispatcher](../../scripts/dispatcher-release.rb),
[generic dispatcher](../../scripts/generic-dispatcher-release.rb),
[Job API](../../scripts/job-api-release.rb),
[upload-intake](../../scripts/upload-intake-release.rb),
[Keycloak](../../scripts/keycloak-release.rb),
[PostgreSQL](../../scripts/postgresql-release.rb),
[MinIO](../../scripts/minio-release.rb),
[RabbitMQ](../../scripts/rabbitmq-release.rb), and
[scaling-auth](../../scripts/scaling-auth-release.rb) release scripts separately check
rendered/source/live spec parity and Helm ownership. The general
preflight does not perform chart-specific rendered diffs; each remaining
component needs its own chart and adoption gate.
