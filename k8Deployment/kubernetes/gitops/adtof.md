# ADTOF worker handoff to Flux

The root selects `flux-system/clouddsp-adtof`, reusing the existing native
release/history in `clouddsp-app`. Its [chart](../helm/adtof/) owns only
`Deployment/clouddsp-adtof` and `ScaledObject/clouddsp-adtof-rabbitmq-scaler`.
The ARM64 CPU image, Pod template, resource limits, scratch volumes, runtime
Secret references, private queue, and shared authentication reference remain
unchanged by the ownership handoff. There is no Service, Ingress, PVC, or
chart-owned smoke Job.

## Ownership and scaling

Prepare and verify `helm/adtof/` in the served sparse source before adding its
[HelmRelease](clusters/clouddsp-local/adtof/helmrelease.yaml). The release uses
Git revision packaging, active drift correction, the original release name,
and explicit target/storage namespace. History retention is bounded at ten.
It waits for the managed MinIO and scaling-auth HelmReleases. That readiness
dependency does not create ADTOF's database/broker/MinIO identities or install
KEDA; ordinary platform bootstrap supplies those prerequisites.

KEDA remains responsible for generated HPA ownership and Deployment scale
decisions. The chart omits `spec.replicas`. Flux's only drift exception is
`/spec/replicas` on the exact `apps/v1` ADTOF Deployment in `clouddsp-app`.
The worker template and complete ScaledObject policy still undergo drift
correction. See the official [Flux ignore rules](https://fluxcd.io/flux/components/helm/helmreleases/#ignore-rules).

To change polling/cooldown, replica limits, queue thresholds, or HPA behavior,
edit [chart values](../helm/adtof/values.yaml), lint/render, review, and publish
to the watched `codex/flux-clouddsp-local` branch. The original parameterized
worker verifier reads that same values file. Do not issue a competing direct
Helm upgrade or kubectl scale. A positive minimum requires Ready workers
within configured limits; default minimum zero has a strict idle gate after
queue drain/cooldown. Worker throughput and GPU behavior are separate concerns;
this local profile validates CPU execution only.

The [reconciliation Role](clusters/clouddsp-local/adtof/reconciliation-rbac.yaml)
can manage namespace Deployments/ScaledObjects and namespace Secrets needed
for Helm history, and read Pods/ReplicaSets. Secret access covers every
application namespace Secret, including runtime credentials. No HPA writes,
explicit `deployments/scale` grant, TriggerAuthentication writes, Pod/Job
creation, PVC, Service/Ingress, namespace, CRD, or cluster RBAC grant is present.
Deployment write permission still includes its spec fields; the chart omission
and exact drift exception preserve KEDA's replica control. The root Flux
reconciler and trusted Git writers retain administrative scope.

## Verify and smoke

From the repository root:

```sh
flux get helmreleases --context k3d-clouddsp-local --namespace flux-system
helm --kube-context k3d-clouddsp-local history clouddsp-adtof --namespace clouddsp-app
ruby k8Deployment/kubernetes/scripts/releases/adtof-release.rb verify-idle
ruby k8Deployment/kubernetes/scripts/releases/adtof-release.rb smoke
```

The [runner](../scripts/releases/adtof-release.rb) and
[adapter](../scripts/gitops/adtof-flux-ownership.rb) require exact identity and
storage, current-generation Ready, precise native Git-packaged revision,
reviewed source/values/dependency/drift exception, and full source/render/
stored/live parity. They preserve original worker policy, idle/warm readiness,
authentication, scaler readiness, and generated HPA owner/target checks. The
only additional resource labels accepted are Flux's exact two origin labels.
Direct install/adopt is blocked whenever the HelmRelease exists, including
failed, suspended, and deleting states; API lookup errors fail closed.

The separate [worker smoke](../tests/adtof-worker-smoke/README.md) requires its
restricted test identities and three fixed PostgreSQL functions/exact-key
MinIO policy. It uploads a controlled private drum fixture, creates a durable
outbox event, uses the real generic dispatcher/queue/KEDA worker path, verifies
task success and private MIDI/tempo bytes, and performs scoped artifact/row
cleanup. The deliberately incomplete four-stem parent fixture has one exact
expected parent-finalizer failure; unrelated failures do not pass. Its client
has no RabbitMQ credential or Kubernetes API permissions.

The smoke is an administrator-triggered versioned Job outside Flux/Helm test
hooks. The runner removes it only after success. Wait for KEDA cooldown and
worker Pod termination before the final idle check. If temporary test
identities were provisioned only for this trial, run the versioned retirement
Jobs after successful artifact/database cleanup and inspect both completions,
then remove those exact Jobs, test Secrets, and policy ConfigMap. Preserve any
failed or interrupted test's evidence before attempting retirement or rerunning.

## Recovery

Forced replacement, ownership takeover, automatic rollback/uninstall, and
failed-upgrade cleanup are disabled. A failure can leave partial state;
inspect native Helm history and Flux conditions and repair the published Git
configuration. Removing an active HelmRelease normally uninstalls its worker
and ScaledObject. KEDA's `restoreToOriginalReplicaCount` may alter the remaining
Deployment during scaler deletion, so treat release removal as deliberate
cleanup. Credential rotation and durable service bootstrap remain separate.
