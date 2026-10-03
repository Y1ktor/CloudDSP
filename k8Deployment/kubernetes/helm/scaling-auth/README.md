# Shared KEDA scaling authentication release

This chart owns exactly two existing namespaced
`TriggerAuthentication` resources in `clouddsp-app`:

- `clouddsp-rabbitmq-scaler-authentication` supplies the restricted RabbitMQ
  observer identity used by ADTOF, Basic Pitch, and Demucs scalers.
- `clouddsp-demucs-postgresql-scaler-authentication` supplies the restricted
  PostgreSQL task-count password used by Demucs and Basic Pitch. The role can
  read only the three task metric columns; each ScaledObject filters its own
  stage in its versioned query.

The KEDA controller and CRDs remain in their pinned `keda` release. Worker
Deployments, their `ScaledObject`s, and KEDA-generated HPAs retain separate
owners. The two app-namespace runtime Secrets are referenced by fixed names
and keys; this chart contains no Secret values or credential overrides.

## One-time adoption and verification

From the repository root:

```bash
./k8Deployment/kubernetes/scripts/releases/scaling-auth-release.rb plan
./k8Deployment/kubernetes/scripts/releases/scaling-auth-release.rb adopt
./k8Deployment/kubernetes/scripts/releases/scaling-auth-release.rb verify
```

`plan` checks the pinned KEDA release, lints and renders this chart, compares
each rendered object with its [source manifest](../keda/) and live spec,
submits an API-server dry run, and verifies the two Secret **names**. It also
checks that all three dependent `ScaledObject`s are Ready and still reference
their intended authentication and worker Deployment, with a correctly owned
generated HPA. Zero worker replicas are normal while the queues and due work
are empty.

`adopt` repeats that gate, then uses Helm's explicit ownership takeover for
these two objects only. It checks that their UIDs, spec generations, and KEDA
finalizers remain stable, as do the dependent ScaledObject, HPA, and worker
Deployment UIDs. `verify` checks Helm's stored manifest and those same live
dependencies without changing the cluster. A failed takeover must be
inspected in place; uninstalling an adopted release could delete an
authentication object still used by live scalers.

On 2026-09-27, release `clouddsp-scaling-auth` reached revision 1. Both
authentication UIDs and spec generations, the three Ready scalers, their
generated HPA UIDs, and the three worker Deployment UIDs were preserved.
This metadata-only adoption did not enqueue work or start a worker Pod.
Each worker's later release will adopt its Deployment and `ScaledObject`
together, then run that worker's smoke test.
