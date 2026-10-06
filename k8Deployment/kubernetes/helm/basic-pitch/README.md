# Basic Pitch worker Helm release

This chart owns only `Deployment/clouddsp-basic-pitch` and
`ScaledObject/clouddsp-basic-pitch-rabbitmq-scaler` in `clouddsp-app`.
The Deployment keeps the pinned image, private Secret references, bounded
scratch volumes, and its internal worker identity. It has no Service or
Ingress because it only consumes RabbitMQ messages. The ScaledObject keeps
its reviewed queue name and default zero-to-three replica policy. It combines
RabbitMQ backlog with the restricted PostgreSQL active-task count: Basic Pitch
acknowledges a request after committing its lease, before CPU inference, so
the queue alone can become empty while a Pod is still needed. KEDA owns the
generated HPA and writes the Deployment's `/scale` subresource; the chart
deliberately omits `spec.replicas`. Its default six-minute HPA scale-down window
covers the five-minute model deadline: after one of several tasks finishes,
the Deployment must not remove another Pod that may still own active work.
The Pod also sets `NUMBA_CPU_NAME=generic`. The fixed Demucs smoke stem
reproduced a Numba illegal-instruction crash during the worker's best-effort
librosa tempo calculation on this ARM64 k3d node; the same calculation
completed with Numba's portable CPU target.

[`values.yaml`](values.yaml) exposes polling/cooldown, replica bounds, queue
and due-work thresholds, and HPA scale-up/scale-down behavior under
`autoscaling`. Defaults retain the previous behavior. Follow the
[scaling policy workflow](../../docs/reference/helm-releases-and-scaling.md#configure-a-worker-scaling-policy)
to keep workers warm or tune concurrency through versioned values and a Helm
upgrade. With a zero minimum, verification retains the strict idle zero-Pod
contract. With a positive minimum, it requires a Ready worker count within the
configured minimum/maximum rather than exactly the minimum.
`verify-idle` requires a zero minimum and the strict idle zero-replica/Pod
contract for maintenance. Fresh `install` allows up to 180 seconds of
additional read-only checks after Helm returns for KEDA/HPA convergence;
it does not repeat the Helm install or override the replica count.

```bash
./k8Deployment/kubernetes/scripts/releases/basic-pitch-release.rb plan
./k8Deployment/kubernetes/scripts/releases/basic-pitch-release.rb adopt
./k8Deployment/kubernetes/scripts/releases/basic-pitch-release.rb verify
./k8Deployment/kubernetes/scripts/releases/basic-pitch-release.rb smoke
```

An already adopted chart 0.1.0 can take the complete scaling fix with the
guarded `upgrade-scaling` mode. It verifies the original installed/live
scaler and worker, then changes only the second trigger, scale-down window,
portable Numba setting, and descriptive label:

```bash
./k8Deployment/kubernetes/scripts/releases/basic-pitch-release.rb upgrade-scaling
./k8Deployment/kubernetes/scripts/releases/basic-pitch-release.rb verify
```

For a cluster already at the intermediate chart 0.1.1, use
`basic-pitch-release.rb upgrade-stabilization` before `verify`. For the
already stabilized chart 0.1.2, use the guarded Pod-template rollout:

```bash
./k8Deployment/kubernetes/scripts/releases/basic-pitch-release.rb upgrade-numba
./k8Deployment/kubernetes/scripts/releases/basic-pitch-release.rb verify
```

All three upgrade paths preserve the existing Deployment, ScaledObject, and
generated HPA identities. `upgrade-numba` checks installed and live 0.1.2
resources before adding the sole new environment variable.

`plan` checks Helm's render against the original source manifests, the image
lock, API-server schema, live specs, scaler readiness, and HPA ownership.
`adopt` is a one-time metadata handoff and preserves both resource UIDs, the
ScaledObject generation, generated HPA UID, and zero idle Pods. `verify` is
read-only. The raw `services/basic-pitch/` manifests retain the original
default-policy source baseline; only the parameterized scaling fields may
differ from it. Stored and live ScaledObjects must match the rendered values
exactly. Change this chart's versioned values for subsequent policy updates.

`smoke` runs the fixed-coordinate Basic Pitch worker Job and removes its Job
only on success. Its one-stem fixture is intentionally incomplete for the
parent Job: the restricted database observation function recognizes only the
finalizer's exact incomplete-stem error, while the client independently
requires a published event, first-attempt succeeded worker task, valid private
MIDI bytes, checksum, and provenance. The repaired smoke passed after the
v002 restricted-function update and pinned client rebuild; see the
[test runbook](../../tests/basic-pitch-worker-smoke/README.md). A different
parent failure retains evidence for diagnosis.

The shared `scaling-auth` release, KEDA controller, runtime Secrets, database
bootstrap, MinIO policy, and RabbitMQ queue are separate dependencies. Never
uninstall this adopted release as a retry strategy: Helm would then consider
the existing Deployment and ScaledObject its resources to delete.

## Dispatcher routing smoke maintenance

The [generic routing runbook](../../tests/generic-dispatcher-smoke/README.md)
uses `values.routing-smoke-pause.yaml` to set
`maintenance.pauseForRoutingSmoke: true`. This adds only KEDA's
`autoscaling.keda.sh/paused-replicas: "0"` annotation. KEDA holds the worker at
zero so the restricted smoke reader can validate its exact synthetic delivery.
Run only with empty queues, no ordinary tasks or retries, and no worker Pods.
Restore normal values after the event/message is removed; never use the
maintenance switch for normal capacity changes. Those native pause/restore
commands apply only before Flux adoption. A reviewed GitOps maintenance path
is required for the synthetic routing test after the handoff. The worker
processing smoke needs no pause or policy override.

## Optional Flux delivery

The [Basic Pitch handoff guide](../../gitops/basic-pitch.md) selects the existing
release and storage in `clouddsp-app`. Flux manages the Deployment and
ScaledObject; KEDA owns its generated HPA and replica control. The chart omits
replicas, and Flux ignores only `/spec/replicas` on the exact worker Deployment.
Both shared RabbitMQ and PostgreSQL authentication references remain unchanged.

Publish chart/values changes to the watched GitOps branch. The ownership-aware
runner verifies native manifests, policy, idle/warm readiness, HPA ownership,
and current Flux reconciliation, and blocks direct install/adopt and the three
historical native upgrade modes whenever its HelmRelease exists. Smoke Jobs
and identity bootstrap/retirement remain separate, versioned administrator
operations outside the reconciled root.
