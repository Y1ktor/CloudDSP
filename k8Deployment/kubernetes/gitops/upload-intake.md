# Upload-intake's handoff from direct Helm to Flux

The versioned root selects the existing native `clouddsp-upload-intake` Helm
release. Its HelmRelease lives in `flux-system`; its release, revision Secrets,
and single Deployment stay in `clouddsp-app`. The base chart remains `0.1.0`,
with its locked image, values, Pod template, selectors, and runtime Secret
references unchanged. Check current-generation readiness and the release
verifier to establish the state of a running cluster.

## Ownership and dependencies

- The [HelmRelease](clusters/clouddsp-local/upload-intake/helmrelease.yaml)
  fixes the existing release name and target/storage namespace. Changing
  those identifiers can uninstall the previous release or lose Helm history.
- [Reconciliation RBAC](clusters/clouddsp-local/upload-intake/reconciliation-rbac.yaml)
  binds `flux-system/clouddsp-upload-intake-helm` into only `clouddsp-app`.
  Its Role can change Deployments and Helm storage Secrets and can read Pods
  and ReplicaSets for readiness. This chart has no Service or Ingress, so
  neither API kind is granted. No Jobs, PVCs, CRDs, namespace creation, or
  cluster RBAC are granted. Helm storage requires namespace-wide Secret
  access; this grant is not limited to this release's revision Secret names.
  The root controller and trusted Git writers retain their administrative scope.
- The [chart](../helm/upload-intake/) owns the outbound consumer Deployment
  only. PostgreSQL schema/identities, RabbitMQ topology/credentials, MinIO
  notifications/IAM, and runtime Secrets retain their bootstrap ownership.
  Credentials are excluded from Git and chart values.
- `dependsOn` waits for the already managed `flux-system/clouddsp-job-api`
  HelmRelease and the PostgreSQL/MinIO/RabbitMQ HelmReleases to be Ready.
  Job API verification is also an existing fresh
  installation prerequisite. This gate does not bootstrap or verify all
  database, broker, or storage identities; the ordinary fresh workflow still
  runs all six original prerequisite checks before direct installation.
- PostgreSQL, MinIO and RabbitMQ delivery have separate Flux handoffs; their
  contents/identities remain bootstrap-owned. Both
  dispatchers have separate handoffs and are not dependencies of this consumer.
  A missing native release can be installed by this configuration; there is
  no adopt-only mode. Prepare and verify dependency state before opting in.

Source preparation adds `helm/upload-intake/` to Git sparse checkout in a
separate commit. Verify the current source archive contains its `Chart.yaml`
before publishing the commit that enables the HelmRelease in the root. This
avoids packaging against an earlier sparse archive.

Flux upgrades the native release in its original storage namespace. Git
revision packaging supplies a suffix such as `0.1.0+abcdef123456.1`. Flux adds
its two exact name/namespace labels only to resource-level metadata. The Pod
template, AMQP consumer settings, image, and restricted Secret references
retain their existing configuration.

## Verification and durable processing smoke

From the repository root:

```sh
flux get helmreleases --context k3d-clouddsp-local --namespace flux-system
helm --kube-context k3d-clouddsp-local history clouddsp-upload-intake --namespace clouddsp-app
./k8Deployment/kubernetes/scripts/releases/upload-intake-release.rb verify
```

The [runner](../scripts/releases/upload-intake-release.rb) and
[Flux adapter](../scripts/gitops/upload-intake-flux-ownership.rb) require exact
source/render/stored/live specs, native Helm ownership, current-generation
Flux readiness, running locked image digest, and one Ready Pod. The consumer
has no HTTP route or process readiness probe; Kubernetes readiness alone does
not establish that it can process a notification.

Use the existing [source-to-outbox smoke runbook](../tests/source-intake-smoke/README.md)
for the behavioral check. It performs a genuine authenticated upload through
the Job API's presigned form, observes MinIO's native RabbitMQ notification,
and requires upload-intake to atomically commit `source_uploaded` and exactly
one pending `demucs.requested` outbox row. It publishes one duplicate source
notification and requires the same single row to remain.

The runbook checks an empty source queue and healthy dispatchers, then pauses
both dispatchers through committed Flux valuesFiles. This
keeps the deliberately tiny test WAV out of DSP processing while the pending
row is asserted. After cleanup, delete the exact disposable Job, restore both
dispatcher charts' normal values, verify both releases, and check the queues.
The test removes only its temporary Keycloak client/user, source object, and
owner-bound database job/outbox row. Credentials and private identifiers are
not printed.

A failed or interrupted test needs its safe log and cleanup state inspected
before restoring dispatchers; an orphaned synthetic row must not be published
as real work. Both dispatcher helpers include read-only pause verifiers that require all
publisher Pods, including terminating Pods, to disappear. The shared values
editor validates both HelmReleases before staging their Git changes.

The smoke is a separate administrator-driven, disposable Job in
`clouddsp-data`. It is not owned by this chart or a Helm test hook, and the
upload-intake reconciliation identity cannot create it. Flux tests are disabled.

## Delivery and recovery

Commit chart/values changes to `codex/flux-clouddsp-local`, keeping the image
lock and retained source manifest consistent. Drift detection repairs direct
live changes against the stored rendered manifest. The helper blocks direct
`install` and `adopt` while the matching HelmRelease exists, including failed,
suspended, or deleting states. API lookup failures fail closed.

After inspecting source and release conditions, reconciliation can be requested:

```sh
flux reconcile helmrelease clouddsp-upload-intake --with-source \
  --context k3d-clouddsp-local --namespace flux-system
```

Automatic rollback/uninstall remediation, forced replacement, and failed-upgrade
cleanup are disabled. A failed action can still leave partial changes; inspect
native Helm history and repair Git. A new chart revision starts a new attempt.
A diagnosed transient failure can be retried with
`flux reconcile helmrelease clouddsp-upload-intake --reset` using the same
context and namespace. Removing an active HelmRelease normally uninstalls
its Deployment; design an explicit ownership return before handing it to
another tool.

The normal fresh bootstrap still prepares dependencies and directly installs
the application before opt-in Flux bootstrap. Host cluster/registry lifecycle
and durable service data/bootstrap remain outside this handoff.
