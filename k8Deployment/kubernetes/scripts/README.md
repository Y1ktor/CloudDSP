# Local cluster scripts

These non-interactive scripts manage only the versioned local k3d profile
declared in [`../cluster/k3d.yaml`](../cluster/k3d.yaml). Run the commands from
the repository root, or change into this directory first; each script resolves
its own location and does not depend on the current working directory.

## Read-only deployment plan

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh plan
```

This is the first stage of the proposed
[deployment orchestrator](../deployment-orchestration-plan.md). It reads the
versioned workload manifests and lock files, then queries only the explicit
`k3d-clouddsp-local` context. It checks namespace and Helm ownership,
StatefulSet selectors/claim templates and bound PVCs, immutable image
references, and the *names* of runtime Secrets. It checks for matching ignored
local Secret filenames and committed example contracts without printing Secret
values. The command makes no cluster changes and returns nonzero when a
blocking inconsistency is found; warnings identify work needed before a fresh
bootstrap. Ruby's standard YAML/JSON libraries are required on the host.

This general command reports resource ownership and source/live identity but
does not render a chart diff. The separate
[Mailpit](mailpit-release.rb), [Keycloak](keycloak-release.rb),
[PostgreSQL](postgresql-release.rb),
[MinIO](minio-release.rb),
[RabbitMQ](rabbitmq-release.rb),
[shared KEDA scaling authentication](scaling-auth-release.rb),
[Basic Pitch worker](basic-pitch-release.rb),
[ADTOF worker](adtof-release.rb),
[Demucs worker](demucs-release.rb),
[frontend](frontend-release.rb),
[legacy dispatcher](dispatcher-release.rb),
[generic dispatcher](generic-dispatcher-release.rb),
[Job API](job-api-release.rb), and
[upload-intake](upload-intake-release.rb) release
scripts perform component-specific render and live-spec comparisons. The
general command does not
install, adopt, migrate, bootstrap, or clean up anything. The
[resource ownership map](../resource-ownership-map.md) records the full
versioned/live snapshot and proposed boundaries.

## Mailpit Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/mailpit-release.rb plan
./k8Deployment/kubernetes/scripts/mailpit-release.rb adopt
./k8Deployment/kubernetes/scripts/mailpit-release.rb verify
./k8Deployment/kubernetes/scripts/mailpit-release.rb smoke
```

The [Mailpit chart](../helm/mailpit/README.md) owns only the existing
Deployment, two Services, and browser Ingress in `clouddsp-data`. Run `plan`
before its one-time `adopt` operation. Once it is adopted, use `verify` for
read-only ownership, readiness, and route checks; `smoke` creates only the
versioned disposable SMTP capture Job and removes it after success. The
script stops on unknown ownership or any rendered/source/live spec drift.
The raw manifests under `services/mailpit/` are now an adoption baseline and
must not be reapplied to the Helm-owned objects.

## PostgreSQL protected Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/postgresql-release.rb plan
./k8Deployment/kubernetes/scripts/postgresql-release.rb adopt
./k8Deployment/kubernetes/scripts/postgresql-release.rb verify
./k8Deployment/kubernetes/scripts/postgresql-release.rb smoke
```

The [PostgreSQL chart](../helm/postgresql/README.md) owns the existing
StatefulSet, normal ClusterIP Service, and governing headless Service. Its
script verifies source/render/live equality and the bound generated PVC.
Before `adopt` can call Helm, the versioned
[`backup and restore rehearsal`](postgresql-backup-and-restore-test.sh)
captures all databases and roles into an ignored owner-only file, restores
them in an isolated Docker container with no network, and compares counts.
Adoption preserved resource and Pod UIDs, Service IPs, and PVC/PV identity.
`smoke` ran the versioned read/write Job through the ordinary Service and
removed its disposable table and Job. The generated PVC, database contents,
Secret, migrations, and bootstrap Jobs remain outside Helm ownership.

## MinIO protected Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/minio-release.rb plan
./k8Deployment/kubernetes/scripts/minio-release.rb adopt
./k8Deployment/kubernetes/scripts/minio-release.rb verify
./k8Deployment/kubernetes/scripts/minio-release.rb smoke
```

