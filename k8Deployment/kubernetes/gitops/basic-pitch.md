# Basic Pitch worker handoff to Flux

The root selects `flux-system/clouddsp-basic-pitch`, reusing the existing native
release/history in `clouddsp-app`. Its [chart](../helm/basic-pitch/) owns only
`Deployment/clouddsp-basic-pitch` and
`ScaledObject/clouddsp-basic-pitch-rabbitmq-scaler`. The ARM64 CPU image, Pod
template, portable Numba setting, resources, runtime Secret references, queue,
and both shared authentication references remain unchanged by the handoff.
There is no Service, Ingress, PVC, or chart-owned test Job.

## Ownership and scaling

Prepare and verify `helm/basic-pitch/` in the served sparse source before adding
its [HelmRelease](clusters/clouddsp-local/basic-pitch/helmrelease.yaml). The
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

For capacity changes, edit [chart values](../helm/basic-pitch/values.yaml),
lint/render, review, and publish to `codex/flux-clouddsp-local`. Use Git to
restore earlier desired values. The original parameterized worker verifier
reads that same values file: a positive minimum requires Ready workers within
the configured limits; default minimum zero has a strict idle gate after work
drains and cooldown ends. Do not issue competing Helm upgrades, rollbacks, or
manual scaling. This profile validates CPU execution only.

The [reconciliation Role](clusters/clouddsp-local/basic-pitch/reconciliation-rbac.yaml)
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
helm --kube-context k3d-clouddsp-local history clouddsp-basic-pitch --namespace clouddsp-app
ruby k8Deployment/kubernetes/scripts/releases/basic-pitch-release.rb verify-idle
ruby k8Deployment/kubernetes/scripts/releases/basic-pitch-release.rb smoke
```

The [runner](../scripts/releases/basic-pitch-release.rb) and
[adapter](../scripts/gitops/basic-pitch-flux-ownership.rb) require exact
release/storage/source/values/dependency/drift configuration, current-generation
Ready, native Git-packaged revision, and full source/render/stored/live parity.
Original policy, idle/warm readiness, both trigger authentication references,
scaler readiness, and generated HPA owner/target checks remain. The only extra
resource labels accepted are Flux's exact two origin labels.

Direct install/adopt and historical `upgrade-scaling`, `upgrade-stabilization`,
and `upgrade-numba` commands are blocked whenever the HelmRelease exists,
including failed, suspended, and deleting states. API lookup failures fail
closed. Those native upgrade paths remain available on pre-Flux clusters.

The separate [worker smoke](../tests/basic-pitch-worker-smoke/README.md) needs
its restricted test identities, three fixed PostgreSQL functions, and exact-key
MinIO policy. It uploads a controlled private vocal fixture, creates a durable
outbox event, uses the real generic dispatcher/queue/KEDA/worker path, verifies
successful task state and private MIDI bytes/hash/provenance, and cleans its
reserved artifacts/rows. The deliberately incomplete two-stem parent fixture
has one exact expected finalization failure; unrelated failures do not pass.
Its client has no RabbitMQ or Kubernetes API permission.

This administrator-triggered versioned Job is outside Flux and Helm test hooks.
The runner removes it only after success. Wait for ordinary KEDA cooldown and
Pod termination before the final idle gate. For identities provisioned solely
for a successful trial, run the versioned retirement Jobs, inspect both
completions, then remove their exact Jobs, test Secrets, and policy ConfigMap.
Failed/interrupted runs preserve evidence for diagnosis before retirement or a
rerun.

The synthetic [generic routing smoke](../tests/generic-dispatcher-smoke/README.md)
is a different test: it requires temporarily pausing Basic Pitch so its queue
reader cannot race a real worker. Its current native pause/restore commands
apply only before this handoff. A reviewed GitOps maintenance path is needed
before running that synthetic test on the Flux-owned worker. The processing
smoke above requires no pause or policy override.

## Recovery

Forced replacement, ownership takeover, automatic rollback/uninstall, and
failed-upgrade cleanup are disabled. A failure can leave partial state;
inspect native history and Flux conditions and repair the published Git
configuration. Removing an active HelmRelease normally uninstalls its worker
and scaler. KEDA's `restoreToOriginalReplicaCount` may alter a remaining
Deployment during scaler deletion, so release removal is deliberate cleanup.
Credential rotation and durable service bootstrap remain separate.
