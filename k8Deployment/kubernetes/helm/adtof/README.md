# ADTOF worker Helm release

This chart owns only `Deployment/clouddsp-adtof` and
`ScaledObject/clouddsp-adtof-rabbitmq-scaler` in `clouddsp-app`.
The Deployment retains the ARM64 CPU image, private Secret references,
non-root account, bounded scratch volumes, and internal queue consumer.
It has no Service or Ingress. The ScaledObject retains the reviewed RabbitMQ
queue, shared KEDA authentication reference, and default zero-to-two replica policy.
KEDA owns the generated HPA and writes the Deployment `/scale` subresource;
the chart deliberately omits `spec.replicas`.

[`values.yaml`](values.yaml) exposes polling/cooldown, replica bounds,
RabbitMQ queue thresholds, and HPA scale-up/scale-down behavior under
`autoscaling`. Defaults retain the previous behavior. Queue coordinates,
authentication references, and worker resource limits remain in the templates.
Follow the [scaling policy workflow](../../docs/reference/helm-releases-and-scaling.md#configure-a-worker-scaling-policy)
to edit versioned values, lint/render, upgrade the worker release, and verify.
The default zero minimum keeps the strict idle zero-Pod check; a positive
minimum requires a Ready worker count within the configured minimum/maximum.
`verify-idle` requires a zero minimum and the strict idle zero-replica/Pod
contract for maintenance. Fresh `install` allows up to 180 seconds of
additional read-only checks after Helm returns for KEDA/HPA convergence;
it does not repeat the Helm install or override the replica count.

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
The raw `services/adtof/` manifests retain the original default-policy source
baseline; only the parameterized scaling fields may differ from it. Stored
and live ScaledObjects must match the rendered values exactly. Use this chart
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

## Optional Flux delivery

The [Flux handoff guide](../../gitops/adtof.md) selects this native release
with the same name and target/storage namespace. After handoff, publish chart
and scaling-value changes to `codex/flux-clouddsp-local` for Flux to reconcile.
The one-time `plan`/`adopt` commands above describe the pre-Flux transition;
direct `install`/`adopt` is blocked while its HelmRelease exists. Continue using
`verify`, `verify-idle`, and the separate administrator-driven `smoke`.
The exact Deployment replica field is ignored by Flux drift correction so
KEDA retains scale decisions; Pod template and scaler policy remain protected.
