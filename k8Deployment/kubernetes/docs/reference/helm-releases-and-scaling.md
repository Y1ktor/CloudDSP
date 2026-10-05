# Helm releases and worker scaling

The [operator guide](../../scripts/README.md) describes fresh deployment and
root verification. Existing root `reconcile` runs Helm helpers in `verify`
mode and never installs, adopts, or upgrades a release.

## Release ownership

The deployment contains 14 CloudDSP charts plus the pinned upstream KEDA
release. Raw workload manifests remain comparison baselines used by release
validation; deploy Helm-owned workloads through their charts. Worker scaler
baselines retain the original default policy. Release validation permits only
the explicitly parameterized scaling fields to differ from those baselines;
the installed Helm manifest and live ScaledObject must match the chart rendered
with the checked-in values.

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
| `verify-idle` | Workers only; requires `autoscaling.minReplicaCount: 0` plus the strict idle zero-replica/Pod checks. Positive warm-worker policies are refused. |
| `smoke` | Available only where configured; creates a reviewed disposable test Job and may mutate scoped test data. |

Each helper's supported subset is defined in its source/chart guide.
`install` checks server schemas before mutation and leaves failed/partial
releases for inspection. Repeating it against a partial installation is
refused. After its single Helm install, a worker helper allows up to another
180 seconds of read-only verification for KEDA to create the HPA and converge
on the configured Ready worker count. It never retries the Helm write or sets
replicas itself; a timeout leaves the release available for inspection.
One-time stateful adoption additionally runs component-specific
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

The default policy expects idle workers at zero replicas. Demucs and Basic Pitch include a
PostgreSQL due-work signal because a queue can empty after a durable task
claim while inference or retry still needs a worker. ADTOF uses its reviewed
RabbitMQ scaler. Demucs verification permits Pods already in termination but
requires no non-terminating idle worker Pod. The CPU profile and caps do not
validate a future NVIDIA profile.

## Configure a worker scaling policy

Each worker chart exposes its policy under `autoscaling` in its versioned
values file:

- [Demucs values](../../helm/demucs/values.yaml), release `clouddsp-demucs`.
- [Basic Pitch values](../../helm/basic-pitch/values.yaml), release `clouddsp-basic-pitch`.
- [ADTOF values](../../helm/adtof/values.yaml), release `clouddsp-adtof`.

All three releases live in `clouddsp-app`. Change the worker release when
tuning a worker; the upstream `keda` release installs the operator
rather than the worker's ScaledObject policy.

| Values field under `autoscaling` | Demucs default | Basic Pitch default | ADTOF default | Purpose |
| --- | --- | --- | --- | --- |
| `pollingInterval` | 15 | 5 | 15 | Seconds between KEDA activation checks. |
| `cooldownPeriod` | 300 | 60 | 180 | Seconds after trigger inactivity before KEDA can return an eligible worker to zero. |
| `minReplicaCount` | 0 | 0 | 0 | Minimum worker count; a positive value keeps workers warm. |
| `maxReplicaCount` | 1 | 3 | 2 | Maximum worker count; real node capacity still controls scheduling. |
| `rabbitmq.queueLength` | 1 | 1 | 1 | Queue length target per worker. |
| `rabbitmq.activationValue` | 0 | 0 | 0 | Queue activation threshold. |
| `rabbitmq.timeout` | 5000 | 5000 | 5000 | RabbitMQ metric request timeout in milliseconds. |
| `postgresql.targetQueryValue` | 1 | 1 | Not used | Due-work count target per worker. |
| `postgresql.activationTargetQueryValue` | 0 | 0 | Not used | Due-work activation threshold. |
| `behavior.scaleUp.stabilizationWindowSeconds` | 0 | 0 | 0 | HPA scale-up stabilization window in seconds. |
| `behavior.scaleUp.value` | 1 | 3 | 1 | Maximum Pods added per scale-up policy interval. |
| `behavior.scaleUp.periodSeconds` | 15 | 15 | 30 | Scale-up policy interval in seconds. |
| `behavior.scaleDown.stabilizationWindowSeconds` | 300 | 360 | 180 | HPA scale-down stabilization window in seconds. |
| `behavior.scaleDown.value` | 1 | 3 | 1 | Maximum Pods removed per scale-down policy interval. |
| `behavior.scaleDown.periodSeconds` | 60 | 60 | 60 | Scale-down policy interval in seconds. |

