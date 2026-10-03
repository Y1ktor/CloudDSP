# Helm releases and worker scaling

The [operator guide](../../scripts/README.md) describes fresh deployment and
root verification. Existing root `reconcile` runs Helm helpers in `verify`
mode and never installs, adopts, or upgrades a release.

## Release ownership

The deployment contains 14 CloudDSP charts plus the pinned upstream KEDA
release. Raw workload manifests remain comparison baselines used by release
validation; deploy Helm-owned workloads through their charts.

| Component/chart | Namespace | Owned resources |
| --- | --- | --- |
| [PostgreSQL](../../helm/postgresql/README.md) | `clouddsp-data` | StatefulSet; normal and headless Services. |
| [RabbitMQ](../../helm/rabbitmq/README.md) | `clouddsp-data` | StatefulSet; normal, management, and headless Services; ingress NetworkPolicy. |
| [MinIO](../../helm/minio/README.md) | `clouddsp-data` | StatefulSet; normal/headless Services; S3 Ingress. |
| [Mailpit](../../helm/mailpit/README.md) | `clouddsp-data` | Deployment; SMTP/web Services; browser Ingress. |
| [Keycloak](../../helm/keycloak/README.md) | `clouddsp-data` | Deployment; Service; browser Ingress. |
| [Job API](../../helm/job-api/README.md) | `clouddsp-app` | Deployment; Service; protected `/auth` and `/jobs` Ingress. |
| [Upload intake](../../helm/upload-intake/README.md) | `clouddsp-app` | Outbound consumer Deployment. |
| [Legacy dispatcher](../../helm/dispatcher/README.md) | `clouddsp-app` | Demucs-only publisher Deployment. |
| [Generic dispatcher](../../helm/generic-dispatcher/README.md) | `clouddsp-app` | Route-aware publisher Deployment. |
| [Frontend](../../helm/frontend/README.md) | `clouddsp-app` | Deployment; Service; browser Ingress. |
| [Demucs](../../helm/demucs/README.md) | `clouddsp-app` | Worker Deployment and dual-trigger ScaledObject. |
| [Basic Pitch](../../helm/basic-pitch/README.md) | `clouddsp-app` | Worker Deployment and dual-trigger ScaledObject. |
| [ADTOF](../../helm/adtof/README.md) | `clouddsp-app` | Worker Deployment and RabbitMQ ScaledObject. |
| [Scaling authentication](../../helm/scaling-auth/README.md) | `clouddsp-app` | Two shared TriggerAuthentications. |
| [Upstream KEDA](../../helm/keda/README.md) | `keda` | Pinned operator, metrics API, webhook, and platform resources. |

Secrets, database/broker/MinIO configuration, migration Jobs, and private
artifact contents have separate lifecycle owners. KEDA owns generated HPAs
and worker scaling. The release helper checks generated bound PVC contracts
for stateful services without treating Helm as data backup or restore.

## Component release helper modes

Component helpers follow `COMPONENT-release.rb` under
[`scripts/releases/`](../../scripts/releases/), using `job-api` and `generic-dispatcher` for those
specific names. Examples:

```bash
ruby ./k8Deployment/kubernetes/scripts/releases/frontend-release.rb verify
ruby ./k8Deployment/kubernetes/scripts/releases/demucs-release.rb verify
ruby ./k8Deployment/kubernetes/scripts/releases/postgresql-release.rb verify
```

| Mode | Preconditions and effect |
| --- | --- |
| `plan` | Read-only render/source/live equality and kubectl-owned adoption preflight; an existing healthy Helm release is not its expected starting owner. |
| `install` | Fresh component creation only; release and every intended resource must be absent, with prerequisites verified first. Stateful helpers also refuse an existing claim/Pod. |
| `adopt` | Optional one-time transfer of existing kubectl-owned resources after strict equality/ownership checks. Helm 4 takeover flags are deliberate; it is not the existing-release update command. |
| `verify` | Read-only checks of source/rendered/stored/live specs, Helm ownership, applicable readiness/image/route/PVC contracts. |
| `smoke` | Available only where configured; creates a reviewed disposable test Job and may mutate scoped test data. |

