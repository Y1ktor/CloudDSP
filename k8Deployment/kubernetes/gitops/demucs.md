# Demucs worker handoff to Flux

The root selects `flux-system/clouddsp-demucs`, reusing the existing native
release/history in `clouddsp-app`. Its [chart](../helm/demucs/) owns only
`Deployment/clouddsp-demucs` and
`ScaledObject/clouddsp-demucs-rabbitmq-scaler`. The ARM64 CPU image, Pod
template, resources, runtime Secret references, queue,
and both shared authentication references remain unchanged by the handoff.
There is no Service, Ingress, PVC, or chart-owned test Job.

## Ownership and scaling

Prepare and verify `helm/demucs/` in the served sparse source before adding
its [HelmRelease](clusters/clouddsp-local/demucs/helmrelease.yaml). The
release uses Git revision packaging, active drift correction, its original
release name, explicit target/storage namespace, and ten-revision retention.
It waits for the managed MinIO and scaling-auth HelmReleases. This readiness
dependency does not provision database/broker/MinIO identities or install KEDA;
ordinary platform bootstrap supplies those prerequisites.

The scaler retains RabbitMQ queue depth and PostgreSQL active/due task counts.
The database trigger keeps durable processing visible after a message has been
acknowledged. KEDA owns the generated HPA and replica decisions. The chart
omits `spec.replicas`; Flux's only drift exception is `/spec/replicas` on this
exact `apps/v1` Deployment in `clouddsp-app`. The worker template and complete
ScaledObject policy remain subject to drift correction. The settings follow
the official [Flux ignore rules](https://fluxcd.io/flux/components/helm/helmreleases/#ignore-rules).

For capacity changes, edit [chart values](../helm/demucs/values.yaml),
lint/render, review, and publish to `codex/flux-clouddsp-local`. Use Git to
restore earlier desired values. The original parameterized worker verifier
reads that same values file: a positive minimum requires Ready workers within
the configured limits; default minimum zero has a strict idle gate after work
drains and cooldown ends. Do not issue competing Helm upgrades, rollbacks, or
manual scaling. This profile validates CPU execution only.

The [reconciliation Role](clusters/clouddsp-local/demucs/reconciliation-rbac.yaml)
can manage application namespace Deployments, ScaledObjects, and Secrets
needed for Helm history, and read Pods/ReplicaSets. Secret access covers every
application namespace Secret. It grants no HPA or shared authentication writes,
explicit Deployment scale subresource access, Pod/Job creation, PVC,
Service/Ingress, namespace, CRD, cluster RBAC, or data namespace Secret access.
Deployment CRUD still permits spec changes, including replicas; chart omission
and the exact drift exception preserve KEDA's intended replica control. The
root Flux reconciler and trusted Git writers retain administrative scope.

## Verify and processing smoke

From the repository root:

```sh
flux get helmreleases --context k3d-clouddsp-local --namespace flux-system
helm --kube-context k3d-clouddsp-local history clouddsp-demucs --namespace clouddsp-app
ruby k8Deployment/kubernetes/scripts/releases/demucs-release.rb verify-idle
ruby k8Deployment/kubernetes/scripts/releases/demucs-release.rb smoke
```

The [runner](../scripts/releases/demucs-release.rb) and
[adapter](../scripts/gitops/demucs-flux-ownership.rb) require exact
release/storage/source/values/dependency/drift configuration, current-generation
Ready, native Git-packaged revision, and full source/render/stored/live parity.
Original policy, idle/warm readiness, both trigger authentication references,
scaler readiness, and generated HPA owner/target checks remain. The only extra
resource labels accepted are Flux's exact two origin labels.

Direct install/adopt is blocked whenever the HelmRelease exists, including
failed, suspended, and deleting states. API lookup failures fail closed.
`reconcile` verifies full idle release parity before and after delivery. With
Flux ownership it performs no direct Helm write; a pre-Flux cluster retains a
gated native upgrade with stable worker/scaler/HPA identity checks.

The [maintenance wrapper](../scripts/maintenance/reconcile-demucs-scaling.sh)
delegates both shared authentication and Demucs delivery to their respective
ownership-aware runners. It verifies existing observer identities first and
requires idle Demucs. It does not bootstrap missing credentials or releases.

The separate [worker smoke](../tests/demucs-worker-smoke/README.md) needs
its restricted test identities, three fixed PostgreSQL functions, and five-key
MinIO policy. It uploads a controlled private source, creates a durable
source/outbox fixture, and uses the real generic dispatcher/queue/KEDA/CPU
Demucs path. The client verifies first-attempt Demucs success, cleared lease,
two private stem WAVs with hashes/provenance, and two durable downstream Basic
Pitch events. It waits for both downstream tasks and the parent to complete
before removing its five exact objects and guarded rows. MIDI content and
musical quality are outside this smoke's assertions.

This administrator-triggered versioned Job is outside Flux and Helm test hooks.
Its client has no RabbitMQ credential or Kubernetes API permission. The runner
removes it only after success. Wait for ordinary KEDA cooldown and Pod
termination for Demucs and Basic Pitch before the final idle gates. Existing
restricted test access may be reused and must remain unchanged. Failed or
interrupted runs preserve evidence for diagnosis before state-specific cleanup
or a rerun; do not rotate credentials, purge queues, or manually scale as a
recovery shortcut.

## Recovery

Forced replacement, ownership takeover, automatic rollback/uninstall, and
failed-upgrade cleanup are disabled. A failure can leave partial state;
inspect native history and Flux conditions and repair the published Git
configuration. Removing an active HelmRelease normally uninstalls its worker
and scaler. KEDA's `restoreToOriginalReplicaCount` may alter a remaining
Deployment during scaler deletion, so release removal is deliberate cleanup.
Credential rotation and durable service bootstrap remain separate.
