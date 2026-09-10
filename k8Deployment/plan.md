# CloudDSP local Kubernetes deployment plan

## Scope and boundary

This is an additive, local Kubernetes deployment track. It does not replace,
migrate, or modify the AWS deployment preserved in
[`../cloudDeployment/`](../cloudDeployment/). The CloudFormation templates,
Lambda handlers, Batch entry points, Cognito integration, and cloud deployment
instructions remain authoritative for the cloud deployment.

Read [`../cloudDeployment/AGENTS.md`](../cloudDeployment/AGENTS.md) before
changing product behavior, source limits, job semantics, artifact retention,
browser behavior, or security policy. Kubernetes-specific code must live only
under `k8Deployment/kubernetes/`. Do not edit cloud source merely to make the
local deployment convenient unless the user explicitly requests a coordinated
change.

The local implementation will maintain equivalent product behavior while using
local deployment services. It is a parallel runtime, not a rewrite of the
preserved cloud source tree.

## Local target

Use **k3d** for the default local profile:

- Cluster name: `clouddsp-local`.
- Topology: one k3s server and two CPU agent nodes.
- Ingress: k3s-packaged Traefik.
- Registry: a k3d-managed local registry for CloudDSP images.
- Storage: local-path PersistentVolumeClaims for development-only state.

Use a separate native Linux x86_64 **k3s GPU profile** for real Demucs
throughput. That profile needs NVIDIA drivers, the NVIDIA GPU Operator or
device plugin, GPU-node labels and taints, and `nvidia.com/gpu` resource
requests. A Mac k3d cluster validates application integration and CPU workers;
it is not a validated CUDA/GPU capacity environment.

## Kubernetes workspace layout

```text
k8Deployment/
  AGENTS.md                 # Scoped instructions for this deployment track
  agent.md                  # Short entry point to AGENTS.md
  plan.md                   # This plan
  kubernetes/               # All future Kubernetes project source
    cluster/                # k3d configuration, namespaces, bootstrap files
    helm/                   # CloudDSP chart and local/GPU values
    services/               # K8-specific API, realtime, and worker code
      frontend/              # K8-specific React variant, image recipe, and delivery manifests
      api/
      realtime/
      upload-intake/
      dispatcher/
      workers/
        demucs/
        basic-pitch/
        adtof/
        yt-dlp/
    scripts/                # Non-interactive build, deploy, and teardown
    tests/                  # Unit, integration, and smoke tests
    images.lock.yaml        # Reviewed tags and immutable image digests
```

`cloudDeployment/` and `k8Deployment/` are sibling, independently deployable
trees. Keep their package manifests, Dockerfiles, Helm resources, and runtime
configuration separate.

## Local services

| Component | Kubernetes responsibility | Local implementation |
| --- | --- | --- |
| Frontend | Static React delivery | Node build stage and NGINX Deployment behind Traefik |
| Identity | Local credentials and OIDC | Keycloak with a dedicated PostgreSQL database |
| Test email | Local SMTP capture and browser inbox | Mailpit; never an external mail relay |
| Job API | Authenticated job contracts and snapshots | K8-specific long-running service |
| Realtime | Best-effort WebSocket notifications | K8-specific realtime Deployment |
| Job state | Durable records, UTC quotas, revisions, and outbox | PostgreSQL |
| Artifacts | Private source, stems, MIDI, and tempo files | MinIO with presigned browser access |
| Work queue | Durable stage work, retry, DLQ, and status fanout | RabbitMQ quorum queues |
| Worker scaling | One worker Job per queued unit of work | KEDA `ScaledJob`s |
| GPU scheduling | Demucs resource isolation | Native Linux k3s GPU profile only |

Use locally owned CloudDSP images for all app services and workers. The
preserved Lambda images and Lambda handlers are cloud deployment artifacts;
they must not become Kubernetes entrypoints unchanged.

## Durable local processing path

```text
Browser ── OIDC ──> Keycloak
   │
   ├─ Job API ──> PostgreSQL (job, quota, task lease, outbox)
   └─ presigned POST ──> MinIO uploads/{job_id}/...
                               │
              native ObjectCreated AMQP event / reconciler
                               │
                RabbitMQ clouddsp.source-intake queue
                               │
                    upload-intake Deployment consumer
                               │
                 PostgreSQL source state + durable outbox
                               │
              dispatcher lease + confirmed publish
                               │
                     RabbitMQ demucs.requested
                               │
                    KEDA Demucs Job (GPU profile)
                               │
             MinIO stems + PostgreSQL state/outbox commit
                    ┌──────────┴──────────┐
                    │                     │
           Basic Pitch queue          ADTOF queue
                    │                     │
             KEDA CPU Job         KEDA CPU Job
                    └──────────┬──────────┘
                               │
                   MIDI + durable state/outbox
                               │
                       job.updated fanout
                               │
             realtime WebSocket hint; browser polls Job API
```

1. The local API validates the Keycloak subject, atomically reserves the UTC
   quota, creates the durable job, and returns a size-constrained MinIO
   presigned POST contract.
   Its owner-bound `GET /jobs/{job_id}` companion returns the current durable
   snapshot for browser polling. Before intake/processing exists, that is an
   `upload_pending` record with empty artifact maps—not a claim that MinIO has
   received the object or that Demucs has started.
2. MinIO publishes an S3-compatible source-created notification natively to
   RabbitMQ's dedicated `clouddsp.source-intake` queue. The upload-intake
   Deployment consumes it, re-verifies the object, and idempotently records
   `source_uploaded`. A reconciler verifies pending uploads so a missed event,
   broker outage, or bounded MinIO event queue cannot strand work.
3. The internal dispatcher leases a due `demucs.requested` outbox row, waits
   for RabbitMQ publisher confirmation, then marks that exact lease published.
   A crash in the confirmation/update gap can publish a duplicate, so workers
   must remain idempotent.
4. A Demucs worker claims its stage lease, validates source size, metadata,
   duration, and audio stream, then writes stable stem keys to MinIO.
5. After its artifact and PostgreSQL commit succeed, Demucs writes one outbox
   event per actual stem: drums route to ADTOF and pitched stems route to Basic
   Pitch.
6. KEDA creates independently retryable worker Jobs. Each persists MIDI and
   terminal state before acknowledging its RabbitMQ message.
