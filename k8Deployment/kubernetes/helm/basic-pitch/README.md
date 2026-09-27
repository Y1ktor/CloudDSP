# Basic Pitch worker Helm release

This chart owns only `Deployment/clouddsp-basic-pitch` and
`ScaledObject/clouddsp-basic-pitch-rabbitmq-scaler` in `clouddsp-app`.
The Deployment keeps the pinned image, private Secret references, bounded
scratch volumes, and its internal worker identity. It has no Service or
Ingress because it only consumes RabbitMQ messages. The ScaledObject keeps
its reviewed queue name and zero-to-three replica policy. It now combines
RabbitMQ backlog with the restricted PostgreSQL active-task count: Basic Pitch
acknowledges a request after committing its lease, before CPU inference, so
the queue alone can become empty while a Pod is still needed. KEDA owns the
generated HPA and writes the Deployment's `/scale` subresource; the chart
deliberately omits `spec.replicas`. Its six-minute HPA scale-down window
covers the five-minute model deadline: after one of several tasks finishes,
the Deployment must not remove another Pod that may still own active work.
The Pod also sets `NUMBA_CPU_NAME=generic`. The fixed Demucs smoke stem
reproduced a Numba illegal-instruction crash during the worker's best-effort
librosa tempo calculation on this ARM64 k3d node; the same calculation
completed with Numba's portable CPU target.

```bash
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb plan
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb adopt
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb verify
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb smoke
```

An already adopted chart 0.1.0 can take the complete scaling fix with the
guarded `upgrade-scaling` mode. It verifies the original installed/live
scaler and worker, then changes only the second trigger, scale-down window,
portable Numba setting, and descriptive label:

```bash
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb upgrade-scaling
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb verify
```

For a cluster already at the intermediate chart 0.1.1, use
`basic-pitch-release.rb upgrade-stabilization` before `verify`. For the
already stabilized chart 0.1.2, use the guarded Pod-template rollout:

```bash
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb upgrade-numba
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb verify
```

All three upgrade paths preserve the existing Deployment, ScaledObject, and
generated HPA identities. `upgrade-numba` checks installed and live 0.1.2
resources before adding the sole new environment variable.

`plan` checks Helm's render against the original source manifests, the image
lock, API-server schema, live specs, scaler readiness, and HPA ownership.
`adopt` is a one-time metadata handoff and preserves both resource UIDs, the
ScaledObject generation, generated HPA UID, and zero idle Pods. `verify` is
read-only. The raw `services/basic-pitch/` manifests are the retained source
baseline; change the Helm chart for subsequent deployments.

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