The [MinIO chart](../helm/minio/README.md) owns its existing StatefulSet,
normal and headless Services, and S3 Ingress. Its script checks exact
source/render/live spec and bound-PVC parity before adoption. The automatic
[`backup and restore rehearsal`](minio-backup-and-restore-test.py) briefly
stops the MinIO Pod, archives its node-local PVC, restarts the original Pod,
and compares an isolated restored server's S3 inventory and object bytes.
The owner-only archive remains under ignored `k8Deployment/.local/backups/`.
The successful Helm takeover preserved the four resource UIDs, post-backup
Pod UID, Service IPs, and bound PVC/PV. `smoke` passed the S3 API
create/read/delete test through the normal Service and removed its Job. The
separate restricted Job API identity smoke passed and its Job was removed.
Bucket data, IAM, Secrets, and bootstrap Jobs remain outside this release.

## RabbitMQ protected Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/rabbitmq-release.rb plan
./k8Deployment/kubernetes/scripts/rabbitmq-release.rb adopt
./k8Deployment/kubernetes/scripts/rabbitmq-release.rb verify
./k8Deployment/kubernetes/scripts/rabbitmq-release.rb smoke
```

The [RabbitMQ chart](../helm/rabbitmq/README.md) owns the existing broker
StatefulSet, AMQP, headless and management Services, and its ingress
NetworkPolicy. Its automatic
[`backup and restore rehearsal`](rabbitmq-backup-and-restore-test.py) checks
that no messages are unacknowledged, briefly stops the broker, archives its
bound PVC, restarts the original Pod, then compares full definitions and queue
depths on a disposable no-network restore. The owner-only archive remains
under ignored `k8Deployment/.local/backups/`. The successful takeover
preserved five resource UIDs, post-backup Pod UID, Service IPs, and bound
PVC/PV identity. `smoke` passed a real AMQP publish/consume/acknowledge round
trip through the normal Service and removed its Job. Broker state, runtime
Secrets, bootstrap Jobs, and KEDA resources remain outside this release.

## Shared KEDA scaling authentication adoption

```bash
./k8Deployment/kubernetes/scripts/scaling-auth-release.rb plan
./k8Deployment/kubernetes/scripts/scaling-auth-release.rb adopt
./k8Deployment/kubernetes/scripts/scaling-auth-release.rb verify
```

The [scaling-auth chart](../helm/scaling-auth/README.md) owns only the two
existing app-namespace `TriggerAuthentication` resources. Its read-only plan
requires exact source/render/live spec parity, the pinned KEDA release, both
referenced Secret names, three Ready worker `ScaledObject`s, and their
correctly owned HPAs. Adoption preserved both authentication UIDs and spec
generations plus all dependent scaler, HPA, and worker Deployment UIDs.
Secret values, worker scale decisions, and the KEDA controller remain outside
this release. Each worker adoption and processing smoke is a later task.

## Basic Pitch worker Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb plan
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb adopt
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb verify
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb upgrade-scaling
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb upgrade-numba
```

The [Basic Pitch chart](../helm/basic-pitch/README.md) owns the existing
Deployment and RabbitMQ ScaledObject. The read-only preflight compared both
source manifests with the chart and live specs. The one-time takeover retained
both resource UIDs, the ScaledObject spec generation, generated HPA UID, and
zero idle replicas. `verify` checks Helm's stored manifest, KEDA Ready state,
shared authentication references, HPA target/ownership, and zero Pods. The first fixed one-request smoke exposed an obsolete parent-status
assertion. After guarded cleanup, a versioned restricted-function update and
a digest-pinned client rebuild, the rerun passed: durable publication,
first-attempt task success, verified MIDI bytes/provenance, and scoped cleanup.
The test fixture's parent Job is intentionally incomplete; only its exact
expected finalization error is accepted.

