# Local cluster scripts

These non-interactive scripts manage only the versioned local k3d profile
declared in [`../cluster/k3d.yaml`](../cluster/k3d.yaml). Run the commands from
the repository root, or change into this directory first; each script resolves
its own location and does not depend on the current working directory.

## Prerequisites

Start Docker Desktop, then confirm that the local command-line tools are
available:

```bash
docker info
k3d version
kubectl version --client
```

`k3d` uses Docker to create the K3s server, agent, load-balancer, and local
registry containers. `kubectl` is the Kubernetes client used by the status
portion of `cluster.sh`; it connects through the `k3d-clouddsp-local` context
created by k3d.

## Create the cluster

```bash
./k8Deployment/kubernetes/scripts/cluster.sh create
```

`create` is also the default action, so this equivalent command is available:

```bash
./k8Deployment/kubernetes/scripts/cluster.sh
```

The script validates Docker, k3d, kubectl, and the cluster configuration first.
It creates the `clouddsp-local` cluster only when it is absent, then lists its
nodes and K3s system Pods. If the cluster already exists, it leaves it intact;
this protects persistent development data added in later phases.

k3d adds the `k3d-clouddsp-local` context to the standard kubeconfig and, by
default, switches the current context to it. Use the context explicitly when a
command must be unambiguous:

```bash
kubectl --context k3d-clouddsp-local get nodes
kubectl --context k3d-clouddsp-local get pods --all-namespaces
```

## Inspect cluster status

```bash
./k8Deployment/kubernetes/scripts/cluster.sh status
```

This is read-only. It shows the three Kubernetes nodes and the Pods in every
namespace. The `default` namespace remains empty until a CloudDSP workload is
deployed; K3s platform Pods such as CoreDNS and Traefik run in `kube-system`.

## Install or reconcile KEDA

```bash
./k8Deployment/kubernetes/scripts/install-keda.sh
```

This is the versioned, non-interactive Helm entry point for the KEDA event
autoscaler. It reads the pinned official chart release and local values from
[`../helm/keda/`](../helm/keda/), targets only the
`k3d-clouddsp-local` context, and waits for KEDA's operator, metrics API
server, admission webhook, and custom resource definitions to become ready.
The three KEDA controller Pods run inside the `keda` namespace; Helm itself is
only the short-lived Mac command that submits their manifests.

The script creates or reconciles the KEDA platform dependency only. It does
not create a `ScaledObject`, resize a processing worker, read a RabbitMQ
credential, or enqueue audio work. Those three independent worker policies
remain later, separately reviewed tasks.

## Verify the local registry

Apply the namespace manifest first, then run the end-to-end registry smoke
test:

```bash
kubectl --context k3d-clouddsp-local apply \
  --filename k8Deployment/kubernetes/cluster/namespaces.yaml

./k8Deployment/kubernetes/scripts/verify-registry.sh
```

The script pulls the exact `busybox:1.37.0` release for the Mac's active
container architecture, tags and pushes it as
`clouddsp-registry.localhost:5001/registry-smoke:1.37.0`, then applies the
versioned `registry-smoke` Job in `clouddsp-app`. The Job uses
`imagePullPolicy: Always`, so K3s must query the local registry on each run.
The script prints the Job's Pod pull events and logs, then leaves the completed
Job and Pod available for inspection. Kubernetes's TTL controller removes them
about five minutes after completion. A later test run deletes only that prior
smoke Job before it creates a fresh one. The harmless test image remains in the
dedicated registry for future checks and is removed when the registry itself is
cleaned up.

## Build the local frontend image

```bash
./k8Deployment/kubernetes/scripts/build-frontend-image.sh
```