7. A linked-media request first runs yt-dlp. Its normalized WAV enters the
   same ordinary upload-intake path; it never creates a second Demucs trigger.

### Upload-intake event contract

The MinIO StatefulSet now enables the native AMQP target and its Secret-backed
connection URI. The completed RabbitMQ bootstrap grants MinIO configure and
write permission for only the source exchange. MinIO connects lazily when it
has an event to deliver. The completed root-only
[`minio-source-intake-notification-bootstrap Job`](kubernetes/services/minio/minio-source-intake-notification-bootstrap-job.yaml)
attached the prefix-limited bucket notification rule. The prepared
[`minio-source-intake-rabbitmq-smoke Job`](kubernetes/tests/minio-smoke/minio-source-intake-rabbitmq-smoke-job.yaml)
will test source-created delivery before the long-running consumer exists.

The versioned local [upload-intake contract](kubernetes/services/upload-intake/README.md)
now includes the direct-upload parser, private MinIO `HeadObject` verifier,
restricted PostgreSQL transaction adapter, manual-ack RabbitMQ consumer, and
its digest-pinned ARM64 worker image. MinIO natively publishes S3-compatible
`clouddsp-uploads/uploads/*` object-created notifications to the dedicated
RabbitMQ source-intake queue. The eventual Deployment re-verifies private
storage and performs an idempotent PostgreSQL transition. Delivery is
at-least-once and is never authoritative by itself, so a PostgreSQL-driven
reconciler will use the same verification/transition logic. The applied
upload-intake Deployment currently stops after durable `source_uploaded`
state; it uses the digest-pinned worker image from the local registry, private
Service DNS, three least-privilege runtime Secrets, non-root/read-only security
settings, and one initial consumer replica without a Service or Ingress. The
applied v002 schema migration adds the transactional outbox table, and the
applied permission bootstrap grants upload-intake the six INSERT columns
needed to create a pending Demucs event plus SELECT only on that event's
three-column idempotency key (`job_id`, `stage`, and `stem_name`), which
PostgreSQL requires to evaluate the named `ON CONFLICT` constraint. It cannot
read event payloads or dispatch outbox rows.
The upload-intake source now commits that event atomically with
`source_uploaded`, and its reviewed ARM64 image digest is now rolled out in
the applied Deployment. The prepared source-to-outbox integration smoke Job
will verify the ordinary authenticated upload route, native MinIO notification,
one durable pending Demucs event, and a duplicate RabbitMQ notification before
the separate dispatcher task safely publishes `demucs.requested` to RabbitMQ.

### Demucs dispatch event contract

The versioned [`demucs.requested` dispatcher contract](kubernetes/services/dispatcher/README.md)
now fixes the private RabbitMQ exchange, routing key, queue, JSON body, AMQP
properties, lease/publisher-confirmation order, idempotency boundary, and
versioning rules. The contract document itself creates no topology or workload.
This topology task created the immutable
[`rabbitmq-processing-topology-v001 ConfigMap`](kubernetes/services/rabbitmq/rabbitmq-processing-topology-v001-configmap.yaml).
That applied definition declares the durable Demucs request queue plus its
30-second retry and DLQ delivery paths; it has not changed RabbitMQ by itself.
The prepared administrator-only
[`rabbitmq-processing-topology-bootstrap Job`](kubernetes/services/rabbitmq/rabbitmq-processing-topology-bootstrap-job.yaml)
has imported it during its explicit apply step. A later dispatcher Deployment
then moves due PostgreSQL outbox rows to that route.

The local [`dispatcher outbox lease adapter`](kubernetes/services/dispatcher/app/outbox_lease.py)
now provides unit-tested atomic claim, expired-lease recovery, guarded
publication completion, and bounded known-failure retry scheduling. It has no
database connection or broker client yet. The prepared
[`dispatcher PostgreSQL bootstrap Job`](kubernetes/services/dispatcher/dispatcher-database-bootstrap-job.yaml)
defines its separate least-privilege role: it can read the durable outbox
payload and lease/publish/retry fields, but cannot insert/delete events or
access `jobs`. The runtime database Secret is applied in `clouddsp-app`; the
temporary data-namespace bootstrap Secret was deleted after role provisioning.
The prepared
[`RabbitMQ dispatcher publisher bootstrap Job`](kubernetes/services/rabbitmq/rabbitmq-dispatcher-publisher-bootstrap-job.yaml)
created a no-tag RabbitMQ user that can publish only to the exact processing
exchange, with no configure or consume permission. Its temporary bootstrap
credential Secret was deleted after the Job passed, while the separate
app-namespace runtime Secret is applied and ready for a future Pod.

The dispatcher now owns a small hash-enforced
[`Pika/Psycopg dependency lock`](kubernetes/services/dispatcher/requirements.lock).
Its unit-tested [`AMQP publisher adapter`](kubernetes/services/dispatcher/app/amqp_publisher.py)
now validates the exact durable body, publishes only to the fixed Demucs route
with persistent properties and publisher confirmation, and distinguishes known
retryable broker outcomes from uncertain post-send confirmation loss. It does
not construct a loop, build an image, or create a Deployment. Its private
[`Psycopg transaction adapter`](kubernetes/services/dispatcher/app/postgresql.py)
now supplies the short commit-or-rollback database scope the later runtime will
use before and after—not during—its RabbitMQ publisher-confirmation step.
The unit-tested [`dispatch_once` composition](kubernetes/services/dispatcher/app/dispatch_once.py)
now performs that one claim → confirmed publish → matching completion/retry
attempt. An incompatible durable event is instead terminally marked
`dead_lettered` with a bounded safe category before it can reach RabbitMQ.
The new unit-tested
[`dispatcher supervisor`](kubernetes/services/dispatcher/app/dispatcher_runtime.py)
now repeats that bounded attempt in one long-running process. It drains a
backlog without an artificial per-event pause, sleeps only while the outbox is
idle, waits a longer bounded interval after a safe PostgreSQL-unavailable
category, and cooperatively handles Kubernetes SIGTERM/SIGINT. It deliberately
fails fast on contract/schema/programming errors rather than hiding a
deterministic fault in an infinite retry loop. Its first non-root ARM64
two-stage image has passed all 45 isolated tests and is now locked by immutable
digest under `images.dispatcher` in
[`images.lock.yaml`](kubernetes/images.lock.yaml). No dispatcher Deployment or
live database/broker connection has been created yet. The prepared
[`dispatcher Deployment`](kubernetes/services/dispatcher/dispatcher-deployment.yaml)
uses that digest with the existing restricted PostgreSQL/RabbitMQ Secrets,
private Service DNS, non-root/read-only Pod security, and no Service or Ingress.
It remains unapplied until the next explicit apply task.

