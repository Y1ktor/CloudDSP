# Demucs worker Helm release

This chart owns `Deployment/clouddsp-demucs` and
`ScaledObject/clouddsp-demucs-rabbitmq-scaler` in `clouddsp-app`. It preserves
the digest-pinned ARM64 CPU worker, private Secret references, scratch volume,
and internal queue consumer. The ScaledObject retains both its RabbitMQ queue
trigger and PostgreSQL due-work trigger. KEDA owns the generated HPA and
writes the Deployment `/scale` subresource; this chart omits `spec.replicas`.
The worker has no Service or Ingress. Its default scaling policy expects zero
active Pods when idle. A Pod already marked for deletion may remain visible
while Kubernetes honors its 780-second grace period; with the default zero
minimum, the release check requires zero desired and observed replicas plus
no Pod outside that termination path. A positive configured minimum instead
requires a Ready worker count within the configured minimum/maximum.

[`values.yaml`](values.yaml) exposes polling/cooldown, replica bounds, queue
and due-work thresholds, and HPA scale-up/scale-down behavior under
`autoscaling`. Defaults retain the previous policy, including the one-Pod CPU
ceiling. Queue coordinates, SQL, authentication references, and worker resource
limits remain in the templates. Follow the [scaling policy workflow](../../docs/reference/helm-releases-and-scaling.md#configure-a-worker-scaling-policy)
to edit versioned values, lint/render, upgrade the worker release, and verify.
`verify-idle` is the stricter maintenance check: it refuses a positive minimum
and requires idle zero replicas/Pods. Fresh `install` allows up to 180 seconds
of additional read-only checks after Helm returns for KEDA/HPA convergence;
it does not repeat the Helm install or override the replica count.

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
`services/demucs/` remain the original default-policy comparison baseline;
only the parameterized scaling fields may differ from that baseline. Stored
and live ScaledObjects must match the rendered values exactly. Use this chart
for later Demucs delivery changes.

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