This uses the local frontend's two-stage Dockerfile: the pinned official Node
image builds Vite assets, then the pinned official NGINX image serves only those
assets as a non-root process on container port 8080. The script reads the
public Keycloak, Job API, and MinIO browser settings from the ignored
`services/frontend/app/.env.production`, pushes the arm64 image to
`clouddsp-registry.localhost:5001/frontend`, and prints its immutable registry
digest plus Docker's local uncompressed size. Vite generates a CSP meta tag and
matching NGINX response-header include, allowing only the explicit configured
MinIO origin for direct presigned POSTs. It does not deploy a Pod; that is the
following Kubernetes delivery task.

## Build the local Job API image

```bash
./k8Deployment/kubernetes/scripts/build-job-api-image.sh
```

This builds the digest-pinned Python 3.12 Job API recipe for `linux/arm64`,
installs its fully hashed FastAPI/Uvicorn/Psycopg/PyJWT/Boto3 dependencies, and
runs the isolated JWT, job-history, direct-upload contract, PostgreSQL helper,
route-composition, and presigned-POST unit tests before it pushes the result to
`clouddsp-registry.localhost:5001/job-api`. It prints the immutable registry
digest and Docker's local uncompressed size. The runtime image contains source
for `/healthz`, database-aware `/readyz`, token validation, `GET /jobs`, and
`POST /jobs`, but no test files, credentials, Pod, Service, or Ingress.

## Build the MinIO-to-RabbitMQ smoke-client image

```bash
./k8Deployment/kubernetes/scripts/build-minio-source-intake-smoke-client-image.sh
```

This builds the small, purpose-built AMQP client used only by the later native
MinIO-notification smoke Job. It has a pinned Python base and a hash-locked
Pika dependency, targets the local ARM64 k3d nodes, pushes to the dedicated
registry, and prints the immutable digest that must be copied into
`images.lock.yaml`. It does not create a test Job, upload an object, read a
RabbitMQ queue, or change MinIO configuration.

## Build the upload-intake worker image

When a previously acknowledged source event left one retained upload pending,
use the versioned one-job recovery command *after* rolling out the corrected
upload-intake image. It accepts only a canonical job UUID, uses the running
Pod's restricted identities, and reuses the normal HeadObject plus atomic
PostgreSQL source/outbox transition:

```bash
./k8Deployment/kubernetes/scripts/reconcile-one-upload-intake-job.sh JOB_UUID
```

```bash
./k8Deployment/kubernetes/scripts/build-upload-intake-image.sh
```

This builds the local long-running upload-intake worker for `linux/arm64` from
the pinned official Python 3.12 slim base. The Dockerfile installs the fully
hash-locked Boto3, Psycopg, and Pika dependencies, then runs the parser,
transaction, MinIO-HeadObject, manual-ack, graceful-shutdown, and reconnect
unit suite while building. Its final non-root image contains only application
source and validated runtime packages; it exposes no HTTP port and contains no
cluster credentials. The script pushes the image to the dedicated local
registry and prints the immutable digest and local Docker size. It does not
create a Pod, Deployment, Service, Ingress, database change, MinIO object, or
RabbitMQ message.

## Build the outbox dispatcher image

```bash
./k8Deployment/kubernetes/scripts/build-dispatcher-image.sh
```

This builds the internal PostgreSQL-outbox dispatcher for `linux/arm64` from
the pinned official Python 3.12 slim base. Its two-stage Dockerfile installs
the fully hashed Pika/Psycopg dependencies and runs the Demucs-compatible and
generic outbox lease, route-selection, publisher-confirmation,
database-transaction, one-attempt composition, and graceful-shutdown tests
before it copies only validated source and runtime packages into a non-root
final image. The image deliberately keeps the Demucs-only runtime as its
default entrypoint; a later generic Deployment must select
`app.dispatcher_generic_runtime` explicitly. The dispatcher accepts no inbound
HTTP traffic and therefore exposes no port. The script pushes the image to the
dedicated local registry and prints the immutable digest and local Docker size;
it does not create or update a Deployment, Pod, Service, Ingress, database row,
or RabbitMQ message.

## Build the dispatcher smoke-client image

```bash
./k8Deployment/kubernetes/scripts/build-dispatcher-smoke-client-image.sh
```