The new pure [`post-Demucs dispatcher request contract`](kubernetes/services/dispatcher/app/downstream_outbox_request.py)
prepares the next handoff without changing the current Demucs-only dispatcher.
It accepts only the durable v004 `basic-pitch.requested` and `adtof.requested`
rows, rechecks exact private WAV metadata, and renders persistent AMQP metadata
for `basic-pitch.requested` or `adtof.requested` on the existing direct exchange.
Five standard-library tests cover every allowed non-drum stem, the drums-only
ADTOF route, and malformed identity/payload/routing rejection. No queue,
binding, worker, image, or Deployment consumes those routes yet. The prepared
immutable [`RabbitMQ downstream topology v002`](kubernetes/services/rabbitmq/rabbitmq-processing-topology-v002-downstream-configmap.yaml)
now defines their separate Basic Pitch/ADTOF quorum main, 30-second retry, and
DLQ queues plus bindings to the already-imported v001 exchanges. It retains the
same five-delivery limit and 1,000-message capacity as the Demucs queue. The
ConfigMap is now applied, and the separate
[`v002 topology import Job`](kubernetes/services/rabbitmq/rabbitmq-processing-topology-v002-downstream-bootstrap-job.yaml)
waits for RabbitMQ management readiness, verifies all three v001 exchanges,
imports only the immutable v002 JSON, then verifies the six new queues and
bindings. It has completed successfully, so the downstream topology is live;
no runtime dispatcher or worker has been changed to publish/consume it yet.
The lease adapter now also has a separate pure generic v004 claim method. It
returns a finite `(stage, stem_name, event_type)` triple for Demucs, Basic
Pitch, or ADTOF and retains the same atomic lease/recovery safeguards, but the
existing Demucs-only runtime deliberately continues to use its narrower claim
method. The next composition task must select the matching strict AMQP request
builder before the generic claim path can be wired into a running dispatcher.
That source-only selector now exists in
[`dispatchable_outbox_request.py`](kubernetes/services/dispatcher/app/dispatchable_outbox_request.py).
It accepts only the validated generic lease data model, reuses the exact Demucs
source-request validator or the Basic Pitch/ADTOF stem-request validator, and
returns a fixed publisher-ready AMQP record without importing Pika, reading
credentials, or contacting RabbitMQ. Its five isolated tests cover all three
stage families plus crossed/malformed and untyped inputs. The generic Pika
publication adapter now exists in
[`amqp_publisher.py`](kubernetes/services/dispatcher/app/amqp_publisher.py).
It accepts only immutable Demucs/downstream request records from strict
builders, rechecks the finite exchange/routing/type allowlist and persistent
AMQP properties, and performs a mandatory publisher-confirmed publish with the
same bounded nack/unroutable versus uncertain-confirmation behavior as the
existing Demucs wrapper. A forged route is rejected before Pika loads. This is
still a source-only boundary: the Demucs-only `dispatch_once` composition and
its runtime/image/Deployment have not been switched to the generic path.
The shared outbox completion, known-failure retry, and terminal-invalid-event
helpers now have stage-neutral names. Their guarded SQL always used only the
immutable event UUID plus current lease UUID, so this is a naming/API boundary
for the future Basic Pitch/ADTOF path rather than a data transition change.
The old Demucs-named helpers remain compatibility wrappers for the existing
composition; three isolated tests confirm a downstream event uses the same
publisher-confirmed ownership, bounded retry, and fixed terminal category.
The new source-only
[`generic one-attempt composition`](kubernetes/services/dispatcher/app/dispatch_dispatchable_once.py)
now commits a generic claim, validates/selects its single AMQP route before
opening RabbitMQ, waits for publisher confirmation, then uses a second short
transaction to complete/retry/terminally retain that same lease. It preserves
the existing no-distributed-transaction boundary: malformed rows are terminal
before broker contact, known pre-confirmation failures receive bounded retry,
and uncertain confirmation relies on lease expiry for duplicate-safe recovery.
Five fake-boundary tests cover Basic Pitch success, idle, invalid-contract,
known-unavailable, and uncertain-confirmation paths. The existing
Demucs-only runtime still does not import or call this composition, so no image,
Deployment, database, or broker state changed.
The separate
[`dispatcher_generic_runtime.py`](kubernetes/services/dispatcher/app/dispatcher_generic_runtime.py)
entrypoint now reuses the existing settings parser, bounded supervisor,
database-recovery behavior, and SIGTERM/SIGINT handling while explicitly
injecting the generic one-attempt composition. The ordinary Demucs-only entry
point remains unchanged, so a later Docker-command/image/Deployment rollout
must explicitly select `python -m app.dispatcher_generic_runtime`. Two mocked
tests prove that selection and the configuration-error exit path without
opening a service connection.
The generic-capable dispatcher source is now built and pushed as the local
Linux/ARM64 image locked at
`clouddsp-registry.localhost:5001/dispatcher@sha256:cc2d36bd78ceb7e8ff8cd58d0d39d312c3325dda5e3d540dfcfad94e0dfdb487`.
Its build runs all 71 isolated dispatcher tests and keeps the Demucs-only
runtime as the image default. No Deployment was changed or applied by this
image task; the next small task is a separate generic dispatcher Deployment
whose explicit command selects the generic runtime.
That prepared [`generic dispatcher Deployment`](kubernetes/services/dispatcher/dispatcher-generic-deployment.yaml)
now pins the new image and makes that command explicit. It uses unique
controller labels, the existing restricted database/RabbitMQ Secrets, private
Service DNS, one small replica, and the same non-root/read-only hardening as
the Demucs-only controller. It does not alter the legacy Deployment and has
not been applied. During a future short migration overlap, PostgreSQL's current
lease token remains the authority that prevents two controllers from recording
the same outbox-event result; a later smoke/rollout task will decide when to
scale the legacy controller to zero.
The prepared separate RabbitMQ smoke-reader identity has no configure/write
rights and read access only to `clouddsp.demucs.requests`; it keeps a future
controlled integration smoke Job from widening the real dispatcher publisher's
least-privilege boundary. Its templates/bootstrap Job remain unapplied.
The prepared dispatcher-smoke client source now separately verifies only a
controlled durable `published` event and its exact persistent AMQP delivery.
Its adjacent unit-tested normal-path orchestrator obtains the event through a
temporary Keycloak user, the existing authenticated Job API, the API-issued
presigned MinIO POST, and the existing upload-intake path—never by seeding an
outbox row or publishing a message. It performs a no-work queue preflight
first, hands only the resulting stable IDs to the restricted verifier, and
then removes exactly its generated MinIO object, PostgreSQL job/outbox row, and
Keycloak user/client. The later image/Job task must add the necessary
namespace-local test credentials without granting runtime components those
administrator privileges.

