# KEDA Helm release

This directory is the reproducible description of CloudDSP's local KEDA
platform dependency. It is deliberately separate from worker manifests:
installing KEDA provides its controller Pods and custom resource definitions,
but it cannot scale anything until a later task adds a `ScaledObject` for a
specific worker Deployment.

## What each file owns

- [`release.lock.yaml`](release.lock.yaml) pins the official chart repository,
  chart version, Helm release name, and namespace. It is a human- and
  script-readable lock record, not a manifest to apply with `kubectl`.
- [`values.yaml`](values.yaml) is the CloudDSP local profile. It scopes KEDA
  to `clouddsp-app`, pins its component tags, establishes local resource
  bounds, and documents the intentional secret-access choice needed for later
  RabbitMQ trigger authentication.
- [`../../scripts/install-keda.sh`](../../scripts/install-keda.sh) is the one
  non-interactive install/upgrade entry point. It uses the explicit k3d
  context, reads both files, waits for readiness, and verifies the CRDs.
- [`../../scripts/keda-release-stage.rb`](../../scripts/keda-release-stage.rb)
  guards fresh bootstrap against an existing release or leftover KEDA
  resources and verifies the exact deployed chart, values, and controllers.

The installed KEDA components run as normal Kubernetes Pods in the `keda`
namespace. Helm is only the host-side client that renders and submits the
versioned chart; it is not a fourth KEDA container and does not remain running
after the command exits.

## Reconcile or verify the platform

Run the versioned installer from the repository root:

```bash
ruby ./k8Deployment/kubernetes/scripts/keda-release-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/keda-release-stage.rb install
ruby ./k8Deployment/kubernetes/scripts/keda-release-stage.rb verify
```

Use the Ruby `install` mode during one-command fresh bootstrap; it refuses a
pre-existing KEDA release, namespace, CRD, or metrics API. The standalone
shell installer is an alternative for explicit reconciliation:

```bash
./k8Deployment/kubernetes/scripts/install-keda.sh
```

It targets `k3d-clouddsp-local` explicitly. `helm upgrade --install`
makes repeated runs intentional reconciliation: it installs the release when
absent and upgrades it only to the exact version in `release.lock.yaml` when
present. It does not create a CloudDSP worker ScaledObject, change a worker's
replica count, or enqueue any processing work.

Useful read-only checks are:

```bash
helm status keda --namespace keda
kubectl --context k3d-clouddsp-local get pods --namespace keda
kubectl --context k3d-clouddsp-local get scaledobjects --namespace clouddsp-app
kubectl --context k3d-clouddsp-local get hpa --namespace clouddsp-app
```

The final two commands should initially report no resources. A KEDA
`ScaledObject` will later create and manage an ordinary Kubernetes Horizontal
Pod Autoscaler (HPA) for exactly one existing processing Deployment.

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
