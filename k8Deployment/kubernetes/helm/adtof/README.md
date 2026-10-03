# ADTOF worker Helm release

This chart owns only `Deployment/clouddsp-adtof` and
`ScaledObject/clouddsp-adtof-rabbitmq-scaler` in `clouddsp-app`.
The Deployment retains the ARM64 CPU image, private Secret references,
non-root account, bounded scratch volumes, and internal queue consumer.
It has no Service or Ingress. The ScaledObject retains the reviewed RabbitMQ
queue, shared KEDA authentication reference, and zero-to-two replica policy.
KEDA owns the generated HPA and writes the Deployment `/scale` subresource;
the chart deliberately omits `spec.replicas`.

```bash
./k8Deployment/kubernetes/scripts/releases/adtof-release.rb plan
./k8Deployment/kubernetes/scripts/releases/adtof-release.rb adopt
./k8Deployment/kubernetes/scripts/releases/adtof-release.rb verify
./k8Deployment/kubernetes/scripts/releases/adtof-release.rb smoke
```

`plan` compares the reviewed image lock and the rendered chart against both
source and live specs, then checks API-server schema, scaler readiness, and
HPA ownership. `adopt` is a one-time metadata handoff; it preserved both
resource UIDs, the ScaledObject generation, generated HPA UID, and zero idle
Pods. `verify` checks Helm's stored manifest and those runtime relationships.
The raw `services/adtof/` manifests remain a source baseline; use this chart
for subsequent ADTOF delivery changes.

`smoke` runs the versioned fixed-coordinate Job and removes it only on success.
Its one-drum four-stem parent fixture is intentionally incomplete. The
restricted database observer recognizes only the finalizer's exact expected
incomplete-stem error. The client additionally requires a published event,
first-attempt succeeded ADTOF task, and independently verified private MIDI
and tempo bytes, checksums, and provenance before scoped cleanup. The repaired
smoke passed after the v002 test-function update and pinned client rebuild.
An unrelated parent failure retains evidence for diagnosis; see the
[test runbook](../../tests/adtof-worker-smoke/README.md).

KEDA, shared `scaling-auth`, runtime Secrets, database bootstrap, MinIO policy,
RabbitMQ queue, and generated HPA remain outside this release. Do not
uninstall an adopted release as a retry strategy: Helm would consider the
existing Deployment and ScaledObject its resources to delete.
