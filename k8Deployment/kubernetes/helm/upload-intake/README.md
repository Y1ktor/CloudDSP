# Upload-intake Helm release

This chart owns only `Deployment/clouddsp-upload-intake` in `clouddsp-app`.
Its one outbound-only Pod consumes private MinIO source notifications from
RabbitMQ, verifies the object, and commits an owner-safe PostgreSQL job
transition and outbox event before acknowledging the message. It has no
Service or Ingress because it accepts no inbound traffic. Broker topology,
MinIO notifications, database bootstrap, runtime Secrets, and integration
Jobs remain outside this release.

The template mirrors
[`../../services/upload-intake/upload-intake-deployment.yaml`](../../services/upload-intake/upload-intake-deployment.yaml).
Helm supplies only the release namespace and ownership label, plus the image
value locked as `images.upload-intake.immutableReference` in
[`../../images.lock.yaml`](../../images.lock.yaml). The Deployment's immutable
selector, Pod labels, Secret references, dependency DNS, resource limits,
security settings, and one-replica strategy remain identical to the
previously running object. No Secret values enter Helm values or history.

## Adoption and verification

From the repository root:

```bash
./k8Deployment/kubernetes/scripts/releases/upload-intake-release.rb plan
./k8Deployment/kubernetes/scripts/releases/upload-intake-release.rb adopt
./k8Deployment/kubernetes/scripts/releases/upload-intake-release.rb verify
```

`plan` runs strict Helm lint, checks the image lock, renders exactly one
Deployment, validates it with a Kubernetes server-side dry run, and compares
all declared fields against the source and live Deployment. `adopt` repeats
those checks before the one-time Helm ownership transfer, then requires the
original Deployment UID and Pod UID to remain unchanged. It does not
automatically uninstall or roll back an adopted release. `verify` checks the
stored Helm manifest, live spec, Pod readiness, and running image digest.

The consumer has no HTTP health endpoint or process readiness probe. Those
checks prove this metadata-only adoption preserved the running workload, but
they do not prove a new source notification reaches the outbox. The separate
[`../../tests/source-intake-smoke/`](../../tests/source-intake-smoke/README.md)
suite tests that durable path. Its Job now uses the reviewed Job API image
digest. Both dispatcher Helm releases are paused through versioned values
while the test checks a pending outbox row, then restored after cleanup.

The live smoke passed on 2026-09-26: a temporary authenticated upload reached
`source_uploaded`, exactly one pending Demucs outbox event survived a duplicate
source notification, and the test cleaned up its identities, object, and row.
The first attempt exposed a missing RabbitMQ management-port NetworkPolicy
exception for this fixed test Pod; the narrow versioned policy rule fixed it.
The disposable Job was deleted, both dispatcher releases were restored to one
replica, and the source and Demucs queues were empty afterward.

On 2026-09-26, the existing Deployment was adopted as release
`clouddsp-upload-intake` revision 1. Its Deployment UID, Pod UID, and running
image digest remained unchanged.

Use this chart for reviewed upload-intake upgrades. Keep the raw Deployment
manifest as the adoption comparison baseline; do not reapply it to the
Helm-owned object.