This builds the disposable normal-upload smoke client for `linux/arm64` from
the pinned Python 3.12 runtime. Its validation stage installs hash-locked
Boto3, Psycopg, and Pika packages and runs the isolated orchestrator/verifier
unit tests. The non-root final image contains only the two smoke modules and
their runtime dependencies, then the script pushes it to the dedicated local
registry and prints an immutable digest and local image size. It does not
create a Kubernetes Job, Keycloak identity, MinIO object, RabbitMQ delivery, or
PostgreSQL record.

## Build the generic dispatcher Basic Pitch smoke-client image

```bash
./k8Deployment/kubernetes/scripts/build-generic-dispatcher-basic-pitch-smoke-client-image.sh
```

This builds the separate, restricted v004 Basic Pitch routing verifier for
`linux/arm64`. Its validation stage installs only hash-locked Psycopg/Pika
dependencies and runs its 16 isolated tests. The non-root runtime image can
call only its three PostgreSQL smoke functions and read/ack its exact Basic
Pitch queue once a later Job supplies the already-applied Secrets. The script
pushes it to the local registry and prints the immutable digest that is
recorded in `images.lock.yaml`; it does not create a Job, synthetic event,
RabbitMQ delivery, or other cluster workload.

## Build the Basic Pitch worker smoke-client image

```bash
./k8Deployment/kubernetes/scripts/build-basic-pitch-worker-smoke-client-image.sh
```

This builds the separate end-to-end Basic Pitch worker verifier for
`linux/arm64`. Its validation stage installs only the hash-locked Boto3 and
Psycopg dependency closure and runs eight isolated tests. The non-root runtime
can call only its fixed PostgreSQL functions and access only its two reserved
MinIO objects once a later Job supplies the already-applied restricted Secrets.
The script pushes the image to the local registry and prints its immutable
digest; it does not create a Job, synthetic event, RabbitMQ message, object, or
other cluster workload.

## Build the ADTOF worker smoke-client image

```bash
./k8Deployment/kubernetes/scripts/build-adtof-worker-smoke-client-image.sh
```

This rebuilds the separate end-to-end ADTOF worker verifier for `linux/arm64`
from its digest-pinned Python 3.12 base. The Docker validation stage installs
only the full hash-pinned Boto3/Psycopg closure and runs the 63 isolated client
tests. The final non-root image can call only its three fixed PostgreSQL smoke
functions and access only its three reserved MinIO object keys after a future
Job provides its restricted Secrets. The script checks Docker, the exact k3d
registry, and all narrow build inputs before it builds and pushes. It then
prints the immutable registry digest and Docker's uncompressed local size; copy
that digest into `images.lock.yaml` before any future Job manifest refers to a
new rebuild. It does not create a Job, synthetic event, RabbitMQ message,
object, or other cluster workload.

## Build the Demucs worker stage smoke-client image

```bash
./k8Deployment/kubernetes/scripts/build-demucs-worker-smoke-client-image.sh
```

This validates the narrow Python client, builds the ARM64 image, pushes it to
the local registry, prints the immutable digest, and verifies that the pushed
digest resolves from the registry.  It deliberately does **not** apply any
Kubernetes resource or create test data.  After a reviewed rebuild, copy the
printed digest and measured image size into `images.lock.yaml`, then update
the smoke Job image reference before applying the test manifests.

## Build the Demucs worker image

```bash
./k8Deployment/kubernetes/scripts/build-demucs-image.sh
```

This validates the entire Linux/ARM64 Demucs worker source and its locked model
artifacts inside the Docker validation stage, then runs a real fixed two-stem
inference through the local ARM64 CPU launcher before pushing the resulting
image to the dedicated local registry. The launcher disables Torch MKLDNN
before Demucs imports because the default local model path reproduced a SIGILL;
the check requires both generated stem files, so a successful import alone is
not treated as proof. The script prints the immutable digest and local layer
size. It creates or updates an OCI image only: it does not apply a Deployment,
Secret, Job, Service, database migration, MinIO object, or RabbitMQ message.
After a reviewed build, record the reported digest in `images.lock.yaml` and
copy the same immutable reference into `services/demucs/demucs-deployment.yaml`.