The dispatcher smoke client now has a built two-stage, non-root ARM64 image
and non-interactive local-registry build script. Its first build passed all 18
isolated tests and its immutable output digest is pinned in `images.lock.yaml`.
The build intentionally did not create a Job or alter cluster state. The next
separate task can define the credential-scoped smoke Job using only that locked
reference.

The prepared, unapplied `dispatcher-normal-path-smoke` Job runs in
`clouddsp-data` and executes the single locked smoke-client process. It mounts
existing local Keycloak/PostgreSQL/MinIO administrator Secrets only to create
and remove its disposable test resources, while a separately applied temporary
data-namespace copy of the restricted RabbitMQ reader Secret allows AMQP
verification. That copy must use the existing smoke username/password and be
deleted once the Job finishes; neither runtime dispatcher nor upload-intake
Pod receives these administrator credentials.

### Demucs worker foundation

The versioned [`Demucs worker contract`](kubernetes/services/demucs/README.md)
defines the local processing boundary before a worker Deployment exists. A
RabbitMQ delivery first creates or claims one PostgreSQL
`(job_id, 'demucs', '')` task lease, then is acknowledged; an expiry/due-task
scan recovers work after a Pod crash without relying on a second broker
message. The worker re-reads the published outbox event, Job row, and private
MinIO source before it runs audio processing. It writes stable private stem
keys, then commits a guarded result only once. Version 1 uses three durable
task attempts with 30-second PostgreSQL-scheduled retries and an explicit
RabbitMQ DLQ for malformed/pre-claim failures. It does not directly invoke
Basic Pitch/ADTOF or Kubernetes; it now records their later dispatch requests
in PostgreSQL and leaves broker delivery/autoscaling as separate focused tasks.

The prepared immutable
[`v003 processing-task migration`](kubernetes/services/api/job-api-schema-migration-v003-processing-tasks-configmap.yaml)
creates the planned `processing_tasks` table only when its separate migration
Job is explicitly applied. It includes the canonical job/stage/stem and event
idempotency constraints, renewable active-lease constraints, terminal
completion timestamps, three-attempt limit, and partial retry/expired-lease
indexes. It has no worker-specific runtime Secret or application side effect.

The prepared
[`Demucs PostgreSQL bootstrap Job`](kubernetes/services/demucs/demucs-database-bootstrap-job.yaml)
is intentionally separate and must run only after v003 succeeds. It provisions
`clouddsp-demucs`, a restricted runtime role in the existing authoritative
database. The role can create/lease/recover `processing_tasks`, read only the
Job and published-outbox identity needed to validate a request, and make
guarded status/revision/stem/error updates. It cannot administer PostgreSQL,
delete records, alter source/owner/retention fields, read owner subjects or
outbox payloads, or publish downstream events. A separate prepared v004
permission-extension Job adds only the INSERT columns needed to atomically
record the next-stage requests after a Demucs completion. Its retained
app-namespace credential and temporary data-namespace bootstrap duplicate are
documented in the Demucs worker directory; the latter is deleted after a
successful bootstrap.

The prepared
[`Demucs RabbitMQ consumer bootstrap Job`](kubernetes/services/rabbitmq/rabbitmq-demucs-consumer-bootstrap-job.yaml)
provisions the separate `clouddsp-demucs` AMQP identity only after the existing
processing topology is present. It receives no management tag, configure, or
write permission; its exact read expression permits consumption and
acknowledgement only on `clouddsp.demucs.requests`. The retained runtime Secret
is in `clouddsp-app`; the data-namespace duplicate exists only for bootstrap and
is deleted after it succeeds. PostgreSQL retry scheduling means the first
worker does not need RabbitMQ publish authority.

The prepared immutable
[`Demucs MinIO policy`](kubernetes/services/minio/minio-demucs-artifacts-policy-v001-configmap.yaml)
and its separate
[`bootstrap Job`](kubernetes/services/minio/minio-demucs-artifacts-bootstrap-job.yaml)
provision the third `clouddsp-demucs` identity. It can read known private
`uploads/*` source objects and write/verify multipart `stems/*` artifacts; it
cannot list a bucket, delete objects, administer MinIO, or generate browser
presigned URLs. The static prefix scope is the narrowest MinIO IAM can express;
the future worker still obtains its one active job from PostgreSQL and binds its
lease token to durable transitions. As with other bootstrap identities, its
runtime Secret remains in `clouddsp-app` while the temporary data-namespace
duplicate is deleted after provisioning.

The first pure
[`Demucs request parser`](kubernetes/services/demucs/app/demucs_requested_message.py)
now verifies the broker envelope, persistent AMQP properties, canonical IDs,
strict 4 KiB UTF-8 JSON, and exact private source path before a future worker
can claim PostgreSQL state or contact MinIO. It rejects duplicate JSON members
and all unknown/widened protocol fields through one safe error category. Unit
tests cover valid version-1 messages plus malformed route/property/body/path
cases without a broker, database, storage object, Kubernetes resource, or ML
runtime. The later AMQP consumer will DLQ only these malformed requests;
transient pre-claim dependency failures remain unacknowledged for redelivery.

