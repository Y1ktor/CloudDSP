# Generic dispatcher Helm release

This chart owns only `Deployment/clouddsp-generic-dispatcher` in
`clouddsp-app`. Its route-aware Pod publishes reviewed Demucs, Basic Pitch,
and ADTOF outbox requests to RabbitMQ. The legacy Demucs-only dispatcher is a
separate Helm release; PostgreSQL, RabbitMQ, bootstrap Jobs, and runtime
Secrets remain outside this chart. This internal publisher has no Service or
Ingress because it accepts no inbound traffic.

The template mirrors
[`../../services/dispatcher/dispatcher-generic-deployment.yaml`](../../services/dispatcher/dispatcher-generic-deployment.yaml).
Helm supplies the release namespace and ownership label. The image value
must equal `images.dispatcher.immutableReference` in the reviewed
[`../../images.lock.yaml`](../../images.lock.yaml), distinct from
`images.dispatcher-demucs-only`. The Pod template retains the explicit
`python -m app.dispatcher_generic_runtime` command: the shared image's
default entrypoint runs the legacy runtime. Its immutable selector, Secret
references, resource limits, and security settings remain identical to the
previously running Deployment.

## Adoption and verification

From the repository root:

```bash
./k8Deployment/kubernetes/scripts/releases/generic-dispatcher-release.rb plan
./k8Deployment/kubernetes/scripts/releases/generic-dispatcher-release.rb adopt
./k8Deployment/kubernetes/scripts/releases/generic-dispatcher-release.rb verify
```

`plan` runs strict Helm lint, renders the one Deployment, checks the image
lock, performs a Kubernetes server-side dry run, and compares every declared
field with the source and live Deployment. `adopt` repeats those checks before
Helm's explicit ownership transfer, then verifies that the original
Deployment UID and Pod UID were preserved. It does not use automatic
uninstall or rollback, since uninstalling an adopted release could delete the
original Deployment. `verify` checks Helm ownership, stored manifest, live
spec, Pod readiness, and the running image digest without changing resources.

There is no HTTP health endpoint or process readiness probe. Pod readiness
and image identity are deployment-path checks; they do not prove that a new
outbox event reaches the correct broker route. The separate
[`../../tests/generic-dispatcher-smoke/`](../../tests/generic-dispatcher-smoke/)
test uses controlled data and temporary credentials to check Basic Pitch
routing. Scaling the legacy dispatcher down is a separate rollout decision.

The controlled Basic Pitch routing smoke passed on 2026-09-26: the generic
controller published one synthetic event to the restricted queue, the test
acknowledged its exact delivery, and its synthetic database record and
disposable Job were removed. The queue returned to zero ready/unacknowledged
messages. The legacy controller remained at one replica.

On 2026-09-26, the existing Deployment was adopted as release
`clouddsp-generic-dispatcher` revision 1. Its Deployment UID, Pod UID,
explicit command, and running generic image digest were unchanged.

The later source-to-outbox integration smoke temporarily paused this release
using `values.smoke-pause.yaml` so its test outbox event stayed pending. After
the test's cleanup, the release returned to one Ready replica at revision 3.
That scale operation replaced the Pod; the Pod UID preservation above refers
to the original adoption.

## Optional Flux ownership

The [Flux guide](../../gitops/generic-dispatcher.md) selects this existing
native release without changing its Pod template or locked image. Once its
HelmRelease exists, `install` and `adopt` are blocked even if Flux is suspended
or failed. `verify` requires current-generation Ready, the precise Git-packaged
chart revision, full source/stored/live parity, and one Ready locked-image Pod.
Use Git changes for upgrades and the [source smoke runbook](../../tests/source-intake-smoke/README.md)
for a committed `values.smoke-pause.yaml` pause and restoration. The separate
`verify-smoke-pause` mode requires exactly zero Pods, including terminating ones;
it cannot authorize a direct Helm write or replace ordinary verification.

After adoption, use this chart through its current delivery owner for reviewed generic dispatcher upgrades. Keep
the raw service manifest as a comparison baseline; do not reapply it to the
Helm-owned Deployment.