The Demucs two-stem smoke later exposed Basic Pitch's early AMQP ACK: the
queue became empty while its model task still held a PostgreSQL lease, and
KEDA scaled the worker away. Chart 0.1.2 added the existing restricted
PostgreSQL task-count trigger and a six-minute scale-in stabilization window.
`upgrade-scaling` verifies the queue-only v0.1.0 baseline and upgrades it to
the current policy. A cluster already at intermediate v0.1.1 uses
`upgrade-stabilization`. A later Demucs trial exposed a Numba illegal
instruction in the Basic Pitch tempo step on the ARM64 k3d node. Chart 0.1.3
sets `NUMBA_CPU_NAME=generic`, validated against that exact WAV in the running
worker image. The guarded `upgrade-numba` path upgrades an installed v0.1.2
release. All paths keep the Deployment, ScaledObject, and generated HPA UIDs
stable.

## ADTOF worker Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/adtof-release.rb plan
./k8Deployment/kubernetes/scripts/adtof-release.rb adopt
./k8Deployment/kubernetes/scripts/adtof-release.rb verify
./k8Deployment/kubernetes/scripts/adtof-release.rb smoke
```

The [ADTOF chart](../helm/adtof/README.md) owns only the existing worker
Deployment and RabbitMQ ScaledObject. Its read-only preflight checked image
lock, source/render/live spec parity, KEDA readiness, HPA ownership, and the
zero-Pod idle state. Helm takeover preserved both resource UIDs, scaler spec
generation, generated HPA UID, and zero replicas. The fixed one-drum worker
smoke passed after its test-only finalizer fixture update: KEDA activated one
Pod, the real dispatcher and worker completed the request, the client verified
MIDI and tempo evidence, and scoped cleanup removed its Job and fixed data.

## Demucs worker Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/demucs-release.rb plan
./k8Deployment/kubernetes/scripts/demucs-release.rb adopt
./k8Deployment/kubernetes/scripts/demucs-release.rb verify
./k8Deployment/kubernetes/scripts/demucs-release.rb smoke
```

The [Demucs chart](../helm/demucs/README.md) owns its existing worker
Deployment and dual-trigger ScaledObject. The read-only preflight checked the
image lock, exact source/render/live specs, both RabbitMQ and PostgreSQL
authentication references, scaler readiness, HPA ownership, and zero idle
Pods. Helm takeover preserved both object UIDs, the scaler generation, the
KEDA-owned HPA UID, and zero replicas. The fixed two-stem smoke validates the
real dispatcher and Demucs worker, verifies stem bytes and provenance, waits
for both downstream Basic Pitch tasks and the parent Job to complete, and
uses guarded cleanup of only its test data. The first trial reached verified
Demucs completion, then stalled because Basic Pitch scaled away during its
second task; the full smoke remains failed with fixed evidence preserved.

## Keycloak Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/keycloak-release.rb plan
./k8Deployment/kubernetes/scripts/keycloak-release.rb adopt
./k8Deployment/kubernetes/scripts/keycloak-release.rb verify
./k8Deployment/kubernetes/scripts/keycloak-release.rb smoke
```

The [Keycloak chart](../helm/keycloak/README.md) owns the existing identity
Deployment, ClusterIP Service, and browser Ingress in `clouddsp-data`. The
release script checks the image lock and exact source/render/live spec before
its one-time takeover. `verify` checks Helm's stored manifest, the Ready Pod,
and the public OIDC discovery route. `smoke` runs the versioned internal
discovery/issuer Job and deletes only that completed Job. Adoption preserved
the three object UIDs, Service IP, and Pod UID. The separate React PKCE
authorization, Keycloak-to-Mailpit verification-email, and temporary-user
authenticated-read smoke Jobs also passed and were deleted after completion.
PostgreSQL, Secrets, and realm, client, and SMTP bootstrap remain outside this
release.

## Frontend Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/frontend-release.rb plan
./k8Deployment/kubernetes/scripts/frontend-release.rb adopt
./k8Deployment/kubernetes/scripts/frontend-release.rb verify
```