The pure
[`Demucs PostgreSQL task-lease adapter`](kubernetes/services/demucs/app/task_lease.py)
now defines the short transaction that converts one parsed request into one
canonical `processing_tasks` lease. It locks a task, then its Job, then repeats
the task lock before insert to close concurrent first-claim races. A matching
task is duplicate-safe; missing/expired/terminal Jobs are safe stale history;
durable Job/outbox disagreement is intentionally not acknowledged until a later
guarded terminal transition records it. Recovery uses `FOR UPDATE SKIP LOCKED`,
token-guarded renewal, a 15-minute bounded lease, and a hard three-attempt
ceiling. The current adapter uses no connection, commit, broker, object store,
Kubernetes API, or model process; 18 parser/lease unit tests prove its decisions
in isolation.

The pure
[`Demucs MinIO HeadObject verifier`](kubernetes/services/demucs/app/source_object.py)
now checks a claimed task's own private source coordinate without downloading
audio. It validates storage-side byte limit, canonical CloudDSP audio MIME
type, and `job-id`/`stem-mode` object metadata. Definite absence/mismatch has a
bounded permanent category while service, network, and authorization faults are
retryable; FFprobe still owns byte-level audio/duration validation. Five new
fake-client tests bring the Demucs pure-boundary suite to 23 passing tests.

The pure
[`Demucs FFprobe-result parser`](kubernetes/services/demucs/app/audio_probe.py)
now validates the later tool's bounded JSON result without starting a process
or reading a media byte. It requires at least one audio stream and an exact,
finite, positive `format.duration` no greater than the shared 500-second
source limit. Malformed/ambiguous tool output remains a retryable adapter
protocol concern; known unsuitable media has a bounded permanent category.
Six focused tests bring the Demucs pure-boundary suite to 29 passing tests.

The small
[`Demucs FFprobe process adapter`](kubernetes/services/demucs/app/ffprobe_process.py)
now composes that parser with one fixed, shell-free local command. It accepts
only a regular non-symlink file below the worker-owned Pod scratch directory,
gives the child no stdin, discards stderr, incrementally limits stdout to the
parser's one-MiB allowance, and terminates its private process group after a
30-second timeout or overflow. The next task will download an already-verified
private MinIO object into the bounded scratch directory; this adapter makes no
storage, PostgreSQL, RabbitMQ, Demucs, or Kubernetes request. Five adapter
tests bring the Demucs foundation suite to 34 passing tests.

The bounded
[`Demucs MinIO source-download adapter`](kubernetes/services/demucs/app/source_download.py)
now streams one `HeadObject`-verified private object into a randomly named
temporary child of the worker's future size-limited `emptyDir`. It makes one
restricted `GetObject` request, compares the response and streamed byte count
to the verified size, never derives a local path from a user filename/S3 key,
and removes the child directory on every normal or exceptional context exit.
MinIO outages remain retryable; a post-verification size mismatch cannot reach
FFprobe. Five fake-client tests bring the Demucs foundation suite to 39 passing
tests; this task makes no cluster request or image change.

The lease-bound
[`Demucs source-preflight composition`](kubernetes/services/demucs/app/source_preflight.py)
now wires the three source boundaries in their required order: verified private
metadata, exact streamed bytes, then bounded FFprobe audio/duration evidence.
It returns no temporary path, because the download context removes local source
bytes before it returns, and preserves each lower-layer category for a future
lease-token-guarded result transition. Four fake-client composition tests bring
the Demucs foundation suite to 43 passing tests. It does not claim/renew/finish
a task, acknowledge RabbitMQ, construct Boto3, run a model, or alter Kubernetes.

The new
[`Demucs MinIO Boto3 settings/factory`](kubernetes/services/demucs/app/minio_client.py)
now provides the future source-preflight client injection point without making
a network request. It accepts only explicit internal `.svc` MinIO Service DNS,
the fixed uploads bucket/local region/path addressing contract, and the
restricted Demucs S3 Secret. Its lazy factory supplies those credentials,
Signature V4, and bounded client timeouts/retries so Boto3 cannot use ambient
AWS credentials. Six mocked-SDK tests bring the Demucs foundation suite to 49
passing tests. [`demucs/requirements.lock`](kubernetes/services/demucs/requirements.lock)
now hash-pins the complete first local-CPU runtime: Boto3's exact S3-client
closure, Psycopg/Pika, Demucs 4.0.1 and its Dora/ML dependencies, plus the
Linux/ARM64 CPU Torch/TorchAudio 2.4.0 wheels and their transitive packages.
The lock's hash-enforced ARM64 download verification passed for all 36 source
archives/wheels. It neither installs a package nor builds an image. A later
Linux/AMD64 CPU/GPU profile must lock its own native wheel artifacts rather
than weakening the ARM64 hash constraint.

The reviewed [`Demucs local CPU image recipe`](kubernetes/services/demucs/image-recipe.md)
and its multi-stage [`Dockerfile`](kubernetes/services/demucs/Dockerfile) have
now built and pushed the first local CPU image. The registry-confirmed ARM64
digest is pinned under [`images.demucs`](kubernetes/images.lock.yaml), while no
Deployment can use the readable tag. The current Apple-Silicon k3d profile uses
the existing digest-pinned Docker Official
Python 3.12.14 Linux/ARM64 base, Debian Bookworm FFmpeg 5.1.9, Demucs 4.0.1,
and CPU-only PyTorch/TorchAudio 2.4.0. This preserves the cloud worker's
PyTorch 2.4.0 plus `htdemucs`/`htdemucs_6s` model baseline without carrying
unusable CUDA libraries. The separate native Linux/AMD64 NVIDIA profile will
need its own locked CUDA image. The 479.50 MiB local image verified model
checksums while building, so a normal Pod has no reason to fetch mutable
weights from the public internet at startup.

The new unit-tested [`Demucs Psycopg connection boundary`](kubernetes/services/demucs/app/postgresql.py)
accepts only the internal PostgreSQL Service DNS, authoritative database, and
restricted `clouddsp-demucs` role from the existing runtime Secret. It opens a
fresh, bounded, dictionary-row transaction only for a future durable lease
action; it contains no claim query, RabbitMQ call, MinIO call, model process,
or Kubernetes API use. Seven fake-driver tests validate settings, password
redaction, commit/rollback scope, and safe outage classification. This
source-only task intentionally does not alter the already-pushed image or add
a Deployment; a later runnable-worker build will replace `images.demucs`.