Each helper's supported subset is defined in its source/chart guide.
`install` checks server schemas before mutation and leaves failed/partial
releases for inspection. Repeating it against a partial installation is
refused. One-time stateful adoption additionally runs component-specific
[PostgreSQL](../../scripts/maintenance/postgresql-backup-and-restore-test.sh),
[MinIO](../../scripts/maintenance/minio-backup-and-restore-test.py), or
[RabbitMQ](../../scripts/maintenance/rabbitmq-backup-and-restore-test.py) backup/recovery
rehearsals. Those tests can pause services and write private archives; they
are not fresh-bootstrap stages. Uninstalling an adopted release deletes
resources Helm now owns and is not a retry strategy.

The shared [helm-release.rb](../../scripts/lib/helm-release.rb) helper
also manages guarded StatefulSet paths. Release readiness is a deployment
check; process readiness alone does not prove a new processing event reaches
its final result. Use the relevant [smoke runbook](image-builds-and-smoke-tools.md).

## KEDA and shared authentication

```bash
ruby ./k8Deployment/kubernetes/scripts/releases/keda-release-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/releases/keda-release-stage.rb verify
ruby ./k8Deployment/kubernetes/scripts/releases/scaling-auth-release.rb verify-prerequisites
ruby ./k8Deployment/kubernetes/scripts/releases/scaling-auth-release.rb verify
```

Fresh KEDA `install` requires absent release/namespace/CRDs/metrics API
registration, runs the pinned installer, then verifies chart/values, controller
rollouts, six CRDs, and metrics registration. The underlying
[install-keda.sh](../../scripts/releases/install-keda.sh) is a separate explicit
install/upgrade entrypoint for that platform dependency; it does not enqueue
work or install worker ScaledObjects.

Scaling-auth supports `plan|install|adopt|verify|verify-prerequisites`.
Fresh install requires KEDA and the restricted PostgreSQL/RabbitMQ scaler
identities, plus absent auth and worker scaler resources.
`verify-prerequisites` checks the release before fresh worker installation;
full `verify` additionally checks all three Ready scalers, authentication
references, worker targets, and correctly owned HPAs.

Idle workers are expected at zero replicas. Demucs and Basic Pitch include a
PostgreSQL due-work signal because a queue can empty after a durable task
claim while inference or retry still needs a worker. ADTOF uses its reviewed
RabbitMQ scaler. Demucs verification permits Pods already in termination but
requires no non-terminating idle worker Pod. The CPU profile and caps do not
validate a future NVIDIA profile.

## Scoped Demucs maintenance

```bash
./k8Deployment/kubernetes/scripts/maintenance/reconcile-demucs-scaling.sh
```

This helper reasserts an already deployed, matching, idle Demucs profile. It
first verifies both scaler identities, scaling-auth, and Demucs. Then it uses
Helm to upgrade existing `clouddsp-scaling-auth` and `clouddsp-demucs` releases
in that order with explicit context/namespace and three-minute waits, and
repeats verification. It creates no bootstrap Secret/Job and takes no ownership
from another controller. Missing/failed releases, drift, or active work stop
before upgrades; desired chart changes need a separately reviewed rollout.
The root `reconcile` command does not invoke this scoped helper.

Basic Pitch retains guarded historical transition modes
`upgrade-scaling`, `upgrade-stabilization`, and `upgrade-numba` for their exact
old chart baselines. See its [release guide](../../helm/basic-pitch/README.md)
and [runner](../../scripts/releases/basic-pitch-release.rb); they are not general fresh
installation or root reconciliation modes.

## Keycloak configuration beyond the chart

```bash
ruby ./k8Deployment/kubernetes/scripts/stages/keycloak/keycloak-realm-stage.rb verify
ruby ./k8Deployment/kubernetes/scripts/stages/keycloak/keycloak-config-verify.rb verify
```

The realm stage supports `plan|bootstrap|verify` and fresh initialization via
six versioned Admin API Jobs. The read-only configuration gate checks realm,
registration/password form, SMTP, public React PKCE client, Job API audience
client/mapper, and issuer. Root reconciliation verifies that state rather than
rewriting it. The [service contract](../../services/keycloak/) and
[credential/database references](credentials-and-identities.md) explain its
separate identities and PostgreSQL boundary.