## Build the Basic Pitch worker image

```bash
./k8Deployment/kubernetes/scripts/build-basic-pitch-image.sh
```

This builds the CPU-only Basic Pitch worker for `linux/arm64` from its separate
pinned Python 3.11 base image. The two-stage Dockerfile installs the complete
hash-locked inference/client dependency closure, runs the worker unit suite,
and proves that the fixed `basic-pitch` command and bundled TensorFlow Lite
model are present before the final non-root image is published to
`clouddsp-registry.localhost:5001/basic-pitch`. The script prints the immutable
registry digest and Docker's local uncompressed size. It does not create or
update a Deployment, Pod, Service, Ingress, Secret, database row, MinIO object,
or RabbitMQ message. Its published digest is recorded separately in
`images.lock.yaml` before a future workload manifest may refer to it.

## Build the ADTOF worker image

```bash
./k8Deployment/kubernetes/scripts/build-adtof-image.sh
```

This builds the CPU-only ADTOF drum-to-MIDI worker for `linux/arm64` from its
dedicated digest-pinned Python 3.11 base. Its throwaway validation stage runs
the complete worker test suite, including Kubernetes `fsGroup` scratch-path and
fixed `/app` CPU-child-import working-directory checks, and confirms the pinned
ADTOF model package and bundled weights are present. The script pushes only the image to
`clouddsp-registry.localhost:5001/adtof` and prints the immutable digest and
Docker's uncompressed local image size. Copy that digest into
`images.lock.yaml`, then explicitly update and apply the ADTOF Deployment; the
script never changes cluster workloads or durable processing data itself.

## Smoke-test the local Job API image

```bash
./k8Deployment/kubernetes/scripts/verify-job-api-local-image.sh
```

This starts the immutable Job API image as a short-lived Docker container with
no network and no database credential. It verifies that `/healthz` returns 200
with the expected image version and that `/readyz` correctly returns a 503
`database_configuration` response. It uses `docker exec` inside the
network-isolated container, so it opens no Mac port, then removes the container
at the end. This is an image/process test; it does not deploy Kubernetes
resources or prove in-cluster PostgreSQL connectivity.

## Verify HTTP routing on port 8080

```bash
./k8Deployment/kubernetes/scripts/verify-http-routing.sh
```

This test pushes a separate BusyBox HTTP-server image, deploys a `Deployment`,
`ClusterIP` Service, and Traefik `Ingress`, then checks the exact response at:

```text
http://routing-smoke.localhost:8080/
```

The resources remain in `clouddsp-app` for inspection. They are deliberately
isolated behind `routing-smoke.localhost`, so they do not match a future
CloudDSP hostname. Remove only this routing test when finished:

```bash
kubectl --context k3d-clouddsp-local delete \
  --filename k8Deployment/kubernetes/tests/routing-smoke/http-routing-smoke.yaml
```

## Delete the local cluster

```bash
./k8Deployment/kubernetes/scripts/cleanup-cluster.sh --confirm
```

This is destructive. It removes exactly the `clouddsp-local` k3d cluster and
the `clouddsp-registry.localhost` image registry. It also destroys workloads,
the K3s node containers, and any future PVC-backed local development data or
images stored in that dedicated registry. It does not use `--all` and does not
delete other k3d clusters, Docker containers, images, networks, or registries.

Use `--help` with any script to print its supported command form without
changing local resources:

```bash
./k8Deployment/kubernetes/scripts/cluster.sh --help
./k8Deployment/kubernetes/scripts/cleanup-cluster.sh --help
./k8Deployment/kubernetes/scripts/verify-registry.sh --help
./k8Deployment/kubernetes/scripts/verify-http-routing.sh --help
```