The new [`Demucs first-claim composition`](kubernetes/services/demucs/app/first_claim.py)
is the smallest layer that joins that transaction boundary to the existing pure
task-lease SQL. One call opens one short write scope, returns a committed
claimed/duplicate/stale result only after the scope exits, and lets an unsafe
inconsistency roll back to a future AMQP layer that must leave the delivery
unacknowledged. It adds no consumer loop, broker acknowledgement, storage or
model operation, Deployment, or image change. Four recording-context tests
cover commit, rollback, and database-unavailable ordering; the old pinned image
intentionally predates this source-only composition.

The companion [`Demucs task-maintenance composition`](kubernetes/services/demucs/app/task_maintenance.py)
now places the existing due-task recovery and token-guarded renewal SQL inside
the same one-call, one-short-transaction boundary. An idle recovery scan commits
normally with no invented work; a returned recovery lease or renewed expiry is
durable before any future CPU/storage action begins; and a `None` renewal means
the worker must stop because it no longer owns that task. Five recording-context
tests cover normal commit paths, malformed-row rollback, and an unavailable
database before SQL runs. It deliberately adds no long-running supervisor,
RabbitMQ handling, media work, Deployment, image rebuild, or cluster change.

The new [`Demucs delivery-claim bridge`](kubernetes/services/demucs/app/delivery_claim.py)
now applies the exact AMQP parser before the already-committed first-claim
composition. It exposes only the parsed request identifiers and PostgreSQL's
claimed/duplicate/stale result, not the raw body, delivery tag, broker client,
or an implicit acknowledgement decision. Four mocked-boundary tests prove that
malformed input cannot reach PostgreSQL and that a database outage still reaches
the future transport untouched. The next separate Pika-only task must define
manual `ack`/`nack`/redelivery behavior explicitly; this bridge adds no network
connection, consumer loop, worker processing, image, Deployment, or cluster
change.

The new [`Demucs manual-ack adapter`](kubernetes/services/demucs/app/amqp_manual_ack.py)
now implements that one-delivery Pika-shaped state machine without opening a
connection. It reads only the fixed `clouddsp.demucs.requests` queue with
`auto_ack=False`, acknowledges only after the bridge's committed result, nacks
only malformed contracts with `requeue=False`, and propagates database or
identity failures without a broker action for later close/reconnect handling.
After a successful acknowledgement, its explicit result retains the exact
committed lease only for a newly claimed task; duplicate/stale/idle/rejected
results carry no lease and cannot start source processing. Eight mocked
channel/result tests cover idle, durable outcomes, malformed DLQ routing,
unacknowledged dependency/inconsistency failures, failed ack/nack calls, and
the missing-lease guard. It adds no Pika connection/configuration, supervisor,
source processing, image, Deployment, or cluster action.

The next
[`acknowledged-lease source-preflight handoff`](kubernetes/services/demucs/app/acknowledged_lease_preflight.py)
now admits work to the existing MinIO/FFprobe source boundary only after the
manual-ack adapter returns `ACKNOWLEDGED_LEASE`. It carries the exact durable
lease token with the returned source evidence, while idle, duplicate/stale, and
malformed-delivery outcomes fail before a storage request or local FFprobe
process can begin. Source failures retain their existing categories for the
future lease-token-guarded result transition. Three mocked tests cover the
admission gate and exception propagation; this adds no loop, task-state write,
Demucs model execution, image, Deployment, or cluster change.

The pure task-lease module now adds its
[`leased`-to-`running` guard](kubernetes/services/demucs/app/task_lease.py) for
the next step after successful source preflight. The parameterized update
matches task ID, job ID, `leased` state, the current lease UUID, and
PostgreSQL's unexpired timestamp before it writes `running`; a no-row result is
the durable instruction to stop rather than start Demucs under stale ownership.
It preserves a prior `started_at` across recovery, and three fake-cursor tests
cover success, ownership loss, and malformed driver data. A later small
transaction-composition task will connect this pure statement to the completed
preflight handoff. No model, image, Deployment, or cluster change occurs here.

That composition now exists in
[`preflight_task_start.py`](kubernetes/services/demucs/app/preflight_task_start.py).
It accepts the acknowledged validated-source handoff, opens exactly one short
write scope, commits the guarded task-start result, and returns a
model-eligible lease/source/timestamp value only afterward. Ownership loss
returns a committed `None`, while an outage or malformed row rolls back and
escapes. Four in-memory tests prove those paths. The task does not read a new
message, invoke Demucs, renew a lease, create artifacts, build an image, or
change a Kubernetes resource.

The new [`Demucs command builder`](kubernetes/services/demucs/app/demucs_command.py)
fixes the future child process's local CPU argument tuple but does not run it.
It preserves CloudDSP's `htdemucs`/`htdemucs_6s` stem-mode mapping and adds the
current image's fixed CPU device and baked model repository. Only a generic
`source.media` file and a fresh empty directory below worker scratch can become
arguments; the output coordinate is deterministically `model/source`. Three
temporary-file tests cover mode selection and reject unsafe local paths. A
later bounded process adapter must obtain its own local source scope and
independently validate Demucs outputs before any MinIO write. No image,
Deployment, or cluster change occurs in this source-only task.

The companion [`Demucs process runner`](kubernetes/services/demucs/app/demucs_process.py)
now executes only a request that it can rebuild exactly through the command
builder. Its production path is shell-free, has no stdin or retained child
output, places the child in a private process group, and limits one local CPU
process to 12 minutes—inside the 15-minute lease and with planned renewal
margin. It returns a safe timeout/unavailable/nonzero category and does not
mistake a zero exit for verified artifacts. Four fake-runner tests cover the
execution boundary without invoking Demucs. No image, Deployment, or live
cluster process changes in this task.

The new [`Demucs artifact inventory validator`](kubernetes/services/demucs/app/demucs_artifacts.py)
is the output-side counterpart to the fixed command. Before any MinIO uploader
can receive local files, it requires the exact mode-specific non-empty WAV
stems in the expected private `model/source` directory, rejecting every helper
file, extra/missing stem, directory, symlink, empty file, or tampered command
record. Its returned inventory contains only stable stem names and ephemeral
local path/size evidence. Four temporary-file tests cover all supported modes
and unsafe cases. It performs no audio inspection, hashing, upload, database
write, image build, Deployment, or cluster action.