Queue coordinates, trigger types, task-count SQL, authentication references,
and worker security/resource settings remain part of the reviewed templates.
Keep the durable PostgreSQL trigger on Demucs and Basic Pitch: an acknowledged
message can disappear from RabbitMQ while inference or a retry still needs a
worker. Shortening their scale-down windows also changes the protection
against removing a Pod that is processing a claimed task. The defaults retain
the existing behavior.

### Example: keep Basic Pitch warm for a busy period

Run the following commands from the repository root. First edit
`k8Deployment/kubernetes/helm/basic-pitch/values.yaml` and change only the
minimum in its existing policy, retaining the other fields:

```yaml
autoscaling:
  minReplicaCount: 1
  maxReplicaCount: 3
```

This is a fragment of the values file, not a replacement for it. One worker
remains warm; demand may increase the count up to three. Increasing only
`maxReplicaCount` raises the ceiling without proactively starting extra Pods.
Review available CPU and memory before increasing a ceiling.

Validate and inspect the rendered resources before updating the existing
release:

```bash
chart_dir=./k8Deployment/kubernetes/helm/basic-pitch

helm lint "$chart_dir"
helm template clouddsp-basic-pitch "$chart_dir" \
  --namespace clouddsp-app

helm upgrade clouddsp-basic-pitch "$chart_dir" \
  --kube-context k3d-clouddsp-local \
  --namespace clouddsp-app \
  --reset-values --values "$chart_dir/values.yaml" \
  --wait --timeout 3m

ruby ./k8Deployment/kubernetes/scripts/releases/basic-pitch-release.rb verify
```

The first two commands work without a running cluster. The upgrade and
verification need the existing cluster, KEDA, and worker release. Helm updates
the ScaledObject; KEDA and the generated HPA adjust the worker count. A policy
change that leaves the Deployment Pod template unchanged does not replace
Pods through a Deployment rollout. `--wait` does not replace the final
component verification of scaler readiness and HPA ownership.

Use checked-in values rather than a one-off `--set` override. Release helpers
render those files and compare the result with the stored Helm manifest and
live objects. With `minReplicaCount: 0`, verification retains the strict idle,
zero-Pod contract. With a positive minimum, verification accepts a Ready
worker count within the configured minimum/maximum, because KEDA may have
scaled above the minimum. A new policy may need time to converge before that
check passes.

### Return to the normal policy

Restore `minReplicaCount: 0` in the same values file, lint/render again, and
repeat the upgrade and verification commands. This changes the policy while
preserving the current worker image and other desired configuration. KEDA's
cooldown and the HPA stabilization window govern when idle Pods disappear.

`helm rollback` is an alternative for undoing a problematic release, but it
restores the entire selected release, including any older image or other
settings. Inspect `helm history clouddsp-basic-pitch` with the same context
and namespace, select an explicit revision, and restore the corresponding
chart/values in Git so subsequent verification and upgrades agree with the
rolled-back release. Helm rollback does not edit repository files.

The charts currently expose queue/task scaling settings only. A scheduled
warm-worker policy would require adding a reviewed KEDA cron trigger; it is
not installed by these values.

## Scoped Demucs maintenance

```bash
./k8Deployment/kubernetes/scripts/maintenance/reconcile-demucs-scaling.sh
```

This helper reasserts an already deployed, matching, idle Demucs profile. It
first verifies both scaler identities, scaling-auth, and Demucs using the
worker's `verify-idle` mode. That mode refuses a positive minimum even when
warm workers are healthy, preserving this helper's idle-only scope. Then it uses
Helm to upgrade existing `clouddsp-scaling-auth` and `clouddsp-demucs` releases
in that order with explicit context/namespace and three-minute waits, and
repeats the idle verification. It creates no bootstrap Secret/Job and takes no ownership
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
