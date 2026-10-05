# KEDA Helm release

This directory is the reproducible description of CloudDSP's local KEDA
platform dependency. It is deliberately separate from worker manifests:
installing KEDA provides its controller Pods and custom resource definitions,
but scaling policies belong to the separate ADTOF, Basic Pitch, and Demucs
worker charts. Each worker has a KEDA-owned HPA and defaults to zero idle Pods.

## What each file owns

- [`release.lock.yaml`](release.lock.yaml) pins the official chart repository,
  chart version, Helm release name, and namespace. It is a human- and
  script-readable lock record, not a manifest to apply with `kubectl`.
- [`values.yaml`](values.yaml) is the CloudDSP local profile. It scopes KEDA
  to `clouddsp-app`, pins its component tags, establishes local resource
  bounds, and documents the intentional secret-access choice needed for later
  RabbitMQ trigger authentication.
- [`../../scripts/releases/install-keda.sh`](../../scripts/releases/install-keda.sh) is the one
  pre-Flux native install/upgrade entry point. It uses the explicit k3d
  context, reads both files, waits for readiness, and verifies the CRDs.
- [`../../scripts/releases/keda-release-stage.rb`](../../scripts/releases/keda-release-stage.rb)
  guards fresh bootstrap against an existing release or leftover KEDA
  resources and verifies the exact deployed chart, values, and controllers.
  After Flux adoption it additionally validates the active upstream source,
  artifact, and ownership record. Both entrypoints reject native writes while
  the KEDA HelmRelease exists, including unhealthy states and API errors.

The installed KEDA components run as normal Kubernetes Pods in the `keda`
namespace. Helm is only the host-side client that renders and submits the
versioned chart; it is not a fourth KEDA container and does not remain running
after the command exits.

## Reconcile or verify the platform

Run the versioned installer from the repository root:

```bash
ruby ./k8Deployment/kubernetes/scripts/releases/keda-release-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/releases/keda-release-stage.rb install
ruby ./k8Deployment/kubernetes/scripts/releases/keda-release-stage.rb verify
```

Use the Ruby `install` mode during one-command fresh bootstrap; it refuses a
pre-existing KEDA release, namespace, CRD, or metrics API. The standalone
shell installer is an alternative for explicit reconciliation:

```bash
./k8Deployment/kubernetes/scripts/releases/install-keda.sh
```

It targets `k3d-clouddsp-local` explicitly. `helm upgrade --install`
makes repeated runs intentional reconciliation: it installs the release when
absent and upgrades it only to the exact version in `release.lock.yaml` when
present. It does not create a CloudDSP worker ScaledObject, change a worker's
replica count, or enqueue any processing work.

Useful read-only checks are:

```bash
helm --kube-context k3d-clouddsp-local status keda --namespace keda
kubectl --context k3d-clouddsp-local get pods --namespace keda
kubectl --context k3d-clouddsp-local get scaledobjects --namespace clouddsp-app
kubectl --context k3d-clouddsp-local get hpa --namespace clouddsp-app
```

The final two commands list the three current worker scalers and their generated
Horizontal Pod Autoscalers. Each ScaledObject targets exactly one existing
worker Deployment; installing KEDA alone never creates a worker or a policy.

## Optional Flux delivery

The [KEDA handoff](../../gitops/keda.md) selects `flux-system/keda`, retaining
native release/storage in `keda` and the official upstream chart at exact
version 2.20.2. Under Flux, publish changes to its versioned HelmRelease on
`codex/flux-clouddsp-local`; use the Ruby `verify` command above for checks.
Native install/upgrade commands are reserved for clusters without that record.

Flux's inline values follow one exact transformation of this native profile:
omit the duplicate `additionalLabels.app.kubernetes.io/part-of` input, then
restore its existing effective label with explicit postrenderer patches. This
avoids invalid duplicate YAML keys while preserving all resource specs and Pod
templates. The native values file remains intact. Update both public mappings
together, retaining this documented transformation.

The handoff retains six exact CRDs with Helm resource-policy annotations and
denies delivery CRD deletion. Narrow runtime CA exceptions preserve automatic
TLS rotation. Its separate platform identity needs named cluster RBAC and
bind/escalate authority, while application credentials, scaling policies, and
worker replicas retain their own owners. Shared scaling-auth depends on KEDA
readiness; all three workers depend on shared authentication.

## RabbitMQ observation prerequisite

Before the first RabbitMQ-based `ScaledObject`, provision the dedicated KEDA
observer identity with the separate, prepared
[`rabbitmq-keda-scaler-bootstrap Job`](../../services/rabbitmq/rabbitmq-keda-scaler-bootstrap-job.yaml).
It uses an ignored temporary data-namespace credential to create a read-only
RabbitMQ `monitoring` user with no AMQP resource permissions. Its matching
ignored app-namespace Secret template lives beside this README. The
[`TriggerAuthentication`](keda-rabbitmq-scaler-trigger-authentication.yaml)
maps only that Secret's username/password into the three current KEDA
RabbitMQ scalers. It does not declare a queue or create an HPA. The resource
is now owned by the separate
[`scaling-auth` release](../scaling-auth/README.md); each worker `ScaledObject`
retains its independent HTTP queue-depth policy.

## Private management-network prerequisite

Demucs and Basic Pitch also use a read-only PostgreSQL task-count trigger.
Unlike the RabbitMQ metric, each stage's query remains active after that
worker durably claims and acknowledges a request, and can wake a worker for
a due database retry. The shared observer role can read only task stage,
status, and due time; each ScaledObject filters its own stage. Its Secret and
role retain separate bootstrap/runtime lifecycles. The corresponding
TriggerAuthentication is owned by the `scaling-auth` release; KEDA never
receives a worker's database credentials.

Each RabbitMQ HTTP scaler will use
`clouddsp-rabbitmq-management.clouddsp-data.svc:15672`, the dedicated private
ClusterIP Service—not the AMQP Service, a StatefulSet Pod name, an Ingress, or
a host port. The coupled
[`RabbitMQ ingress NetworkPolicy`](../../services/rabbitmq/rabbitmq-ingress-network-policy.yaml)
selects the broker endpoint Pod and permits port 15672 only from the KEDA
operator and narrowly labelled, temporary broker bootstrap Jobs. It also keeps
the reviewed AMQP data-plane clients and future RabbitMQ peer ports working.
Applying a ClusterIP alone would not restrict another Pod, so apply and
live-test both resources together in the later explicit network-policy task.

## First worker policy: ADTOF

The prepared
[`ADTOF ScaledObject`](../../services/adtof/adtof-scaledobject.yaml) is the
first consumer-stage policy. It observes only
`clouddsp.adtof.requests` on the `/clouddsp` vhost over the private management
Service, references the namespaced observer TriggerAuthentication, and scales
the existing long-running ADTOF Deployment from zero to two Pods. It is not an
AMQP consumer and it creates no worker message, Secret, or queue. When applied,
KEDA creates the standard HPA; a separate live task must observe its idle,
active, and scale-down behavior before another worker receives a policy.