The following [`Demucs artifact hash boundary`](kubernetes/services/demucs/app/demucs_artifact_hash.py)
turns that inventory into stable local evidence before any future MinIO output
adapter exists. It repeats the strict inventory validation, streams each
current WAV in 64 KiB chunks, and returns only the fixed stem name, current
path, recorded byte count, and SHA-256 digest. It rejects a symlink, a
non-regular file, any size change during reading, and caller-supplied inventory
metadata that no longer matches the directory. Three temporary-file tests show
the intended evidence and unsafe-case rejection. It does not upload anything,
call MinIO, mutate PostgreSQL, acknowledge RabbitMQ, rebuild the image, or
change Kubernetes resources.

The following pure
[`Demucs output-object contract`](kubernetes/services/demucs/app/demucs_output_object.py)
defines the only artifact coordinates a future MinIO uploader may receive. It
re-establishes current SHA-256 evidence and joins it to the canonical durable
lease, then maps every exact stem deterministically to
`stems/{job_id}/{stem_name}.wav` in the reviewed private bucket. Each immutable
plan fixes `audio/wav`, length, the ephemeral local source path, and ordered S3
metadata for schema/producer/job/task/stem/size/digest evidence. A same-size
byte replacement, forged hash dataclass, malformed lease, mismatched job
source prefix, or stem-mode mismatch cannot choose a storage object. Three
temporary-file tests prove those paths. It makes no S3 request or durable
write, does not acknowledge RabbitMQ, and changes no image, Deployment, or
cluster resource.

The subsequent [`Demucs MinIO artifact-upload adapter`](kubernetes/services/demucs/app/demucs_artifact_upload.py)
accepts only one of those fixed plans and one Boto3-compatible `PutObject`
client. It repeats the object/metadata contract, pre-hashes the current local
regular WAV, then streams it to the reviewed private key while calculating a
second SHA-256. It returns durable-safe bucket/key/size/digest evidence only if
the client consumed the entire planned body with the expected digest; an S3
ETag is deliberately not used as a portable integrity signal. Same-size local
replacement, partial client consumption, and raw SDK transport errors remain
safe rejection/unavailable categories. Three fake-client tests cover the
normal and failure paths. It neither creates an SDK client nor lists/deletes
objects, changes PostgreSQL/RabbitMQ, rebuilds an image, or changes cluster
resources.

The new
[`Demucs complete-stem-set composition`](kubernetes/services/demucs/app/demucs_stem_set_publish.py)
joins the reviewed model/process, exact output tree, hash, output-plan, and
one-stem upload boundaries in order. A matching running lease and separation
produce a complete in-memory receipt set only once every expected stem has
uploaded to its fixed private key and each receipt matches its plan. A model
failure, incomplete output tree, task-mode mismatch, or forged injected
uploader result prevents any complete success result. Already-uploaded objects
may remain private at stable retry-overwritable keys, but this source-only
composition writes neither PostgreSQL success state nor downstream outbox
events. Three fake process/client tests cover those cases; no image or
Kubernetes resource changes.

The pure [`Demucs completion SQL guard`](kubernetes/services/demucs/app/task_lease.py)
now locks a retained `source_uploaded` Job before it changes any row, then
requires the exact `running`, unexpired task token before it atomically writes
the complete `stems` JSONB document, transitions the task to `succeeded`,
clears its lease, advances the Job to `midi_processing`, and increments its
revision. A no-row result leaves both task and Job unchanged when a recovery,
expiry, deletion, or conflicting transition has made the worker stale. The
same statement inserts every finite downstream event: `drums` requests ADTOF,
and every non-drum output requests Basic Pitch. A new immutable v004 migration
extends the v002 outbox constraints to exactly that vocabulary, and a separate
prepared bootstrap Job grants the restricted Demucs role only the required
INSERT columns. Four fake-cursor cases cover success, no-row ownership loss,
malformed evidence, and a mismatched inserted-event count.

The companion [`Demucs task-completion transaction`](kubernetes/services/demucs/app/demucs_task_completion.py)
converts a complete verified receipt set into a compact parameterized JSONB
map. It keeps the cloud-compatible stem `status: ready` and `s3_key` fields,
and records bucket, fixed type, byte count, and SHA-256 for future independent
workers. It builds the exact downstream outbox document before a short
transaction commits; invalid receipt/event evidence never opens that
transaction and a database failure rolls it back. Three in-memory tests prove
those boundaries. It makes no storage/broker request, image change, or
Kubernetes change.

The separate [`Demucs AMQP connection boundary`](kubernetes/services/demucs/app/amqp_connection.py)
now loads only the restricted Demucs RabbitMQ Secret and fixed private Service
topology. It rejects host/port/vhost/queue/username widening, hides the password
from normal representations, bounds connection timing, lazily imports Pika, and
maps a connection diagnostic to one safe retryable category. Six mocked tests
cover settings, exact private Pika parameters, and outage handling without a
broker connection. It intentionally adds no channel, consumer, acknowledgement,
supervisor, image, Deployment, or cluster change.

The companion [`Demucs AMQP channel helper`](kubernetes/services/demucs/app/amqp_channel.py)
now applies `prefetch_count=1` and passively verifies only the existing reviewed
request queue. It cannot declare/bind/modify RabbitMQ topology or receive a
delivery; a setup failure maps to a bounded category for the later reconnect
supervisor. Four mocked tests cover normal flow control, passive-check failure,
and the repeated fixed-queue guard. No actual broker connection, image,
Deployment, or cluster action is part of this source-only task.

The completed [`Demucs model-artifact lock`](kubernetes/services/demucs/model-artifacts.lock.yaml)
now fixes the exact `htdemucs` and `htdemucs_6s` source URLs, descriptor files,
byte counts, full SHA-256 checksums, and Demucs's shorter filename-check
prefixes. The two weights total about 132.7 MiB but remain outside Git; the
Dockerfile downloads, verifies, then bakes them into its read-only local model
repository during a future image build. This gives a normal Pod no reason to
use public-internet egress when it starts processing.

The prepared upload-intake database bootstrap creates a separate
`clouddsp-upload-intake` PostgreSQL login inside the existing authoritative
`clouddsp_job_api` database. It has only the reviewed column-level `jobs`
permissions required to correlate and conditionally transition a direct
upload; it is not a database owner and cannot create, delete, or alter durable
job records beyond that bounded state transition.

