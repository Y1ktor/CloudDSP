# Demucs worker Helm release

This chart owns `Deployment/clouddsp-demucs` and
`ScaledObject/clouddsp-demucs-rabbitmq-scaler` in `clouddsp-app`. It preserves
the digest-pinned ARM64 CPU worker, private Secret references, scratch volume,
and internal queue consumer. The ScaledObject retains both its RabbitMQ queue
trigger and PostgreSQL due-work trigger. KEDA owns the generated HPA and
writes the Deployment `/scale` subresource; this chart omits `spec.replicas`.
The worker has no Service or Ingress, and zero active Pods is healthy when
idle. A Pod already marked for deletion may remain visible while Kubernetes
honors its 780-second grace period; the release check requires zero desired
and observed replicas plus no Pod outside that termination path.

```bash
./k8Deployment/kubernetes/scripts/releases/demucs-release.rb plan
./k8Deployment/kubernetes/scripts/releases/demucs-release.rb adopt
./k8Deployment/kubernetes/scripts/releases/demucs-release.rb verify
./k8Deployment/kubernetes/scripts/releases/demucs-release.rb smoke
```

`plan` checks the image lock, rendered chart, source and live specs, API
schema, both scaler authentication references, scaler readiness, and HPA
ownership. The one-time Helm takeover preserved both resource UIDs, scaler
generation, generated HPA UID, and zero idle Pods. `verify` checks Helm's
stored manifest and the live relationships. The source manifests under
`services/demucs/` remain a comparison baseline; use this chart for later
Demucs delivery changes.

`smoke` creates the fixed-coordinate, digest-pinned Job described in the
[test runbook](../../tests/demucs-worker-smoke/README.md), waits for the real
dispatcher and worker to finish, and deletes that Job only after a passing
result. Its client verifies private stem bytes and provenance. It waits for
both downstream Basic Pitch tasks and the parent Job to complete before its
guarded cleanup removes only the test objects and rows.

The first post-adoption trial proved Demucs completion and stem artifacts,
but the full smoke did not pass: Basic Pitch scaled to zero while one of its
acknowledged tasks was still running. The client preserved the fixed test
data for diagnosis. Demucs Helm ownership and idle-state `verify` passed
again afterward. See the [test runbook](../../tests/demucs-worker-smoke/README.md)
before any rerun.

KEDA, shared `scaling-auth`, runtime Secrets, database bootstrap, MinIO
policy, RabbitMQ topology, and the generated HPA remain outside this release.
Do not uninstall an adopted release as a retry strategy: Helm would consider
the existing Deployment and ScaledObject its resources to delete.
