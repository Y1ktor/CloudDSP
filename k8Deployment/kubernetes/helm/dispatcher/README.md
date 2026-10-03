# Legacy dispatcher Helm release

This chart owns only `Deployment/clouddsp-dispatcher` in `clouddsp-app`. Its
single Pod publishes durable `demucs.requested` outbox rows to RabbitMQ. It
has no Service or Ingress because it accepts no inbound traffic. PostgreSQL,
RabbitMQ, the generic dispatcher, bootstrap Jobs, and runtime Secrets remain
outside this release.

The template mirrors
[`../../services/dispatcher/dispatcher-deployment.yaml`](../../services/dispatcher/dispatcher-deployment.yaml).
Helm supplies the release namespace and ownership label. The only image value
must equal `images.dispatcher-demucs-only.immutableReference` in
[`../../images.lock.yaml`](../../images.lock.yaml). This is the earlier
Demucs-only image; `images.dispatcher` belongs to the generic controller.
The immutable Deployment selector, Pod labels, environment, Secret references,
security settings, resources, and one-replica strategy are unchanged.

## Adoption and verification

From the repository root:

```bash
./k8Deployment/kubernetes/scripts/releases/dispatcher-release.rb plan
./k8Deployment/kubernetes/scripts/releases/dispatcher-release.rb adopt
./k8Deployment/kubernetes/scripts/releases/dispatcher-release.rb verify
```

`plan` runs strict Helm lint, renders the one Deployment, checks the image
lock, validates it with a Kubernetes server-side dry run, and compares its
declared spec with the source and live Deployment. `adopt` repeats those
checks before Helm's explicit ownership transfer and then verifies the
original Deployment UID and Pod UID are unchanged. It does not use automatic
uninstall or rollback; uninstalling an adopted release could delete the
original Deployment. Use `verify` for a read-only ownership, stored-manifest,
spec, Pod readiness, and running image-digest check.

The dispatcher has no HTTP health endpoint or Kubernetes readiness probe.
Pod readiness and the unchanged digest are a deployment-path check, not proof
that a new outbox row traverses PostgreSQL and RabbitMQ. The separate
[`../../tests/dispatcher-smoke/`](../../tests/dispatcher-smoke/) flow tests
that message path and requires its own temporary credentials and cleanup.

On 2026-09-26, the existing Deployment was adopted as release
`clouddsp-dispatcher` revision 1. Its Deployment UID and Pod UID did not
change, and the running digest remained the locked Demucs-only image. The
generic dispatcher was subsequently adopted into its own
[`../generic-dispatcher/`](../generic-dispatcher/README.md) release.

The source-to-outbox integration smoke later used this chart's versioned
`values.smoke-pause.yaml` to pause publication while asserting a pending test
event. The release was restored to one Ready replica at revision 3 after the
test cleaned up its synthetic row. The temporary scale change replaced the
Pod, so the unchanged Pod UID claim above applies to the original adoption.

After adoption, use this chart for reviewed legacy dispatcher upgrades. Keep
the raw `services/dispatcher/dispatcher-deployment.yaml` as a comparison
baseline; do not reapply it to this Helm-owned object.