Workers do not create Kubernetes Jobs or invoke other workers directly. They
use stable `(job_id, stage, stem_name)` idempotency keys, manual
acknowledgements after durable work, bounded retries, DLQs, and PostgreSQL
outbox publication. RabbitMQ and WebSockets are at-least-once/best-effort;
PostgreSQL is authoritative.

## Authentication and browser rules

- Keycloak owns password hashing, registration, confirmation, resets, and MFA
  in local PostgreSQL; application services must not own a password table.
- Local self-registration requires password and confirmation on its initial
  Keycloak form, then email verification. The dedicated Password Validation
  configuration Job documents that deliberate UX choice and is idempotent;
  revalidate it when upgrading Keycloak because Keycloak marks the setting
  deprecated in favor of deferred password creation after verification.
- The local Keycloak issuer and browser entry point is
  `http://keycloak.localhost:8080`. Traefik routes that host only to Keycloak's
  application Service; never route its management health/metrics port.
- The local Mailpit browser inbox is `http://mailpit.localhost:8080`. Traefik
  routes that host only to Mailpit's web-UI Service; SMTP stays internal at
  `clouddsp-mailpit-smtp:1025` and must never receive an Ingress route.
- The local frontend uses OIDC Authorization Code with PKCE. Its local React
  variant redirects credential entry to Keycloak, validates callback state,
  and uses OAuth access tokens for API/WebSocket calls; it does not handle a
  password or contain a client secret. API and WebSocket services validate
  issuer, audience, expiration, signing keys, and immutable `sub` ownership.
- The local React SPA has the browser origin `http://clouddsp.localhost:8080`
  and a Keycloak public client named `clouddsp-react`.  Its only redirect and
  post-logout URL is `http://clouddsp.localhost:8080/`; it uses Authorization
  Code with S256 PKCE and never has a browser-visible client secret, implicit
  flow, direct password grant, or service-account grant.
- The local frontend delivery layer lives under
  `kubernetes/services/frontend/`. Its `app/` directory is a K8-specific React
  variant copied from a recorded cloud Git revision. The local Keycloak
  adapter, container recipe, and manifests are versioned in that local layer;
  they must not change the preserved cloud source tree. Synchronization between
  the two variants is deliberate and documented in the local app provenance.
- Keep browser uploads presigned and private. Store object keys—not signed
  URLs—in durable state.
- Preserve browser polling as the artifact-retrieval fallback. WebSocket
  messages are hints only.
- Keep CSP exact. Add only reviewed local API, WSS, OIDC, and MinIO origins;
  never add broad origins or unsafe directives.

## Images and build policy

Start from reviewed upstream images and lock their digests in
`kubernetes/images.lock.yaml` before deployment:

| Purpose | Image basis |
| --- | --- |
| React build | `node:24-bookworm-slim` |
| React delivery | `nginx:1.28-alpine` |
| API, realtime, dispatchers | `python:3.12-slim-bookworm` |
| Identity | `quay.io/keycloak/keycloak` |
| Job and identity databases | `postgres:17-bookworm` |
| Object storage | `quay.io/minio/minio` |
| Queues | `rabbitmq:4-management` locally |
| Demucs | CloudDSP image based on `pytorch/pytorch:2.4.0-cuda12.1-cudnn9-runtime` |
| Basic Pitch | CloudDSP image with the tested Basic Pitch runtime |
| ADTOF | CloudDSP image with pinned CPU PyTorch dependencies |
| yt-dlp | CloudDSP image with pinned yt-dlp, Deno, and FFmpeg |

Build frontend assets with Node and serve the immutable Vite output through
NGINX. Build `linux/amd64` GPU images only for the native Linux GPU profile.
Do not use floating image tags, random third-party worker images, Lambda bases,
or development servers as production-like runtime containers.

## Scaling, resilience, and security

- KEDA scales each worker stage independently from queue backlog. Cap Demucs
  replicas at actual GPU capacity and set `prefetch=1`.
- Give CPU workers independent CPU, memory, ephemeral-storage, deadline, and
  retry limits. Use HPA for API, realtime, and dispatcher Deployments.
- Apply `ResourceQuota`, `LimitRange`, `PriorityClass`, PodDisruptionBudgets,
  and default-deny `NetworkPolicy`.
- MinIO, PostgreSQL, and RabbitMQ use PVCs. Keycloak is a replaceable
  Deployment whose durable identity data lives in its dedicated PostgreSQL
  database. Local data is disposable only when explicitly destroyed; provide
  backup guidance before claiming high availability.
- Store credentials only in Kubernetes Secrets sourced from ignored local
  configuration. Never commit tokens, passwords, proxy addresses, presigned
  URLs, or live local data.
- Workers do not receive Kubernetes API credentials. MinIO, database, queue,
  and network access must be least-privilege.
- Preserve accepted media types, source byte/duration limits, host allowlists,
  per-user UTC quotas, terminal deletion rules, and 14-day retention behavior.

Kubernetes improves scheduling, isolation, retry handling, rollout control,
and stage-specific scale-out. It cannot provide high availability or GPUs
beyond the physical local nodes available to the cluster.

## Delivery phases

1. Scaffold the k3d cluster, local registry, namespaces, Helm chart, values,
   and ignored local configuration template.
2. Deploy PostgreSQL, MinIO, RabbitMQ, Keycloak, KEDA, ingress, and
   observability with persistent local storage.
3. Implement K8-specific API, realtime, upload-intake, PostgreSQL outbox, and
   Keycloak integration without touching the cloud source tree.
4. Build K8-specific worker CLIs and images for yt-dlp, Demucs, Basic Pitch,
   and ADTOF.
5. Add KEDA policies, GPU node profile, retention, terminal deletion, retry,
   DLQ, and duplicate-delivery recovery.
6. Run end-to-end and load tests before describing the local profile as
   high-workload ready.

## Required validation

Before Kubernetes changes are complete, run the relevant unit tests, frontend
lint/build, Helm lint/template, Kubernetes schema validation, `git diff
--check`, image/SBOM scanning, and an end-to-end local smoke test.

The smoke test must cover login, direct upload, linked ingestion, one terminal
processing job, MIDI availability, polling after a missed WebSocket update,
duplicate-message idempotency, retry/DLQ handling, retention, and terminal-job
deletion.