The [frontend chart](../helm/frontend/README.md) owns its existing
Deployment, ClusterIP Service, and Traefik Ingress in `clouddsp-app`.
The shared [`stateless-release.rb`](stateless-release.rb) helper supplies the
same source/render/live comparison and one-time ownership gate as Mailpit.
`verify` also checks the local browser app shell, CSP header, and linked
JavaScript and CSS assets. After adoption, the raw
`services/frontend/` manifests are a retained comparison baseline; use
the Helm chart for subsequent delivery changes.

## Legacy dispatcher Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/dispatcher-release.rb plan
./k8Deployment/kubernetes/scripts/dispatcher-release.rb adopt
./k8Deployment/kubernetes/scripts/dispatcher-release.rb verify
```

The [dispatcher chart](../helm/dispatcher/README.md) owns only the existing
`Deployment/clouddsp-dispatcher` in `clouddsp-app`. Its image is checked
against `images.dispatcher-demucs-only`, distinct from the generic controller's
image lock. The shared release helper compares source, render, and live spec
before the one-time ownership handoff. `verify` checks Helm ownership, stored
manifest, unchanged spec, Ready Pod, and running image digest. There is no
Service or HTTP route for this internal outbox publisher. The separate
normal-path smoke test is required to prove actual message publication; this
adoption does not run that credential-bearing integration test.

## Generic dispatcher Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/generic-dispatcher-release.rb plan
./k8Deployment/kubernetes/scripts/generic-dispatcher-release.rb adopt
./k8Deployment/kubernetes/scripts/generic-dispatcher-release.rb verify
```

The [generic dispatcher chart](../helm/generic-dispatcher/README.md) owns only
`Deployment/clouddsp-generic-dispatcher` in `clouddsp-app`. Its pinned image
and explicit `app.dispatcher_generic_runtime` command are compared with the
source and live Deployment before the one-time Helm ownership transfer.
`verify` checks the stored release manifest, unchanged live spec, Ready Pod,
and running image digest. It has no Service or HTTP route. The separate
Basic Pitch routing smoke test is needed to demonstrate message behavior;
it passed on 2026-09-26 with one controlled event published, acknowledged,
and cleaned up. The disposable Job was removed. This adoption keeps the legacy
dispatcher running at one replica.

## Job API Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/job-api-release.rb plan
./k8Deployment/kubernetes/scripts/job-api-release.rb adopt
./k8Deployment/kubernetes/scripts/job-api-release.rb verify
```

The [Job API chart](../helm/job-api/README.md) owns its existing Deployment,
ClusterIP Service, and same-origin `/auth` and `/jobs` Ingress in
`clouddsp-app`. The release script checks the image lock, source/render/live
spec parity, and API-server schema before one-time adoption. `verify` checks
Helm ownership, stored manifest, ready Pod and running digest, and that both
protected browser paths still reject unauthenticated requests with HTTP 401.
The Service IP, three resource UIDs, and Pod UID were preserved. Database
migrations and runtime Secret contents remain outside Helm.
The versioned authenticated-read smoke Job passed with a temporary Keycloak
user and client, then cleaned up both identities and its disposable Job.

## Upload-intake Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/upload-intake-release.rb plan
./k8Deployment/kubernetes/scripts/upload-intake-release.rb adopt
./k8Deployment/kubernetes/scripts/upload-intake-release.rb verify
```

The [upload-intake chart](../helm/upload-intake/README.md) owns only its
outbound Deployment in `clouddsp-app`. It preserves the Pod's three restricted
PostgreSQL, MinIO, and RabbitMQ Secret references and the reviewed image
digest. The release checker requires source/render/live equality before the
one-time adoption and then verifies the original Deployment and Pod UIDs.
`verify` checks Helm ownership, stored manifest, Ready Pod, and running
digest. The separate [source-to-outbox integration smoke](../tests/source-intake-smoke/README.md)
subsequently passed after its test image was aligned with the Job API lock and
the fixed test Pod received narrowly scoped RabbitMQ management access. Its
runbook pauses and restores both dispatcher releases around the pending-row
assertion.

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
