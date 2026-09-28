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
| Worker scaling | Long-running stage consumers; Demucs watches both its request queue and durable active/due PostgreSQL tasks because it acknowledges early | KEDA `ScaledObject` + Kubernetes HPA |
| GPU scheduling | Demucs resource isolation | Native Linux k3s GPU profile only |

Keycloak's live Deployment, ClusterIP Service, and browser Ingress are now
owned by the independent
[`clouddsp-keycloak` Helm release](kubernetes/helm/keycloak/README.md).
Adoption preserved their UIDs, the Service IP, the running Pod, and the
`http://keycloak.localhost:8080` issuer. Its PostgreSQL database, runtime
Secrets, realm/client/SMTP bootstrap Jobs, and durable identity records have
separate lifecycles. OIDC discovery, React PKCE authorization to the login
form, verification email capture in Mailpit, and temporary-user token reads
from `/auth/me` and `/jobs` passed after adoption.

The durable PostgreSQL StatefulSet and its two Services are now owned by the
independent [`clouddsp-postgresql` Helm release](kubernetes/helm/postgresql/README.md).
The protected takeover followed a full logical backup and successful restore
into an isolated no-network instance. StatefulSet, Pod, Service, and bound
PVC/PV identities stayed unchanged. The in-cluster read/write smoke passed
through the ClusterIP Service. The database files, credentials, migrations,
and bootstrap Jobs remain separate from the Helm chart.

The MinIO StatefulSet, two Services, and S3 Ingress are now owned by the
independent [`clouddsp-minio` Helm release](kubernetes/helm/minio/README.md).
A stopped-volume snapshot was verified by an isolated restore of two buckets,
489 current objects, their versions and notification settings, plus an object
download. Adoption preserved the four resource UIDs, post-backup Pod UID,
Service IPs, and bound PVC/PV. The in-cluster S3 API and restricted Job API
access smokes passed. Bucket contents, IAM state, credentials, and bootstrap
Jobs remain separate from the Helm chart.

The RabbitMQ StatefulSet, three Services, and ingress NetworkPolicy are now
owned by the independent
[`clouddsp-rabbitmq` Helm release](kubernetes/helm/rabbitmq/README.md).
A stopped-volume backup restored into an isolated no-network broker with the
same node name; two vhosts, 12 queues, full definitions, and queue depths
matched. Adoption preserved the five resource UIDs, post-backup Pod UID,
Service IPs, and bound PVC/PV. The in-cluster AMQP
publish/consume/acknowledge smoke passed. Broker state, credentials,
bootstrap Jobs, and KEDA resources remain separate from the Helm chart.

The two shared KEDA `TriggerAuthentication` objects are now owned by the
independent [`clouddsp-scaling-auth` Helm release](kubernetes/helm/scaling-auth/README.md).
Their UIDs, spec generations, and KEDA finalizers stayed unchanged. All
three worker ScaledObjects remained Ready with their generated HPA and
worker Deployment UIDs preserved. Observer Secret values remain outside Helm;
each worker Deployment and ScaledObject will be adopted separately.

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
             KEDA-scaled Demucs Deployment (GPU profile)
                               │
             MinIO stems + PostgreSQL state/outbox commit
                    ┌──────────┴──────────┐
                    │                     │
           Basic Pitch queue          ADTOF queue
                    │                     │
      KEDA-scaled Basic Pitch     KEDA-scaled ADTOF
          CPU Deployment            CPU Deployment
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

   When a Pod disappears after RabbitMQ acknowledgement, PostgreSQL—not the
   broker—releases the expired lease for recovery. The restricted Demucs role
   does not receive general `outbox_events.payload` read access. A reviewed
   PostgreSQL security-definer verifier instead compares the exact immutable
   `demucs.requested` payload with the reclaimed lease and returns only a
   boolean. This preserves recovery while keeping other private event payloads
   outside the worker's authority.

   The local CPU model has a 12-minute processing budget. A process timeout
   now atomically fails its task and Job on the first occurrence, without a
   scheduled retry. A recovery scan also fails a task whose durable first
   `started_at` crossed that deadline if its Pod vanished or was force-killed;
   it runs before any new lease is issued. `GET /jobs/{job_id}` translates the
   fixed timeout code into a clear, owner-visible terminal message. Other
   reviewed transient failures retain their existing bounded retry policy.
5. After its artifact and PostgreSQL commit succeed, Demucs writes one outbox
   event per actual stem: drums route to ADTOF and pitched stems route to Basic
   Pitch.
6. KEDA scales each long-running worker Deployment. Basic Pitch and ADTOF
   use their primary RabbitMQ queues. Demucs uses both its request queue and a
   read-only PostgreSQL count of leased/running or due-retry tasks: its AMQP
   delivery is acknowledged immediately after a durable claim, so queue depth
   alone cannot keep a multi-minute CPU process alive. The broker retry/DLQ
   topology and PostgreSQL lease recovery—not a Kubernetes Job per
   delivery—govern redelivery and task recovery.
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
the applied Deployment. The source-to-outbox integration smoke Job verifies
the ordinary authenticated upload route, native MinIO notification, one
durable pending Demucs event, and a duplicate RabbitMQ notification before
the separate dispatcher publishes `demucs.requested` to RabbitMQ.
The live source-to-outbox smoke has now passed against the Helm-owned
upload-intake Deployment. Its first attempt exposed that the versioned
RabbitMQ ingress NetworkPolicy excluded this fixed test Pod from the
management listener needed for one duplicate source notification. A narrow
TCP 15672 rule now admits only `source-to-outbox-smoke` in `clouddsp-data`
with the `integration-test` component label; KEDA, bootstrap Jobs, and the
existing six-stem observer retain their own separate rules. The test Job uses
the current reviewed Job API image digest. Both dispatcher Helm releases were
paused through versioned values while the test asserted one pending Demucs
outbox event, then restored to one replica after the Job cleaned up its
temporary user, object, and database row. The source and Demucs queues were
empty afterward.

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
now been applied and verified as one Running zero-restart Pod with the pinned
digest and explicit generic command. During a future short migration overlap,
PostgreSQL's current lease token remains the authority that prevents two
controllers from recording the same outbox-event result; a later smoke/rollout
task will decide when to scale the legacy controller to zero.
The next prepared smoke-test boundary is a distinct no-tag RabbitMQ identity,
`clouddsp-basic-pitch-smoke`, created only by the administrator-only
[`Basic Pitch smoke-reader bootstrap Job`](kubernetes/services/rabbitmq/rabbitmq-basic-pitch-smoke-consumer-bootstrap-job.yaml).
Its exact `/clouddsp` vhost permissions deny all configure/write operations and
allow reads only from `clouddsp.basic-pitch.requests`. The matching permanent
app-namespace runtime Secret and temporary data-namespace bootstrap Secret are
documented as ignored templates. This lets a later controlled routing test
observe one queue delivery without granting the real dispatcher consumption
rights or giving the test any publication/topology authority. Local copies of
both Secrets were applied and the bootstrap Job completed successfully: its
permission output confirms empty configure/write expressions and read access
only to `clouddsp.basic-pitch.requests`. The permanent app Secret remains for
the later smoke Job; the temporary data Secret awaits explicit cleanup.
The matching PostgreSQL smoke identity takes the same least-privilege
approach: [`generic-dispatcher-smoke-database-bootstrap-job.yaml`](kubernetes/tests/generic-dispatcher-smoke/generic-dispatcher-smoke-database-bootstrap-job.yaml)
created `clouddsp-generic-dispatcher-smoke` with no table, sequence, or
schema-create access. Instead, it grants only three security-definer functions
to create one short-lived synthetic Basic Pitch outbox event, observe only its
publication state, and delete it only after publication. This allows an end-to-
end dispatcher routing test without granting a Pod arbitrary durable-work
insertion or database browsing. Its ignored local app/data Secret copies were
applied, and the bootstrap Job completed successfully. Its final privilege
check confirmed no direct `jobs`/`outbox_events` access and execute permission
only on those three functions. This required the now-applied v004 downstream
outbox migration; it is recorded in the authoritative migration ledger.
The new, unit-tested
[`generic Basic Pitch smoke client`](kubernetes/tests/generic-dispatcher-smoke/client/generic_dispatcher_basic_pitch_smoke.py)
uses precisely those functions plus the already-restricted Basic Pitch AMQP
reader identity. It creates one opaque synthetic event, waits for the generic
dispatcher to record `published`, validates and acknowledges its one exact
`basic-pitch.requested` delivery, then asks the restricted cleanup function to
remove the synthetic Job. On a failed delivery it deliberately leaves the
short-lived event intact for diagnosis instead of deleting unverified work.
Its adjacent [`requirements.lock`](kubernetes/tests/generic-dispatcher-smoke/client/requirements.lock)
contains only hash-pinned Pika, Psycopg, Psycopg's binary wheel, and its
typing helper. The lock was resolved successfully for the planned
Linux/ARM64 CPython 3.12 image profile. Its
[`two-stage Dockerfile`](kubernetes/tests/generic-dispatcher-smoke/client/Dockerfile)
uses the immutable Python 3.12 base, installs that lock and runs the isolated
tests in a temporary validation stage, then copies only verified packages and
the one client module into a non-root runtime stage. The built image passed all
16 tests, was pushed to the local registry, and is locked at
`clouddsp-registry.localhost:5001/generic-dispatcher-basic-pitch-smoke-client@sha256:886bdb2d29b29a6aa6eee674283c769524e0e51f86b6e310e03e437f25af9eb3`.
The prepared, unapplied
[`routing-smoke Job`](kubernetes/tests/generic-dispatcher-smoke/generic-dispatcher-basic-pitch-routing-smoke-job.yaml)
runs it in `clouddsp-app` with only the already-applied restricted database and
Basic Pitch reader Secrets. It has no administrator credentials, Kubernetes API
token, data volume, public route, service, or ingress. Its 60-second client
wait is bounded by a 180-second Job deadline; a failed synthetic event remains
for diagnosis rather than being retried automatically.
The prepared separate RabbitMQ smoke-reader identity has no configure/write
rights and read access only to `clouddsp.demucs.requests`; it keeps a future
controlled integration smoke Job from widening the real dispatcher publisher's
least-privilege boundary. Its templates/bootstrap Job remain unapplied.

### Basic Pitch worker foundation

The first Basic Pitch boundary is the pure, unit-tested
[`request/output contract`](kubernetes/services/basic-pitch/app/basic_pitch_requested_message.py).
It accepts only the version-1 persistent `basic-pitch.requested` delivery from
the processing exchange, for the finite non-drum stem vocabulary `vocals`,
`no_vocals`, `bass`, `other`, `guitar`, and `piano`. It rejects the ADTOF-only
`drums` route, foreign broker metadata, duplicate/oversized JSON, an arbitrary
object key/bucket, invalid byte/checksum evidence, and any unexpected field.
Each accepted input maps only to the cloud-compatible private coordinate
`midi/{job_id}/{stem_name}.mid`; it does not access MinIO, run ML, create an
image, claim/complete a PostgreSQL task, or acknowledge RabbitMQ. Those
responsibilities remain separate focused tasks so at-least-once delivery cannot
be mistaken for authority to process arbitrary private audio.
The immutable, now-applied
[`v005 Basic Pitch processing-task migration`](kubernetes/services/api/job-api-schema-migration-v005-basic-pitch-processing-tasks-configmap.yaml)
was the database-only prerequisite. It required the applied v003 task and v004
downstream-outbox migrations, then replaced only v003's three Demucs-only
checks with one atomic stage/stem/private-input-key constraint. Demucs retains
its canonical job-wide `uploads/{job-id}/...` source coordinate; Basic Pitch
can use only a finite non-drum stem name and
`stems/{job-id}/{stem}.wav`. The existing primary/request-event uniqueness,
`(job_id, stage, stem_name)` idempotency, retry/lease indexes, status/lease
checks, timestamp trigger, and historical rows remain unchanged. Its separate
one-shot Job used only the existing app-namespace Job API schema-owner Secret
and had no Kubernetes token or RabbitMQ/MinIO/Keycloak credentials. It
completed successfully and recorded `v005_basic_pitch_processing_tasks` in the
authoritative ledger.

The completed
[`Basic Pitch PostgreSQL bootstrap Job`](kubernetes/services/basic-pitch/basic-pitch-database-bootstrap-job.yaml)
created the distinct `clouddsp-basic-pitch` login using a short-lived
`clouddsp-data` administrator Secret. Its permanent `clouddsp-app` credential
can create and lifecycle-manage a processing task and read the limited Job and
outbox fields needed to compare a durable request. It cannot read owners or
source coordinates, update Jobs, write/lease/publish outbox events, delete
records, or run DDL. The completed grant report confirmed both the intended
access and those denials; its temporary data-namespace Secret was then deleted.

The applied immutable
[`Basic Pitch MinIO policy`](kubernetes/services/minio/minio-basic-pitch-artifacts-policy-v001-configmap.yaml)
and completed
[`MinIO artifacts bootstrap Job`](kubernetes/services/minio/minio-basic-pitch-artifacts-bootstrap-job.yaml)
created and restricted a separate `clouddsp-basic-pitch` identity. It can read
only `stems/*` and can upload/verify multipart-safe `midi/*` objects; it cannot
list or delete objects, access `uploads/*`, administer MinIO, or issue browser
presigned URLs. The bootstrap's temporary root credential Secret was deleted;
only the permanent app-namespace runtime Secret remains.

The completed
[`RabbitMQ consumer bootstrap Job`](kubernetes/services/rabbitmq/rabbitmq-basic-pitch-consumer-bootstrap-job.yaml)
created an independent untagged `clouddsp-basic-pitch` broker user. In the
`/clouddsp` vhost it has empty configure/write permissions and read access only
to `clouddsp.basic-pitch.requests`; it cannot alter topology, publish, read a
retry/DLQ queue, or use the management API. Its temporary data-namespace
bootstrap Secret was deleted, while the permanent app-namespace runtime Secret
remains.

The completed
[`Basic Pitch AMQP connection boundary`](kubernetes/services/basic-pitch/app/amqp_connection.py)
now accepts only that restricted runtime identity and the private RabbitMQ
ClusterIP AMQP listener at
`clouddsp-rabbitmq.clouddsp-data.svc:5672`, vhost `/clouddsp`, and the fixed
`clouddsp.basic-pitch.requests` queue. It rejects endpoint/management-port/
vhost/queue/identity widening from the environment and direct dataclass use,
hides the password, bounds connection attempts/timeouts/heartbeats, and lazily
imports Pika. The local profile has a plain private AMQP listener rather than
an AMQPS listener, so the reviewed configuration deliberately passes no TLS
option. Opening the connection creates no channel, queue action, delivery,
acknowledgement, MinIO call, model process, or Kubernetes change.

The completed
[`Basic Pitch AMQP passive-channel boundary`](kubernetes/services/basic-pitch/app/amqp_channel.py)
now sets `prefetch_count=1` and uses only a passive declaration for
`clouddsp.basic-pitch.requests`. This bounds one CPU worker to one
unacknowledged candidate at a time and verifies the topology bootstrap's queue
without letting the restricted identity create, bind, delete, or alter it. A
Pika channel/topology failure becomes one safe retryable category; the boundary
still receives no delivery, acknowledges/retries nothing, and calls no
PostgreSQL, MinIO, model, or Kubernetes API.

The new pure
[`Basic Pitch MinIO client boundary`](kubernetes/services/basic-pitch/app/minio_client.py)
reads only the permanent restricted S3 Secret and fixed future-Pod values for
the private MinIO ClusterIP Service, `clouddsp-uploads`, `us-east-1`, and
path-style addressing. Its lazy Boto3 factory supplies those credentials
directly with bounded timeouts/retries, so it does not use ambient AWS
providers, host profiles, an Ingress route, or any network call during
configuration. It deliberately does not read an object; the next isolated
task is a claim-bound `HeadObject` verifier that compares the exact stem's
MinIO metadata with the already-validated request and durable lease.

The completed pure
[`Basic Pitch stem HeadObject verifier`](kubernetes/services/basic-pitch/app/stem_object.py)
now makes exactly that one metadata-only MinIO request after revalidating both
the task lease and the persistent delivery identity. It accepts only the
claimed `stems/{job_id}/{stem_name}.wav` key and compares its current WAV
content type, byte length, and complete versioned Demucs metadata—job, stem,
mode, size, and SHA-256—with the durable evidence. Missing input is bounded
permanent history; MinIO/client outages remain retryable; malformed metadata
is a safe protocol failure. It does not download bytes, alter task state,
acknowledge RabbitMQ, invoke the model, or persist MIDI. A later isolated
streaming-download adapter must compare the actual bytes with this evidence.

The completed pure
[`Basic Pitch stem download adapter`](kubernetes/services/basic-pitch/app/stem_download.py)
now makes one matching `GetObject` request only after revalidating the
HeadObject evidence. It bounds a single stem to 256 MiB, compares GetObject's
current type/length headers, hashes the streaming bytes in 64 KiB chunks, and
yields a generic `stem.wav` only while its private random scratch child exists.
Every success/failure/exception path removes that child. The next separate
task is a PostgreSQL lease-token-guarded `leased → running` transition; model
execution must not begin on an expired or superseded task lease.

The completed pure
[`Basic Pitch task-start adapter`](kubernetes/services/basic-pitch/app/task_lease.py)
now makes that one parameterized `leased → running` statement. It binds the
task/Job/stage/stem/token identity and requires `lease_expires_at` to be later
than PostgreSQL's current time; zero returned rows are a normal stale-owner
stop signal. It preserves the first start timestamp across recovery attempts,
and has no database connection, transaction, MinIO, model, RabbitMQ, or
Kubernetes responsibility. A later isolated PostgreSQL composition task must
commit this decision before a Basic Pitch process may begin.

The completed restricted
[`Basic Pitch PostgreSQL client`](kubernetes/services/basic-pitch/app/postgresql.py)
now provides that short dictionary-row transaction scope. It accepts only the
private PostgreSQL Service/port, the authoritative job database, and the
existing least-privilege Basic Pitch login, while hiding the mounted password
from normal representations. The lazy Psycopg import, three-second connect
timeout, and five-second server-side statement timeout bound the dependency;
normal context exit commits and exceptions roll back. It does not claim/start a
task, contact MinIO/RabbitMQ, run Basic Pitch, or create a Kubernetes resource.
The next isolated composition task will commit the guarded start decision
before exposing downloaded-stem evidence to a future model runner.

The completed
[`Basic Pitch verified-stem task-start composition`](kubernetes/services/basic-pitch/app/stem_task_start.py)
now preserves that boundary in one context manager. It downloads/hashes the
exact claimed stem while the task is `leased`, commits the same-token guarded
start statement in the restricted short PostgreSQL transaction, and yields the
generic temporary WAV only after the commit. Ownership loss removes the local
stem before yielding `None`; database/protocol exceptions propagate only after
the download scope cleans up. It does not invoke Basic Pitch, upload MIDI,
acknowledge RabbitMQ, renew a lease, or mutate a result.

The completed
[`Basic Pitch process adapter`](kubernetes/services/basic-pitch/app/basic_pitch_process.py)
now creates one fresh mode-0700 local output directory beside the verified
temporary stem and runs only the fixed shell-free Basic Pitch CLI. It supplies
no stdin, retains no child output, starts a private process group, and bounds
the CPU execution to five minutes by default (ten minutes maximum). A zero exit
returns only the deterministic local `stem_basic_pitch.mid` plan. It does not
call MinIO/PostgreSQL/RabbitMQ or update Kubernetes state.

The completed
[`Basic Pitch MIDI artifact verifier`](kubernetes/services/basic-pitch/app/midi_artifact.py)
now revalidates that exact fixed command/output coordinate, opens only a
non-symlink regular local MIDI file, limits it to 16 MiB, and verifies its
Standard MIDI File header plus exact declared track-chunk layout while streaming
a SHA-256 digest in 64 KiB pieces. Its MIME type, byte count, and checksum are
only temporary local evidence; it does not upload an object, update PostgreSQL,
acknowledge RabbitMQ, or create/update Kubernetes state.

The completed
[`Basic Pitch MIDI output-object contract`](kubernetes/services/basic-pitch/app/midi_output_object.py)
now joins the revalidated local MIDI evidence with the matching durable request
and task-lease identity. It maps the result only to the stable private
`midi/{job_id}/{stem_name}.mid` coordinate and attaches immutable provenance:
schema/producer, Job/task/request IDs, stem/mode, MIDI size/SHA-256, and input
stem SHA-256. It repeats the local MIDI verification to reject an old proof of
a same-size byte replacement. It does not call MinIO, mutate PostgreSQL,
acknowledge RabbitMQ, or create/update Kubernetes state.

The completed
[`Basic Pitch MIDI MinIO uploader`](kubernetes/services/basic-pitch/app/midi_artifact_upload.py)
now revalidates the fixed private object plan and all provenance metadata,
repeats local MIDI framing/hash proof, hashes the current file before upload,
and streams it through exactly one private `PutObject` request while calculating
a second digest. It returns a receipt only if the S3-compatible client consumed
the planned byte count and SHA-256, never trusting a raw ETag. It does not
mutate PostgreSQL, acknowledge RabbitMQ, or create/update Kubernetes state.

The completed
[`Basic Pitch stored-MIDI verifier`](kubernetes/services/basic-pitch/app/midi_artifact_head_object.py)
now makes one private `HeadObject` request for the fixed upload receipt key and
requires MinIO's current byte count, `audio/midi` type, and complete normalized
provenance metadata to agree with the immutable output plan. Definite absence,
size/type/metadata mismatch use bounded result codes; an unavailable MinIO
dependency remains retryable. It returns only bucket/key/size/SHA-256 evidence
and does not read MIDI bytes, mutate PostgreSQL, acknowledge RabbitMQ, or
create/update Kubernetes state.

The completed
[`Basic Pitch task-completion SQL adapter`](kubernetes/services/basic-pitch/app/midi_task_completion.py)
now validates that stored MIDI proof against the exact leased Job/stem output
coordinate and issues one parameterized `running → succeeded` statement. The
predicate binds task/Job/stem/input/mode/lease token and PostgreSQL's current
time, so expiry, recovery, deletion, another terminal transition, or a changed
coordinate produce a normal no-row ownership-loss result. Success clears active
lease fields and records PostgreSQL's completion time for only that per-stem
task; it intentionally does not alter the overall `midi_processing` Job.
It opens no transaction/connection and does not acknowledge RabbitMQ, touch
MinIO, invoke Basic Pitch, or create/update Kubernetes state.

The completed
[`Basic Pitch completion transaction composition`](kubernetes/services/basic-pitch/app/midi_task_completion_commit.py)
now supplies that restricted short PostgreSQL scope. It calls the pure
lease-token-guarded completion statement only after all CPU and MinIO work has
finished, and exposes a non-`None` completion only after normal context exit
commits. A no-row stale-owner result commits no mutation; any database or
evidence exception rolls back. It does not call MinIO/RabbitMQ/Basic Pitch,
update the overall Job, or create/update Kubernetes state. The next isolated
task was the post-claim execution coordinator.

The completed
[`Basic Pitch post-claim execution coordinator`](kubernetes/services/basic-pitch/app/basic_pitch_task_execution.py)
now joins the already-reviewed runtime boundaries in their only permitted
order, but receives no AMQP frame and owns no RabbitMQ acknowledgement. After
a future parser/first-claim layer has committed and acknowledged a `claimed`
lease, it verifies the exact private stem, uses the temporary stem scope to
commit `leased → running`, runs and validates the fixed Basic Pitch command,
plans/uploads/HeadObject-verifies MIDI, and commits the per-task completion.
The short PostgreSQL transactions remain outside MinIO and CPU work; a normal
lease loss stops the sequence, while dependency/process errors propagate for a
future retry/recovery supervisor.

The first pure
[`Basic Pitch task-claim adapter`](kubernetes/services/basic-pitch/app/task_lease.py)
now defines the narrow transaction that converts one already-parsed delivery
into a durable per-stem lease. It locks the exact task, locks the Job, repeats
the task lookup to close a concurrent first-claim race, then checks the
retained `midi_processing` Job/stem-mode coordinate and all durable outbox
payload evidence before inserting a PostgreSQL-clocked `leased` row. A matching
existing task is a read-only duplicate; missing, expired, or terminal Jobs are
safe stale history; a disagreement raises rather than being acknowledged. It
makes no PostgreSQL connection, RabbitMQ acknowledgement, MinIO call, model
invocation, image, or Kubernetes action. Start/renew/recovery, object
verification, model execution, output evidence, and completion remain separate
follow-up tasks.

The completed
[`Basic Pitch first-claim transaction composition`](kubernetes/services/basic-pitch/app/first_claim.py)
now supplies the adapter's one restricted, short PostgreSQL `write_cursor()`
scope. It exposes the pure `claimed`, duplicate, or stale result only after a
normal transaction commit, so a future broker layer can make its manual
acknowledgement decision afterwards. Durable inconsistency, malformed evidence,
or a database outage instead leaves the context exceptionally and rolls back;
the delivery is not accidentally made safe. The composition has no AMQP frame,
delivery tag, MinIO/model call, retry loop, or Kubernetes behavior. The next
isolated task is the parser-plus-first-claim bridge described below, before an
acknowledgement adapter or consumer loop is introduced.

The completed
[`Basic Pitch delivery-claim bridge`](kubernetes/services/basic-pitch/app/delivery_claim.py)
now parses exactly one raw `basic-pitch.requested` envelope/properties/body
contract before it calls the committed first-claim composition. It returns only
the parsed request identifiers and PostgreSQL's `claimed`, duplicate, or stale
result—no raw body, AMQP properties, delivery tag, broker client, cursor, or
credentials. A malformed request cannot touch PostgreSQL, while a database
outage propagates without any transport action. This boundary imports no Pika,
acknowledges/retries nothing, and calls no MinIO/model/Kubernetes component.

The completed
[`Basic Pitch manual-ack AMQP adapter`](kubernetes/services/basic-pitch/app/amqp_manual_ack.py)
now reads at most one `basic_get(..., auto_ack=False)` delivery from the fixed
private queue. It calls the bridge, then acknowledges only a committed new
lease, duplicate, or stale result; an `ACKNOWLEDGED_LEASE` result is the sole
normal result that carries execution authority and its same parser-validated
message. A permanent malformed contract is nacked without requeue to the
topology-configured DLQ. Database/durable-identity/claim-shape errors and
failed receive/ack/nack actions do not become a successful broker decision,
preserving at-least-once redelivery. This Pika-shaped adapter starts no loop
and calls no MinIO/model/Kubernetes component.

The completed
[`Basic Pitch acknowledged-lease execution gate`](kubernetes/services/basic-pitch/app/acknowledged_lease_execution.py)
now invokes the existing post-claim coordinator only when the manual-ack
adapter produced `ACKNOWLEDGED_LEASE` with both the committed lease and strict
request message. All idle/no-work/DLQ outcomes stop before MinIO or CPU work;
coordinator exceptions propagate without any second acknowledgement decision.
The gate accepts no channel/delivery tag, opens no transaction itself, and
creates no Kubernetes state.

The completed
[`Basic Pitch single worker iteration`](kubernetes/services/basic-pitch/app/receive_execute_once.py)
now joins exactly one manual-ack receive decision with the post-ack execution
gate. It returns only `idle`, acknowledged-no-work, malformed-rejected, or an
executed coordinator result; only the acknowledged lease path reaches MinIO/
CPU work. Receive/acknowledgement/database/storage/model errors remain
exceptions, preserving a future supervisor's ability to reconnect and retry
without inventing a successful result. It deliberately has no loop, sleep,
connection lifecycle, recovery scan, image entrypoint, or Kubernetes behavior.
The completed pure
[`Basic Pitch supervisor backoff policy`](kubernetes/services/basic-pitch/app/supervisor_backoff.py)
now maps only explicit runtime events to a next action: ordinary progress
continues immediately, an empty broker poll waits one second, a retryable
failure follows a bounded `1, 2, 4, 8, 16, 30`-second exponential sequence
with up to 25% supplied jitter, and fatal configuration exits. It retains only
a capped local retry-failure count and resets it on any normal iteration. It
contains no sleep, random-number source, error classification, connection,
worker loop, image, Deployment, or cluster behavior. The following isolated
task established the pre-model terminal-failure boundary that a later exception
classifier and interruptible long-running runtime will use.

The completed pure
[`Basic Pitch pre-model terminal-failure adapter`](kubernetes/services/basic-pitch/app/stem_task_terminal_failure.py)
now guards the permanent input-validation path before any model work begins.
It accepts only a current Basic Pitch ``leased`` task and one finite safe code
for a missing, size/type/metadata-mismatched, or streamed-checksum-mismatched
Demucs stem. Its parameterized SQL atomically changes that task to ``failed``,
clears its lease, records PostgreSQL's completion time, and preserves the Job's
``midi_processing`` state for a later aggregate. A stale token, expiry, state
change, or recovery race returns no result and cannot overwrite another
outcome. It has no connection, transaction, exception catch, AMQP action,
MinIO operation, worker loop, image, Deployment, or cluster behavior. The next
isolated task added the short transaction composition and classifier handoff.

The completed
[`Basic Pitch terminal-failure commit and classifier`](kubernetes/services/basic-pitch/app/stem_task_terminal_failure_commit.py)
now makes the pre-model outcome usable by a later runtime without widening its
authority. The composition returns only after the existing restricted
``write_cursor()`` commits the guarded ``leased → failed`` SQL result. Its
companion classifier recognizes only the existing permanent MinIO
``HeadObject`` validation categories and the streamed download-consistency
error; all MinIO outages, protocol errors, database errors, and model failures
remain unclassified for a later retry/fatal policy. These modules do not catch
errors around the coordinator, acknowledge RabbitMQ, or create a worker loop.

The completed
[`Basic Pitch acknowledged-lease terminal-result integration`](kubernetes/services/basic-pitch/app/acknowledged_lease_execution.py)
now handles the two reviewed pre-model categories after the coordinator raises:
a permanent input mismatch commits its guarded ``leased → failed`` record, and
a temporary stem-storage outage commits either ``retry_scheduled`` on attempts
one/two or terminal retry exhaustion on attempt three. A no-row commit race
becomes ownership loss. The RabbitMQ delivery remains acknowledged from the
earlier committed claim, so this gate does not retry or DLQ it directly; a
later recovery component will arrange a new delivery for durable scheduled
retries. Protocol errors, database/model failures, post-model MIDI-store
failures, and every other unclassified error still escape without a new broker
decision. This is not a worker loop, image, Deployment, or Kubernetes action.

The completed pure
[`Basic Pitch transient pre-model retry-scheduling adapter`](kubernetes/services/basic-pitch/app/stem_task_retry_schedule.py)
now gives a temporary stem-storage outage a durable outcome that is distinct
from a permanent mismatch. It accepts only a current unexpired ``leased``
Basic Pitch task with attempts remaining and the finite
``basic_pitch_stem_storage_unavailable`` code. Its parameterized SQL atomically
changes only that task to ``retry_scheduled``, clears the lease, and sets a
bounded PostgreSQL-clock ``available_at`` time; it does not change the Job,
set task completion, start the model, open a transaction, classify exceptions,
send a RabbitMQ message, run a loop, or create a Kubernetes resource. Its
no-row result safely covers ownership loss, expiry, a different task state, or
the final allowed attempt. A later narrowly scoped composition will classify
reviewed temporary dependency errors, commit the schedule, define the explicit
attempt-exhaustion outcome, and arrange re-delivery after the durable delay.

The completed
[`Basic Pitch retry-scheduling transaction composition`](kubernetes/services/basic-pitch/app/stem_task_retry_schedule_commit.py)
now wraps that pure statement in the existing restricted ``write_cursor()``
scope. It returns retry evidence only after PostgreSQL commits; a no-row
schedule remains a normal stop result, and an adapter/database error leaves the
scope exceptionally so it rolls back. It does not classify errors, decide the
exhaustion outcome, schedule a loop, contact MinIO/RabbitMQ, run Basic Pitch,
or create a Kubernetes resource. The next isolated task can classify the one
reviewed temporary storage failure and connect it to this commit boundary.

The completed
[`Basic Pitch transient storage classifier`](kubernetes/services/basic-pitch/app/stem_retry_classification.py)
now recognizes exactly the safe temporary MinIO wrappers produced before model
start: unavailable stem ``HeadObject`` and unavailable stem
``GetObject``/streaming download. Both map to the one finite durable storage
retry code; permanent mismatches, malformed protocol responses, database/model
errors, and post-model MIDI-store errors intentionally remain outside this
policy. The adjacent
[`classifier-to-commit handoff`](kubernetes/services/basic-pitch/app/stem_retry_handling.py)
now turns that finite mapping into an unclassified no-op, a committed retry
schedule on attempts one/two, a committed terminal exhaustion result on attempt
three, or an explicit no-row result. It does not catch around the execution
coordinator, create a recovery delivery, loop, image, Deployment, or Kubernetes
action. The two adjacent exhaustion boundaries below provide the third-attempt
terminal evidence used by this handoff.

The completed pure
[`Basic Pitch final-attempt storage-retry exhaustion adapter`](kubernetes/services/basic-pitch/app/stem_task_retry_exhaustion.py)
now defines that bounded terminal outcome. Only a current unexpired Basic Pitch
``leased`` task on attempt three may change to ``failed`` with the finite
``basic_pitch_stem_storage_retry_exhausted`` code; PostgreSQL records its
completion time and clears its lease. The transition is deliberately distinct
from a permanent input mismatch: its code says automatic storage retries were
exhausted, not that stem identity was invalid. It never changes ``jobs`` or
``started_at``, and it has no connection, transaction, exception catch,
MinIO/RabbitMQ action, worker loop, image, Deployment, or Kubernetes behavior.
The next isolated task is its short transaction composition.

The completed
[`Basic Pitch retry-exhaustion transaction composition`](kubernetes/services/basic-pitch/app/stem_task_retry_exhaustion_commit.py)
now wraps that guarded final-attempt statement in the existing restricted
``write_cursor()`` scope. It returns exhaustion evidence only after PostgreSQL
commits, preserves a no-row result as normal ownership loss, and lets an
adapter/database error leave the scope exceptionally for rollback. It does not
catch/classify worker errors, retry, contact MinIO/RabbitMQ, update the Job, or
create a worker loop, image, Deployment, or Kubernetes resource. The next
small task can combine the reviewed storage classifier with the normal
retry-schedule versus final-exhaustion choice.

The completed
[`Basic Pitch storage-failure attempt chooser`](kubernetes/services/basic-pitch/app/stem_retry_handling.py)
now makes that finite choice from the immutable lease evidence. A reviewed
storage outage on attempts one or two uses the guarded ``retry_scheduled``
transaction; the same outage on attempt three uses the guarded terminal
``retry_exhausted`` transaction instead. Its result distinguishes both
committed evidence types, an unclassified no-op that performed no SQL, and a
no-row guard miss without manufacturing a result. It still does not catch
around the execution coordinator or arrange RabbitMQ re-delivery. The completed
[`acknowledged post-lease gate`](kubernetes/services/basic-pitch/app/acknowledged_lease_execution.py)
now maps those committed outcomes into its own result type after the original
delivery has already been acknowledged. It does not create a worker
loop/image/Deployment/Kubernetes action. The next small task is durable
recovery: select due ``retry_scheduled`` tasks and arrange safe re-delivery.

The completed pure
[`Basic Pitch due-retry recovery claim`](kubernetes/services/basic-pitch/app/task_lease.py)
now atomically selects at most one due ``retry_scheduled`` Basic Pitch task,
uses ``FOR UPDATE SKIP LOCKED`` so replicas cannot wait on or duplicate a
candidate, increments the attempt count, and grants a fresh ``leased`` token.
It excludes final-attempt rows and expired active tasks: final storage failure
uses the reviewed exhaustion transition, while recovering possibly started
model work needs a separate policy. The claim has no connection, transaction,
outbox read, RabbitMQ publish, MinIO request, model process, worker loop,
image, Deployment, or Kubernetes behavior. A later small recovery composition
must reconstruct the strict durable request evidence and arrange safe work from
the committed recovery lease.

The completed pure
[`Basic Pitch recovery-request reader`](kubernetes/services/basic-pitch/app/recovery_request.py)
now provides that strict evidence reconstruction without treating an old
RabbitMQ message as current authority. It joins a fresh `leased` recovery task
to its matching immutable `published` Basic Pitch outbox event, binds every
task/lease coordinate plus PostgreSQL's current expiry time, and recreates the
same narrow request object used by the ordinary AMQP path. Its exact JSONB
shape/type/size/checksum and fixed private stem coordinate checks reject any
inconsistent row; a no-row result is normal ownership loss. It is read-only:
it claims no work, opens/commits no transaction, publishes no broker message,
and touches no MinIO/model/Kubernetes component.

The completed
[`Basic Pitch due-retry recovery composition`](kubernetes/services/basic-pitch/app/due_retry_recovery.py)
now places that due-retry claim and evidence reader inside exactly one
restricted PostgreSQL transaction. It returns a lease/request pair only after
normal context exit commits both facts. An empty indexed claim commits normally
as idle recovery; an impossible missing event after a successful claim raises
and rolls the fresh lease back rather than stranding work. This narrow boundary
does not poll/acknowledge/publish RabbitMQ, call MinIO, invoke the model, sleep,
run a worker loop, or change Kubernetes. The next isolated task can pass its
committed pair into the existing pre-model executor and apply the reviewed
terminal/retry handling without creating a second broker delivery.

The completed
[`Basic Pitch recovered-retry execution gate`](kubernetes/services/basic-pitch/app/recovered_retry_execution.py)
now performs that handoff without inventing a new RabbitMQ request. It accepts
only the committed retry lease/request pair and invokes the shared post-lease
execution policy used by the acknowledged normal-delivery path. Therefore the
same guarded preflight/model start/MIDI completion ordering and finite
permanent-failure versus storage-retry outcomes apply to every attempt. The
gate makes no broker acknowledgement/publish decision and does not directly
claim work, access MinIO, run the model, open a transaction, sleep, loop, or
change Kubernetes.

The completed pure
[`Basic Pitch fair work-source policy`](kubernetes/services/basic-pitch/app/work_schedule.py)
now alternates each bounded selection between a normal RabbitMQ delivery and a
due PostgreSQL retry-recovery claim. It advances its small local preference
even when the selected source is idle, allowing the other source an immediate
fair fallback before the existing supervisor applies an idle delay. The
preference is disposable on Pod restart and does not replace RabbitMQ message
or PostgreSQL task authority. It polls neither source, sleeps, opens no
connection/transaction, runs no model, and changes no Kubernetes state. The
next isolated task is a bounded runtime iteration that obeys this policy and
reports whether it found normal work or needs the second source/idle decision.

The completed
[`Basic Pitch fair work-source iteration`](kubernetes/services/basic-pitch/app/work_source_iteration.py)
now performs that composition at most twice per call: it checks the selected
RabbitMQ or due-retry source once, and checks the other source only when the
first was idle. Only two empty checks return `idle`; every normal delivery or
recovered task produces compact `progress` evidence and the next fair state.
It catches no broker/database/MinIO/model failure, holds no cross-boundary
transaction, and has no loop, sleep, connection lifecycle, or Kubernetes
action. The next isolated task is to adapt the existing supervisor event and
backoff policy to this two-source normal result.

The completed
[`Basic Pitch fair-iteration supervisor classifier`](kubernetes/services/basic-pitch/app/supervisor_backoff.py)
now maps the two-source normal result into the existing bounded backoff events.
Only a fair result that confirmed both sources empty becomes `iteration_idle`;
every compact normal broker or recovery result becomes immediate progress and
resets the local failure streak. It has no loop, sleep, source polling,
connection lifecycle, exception classification, or Kubernetes behavior. The
next isolated task is an explicit real-exception classifier for retryable
runtime faults versus fatal worker configuration.

The completed pure
[`Basic Pitch supervisor failure classifier`](kubernetes/services/basic-pitch/app/supervisor_failure_classification.py)
now maps only known safe outer-worker conditions to existing backoff events.
Static AMQP/PostgreSQL/MinIO configuration or a missing worker executable is
fatal; bounded AMQP connection/channel/receive and PostgreSQL availability
wrappers are retryable. Task-specific storage/model/MIDI/protocol/integrity and
unknown failures intentionally remain unclassified, because generic restart
would not supply their required durable task outcome. It opens no connection,
catches nothing, sleeps, loops, or changes Kubernetes. The next isolated task
is a single runtime-decision composition over one fair iteration or one
classified exception; loop/sleep/reconnect behavior remains separate.

The completed
[`Basic Pitch supervisor step`](kubernetes/services/basic-pitch/app/supervisor_step.py)
now joins one fair iteration (or one classified worker-level exception) to the
existing supervisor decision and carries both round-robin and bounded-backoff
state forward. Normal progress/verified two-source idle advance fair state;
retryable or fatal exceptions retain the previous fair preference because an
attempt did not complete normally. Task-specific unclassified failures still
escape. This is not a loop: it does not sleep, reconnect, close channels, or
change Kubernetes. The next isolated task is an injectable interruptible
wait/action boundary for a later actual worker loop.

The completed
[`Basic Pitch supervisor action boundary`](kubernetes/services/basic-pitch/app/supervisor_action.py)
now applies one existing decision through an injected shutdown-aware waiter.
It calls that waiter exactly once for bounded idle/backoff delays, continues
when the timeout expires, returns clean-stop when shutdown interrupts it, and
returns fatal exit without waiting. It has no `sleep`, loop, reconnect,
resource closure, model, or Kubernetes action. The next isolated task is a
real worker entrypoint composition with explicit broker/resource lifecycle and
shutdown behavior.

The completed
[`Basic Pitch worker runtime`](kubernetes/services/basic-pitch/app/worker_runtime.py)
now owns the broker-session lifecycle. With already-constructed restricted
dependencies, it checks shutdown before opening RabbitMQ, creates one
prefetched/passively verified channel, runs supervisor steps/actions until
clean shutdown or fatal configuration, and closes every post-open session on
normal or exceptional paths. A classified retryable failure closes its session
*before* bounded backoff, releasing any unacknowledged `prefetch=1` delivery
for RabbitMQ redelivery; it then creates a fresh connection/channel after the
delay. This fixes a temporary database failure that previously could strand a
delivery on the old channel. It does not alter PostgreSQL grants, task SQL,
RabbitMQ topology, or Kubernetes resources. It deliberately does not construct
environment-backed clients, install signals, or call `sys.exit`.

The completed
[`Basic Pitch worker bootstrap entrypoint`](kubernetes/services/basic-pitch/app/worker_entrypoint.py)
now builds only the validated mounted AMQP/PostgreSQL/MinIO dependencies,
requires the fixed pre-mounted `/worker-scratch` directory, delegates to the
runtime, and maps clean shutdown to exit `0` or runtime fatal configuration to
`78`. It does not catch configuration errors, call `sys.exit`, install signals,
reconnect, or change Kubernetes. The next isolated task is a minimal
executable wrapper that installs SIGTERM/SIGINT shutdown handling and returns
the bootstrap's explicit status.

The completed
[`Basic Pitch executable worker wrapper`](kubernetes/services/basic-pitch/app/worker_main.py)
now installs one cooperative SIGTERM/SIGINT event handler, adapts that event
to the runtime's shutdown-wait protocol, and returns the bootstrap's explicit
process status. Its handler performs only `Event.set()`; graceful connection
closure and durable work behavior stay in normal runtime code. Known static
bootstrap configuration faults return non-sensitive status `78`, while
task-specific and unexpected runtime errors still propagate after their
existing cleanup. The next isolated task is to select this module in the
Basic Pitch image command; this task has not built an image or changed a
Kubernetes Deployment.

The completed
[`Basic Pitch dependency lock`](kubernetes/services/basic-pitch/requirements.lock)
now pins the full Linux/ARM64/Python 3.11 wheel closure for Basic Pitch 0.4.0,
its TensorFlow CPU inference runtime, audio/MIDI libraries, and the restricted
MinIO/RabbitMQ/PostgreSQL clients. Basic Pitch's Linux TensorFlow 2.15 runtime
requires Python 3.11; the dedicated immutable Python 3.11.16 base-image record
and future Basic Pitch build provenance now live in
[`images.lock.yaml`](kubernetes/images.lock.yaml). The Linux/ARM64 TensorFlow
selector names its CPU binary `tensorflow-cpu-aws`; that package name does not
add AWS connectivity or credentials. No Dockerfile, built image, image digest,
or Deployment exists yet. The next isolated task is a two-stage Dockerfile
that installs this hash-verified lock and explicitly starts
`python -m app.worker_main`.

The completed
[`Basic Pitch Dockerfile`](kubernetes/services/basic-pitch/Dockerfile) now
builds the local Linux/ARM64 CPU worker from that separate immutable Python
3.11.16 base. Its temporary validation stage installs every lock entry with
hash enforcement, runs worker unit tests, requires the fixed Basic Pitch CLI,
and proves the wheel carries a TFLite model so a live Pod cannot fetch it on
demand. The final stage copies only validated packages, the fixed executable,
and application source; it runs as UID/GID `10004`, intentionally does not
create `/worker-scratch`, and uses `python -m app.worker_main` as its
exec-form/PID-1 entrypoint for cooperative SIGTERM handling. The Dockerfile
does not itself add a reviewed registry-digest lock or Kubernetes Deployment;
the adjacent completed build-and-push script supplies the separate publication
boundary.

The completed
[`Basic Pitch build-and-push script`](kubernetes/scripts/build-basic-pitch-image.sh)
now validates its local Docker/k3d prerequisites, rebuilds the Linux/ARM64 CPU
image through that Dockerfile, pushes only to the dedicated local registry,
and prints its immutable repository digest and uncompressed local image size.
It never invokes `kubectl`, changes `images.lock.yaml`, or creates a workload.

The completed
[`Basic Pitch image lock record`](kubernetes/images.lock.yaml) now binds the
published ARM64 CPU image's readable provenance tag to its exact local-registry
digest, source/Dockerfile/dependency locks, fixed PID-1 entrypoint, bundled
TFLite-model evidence, and Docker-reported uncompressed size. It makes the
image eligible for a later manifest while explicitly leaving the worker
undeployed: no Pod, Service, Secret, queue action, database change, or
Kubernetes controller was created by this record. The adjacent prepared
Deployment manifest uses only this immutable reference but remains unapplied.

The prepared
[`Basic Pitch Deployment manifest`](kubernetes/services/basic-pitch/basic-pitch-deployment.yaml)
now defines that controller without applying it. It starts one internal-only,
CPU-bounded worker from `images.basic-pitch`'s immutable registry digest;
mounts only its three app-namespace least-privilege Secrets; uses fixed private
service DNS; and creates no Service, Ingress, ServiceAccount token, CUDA
request, or Kubernetes API authority. Its read-only root filesystem retains
only bounded `emptyDir` locations for the fixed worker scratch path, temporary
library files, and non-root HOME. The rollout avoids a temporary two-worker
CPU overlap and allows 330 seconds for a five-minute bounded CLI process to
finish after SIGTERM. The next isolated task is a preflight and deliberate
application of this one manifest, followed by inspection—not a new worker
feature or scaling policy.

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

The source-controlled [`Basic Pitch worker smoke contract`](kubernetes/tests/basic-pitch-worker-smoke/README.md)
now reserves one fixed Job/event, synthetic upstream-Demucs task provenance,
and exact MinIO input/output coordinates for a future real-worker verification.
It specifies that a restricted test client first writes a deterministic
contract-valid synthetic Demucs WAV, then uses a fixed-scope PostgreSQL prepare
function to create the durable outbox event. The already deployed dispatcher
and Basic Pitch worker—not the test—own RabbitMQ publication/consumption and
the worker creates its normal per-stem task. Success requires that durable
task to be `succeeded` and private MIDI to be independently verified; a failed
run preserves only its fixed evidence for diagnosis. This documentation task
creates no runtime identity, image, Job, database object, broker message, or
cluster resource.

The Basic Pitch worker's first durable claim requires a PostgreSQL row lock on
the authoritative Job to serialize first-task insertion with terminal or
deletion transitions. PostgreSQL requires `UPDATE` privilege for every
`SELECT … FOR UPDATE` form, even when the caller makes no update. Rather than
widening the worker's direct `jobs` authority, the revised database bootstrap
creates one administrator-owned, typed `SECURITY DEFINER` function:
`public.clouddsp_lock_basic_pitch_job_for_claim(uuid)`. It locks only the
requested row and returns only its existing five-field claim projection. The
Basic Pitch role receives only `EXECUTE` on that exact signature; direct Job
`UPDATE` remains denied. The adapter now calls the function inside its existing
short claim transaction, preserving the same task recheck and idempotency race
closure. This source-and-manifest change has not yet been applied to the local
cluster or worker image.

The prepared, unapplied [`Basic Pitch worker smoke database bootstrap Job`](kubernetes/tests/basic-pitch-worker-smoke/basic-pitch-worker-smoke-database-bootstrap-job.yaml)
adds the contract's PostgreSQL boundary without granting its future test client
table access. It creates a distinct no-membership login and exactly three
fixed-coordinate `SECURITY DEFINER` functions: `prepare` commits only the
reserved Job/outbox event after the synthetic WAV exists; `observe` exposes
only publication and per-task lifecycle evidence; and `cleanup` can delete
only an observed successful test Job after object cleanup. The actual worker
still creates its normal task after consuming the event. Adjacent ignored
Secret templates keep the app client credential separate from the short-lived
data-namespace bootstrap copy. Local copies of those two Secrets and the
bootstrap Job were applied successfully: its safe privilege report confirms no
direct Job/outbox/task table access and execute access to all three functions.
It created no smoke Job/event or broker message.

The next prepared, unapplied object boundary is the immutable
[`Basic Pitch worker smoke MinIO policy`](kubernetes/tests/basic-pitch-worker-smoke/basic-pitch-worker-smoke-minio-policy-v001-configmap.yaml)
and its matching bootstrap Job. It creates a distinct test identity with
`GetObject`/`PutObject`/`DeleteObject` authority over exactly the reserved WAV
and MIDI keys—no bucket listing, presigning, normal upload/stem/MIDI prefixes,
or MinIO administration. The Job uses MinIO root credentials only in ordered
init containers, removes its temporary root alias before completion, and never
touches PostgreSQL, RabbitMQ, a model, or an object. Its ignored app/data
Secret templates were applied locally; the Job completed successfully, created
the fixed-key policy/user association, and the temporary data-namespace Secret
was deleted. Only the permanent app-namespace credential remains.

The new unit-tested [`Basic Pitch worker smoke client`](kubernetes/tests/basic-pitch-worker-smoke/client/basic_pitch_worker_smoke.py)
uses only those fixed PostgreSQL functions and the exact-key MinIO identity.
It deterministically creates a small WAV, uploads complete Demucs-compatible
metadata, waits for a real worker-created task to reach one-attempt success,
independently verifies the stored MIDI framing/hash/provenance, then removes
only its two objects before fixed-scope database cleanup. It receives no AMQP
credential or table authority and preserves durable evidence on every
post-prepare failure. The source-only task includes eight isolated fake-client
tests; it creates no image, smoke Job, object, database event, or message.

The adjacent [`Basic Pitch worker smoke dependency lock`](kubernetes/tests/basic-pitch-worker-smoke/client/requirements.lock)
now pins the smoke client's entire CPython 3.12/Linux ARM64 runtime closure:
Boto3/Botocore and their S3 support packages plus Psycopg's binary libpq
wheel. It intentionally excludes RabbitMQ, Keycloak, web, Kubernetes, audio,
ML, and GPU dependencies because the test must use the already-deployed
dispatcher and worker rather than recreate their responsibilities. A
`pip download --require-hashes` resolution fetched exactly the ten locked
wheels and verified every downloaded SHA-256 against the lock. The new
two-stage non-root [`smoke-client Dockerfile`](kubernetes/tests/basic-pitch-worker-smoke/client/Dockerfile)
installs that lock and runs the eight isolated tests only in its disposable
validation stage. Its runtime retains one client module and verified packages,
uses UID/GID `10003`, and has no baked Secret or listener. The matching
non-interactive [`build script`](kubernetes/scripts/build-basic-pitch-worker-smoke-client-image.sh)
targets only the local ARM64 registry and prints the immutable digest required
for an image-lock record. That script built the image successfully, ran all
eight tests in its validation stage, and published
`clouddsp-registry.localhost:5001/basic-pitch-worker-smoke-client` as
[`sha256:c37acb0177e213cdecff061057f1f91e4ecf554bf2f52f52a5d6f8b8c62f72d1`](kubernetes/images.lock.yaml).
The image's uncompressed local size is 66,125,383 bytes (63.06 MiB); its pinned
record is now available for a future Job. No Pod, Job, object, database event,
or message was created. The prepared, unapplied
[`Basic Pitch worker smoke Job`](kubernetes/tests/basic-pitch-worker-smoke/basic-pitch-worker-smoke-job.yaml)
uses that pinned image in `clouddsp-app` with only the already-applied
function-scoped PostgreSQL and exact-key MinIO Secrets. Its five-minute outer
deadline bounds the client's 180-second durable poll; `backoffLimit: 0`
preserves failed evidence for review, and the TTL later removes a completed
credential-bearing Pod. It receives neither an AMQP credential nor a Kubernetes
service-account token, uses no volume, and creates no Service/Ingress. The
manifest remains unapplied: the next separate task is to review its execution
and inspection/cleanup commands before the first controlled run.

The prepared, unapplied `dispatcher-normal-path-smoke` Job runs in
`clouddsp-data` and executes the single locked smoke-client process. It mounts
existing local Keycloak/PostgreSQL/MinIO administrator Secrets only to create
and remove its disposable test resources, while a separately applied temporary
data-namespace copy of the restricted RabbitMQ reader Secret allows AMQP
verification. That copy must use the existing smoke username/password and be
deleted once the Job finishes; neither runtime dispatcher nor upload-intake
Pod receives these administrator credentials.

### ADTOF worker foundation

The new versioned [`ADTOF worker contract`](kubernetes/services/adtof/README.md)
records the Kubernetes-local drum-transcription boundary before a database
migration, runtime identity, image, Deployment, smoke Job, or KEDA resource is
introduced. It accepts only the existing v004 `adtof.requested` delivery for
the `drums` stem, creates or recovers only the durable
`(job_id, 'adtof', 'drums')` task coordinate, verifies the private Demucs WAV
through MinIO metadata and a streamed SHA-256 before inference, and writes only
the stable `midi/{job_id}/drums.mid` and
`midi/{job_id}/drums_bpm.json` coordinates. A guarded completion will preserve
the cloud-compatible `jobs.midi.drums` record while leaving the Job in
`midi_processing` for a later all-task aggregate. This keeps RabbitMQ
at-least-once delivery, PostgreSQL leases, private-object integrity, and the
cloud tempo-candidate behaviour aligned without importing the cloud Lambda
handler into Kubernetes.

The contract deliberately defers KEDA until the worker has a measured image
size, CPU/memory envelope, timeout, and retry behaviour. The applied
[`v006 ADTOF processing-task ConfigMap`](kubernetes/services/api/job-api-schema-migration-v006-adtof-processing-tasks-configmap.yaml)
and completed [`migration Job`](kubernetes/services/api/job-api-schema-migration-v006-adtof-processing-tasks-job.yaml)
extend only the `processing_tasks` stage/stem/key check for ADTOF drums. They
use the existing Job API schema-owner Secret and preserve all earlier Demucs/
Basic Pitch task rows, but create no worker identity, storage/broker access,
image, Deployment, KEDA resource, or task row. The prepared separate
[`ADTOF PostgreSQL bootstrap Job`](kubernetes/services/adtof/adtof-database-bootstrap-job.yaml)
completed successfully and provisioned the restricted `clouddsp-adtof` runtime
role; its temporary data-namespace Secret was then removed. The applied
immutable [`ADTOF MinIO policy`](kubernetes/services/minio/minio-adtof-artifacts-policy-v001-configmap.yaml)
and completed separate
[`bootstrap Job`](kubernetes/services/minio/minio-adtof-artifacts-bootstrap-job.yaml)
provisioned the restricted `clouddsp-adtof` S3 identity. They permit only reading one
private Demucs drums WAV and writing/verifying the two deterministic ADTOF
drum-MIDI/tempo objects; no list, delete, browser presign, bucket access, or
other worker identity is included. The completed independent RabbitMQ identity
can consume only the existing `clouddsp.adtof.requests` queue and acknowledge
its deliveries. Its separate runtime/temporary-bootstrap Secret templates and
bootstrap Job grant no broker
topology, publishing, retry/DLQ, or management-console access; only an exact
vhost `read` regex was created by the completed bootstrap Job and its temporary
data-namespace Secret was removed. The pure
[`ADTOF request parser`](kubernetes/services/adtof/app/adtof_requested_message.py)
now repeats that route/AMQP/body/object contract at the worker boundary before
any database, MinIO, RabbitMQ-client, ADTOF, or Kubernetes API action can
occur. Its standard-library tests reject malformed envelopes, AMQP properties,
JSON, non-drums stems, private-object coordinates, sizes, and checksums.

The pure [`ADTOF PostgreSQL first-claim adapter`](kubernetes/services/adtof/app/task_claim.py)
now locks and rechecks the canonical drums task, narrow Job state, and immutable
published outbox evidence before inserting one `leased` task. It returns only
`claimed`, `duplicate`, or `stale` durable facts; it neither opens a database
connection nor acknowledges RabbitMQ, accesses MinIO, starts ADTOF, changes Job
state, creates output, or reaches Kubernetes. Its tests cover an initial claim,
redelivery, stale Job, concurrent-insertion race, conflict, and direct-object
validation.

The new [`ADTOF first-claim transaction composition`](kubernetes/services/adtof/app/first_claim.py)
now holds the restricted database write context only around the pure first-claim
SQL decision. Claim, duplicate, and stale facts return only after commit; a
conflict or database exception rolls back before any future RabbitMQ decision.
It has no Pika, MinIO, ADTOF, Psycopg-driver, image, Deployment, or Kubernetes
dependency. Its five in-memory tests verify commit/rollback order and the
absence of a SQL call when a database context cannot open.

The concrete [`ADTOF Psycopg database adapter`](kubernetes/services/adtof/app/postgresql.py)
now validates only the restricted `clouddsp-adtof` runtime Secret values and
the fixed private PostgreSQL Service before it opens one short dictionary-row
transaction. It maps raw driver/OS failures to one retryable safe category and
keeps connection/statement limits bounded, while lazy imports let all current
ADTOF unit tests run before a Psycopg dependency or worker image exists. Its
tests use a fake driver to verify settings validation, password redaction,
commit/rollback scope, and diagnostic redaction. The next small task will pin
Psycopg and its required transitive wheels in the future worker's requirements
lock.

[`ADTOF requirements.lock`](kubernetes/services/adtof/requirements.lock) now
pins the CPython 3.11/Linux ARM64 `psycopg`/`psycopg-binary` pair plus its
explicit `typing-extensions` runtime dependency. Its binary hash is the
already-reviewed Apple-Silicon k3d wheel, and the lock documents the mandatory
`--require-hashes --no-deps --only-binary=:all:` future image-install command.
It intentionally excludes RabbitMQ, MinIO, ADTOF/PyTorch, and audio/MIDI
dependencies, so it does not claim an image is buildable yet.

The pure [`ADTOF RabbitMQ connection-settings adapter`](kubernetes/services/adtof/app/amqp_connection.py)
now accepts only the private RabbitMQ Service, exact `/clouddsp` ADTOF queue,
prefetch one, bounded timeouts, and the restricted untagged runtime identity.
It imports no Pika and cannot open a socket, declare topology, consume/ack a
delivery, or make database/storage/model/Kubernetes requests. Its tests prove
the configuration rejects every endpoint, topology, identity, prefetch, and
timing widening before a future broker factory exists.

The ADTOF lock now also pins Pika 1.4.4's universal wheel with its reviewed
SHA-256. Pika adds no transitive runtime distribution, so the lock's explicit
database closure plus this single pure-Python AMQP package remains complete for
the connection boundaries introduced so far. It still excludes MinIO and model
dependencies and does not create a client, broker connection, or image.

The same ADTOF AMQP module now exposes a lazy Pika connection factory. It
revalidates the fixed private endpoint before opening only three bounded
plain-AMQP connection attempts, with no TLS toggle and safe diagnostic
redaction. It intentionally does not make a channel, consume/acknowledge a
delivery, inspect/declare topology, or start a worker loop; those are separate
layers after the durable first-claim ordering is composed.

The completed [`ADTOF AMQP passive-channel boundary`](kubernetes/services/adtof/app/amqp_channel.py)
now applies `prefetch_count=1` and passively checks only
`clouddsp.adtof.requests`. This stops one CPU-bound Pod from reserving several
unacknowledged drums-stem deliveries while it can process only one, while a
passive declaration verifies the bootstrap-owned queue without granting this
restricted worker a topology mutation. QoS or passive-lookup errors become one
safe retryable category; the adapter receives no delivery, acknowledges
nothing, and makes no PostgreSQL, MinIO, model, image, Deployment, or
Kubernetes call. Five mocked tests prove the fixed queue, prefetch bound,
redacted failures, no topology fallback, and direct-setting rejection before
channel I/O.

The new [`ADTOF parser-to-first-claim bridge`](kubernetes/services/adtof/app/delivery_claim.py)
now performs the required parser-before-database composition without making a
RabbitMQ decision. It exposes only parser-validated drums-stem evidence and a
PostgreSQL result that has committed before normal return; it does not retain
raw AMQP input, a delivery tag, a cursor, or a broker client. A malformed
request cannot touch the database, while a database outage propagates with no
acknowledgement/rejection action. Its four mocked tests prove that ordering and
leave DLQ/redelivery policy for the following manual-ack layer. No MinIO/model
work, image build, Deployment, or Kubernetes action is introduced.

The completed [`ADTOF manual-ack adapter`](kubernetes/services/adtof/app/amqp_manual_ack.py)
now reads one `clouddsp.adtof.requests` delivery using `auto_ack=False`, asks
the parser-to-first-claim bridge for a committed fact, and acknowledges only a
new lease, duplicate, or stale result. A malformed contract is nacked without
requeueing into the bootstrap-owned DLQ; database, durable-evidence, or broker
action errors stay unacknowledged for duplicate-safe redelivery. A claimed
lease/message pair leaves this boundary only after acknowledgement succeeds.
Nine mocked tests prove idle, durable, malformed, transient, malformed-claim,
and failed-ack/nack behavior. It does not start a loop, contact MinIO/ADTOF,
build an image, create a Deployment, or change the cluster.

The new [`ADTOF MinIO settings/client boundary`](kubernetes/services/adtof/app/minio_client.py)
now accepts only the restricted S3 identity and exact private MinIO Service,
`clouddsp-uploads` bucket, `us-east-1`, and path-style configuration. Its lazy
Boto3 factory provides the mounted pair explicitly, avoiding ambient AWS
providers, host profiles, and browser/Ingress routes; MinIO's S3 protocol does
not make an AWS request. Direct constructed settings are also revalidated
before the SDK can load, stopping a code-path bypass from redirecting the key
pair to another endpoint. Seven mocked tests prove strict configuration,
credential redaction, bounded client construction, and the absence of client
or network work until a later object verifier. No object is read/written and
no image, Deployment, or cluster resource is created.

The completed [`ADTOF drums HeadObject verifier`](kubernetes/services/adtof/app/stem_object.py)
now revalidates a claimed ADTOF lease and strict `drums` request before it
makes one metadata-only call for the fixed private Demucs WAV. It requires the
exact current byte count, WAV type, and full immutable Demucs schema/producer/
Job/task/stem/mode/size/SHA-256 metadata inventory. Missing inputs and known
evidence mismatches have bounded permanent codes; unavailable MinIO is
retryable; malformed headers are a safe protocol error. Five fake-client tests
prove exact call count, evidence matching, input-bound validation, and failure
classification. It neither downloads audio nor updates a task, acknowledges
RabbitMQ, invokes ADTOF, builds an image, creates a Deployment, or changes the
cluster.

The completed [`ADTOF bounded drums-download adapter`](kubernetes/services/adtof/app/stem_download.py)
now accepts only the verified canonical drums evidence, makes one matching
GetObject call, rechecks its headers, and streams at most 256 MiB into a random
0600 local file below Pod scratch while computing SHA-256. It yields the path
only inside a cleanup-guaranteed context and only after exact byte/hash
evidence agrees; short/grown/malformed/changed streams never reach model code.
Five fake-client tests prove bounded reads, private scratch cleanup, exact
checksum verification, retryable transport failures, and rejection before I/O
for forged evidence or symlinked scratch. It does not start/complete a task,
acknowledge RabbitMQ, invoke ADTOF, upload output, build an image, create a
Deployment, or change the cluster.

The pure [`ADTOF guarded task-start adapter`](kubernetes/services/adtof/app/task_claim.py)
now changes only a current `leased` drums task to `running`. Its parameterized
PostgreSQL update matches the exact task/Job/stage/stem/token identity and
PostgreSQL-clock unexpired predicate, returning a timezone-aware start time
only after successful admission. A missing returned row is normal ownership
loss and exposes no execution authority; direct forged lease values and bad
driver rows are rejected. Four in-memory tests cover admission, expiry/loss,
input validation, and return validation. The adapter creates no connection or
transaction and performs no MinIO, RabbitMQ, ADTOF, image, Deployment, or
cluster action.

The completed [`ADTOF verified-stem-to-running composition`](kubernetes/services/adtof/app/stem_task_start.py)
now keeps the verified temporary drums WAV inside its scratch scope, invokes
the guarded PostgreSQL start transition only after download proof exists, and
yields the path only after that transaction commits. Ownership loss removes the
WAV before it yields `None`; transaction/database/validation failure rolls
back or propagates after cleanup. Five mocked-boundary tests prove ordering,
commit/rollback, stale-ownership cleanup, and no cross-task source use. It
does not receive/ack RabbitMQ, run ADTOF, upload output, build an image, create
a Deployment, or call Kubernetes.

The new pure [`ADTOF output-object planner`](kubernetes/services/adtof/app/output_object_plan.py)
now accepts only a committed `RunningADTOFStem` handoff and returns the exact
two private, non-attempt-specific coordinates: `midi/{job_id}/drums.mid` and
`midi/{job_id}/drums_bpm.json`. Its frozen base provenance binds each output
to the Job, ADTOF task, outbox event, drums stem/mode, verified input SHA-256,
artifact kind, and the preserved cloud-default CPU ADTOF configuration
identity (pinned `adtof-pytorch` revision, 100 FPS, and five thresholds). It
explicitly excludes output byte counts and output SHA-256 because no model
artifact exists at this boundary; a later verifier must derive them before an
uploader may assemble complete metadata. Three standard-library tests prove
fixed keys, exact provenance, no attempt suffix, and rejection of forged lease
or download values. The planner performs no filesystem I/O, model inference,
MinIO operation, PostgreSQL mutation, RabbitMQ acknowledgement, image build,
or Kubernetes action.

The new [`ADTOF local output-artifact verifier`](kubernetes/services/adtof/app/output_artifact.py)
now accepts one exact output-object plan plus a future model runner's controlled
local filename. It prevents plan/key widening before a file is opened; rejects
symlinks, non-regular/wrong-name/oversized files, malformed Standard MIDI
framing, malformed or duplicate/non-finite tempo JSON, and cloud-incompatible
tempo candidate fields. It streams the local bytes to produce immutable size
and SHA-256 evidence, returning structured tempo data only for the JSON output.
Its four standard-library tests cover valid two-artifact evidence, pre-read plan
rejection, invalid MIDI/JSON, and local symlink/filename rejection. It invokes
no model, contacts no MinIO/PostgreSQL/RabbitMQ service, and makes no image or
Kubernetes change.

The new pure [`ADTOF CPU inference-command builder`](kubernetes/services/adtof/app/adtof_inference_command.py)
now reserves a fresh mode-0700 `adtof-output` sibling of a verified temporary
drums WAV and returns one fixed no-shell Python-module argv. It independently
reuses the running-lease/output-plan proof before it touches a local path, then
fixes the preserved cloud model revision, CPU device, FPS, five thresholds, and
`drums.mid`/`drums_bpm.json` filenames. A shared
[`model configuration`](kubernetes/services/adtof/app/model_configuration.py)
prevents provenance metadata and inference arguments drifting apart. Three
standard-library tests cover exact argv/path creation, unsafe input/stale output
rejection, and forged identity rejection before filesystem access. It imports
no model package and does not execute ADTOF, access MinIO/PostgreSQL/RabbitMQ,
build an image, or change Kubernetes.

The new [`ADTOF CPU inference entrypoint`](kubernetes/services/adtof/app/adtof_cpu_inference_entrypoint.py)
now parses only that exact flag ordering and fixed revision/FPS/threshold/device
configuration, rechecks a fresh sibling output tree, and lazily calls the
pinned `adtof_pytorch` CPU API. It writes only `drums.mid` and a strict,
cloud-compatible tempo JSON after passing the shared output-artifact parser.
Lazy imports keep source tests independent of the eventual PyTorch/Librosa/
PrettyMIDI image closure. Three injected-stand-in tests cover fixed CPU model
arguments and outputs, altered argv rejection before model execution, and safe
stale-output/malformed-tempo failure. It has no timeout/process supervision,
MinIO/PostgreSQL/RabbitMQ/Kubernetes access, or image build.

The new [`ADTOF CPU process runner`](kubernetes/services/adtof/app/adtof_cpu_process.py)
now repeats command/configuration/scratch-tree checks directly before it runs
one no-shell Python child in a private process session. It provides a ten-minute
normal CPU deadline and twelve-minute hard cap within the existing fifteen-minute
task lease; timeout cleanup sends `SIGTERM`, then `SIGKILL` if necessary, to the
full process group. Three fake-runner tests prove normal execution, tampered or
stale requests stopping before any model launch, and safe timeout propagation.
It neither validates outputs nor contacts MinIO/PostgreSQL/RabbitMQ/Kubernetes.

The new [`ADTOF local task execution composition`](kubernetes/services/adtof/app/local_task_execution.py)
now joins an already-running temporary drums stem, fixed output plans, the
bounded CPU process runner, and both local artifact checks. It returns the MIDI
and tempo evidence together only after exit-zero and both bounded local formats
hash successfully; no partial MIDI result escapes a missing/invalid tempo file.
Its two injected-runner tests cover complete local evidence and safe process or
artifact failure propagation. All paths remain valid only inside the existing
running-stem scratch context, and this composition has no MinIO/PostgreSQL/
RabbitMQ/Kubernetes access.

The new pure [`ADTOF upload-object planner`](kubernetes/services/adtof/app/upload_object.py)
now rebuilds base plans from the running lease, repeats local MIDI/tempo
validation immediately before use, and appends only the newly proven
`size-bytes` and output `sha256` fields to immutable provenance metadata. It
returns the two complete private MinIO plans together, retaining their scratch
paths only for the following streaming uploader. Its two tests prove exact
coordinate/metadata construction and rejection when local bytes change after
earlier verification. It performs no S3/MinIO call, PostgreSQL mutation,
RabbitMQ action, image build, or Kubernetes operation.

The new [`ADTOF streaming MinIO uploader`](kubernetes/services/adtof/app/minio_upload.py)
now accepts only that complete pair, validates both full plans before the first
request, repeats each local format/SHA proof, and opens only non-symlink
regular files. Its streaming request wrapper calculates a second digest from
the exact bytes the S3-compatible client consumes, returning receipts only
when byte count and SHA-256 agree with the immutable plans. MIDI and tempo
writes are deliberately sequential—never misrepresented as cross-object
atomic—but their deterministic keys make a retry safe after a partial pair.
The adapter makes no PostgreSQL change, RabbitMQ acknowledgement, model call,
image build, or Kubernetes operation; stored-object verification must still
precede guarded task completion.

The new [`ADTOF stored-output verifier`](kubernetes/services/adtof/app/output_artifact_head_object.py)
now validates the MIDI/tempo plans and receipts as a single task pair before
issuing either metadata-only `HeadObject` request. It proves both current MinIO
objects retain their fixed bucket/key/type, length, output SHA-256, and full
provenance metadata. It yields only safe stored evidence, distinguishes a
missing/mismatched object through reviewed permanent codes, and maps MinIO
outages to one redacted retryable category. It neither reads bytes nor changes
PostgreSQL/RabbitMQ, invokes a model, builds an image, or calls Kubernetes; a
later lease-token-guarded completion transaction must still decide durable
success.

The new [`ADTOF guarded completion adapter`](kubernetes/services/adtof/app/task_completion.py)
now validates the current drums lease, both verified stored outputs, and a
strictly re-parsed cloud-compatible tempo candidate before it calls one typed
administrator-owned completion function. That function locks the two rows,
requires the current running unexpired lease and retained `midi_processing`
Job, writes only the fixed `midi.drums` key/tempo record, increments the Job
revision, and marks the task succeeded without advancing overall Job status.
This source creates no database connection/transaction, accesses no
MinIO/RabbitMQ/Kubernetes API, and runs no model or image.

The existing [`ADTOF PostgreSQL bootstrap Job`](kubernetes/services/adtof/adtof-database-bootstrap-job.yaml)
now also prepares that `SECURITY DEFINER` function. Its administrator-owned
body repeats fixed task/key/tempo constraints, revokes PostgreSQL's default
`PUBLIC` function execution, and grants only `clouddsp-adtof` the exact typed
call; the role still has no direct Job UPDATE grant. The manifest includes an
all-zero no-row invocation that verifies only signature access. The fixed-name
bootstrap Job was deliberately reconciled successfully; the function is live
and its temporary data-namespace credential Secret was removed afterwards.

The new [`ADTOF completion-commit composition`](kubernetes/services/adtof/app/task_completion_commit.py)
now places only the reviewed typed function call inside the existing short
`write_cursor()` transaction scope. It exposes a completion only after normal
commit, treats a no-row ownership loss as a non-error stop, and guarantees an
adapter/database exception triggers rollback before a later supervisor could
acknowledge a broker delivery. It does not apply the bootstrap Job, call MinIO
or RabbitMQ, run a model, or access Kubernetes.

The new [`ADTOF post-inference finalization composition`](kubernetes/services/adtof/app/task_finalization.py)
now joins both local verified artifacts to their deterministic private upload
plans, the two stored-object metadata proofs, and the committed guarded result
write. It leaves MinIO outside every PostgreSQL transaction, returns no success
when final ownership is lost, and lets storage/database errors propagate to a
later supervisor. It does not parse/acknowledge RabbitMQ, start a task, run a
model, select a retry, apply a manifest, or use Kubernetes.

The new [`ADTOF post-claim success coordinator`](kubernetes/services/adtof/app/claimed_task_success.py)
now composes one already committed first-claim lease with its matching parsed
request through the existing drums `HeadObject` proof, bounded download plus
guarded `running` transition, fixed CPU output composition, and post-inference
finalization. CPU execution and finalization remain inside the temporary
running-stem context, ensuring its scratch paths cannot be cleaned before the
last required output upload/proof. It returns only committed `succeeded`
evidence or normal `ownership_lost`; operational failures propagate unchanged
to a later delivery supervisor. It neither parses, acknowledges, nor rejects a
RabbitMQ delivery, selects retry policy, creates a worker Deployment, or calls
Kubernetes.

The new [`ADTOF acknowledged-lease execution gate`](kubernetes/services/adtof/app/acknowledged_lease_execution.py)
now accepts only the prior manual-ack adapter's `ACKNOWLEDGED_LEASE` result.
That result proves both that PostgreSQL committed the canonical drums lease and
that RabbitMQ accepted acknowledgement for the delivery which produced it. The
gate revalidates the exact lease/request evidence, then delegates to the
post-claim success coordinator; idle, duplicate/stale, malformed-DLQ, and
forged results cannot start MinIO or CPU work. It neither makes any RabbitMQ
call nor opens a transaction, chooses retry policy, starts a loop, builds an
image, creates a Deployment, or calls Kubernetes.

The new [`ADTOF receive-and-execute-once composition`](kubernetes/services/adtof/app/receive_execute_once.py)
now joins one already-prepared Pika-shaped channel with the existing manual-ack
adapter and acknowledged-lease gate. The former retains commit-before-ack/DLQ
responsibility; the latter starts the existing success coordinator only for an
acknowledged current lease. `idle`, acknowledged duplicate/stale, and
malformed-DLQ outcomes return compact no-work facts and cannot reach MinIO or
CPU work. AMQP, PostgreSQL, MinIO, and ADTOF failures propagate unchanged to a
later supervisor. The composition has no loop, sleep/backoff, connection
lifecycle, retry policy, lease recovery, image entrypoint, Deployment, or
Kubernetes action.

The new pure [`ADTOF supervisor decision policy`](kubernetes/services/adtof/app/supervisor_backoff.py)
now maps normal one-cycle results to either a fixed one-second idle wait or an
immediate next check, resetting its local retry-failure counter in both cases.
It also reserves explicit retryable and fatal events for a later exception
classifier: retryable failures follow a capped 1, 2, 4, 8, 16, 30-second
sequence with runtime-supplied bounded jitter, while fatal configuration exits
immediately. Its compact in-memory state is not durable task retry state. The
policy does not sleep, reconnect, poll any service, mutate PostgreSQL, run
ADTOF, build an image, create a Deployment, or call Kubernetes.

The new pure [`ADTOF supervisor failure classifier`](kubernetes/services/adtof/app/supervisor_failure_classification.py)
now maps only reviewed static configuration/image-entrypoint errors to
`fatal_configuration` and only established RabbitMQ/PostgreSQL/MinIO
availability wrappers to `retryable_failure`. Integrity/contract/checksum,
model/time-limit, and unknown exceptions deliberately remain unclassified.
Classification examines only exception types: it does not catch an operational
error, sleep, reconnect, acknowledge RabbitMQ, alter a task, build an image,
create a Deployment, or call Kubernetes. A later runtime must still pair a
retryable event with explicit lease/recovery policy before any action.

The new [`ADTOF supervisor step`](kubernetes/services/adtof/app/supervisor_step.py)
now invokes exactly one receive-and-execute iteration, maps a normal result to
idle/progress or only a reviewed exception to retryable/fatal, then returns the
matching pure backoff decision and next in-memory state. Classified failures
retain no fake normal iteration result and unknown exceptions propagate
unchanged. It does not sleep, reconnect, loop, open/close clients, add task
mutations beyond the invoked iteration, build an image, create a Deployment, or
call Kubernetes; a following shutdown-aware action adapter must perform any
actual wait or exit.

The new [`ADTOF supervisor action adapter`](kubernetes/services/adtof/app/supervisor_action.py)
now applies a single existing decision through an injected shutdown-aware
waiter. It continues immediately for progress, waits once for idle/backoff and
returns `shutdown_requested` if interrupted, or returns a visible fatal-exit
fact without waiting. It installs no signal handler and does not loop, sleep
directly, reconnect/close clients, mutate a task, run ADTOF, build an image,
create a Deployment, or call Kubernetes. Durable expired-lease recovery must
be built before a long-running retrying runtime uses its retryable path.

The new pure [`ADTOF expired-active-lease recovery claim`](kubernetes/services/adtof/app/task_claim.py)
now grants at most one new `leased` token for an expired `leased` or `running`
drums task, allowing recovery after a Pod crash or a post-ack failure without a
second RabbitMQ delivery. PostgreSQL's own clock, the active-lease index, and
`FOR UPDATE SKIP LOCKED` make the candidate decision safe across overlapping
Pods. It increments only attempts one/two, leaving exhausted attempt-three
handling and scheduled-retry policy for later reviewed transitions. It returns
only durable lease/event coordinates; a following transaction-local reader must
rebuild strict request evidence before external work. It does not open a
connection/transaction, read event payloads, call MinIO/RabbitMQ/ADTOF, sleep,
build an image, create a Deployment, or call Kubernetes.

The new read-only [`ADTOF recovery-request reader`](kubernetes/services/adtof/app/recovery_request.py)
now runs immediately after that claim inside its same short transaction. It
requires the exact fresh second/third-attempt lease, binds every task/event/
input/token coordinate plus PostgreSQL-clock lease expiry in its query, and
rebuilds the ordinary strict drums request only from the immutable published
outbox row. `None` is normal ownership loss; unsafe lease/event/payload proof
raises so a following composition can roll back rather than commit a recoverable
task with no executable evidence. The reader opens/commits no transaction and
does not call RabbitMQ, MinIO, ADTOF, sleep, build an image, create a Deployment,
or call Kubernetes.

The new [`ADTOF expired-lease recovery composition`](kubernetes/services/adtof/app/recovery.py)
now calls that claim and reader through the same restricted `write_cursor()`
context, returning a recovery lease/request pair only after normal commit. An
idle scan makes no mutation and commits normally. If a fresh claim loses its
matching evidence before the reader finishes, an internal transaction-local
sentinel forces rollback before the composition returns normal no-work; invalid
evidence and database errors also propagate through rollback. This prevents a
durable orphan lease with no strict execution input. It has no RabbitMQ, MinIO,
ADTOF, sleep/loop, image, Deployment, or Kubernetes action.

The new delivery-free [`ADTOF recovered-task execution gate`](kubernetes/services/adtof/app/recovered_task_execution.py)
now accepts only that committed pair and repeats its canonical UUID, fresh
recovery-attempt, lease timestamp, fixed drums-task/private-object, and shared
event/Job/object identity proof before it delegates to the established
post-claim success coordinator. It deliberately does not invent a RabbitMQ
acknowledgement: recovery is authorized by PostgreSQL after the original
delivery was already acknowledged. This source opens no transaction, scans no
task, and makes no RabbitMQ/MinIO/ADTOF, sleep/loop, image, Deployment, or
Kubernetes action itself.

The new [`ADTOF recovery execute-once composition`](kubernetes/services/adtof/app/recovery_execute_once.py)
now joins one expired-lease scan/transaction with the delivery-free gate. Its
normal `idle` result means no safe expired lease exists; its `executed` result
preserves only the existing post-claim success/ownership-loss fact. Invalid
recovery output and operational errors intentionally propagate rather than
being misreported as idle. It accepts no RabbitMQ channel and makes no broker
operation, sleep/loop, image, Deployment, or Kubernetes call. A separate pure
cadence policy must still bound how often the future supervisor invokes it when
normal queue traffic remains busy.

The pure [`ADTOF normal/recovery cadence policy`](kubernetes/services/adtof/app/recovery_cadence.py)
now starts every new Pod with one recovery scan and then strictly alternates a
bounded recovery iteration with one bounded normal AMQP iteration. This makes
an expired task wait for no more than one normal iteration, even under a busy,
duplicate, or malformed queue; an empty recovery scan is only a small
no-mutation PostgreSQL check. Its immutable next-action state validates action
ordering and complete iteration-result shapes, but makes no database, MinIO,
RabbitMQ, sleep/loop, image, Deployment, or Kubernetes call. A following
single-cycle composition will execute exactly the action selected by it.

The new [`ADTOF cadence-driven worker cycle`](kubernetes/services/adtof/app/worker_cycle.py)
now executes exactly one cadence-selected branch and returns its compact result
plus the only valid next cadence state. A normal action alone receives the AMQP
channel; the recovery action cannot receive/acknowledge/reject/publish a broker
message because it passes no channel to its child. Errors leave the frozen input
state unadvanced for a future bounded retry. The cycle owns no loop, wait,
connection lifecycle, image, Deployment, or Kubernetes action. The next small
change is to have the existing supervisor step own this result/state while
keeping its reviewed backoff behavior.

The updated [`ADTOF supervisor step`](kubernetes/services/adtof/app/supervisor_step.py)
now owns both its bounded retry-backoff count and the worker cadence's next
action. It runs exactly one worker cycle and advances cadence only when that
cycle returns normally; recognized retryable/fatal failures preserve the prior
action, so an outage cannot skip a selected recovery scan. Normal AMQP idle
retains the short one-second wait, whereas recovery idle maps to immediate
progress because it does not indicate the next normal broker poll is empty.
The step still does not apply its decision, sleep, loop, reconnect, build an
image, or create/use any Kubernetes resource. The following small composition
will join this step to the existing shutdown-aware decision action once.

The new [`ADTOF one-step supervisor runner`](kubernetes/services/adtof/app/supervisor_once.py)
now composes one cadence-aware step with the existing shutdown-aware action
adapter, returning the exact step, applied control result, and verified next
state together. The action must match the step decision and the state must be
the state advanced by that step, so a future loop cannot continue with unrelated
local state. It has no persistent loop, signal handler, service lifecycle,
image, Deployment, or Kubernetes action; failures propagate instead of being
made-up `continue` facts. A subsequent focused shutdown-event adapter will own
the SIGTERM/SIGINT event that its waiter needs.

The new scoped [`ADTOF shutdown-event adapter`](kubernetes/services/adtof/app/shutdown_event.py)
now installs main-thread SIGTERM/SIGINT handlers that only set one shared
`threading.Event`, exposes the bounded 0–30 second supervisor-waiter protocol,
and restores prior process handlers on normal/error cleanup or partial setup
failure. It performs no service/model/Kubernetes work inside a signal callback,
allowing a future loop to stop before a new cycle once its current bounded work
returns. The next small source task will introduce that loop while keeping
client creation and closure outside it.

The new [`ADTOF shutdown-aware supervisor loop`](kubernetes/services/adtof/app/supervisor_loop.py)
now repeats the one-step runner only after `continue`, returns on
`shutdown_requested` or `exit_fatal`, and performs a zero-second event check
before every new cycle. Thus SIGTERM that arrives during CPU work is observed
before another RabbitMQ receive or recovery scan can begin, even when the prior
step selected immediate progress. The loop takes already-created dependencies
and owns no client/signal lifecycle, image, Deployment, or Kubernetes action.
The next focused adapter will make the existing AMQP connection/channel setup
closeable around this loop.

The new closeable [`ADTOF AMQP session`](kubernetes/services/adtof/app/amqp_session.py)
now composes the reviewed restricted connection factory and passive
prefetch-one channel preparation, yielding only the prepared channel to a
caller. It attempts channel-then-connection closure on every setup/body/normal
cleanup path, preserving caller errors while mapping a normal cleanup failure
to the existing redacted retryable channel category. It has no delivery,
acknowledgement/rejection/publish/topology mutation, PostgreSQL/MinIO client,
signal/loop, image, Deployment, or Kubernetes action. The next composition can
now join restricted database/storage construction, scoped signal handling,
session, and loop into one process entrypoint.

The new [`ADTOF worker bootstrap entrypoint`](kubernetes/services/adtof/app/worker_entrypoint.py)
now makes exactly that composition. It constructs the existing restricted
PostgreSQL adapter and MinIO client, requires the future fixed
`/worker-scratch` `emptyDir` mount to be an existing non-symlink directory,
then scopes the reviewed SIGTERM/SIGINT event around one prepared private AMQP
session and the persistent supervisor loop. Channel cleanup occurs before
connection cleanup on normal and exceptional paths; unclassified operational
errors still propagate rather than being disguised as success. The normal
supervisor terminal facts map to `0` for shutdown and `78` for fatal
configuration without calling `sys.exit`. It builds no image and makes no
Deployment, manifest, or Kubernetes change. The next small task is a thin
executable wrapper that emits one non-sensitive diagnostic only for known
static configuration failures and returns the entrypoint status.

The new [`ADTOF executable worker wrapper`](kubernetes/services/adtof/app/worker_main.py)
now does only that final process-boundary work. It calls the bootstrap
entrypoint, returns its ordinary status unchanged, and converts only malformed
mounted AMQP/PostgreSQL/MinIO configuration or a missing/unsafe scratch mount
to the stable non-sensitive stderr diagnostic plus exit status `78`. It does
not print raw exception details and intentionally lets workload, availability,
and unexpected exceptions propagate after the entrypoint's existing cleanup.
The next small task is a source-only CPU container recipe that pins the already
reviewed dependencies, starts this module as PID 1 under a non-root account,
and preserves `/worker-scratch` for the future Deployment's bounded `emptyDir`
instead of making it in the image.

The expanded [`ADTOF CPU requirements lock`](kubernetes/services/adtof/requirements.lock)
now provides the complete CPython 3.11/Linux ARM64 dependency closure needed
by that recipe. It preserves the cloud's CPU Torch 2.5.1, Librosa 0.10.2.post1,
PrettyMIDI 0.2.10, NumPy/Scipy/Numba audio stack, and the exact
`adtof-pytorch` commit `85c192e78f716ea0b111cc8a5ee4a8f6a3a4f8a9` as a
SHA-256-pinned GitHub source archive. It also adds the restricted existing
Boto3, Pika, and Psycopg closures. Every wheel/source archive is hash pinned;
the two reviewed source distributions use explicit locked `setuptools`/`wheel`
build tools with build isolation disabled. The lock records only the target
Python-3.11 dependencies—Python-3.13-only Audioread dead-battery packages are
excluded deliberately. Source-package hash installation was exercised in an
isolated Python 3.11 temporary environment without model imports or inference;
no image or cluster resource was created.

The new source-only [`ADTOF worker Dockerfile`](kubernetes/services/adtof/Dockerfile)
now turns that reviewed closure into a build recipe without building, pushing,
or deploying an image. Its validation stage begins from the locked official
Python 3.11 multi-platform base, installs the lock's reviewed build tools, then
fails closed with the complete hash-verified CPU closure and CPU PyTorch index.
It runs every ADTOF unit test and verifies the installed ADTOF package carries
its bundled `.pth` weights without performing transcription. The final stage
copies only validated site packages and `app/`, runs as UID/GID `10005`, and
uses the exec-form `python -m app.worker_main` entrypoint so SIGTERM reaches
the reviewed shutdown path. It deliberately leaves `/worker-scratch` absent:
the eventual restricted Deployment must supply its bounded writable `emptyDir`
rather than allowing temporary media on the image filesystem. The next small
task is source provenance only: add an unbuilt ADTOF entry to
`kubernetes/images.lock.yaml`; do not yet build/push an image, record an output
digest, or create a workload manifest.

The new unbuilt [`ADTOF image-source provenance`](kubernetes/images.lock.yaml)
record now fixes the Dockerfile, application/test directories, complete
requirements lock, pinned Python 3.11 base, Linux/ARM64 CPU target, intended
local registry repository, exact ADTOF revision, direct dependencies, and
exec-form `app.worker_main` PID-1 contract. It intentionally creates neither
an `images.adtof` entry nor a readable-tag deployment reference, immutable OCI
digest, or registry output record: the image does not exist yet. Text-only
tests ensure the input record cannot be mistaken for a deployable artifact.
The next small task may build this reviewed source for Linux/ARM64 under a
non-deployable local tag, inspect the build/test result and image size, and
stop before any registry push, digest entry, workload manifest, or Kubernetes
action.

The reviewed local ADTOF source recipe has now built successfully as the
non-deployable Docker tag `clouddsp-adtof:0.1.0-cpu-worker-local-only` for
Linux/ARM64. The dependency install rejected and exposed one mistyped Numba
wheel hash before it could create an image; the corrected lock is now covered
by a structural test. The build then exposed `pretty-midi==0.2.10`'s required
legacy `pkg_resources` import, so the closure explicitly pins its compatible
hash-verified `setuptools==79.0.1` runtime rather than relying on mutable base
image tooling. The final image's uncompressed Docker size is `345,133,026`
bytes (`329.14 MiB`), it runs as UID/GID `10005:10005`, starts the reviewed
exec-form `app.worker_main` entrypoint, imports the ADTOF/audio/CPU-Torch stack
offline as that user, and leaves `/worker-scratch` absent for the future
bounded Pod mount. The local-only tag has not been pushed and does not permit
an `images.adtof` lock entry, digest-based Deployment, or Kubernetes action.
The next small task is a registry push plus digest inspection only; do not add
that result to `images.lock.yaml` or deploy it yet.

The same ARM64 image has now been pushed to the dedicated local k3d registry as
`clouddsp-registry.localhost:5001/adtof:0.1.0-cpu-worker-runtime`. A read-only
registry manifest request returned HTTP 200 and the immutable
`Docker-Content-Digest`
`sha256:8b045fc256d80a95d8d0e94a2dbf6bd515a235ea274ae605bc68d24e556ddc73`,
matching the locally inspected Linux/ARM64 image. This proves the local
registry has the precise artifact, but it is still deliberately absent from
the `images` output section: no manifest may consume its mutable tag or digest
until a following lock-only task records `images.adtof`. No Kubernetes resource
was applied.

The verified registry artifact is now locked as `images.adtof` in
[`kubernetes/images.lock.yaml`](kubernetes/images.lock.yaml). It binds only
`clouddsp-registry.localhost:5001/adtof@sha256:8b045fc256d80a95d8d0e94a2dbf6bd515a235ea274ae605bc68d24e556ddc73`
for Linux/ARM64 CPU use, records the `329.14 MiB` local Docker size, and links
its source Dockerfile, complete hash lock, Python base, test directory, CPU
policy, bundled ADTOF weights/model revision, PID-1 wrapper, and runtime import
check back to `buildSources.adtof`. The readable tag remains non-deployable.
No Deployment, Pod, Secret, Service, scaler, or other Kubernetes resource has
been created or applied. The next small task is to prepare—but not apply—the
restricted ADTOF worker Deployment that consumes this immutable image.

The prepared [`ADTOF Deployment`](kubernetes/services/adtof/adtof-deployment.yaml)
now defines one internal Linux/ARM64 CPU worker without applying it. It consumes
only the locked `images.adtof` digest, has no Service/Ingress or Kubernetes API
token, runs non-root with an immutable root filesystem and no capabilities, and
uses the three existing least-privilege ADTOF runtime Secrets. Bounded `emptyDir`
mounts provide `512Mi` source/output scratch, `128Mi` temporary/Numba cache,
and `64Mi` HOME space; resource accounting reserves one CPU/2 GiB and caps the
worker at two CPU/4 GiB/1 GiB ephemeral storage. Its 660-second termination
grace accommodates the ten-minute CPU inference deadline before durable lease
recovery is needed. The next focused task is read-only confirmation of the
three runtime Secrets and their completed backend bootstrap state before any
explicit Deployment apply request.

That read-only pre-apply check is now complete. The three app-namespace ADTOF
runtime Secret objects exist with their expected non-sensitive key counts;
their temporary bootstrap counterparts and three completed bootstrap Jobs have
been intentionally removed. Live PostgreSQL confirms the non-superuser,
non-inheriting `clouddsp-adtof` login role and its execution grants for both
guarded lease/completion functions. Live RabbitMQ confirms the durable ADTOF
request/retry/DLQ queues and exact no-configure/no-publish/read-only request
queue permission. MinIO and its private Service are Ready, and the retained
non-secret v001 policy ConfigMap grants only the reviewed drums read and
deterministic MIDI/tempo actions. The one unavailable proof is the live MinIO
user-to-policy attachment: its administrative bootstrap evidence was correctly
cleaned up and the hardened MinIO server contains no admin client. Therefore no
Deployment was applied; a separate restricted-runtime MinIO policy smoke Job
must prove allowed versus denied S3 access before deployment authorization.

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
the same one-call, one-short-transaction boundary. Recovery now claims one
due/expired task *and* reads its exact published outbox evidence on that cursor,
committing only a matched `DemucsRecoveredTask` pair or an idle scan. A missing
post-claim pair triggers an internal rollback sentinel, so no bare lease can
be committed without request evidence. It now first terminalizes one active
expired third attempt by atomically failing the Demucs task and its retained
`source_uploaded` Job with the fixed `demucs_lease_expired_attempts_exhausted`
code; Demucs is job-wide, so unlike a downstream stem failure this changes the
Job too. A renewed expiry still commits normally; a `None` renewal means the
worker must stop because it no longer owns that task. Eleven recording-context
tests cover terminalization, idle/pair commits, lost/invalid evidence rollback,
malformed rows, renewal, outage ordering, and mixed-pair rejection. It
deliberately adds no long-running supervisor, RabbitMQ handling, media work,
Deployment, image rebuild, or cluster change.

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

The new
[`executed Demucs separation workspace`](kubernetes/services/demucs/app/executed_separation_workspace.py)
joins the committed `running` source workspace to the fixed command and bounded
process runner. It first validates the Pod scratch root, creates a new private
random output directory beside—not inside—the generic temporary source, derives
the model solely from the committed task lease, and yields the zero-exit output
coordinate only while both nested contexts remain open. Any source cleanup,
unsafe scratch root, or process failure prevents a usable result and removes
the output. A zero exit has no durable success meaning and performs no artifact
inventory, hashing, MinIO operation, PostgreSQL mutation, renewal, RabbitMQ
action, image rebuild, Deployment change, or cluster change. Four fake-runner
tests cover the fixed model choices, closed-source gate, failure propagation,
and cleanup; the next small task is to validate the exact stem inventory inside
this output scope.

That inventory handoff now exists in
[`validated_stem_inventory_workspace.py`](kubernetes/services/demucs/app/validated_stem_inventory_workspace.py).
It invokes the existing exact local WAV validator only while the executed
output scope remains open and preserves the *same command object* beside the
returned stem/path/byte-count evidence. An incomplete, extra, empty, symlinked,
or substituted output cannot cross this boundary, and the outer workspace
removes its temporary output after normal return or an exception. This source-
only handoff has no hashing, MinIO, PostgreSQL, lease, RabbitMQ, image,
Deployment, or cluster behavior. Four fake-runner tests cover the completed
four-stem inventory, incomplete-output rejection, cleanup, and a cloned-command
substitution attempt; the next small task is SHA-256 hashing of this exact
in-scope inventory.

That hashing handoff now exists in
[`hashed_stem_inventory_workspace.py`](kubernetes/services/demucs/app/hashed_stem_inventory_workspace.py).
It invokes the existing streaming SHA-256 boundary only inside the validated
inventory and executed-output scopes, then requires the same command instance
and every original stem name/path/byte-count to accompany the resulting digest.
The hash adapter independently revalidates current local files, so a mutation
after inventory cannot receive evidence for a future object plan. No MinIO,
PostgreSQL, lease, RabbitMQ, image, Deployment, or cluster action occurs; the
outer workspace removes all local output on normal or exceptional exit. Four
fake-runner tests cover successful four-stem hashes, post-inventory mutation,
cleanup, and a cloned-command substitution attempt. The next small task is to
build deterministic private MinIO object plans from this exact hashed evidence.

That private-plan handoff now exists in
[`stem_output_plan_workspace.py`](kubernetes/services/demucs/app/stem_output_plan_workspace.py).
It calls the existing planner while the hash/output scopes remain open and
preserves the full one-to-one relationship between the running lease, hashed
artifact, deterministic private object key, fixed content type, byte count, and
immutable provenance metadata. A post-hash byte substitution fails the
planner's repeated proof before any plan is yielded; a plan is still not an
object transfer. Four fake-runner tests cover complete stable plans, mutation
rejection, cleanup, and a substituted planner result. This source-only step has
no MinIO client/transfer, PostgreSQL, lease, RabbitMQ, image, Deployment, or
cluster action. The next small task is a one-stem restricted MinIO upload inside
this scope, with independent streaming hash verification.

That one-stem upload handoff now exists in
[`planned_stem_upload.py`](kubernetes/services/demucs/app/planned_stem_upload.py).
It accepts only the identity-preserved plan instance from the open private-plan
workspace, delegates one `PutObject` to the existing restricted streaming/hash
adapter, and accepts only a matching bucket/key/length/SHA-256 receipt. The
adapter independently verifies the current local bytes before and while the
client reads them, so neither a stale clone nor a same-size byte substitution
can become an upload success. Four fake-client tests cover that exact selected
write, plan/byte rejection, receipt validation, and arbitrary-workspace guard.
This step does not aggregate all stems, update PostgreSQL, make a task terminal,
renew a lease, publish/acknowledge RabbitMQ work, rebuild an image, deploy a
worker, or change the cluster. The next small task is sequentially uploading
every fixed plan and returning a complete in-memory receipt set—still before
any durable result transaction.

That complete-upload handoff now exists in
[`complete_stem_upload.py`](kubernetes/services/demucs/app/complete_stem_upload.py).
It loops over only the fixed plan tuple in deterministic order, reuses the
one-plan restricted upload/receipt guard for each item, and returns the existing
receipt-only `PublishedDemucsStemSet` type only if every object upload succeeds.
After an upload failure, it stops later writes; already-written artifacts stay
private under their stable retry-overwritable keys and there is no complete set
for a PostgreSQL completion call. Three fake-client tests prove the ordered full
set, a later transport failure, and arbitrary-workspace rejection. This changes
no PostgreSQL row, task lease, RabbitMQ state, image, Deployment, or cluster.
The next small task is to pass this complete receipt set into the existing
token-guarded PostgreSQL completion transaction.

That upload-to-completion composition now exists in
[`complete_stem_upload_commit.py`](kubernetes/services/demucs/app/complete_stem_upload_commit.py).
It validates the required PostgreSQL transaction and outbox-ID factory before
any private upload, runs the fixed complete upload sequence with no database
lock open, then passes only its full receipt set to the existing guarded
task/Job/outbox transaction. A committed no-row lease loss returns `None`, so
no terminal success or downstream broker action escapes; database and storage
failures propagate to a future retry policy. Three mocked-boundary tests prove
the upload-before-commit order, ownership loss, and database-capability gate.
It does not publish/acknowledge RabbitMQ directly, rebuild an image, deploy a
worker, or change the cluster. The next small task is an outer one-task runtime
composition that nests source workspace, process, validation, hashing, plans,
uploads, and this guarded completion path without receiving a new AMQP message.

That outer single-task composition now exists in
[`task_runtime_once.py`](kubernetes/services/demucs/app/task_runtime_once.py).
Given only an already acknowledged receive result, it nests the exact source
preflight, committed `running` transition, bounded CPU execution, local stem
proofs, private upload sequence, and existing token-guarded completion
transaction. It validates source-read, artifact-write, database, and outbox-ID
capabilities before any source/model work; PostgreSQL contexts remain confined
to their short start/completion calls and every scratch context unwinds on
success, loss, or failure. A running or completion ownership loss returns
`None`; all other exceptions remain for a future retry/recovery supervisor.
Three mocked-boundary tests prove the complete nesting order, early ownership
loss, and database gate. It does not receive a new AMQP delivery, loop, sleep,
retry, directly publish/acknowledge RabbitMQ, rebuild an image, deploy a worker,
or change the cluster. The next small task is to map one-task exception
categories into the existing retry/terminal-failure decision without creating
the long-running supervisor yet.

That review now starts with the pure
[`Demucs pre-model source-failure classifier`](kubernetes/services/demucs/app/source_failure_classification.py).
It maps only source-boundary exceptions that occur before the guarded
``leased -> running`` transition: permanent HeadObject and FFprobe media
categories, plus a HeadObject/GetObject consistency mismatch, receive finite
terminal codes; the two redacted MinIO availability wrappers receive the one
storage-retry code. Protocol/process/model/output/database/unknown failures
remain unclassified and therefore cannot accidentally enter durable task state.
The classifier has no SQL, broker, retry loop, or Kubernetes operation. The
next small task is its consumer: a lease-token-guarded PostgreSQL adapter that
commits one of those outcomes (and fails the Job atomically for every terminal
Demucs result, including final retry exhaustion).

That pure
[`Demucs pre-model failure-transition adapter`](kubernetes/services/demucs/app/pre_model_failure_transition.py)
now performs the one guarded SQL decision. It locks the still-retained
`source_uploaded` Job and binds the full source-task identity, exact attempt,
lease UUID, and PostgreSQL expiry before it changes state. A known MinIO
outage on attempts one/two is made durable as `retry_scheduled` at a
PostgreSQL-clock time 30 seconds later. An immutable source mismatch, or the
third such outage, atomically marks both `processing_tasks` and `jobs` failed,
increments the Job revision, and stores the one finite category in each
allowed error field. A no-row result is normal ownership/job-state loss. This
pure cursor adapter opens no connection/transaction and performs no broker,
model, worker-loop, image, Deployment, or Kubernetes action. Five fake-cursor
tests cover retry, atomic task/Job failure, exhaustion, a no-row race, and the
unclassified-category guard. The next small task is a short transaction
composition that exposes this result only after PostgreSQL commits.

That transaction composition now exists in
[`pre_model_failure_transition_commit.py`](kubernetes/services/demucs/app/pre_model_failure_transition_commit.py).
It opens exactly one restricted ``write_cursor()`` scope around the pure
failure-transition adapter and returns its retry/terminal/no-row result only
after normal exit commits. A malformed SQL result or database outage escapes
through the context, which rolls back rather than exposing an uncertain task
outcome. It catches no source/runtime exception, does not sleep/recover/renew,
and performs no broker, model, image, Deployment, or Kubernetes action. Four
in-memory tests prove the normal commit, ownership-loss commit, exceptional
rollback, and missing-capability guard. The next small task is a narrow
runtime handoff that catches only classified source-preflight exceptions and
uses this committed decision.

That narrow handoff now exists in
[`pre_model_failure_runtime.py`](kubernetes/services/demucs/app/pre_model_failure_runtime.py).
It wraps one already-acknowledged one-task attempt and preserves its existing
success/ownership-loss results. Only the finite source-preflight classifier
categories are caught: the current acknowledged lease feeds the committed
retry/terminal transition, which then becomes an explicit runtime outcome; a
no-row transition becomes normal ownership loss. Model/artifact/database,
source-protocol, and unknown exceptions still propagate unchanged, so this
pre-model policy cannot misclassify work after the `running` transition. It
does not receive/acknowledge AMQP, retry/sleep/recover/renew, or change an
image, Deployment, KEDA policy, or Kubernetes resource. Five mocked-boundary
tests prove normal success, terminal source failure, retry/race handling,
unclassified model-error propagation, and normal ownership loss. The next
small task is a separately reviewed policy for failures after Demucs begins
running.

That policy now starts with the pure
[`Demucs running-failure classifier`](kubernetes/services/demucs/app/running_failure_classification.py).
It recognizes only reviewed after-model disruptions: process start/timeout/
nonzero failure, transient invalid output, local artifact-integrity evidence
loss, or private MinIO stem-write unavailability. Each receives one finite
retry code and an explicit paired third-attempt exhaustion code. This is safe
because a new lease reruns the verified source against deterministic private
stem keys; a partial prior upload remains private and can be overwritten, not
published as a second result. Image/command/plan contracts, database/completion
errors, and unknown exceptions remain unclassified for operator-visible
handling. The classifier has no SQL or runtime catch. Four tests prove the
mapping/exclusions and complete exhaustion mapping. The next small task is a
token-guarded `running -> retry_scheduled/failed` PostgreSQL transition using
these reviewed categories.

That pure
[`Demucs running-failure transition`](kubernetes/services/demucs/app/running_failure_transition.py)
now commits the one guarded SQL decision. It locks the still-retained
`source_uploaded` Job and checks the full source-task coordinate, `running`
state, exact attempt, lease UUID, and database-clock expiry. Attempts one/two
clear the active lease and schedule PostgreSQL recovery after 30 seconds. The
third matching failure uses the category's explicit exhaustion mapping to
atomically mark the task and Job `failed`, increment the Job revision, and
retain `started_at` as truthful model-start evidence. Private partial stems are
still inaccessible and retry-overwritable at deterministic keys. Four fake
cursor tests prove the retry, terminal task/Job result, no-row ownership loss,
and unclassified guard. This adapter opens no transaction or external client.

[`Demucs running-failure commit wrapper`](kubernetes/services/demucs/app/running_failure_transition_commit.py)
now provides that narrow durable boundary: one restricted `write_cursor()`
calls the pure decision exactly once, then exposes retry/terminal evidence only
after normal commit. A guarded no-row ownership/state race commits normally as
a stop signal; database, SQL, or protocol errors escape through the context and
roll back. It deliberately adds no exception classification, retry sleep,
recovery scan, lease renewal, AMQP/MinIO/model operation, image/Deployment, or
KEDA update. Its four fake tests prove normal commit, no-row commit, exception
rollback, and the capability guard.

[`Demucs running-failure runtime handoff`](kubernetes/services/demucs/app/running_failure_runtime.py)
now composes the completed source-policy handoff with the completed running
policy, still for exactly one acknowledged task attempt. Inner success,
ownership loss, and source-policy retry/terminal evidence pass through without
being relabeled. Only a later exception the fail-closed running classifier
recognizes reaches the committed `running` retry/exhaustion transition with the
same acknowledged lease. Its no-row race becomes ownership loss; database,
completion, source-protocol, image-contract, and unknown errors retain their
original exception rather than becoming a browser-visible result. The shared
worker-facing result retains the concrete pre-model or running transition so
their distinct lease/state predicates and error vocabularies are not flattened.
Six mocked-boundary tests cover pass-through, retry, exhaustion, ownership loss,
and unclassified propagation. This adds no AMQP receive/ack, loop, sleep,
recovery, image rebuild, Deployment, live-cluster, or KEDA action; its nested
execution workspace owns short renewal checkpoints while the model runs.

The first small supervisor building block is now
[`Demucs receive-and-execute once`](kubernetes/services/demucs/app/receive_execute_once.py).
It receives at most one manual-ack RabbitMQ delivery and invokes the completed
one-attempt policy only when the existing transport boundary returned an
acknowledged current lease. Idle, duplicate/stale, and malformed-DLQ results
become compact non-execution outcomes and cannot touch MinIO, FFprobe, or
Demucs; the RabbitMQ channel never crosses into the post-ack task path. A
receive or unclassified runtime exception stays an exception for a later
reconnect/backoff policy. Four mocked-boundary tests prove exact forwarding,
the no-work gate, propagation, and result pairing. This adds no loop, wait,
recovery scan, signal handling, connection lifecycle, image, Deployment,
live-cluster, or KEDA behavior; its nested one-attempt runtime owns short
renewal checkpoints.

[`Demucs recovery request evidence`](kubernetes/services/demucs/app/recovery_request.py)
now provides that read-only boundary. A due retry or expired active task has no
new AMQP delivery, so immediately after PostgreSQL grants its fresh
attempt-two/three lease, the same short transaction reads only the exact
matching *published* immutable `demucs.requested` outbox record. The query
rebinds task/job/event/source/mode/attempt/token coordinates and the database
lease clock, then the reader requires the exact version-1 JSON payload and
reuses the ordinary source-coordinate validator to reconstruct a normal
`DemucsRequestedMessage`. A missing row is normal ownership loss; malformed
evidence raises and rolls back the new lease rather than authorizing MinIO or
Demucs. Four fake-cursor tests cover success, stale ownership, pre-query
first-attempt rejection, and event/payload/publication mismatch. This does not
claim/start work, commit, acknowledge/publish RabbitMQ, contact MinIO, run a
model, loop, rebuild/deploy an image, or change KEDA.

The existing [`Demucs task-maintenance composition`](kubernetes/services/demucs/app/task_maintenance.py)
now performs that exact one-transaction join. It exposes a
`DemucsRecoveredTask` only after commit; an evidence loss after a fresh claim
forces rollback and returns normal no-safe-work, so later code cannot process
from a bare lease. Its frozen pair defensively rejects first attempts, mixed
coordinates, and unsafe direct construction. It adds no AMQP delivery, MinIO,
model, loop, image/Deployment, or KEDA behavior.

[`Demucs recovered-task execution`](kubernetes/services/demucs/app/recovered_task_execution.py)
now admits only that committed pair to the established one-task pre-model and
running-failure policy. It creates a data-only internal lease carrier for the
legacy post-acknowledgement runtime shape; the carrier has no channel, delivery
tag, AMQP body/properties, or acknowledgement method, and cannot contact
RabbitMQ. The pair remains the authority, while the reused path retains source
preflight, guarded start, periodic lease renewal, private artifact upload, and
its existing success/ownership/retry/terminal results. Three mocked tests cover
lease forwarding, forged-input rejection, and unmodified downstream errors.
The next small task was a guarded terminal transition for an expired third
attempt, which must never receive a fourth Demucs lease.

That final-expiry path now exists in
[`Demucs task lease SQL`](kubernetes/services/demucs/app/task_lease.py) and
its [`maintenance composition`](kubernetes/services/demucs/app/task_maintenance.py).
It locks one active third attempt with `FOR UPDATE SKIP LOCKED`, locks its
retained source-uploaded Job, and atomically marks task and Job `failed`, clears
the lease fields, records completion/revision, and uses only the fixed
`demucs_lease_expired_attempts_exhausted` category. A no-row result is normal
for an idle/concurrent/deleted/expired/advanced candidate; it never authorizes
a fourth lease or model call. Two pure SQL tests plus three transaction tests
cover the terminal proof, idle result, return-shape rejection, and rollback.
The next small task is a bounded recovery iteration that terminalizes first,
then claims and executes at most one safe recovered task, without adding a
worker loop.

[`Demucs recovery execute once`](kubernetes/services/demucs/app/recovery_execute_once.py)
now performs that single bounded step. It terminalizes first, then only when no
final attempt changed state recovers one committed pair and sends it through the
existing recovery execution gate. Its `idle`, `terminalized`, and `executed`
results retain only their matching compact durable evidence, so task/Job
finalization cannot be reported as model execution. Recovery/operational errors
remain errors, never idle. It has no AMQP channel/action, loop, sleep, backoff,
signal handler, client lifecycle, or Kubernetes behavior. Five mocked tests
cover idle, execution, terminalization priority, propagated errors, and exact
result evidence pairing. The next small task is a pure cadence policy for
interleaving this bounded recovery step with ordinary broker receives, without
starting a worker loop.

[`Demucs recovery cadence`](kubernetes/services/demucs/app/recovery_cadence.py)
now makes that interleaving a pure, frozen local policy. Each new Pod begins
with a recovery scan, then strictly alternates one recovery iteration with one
normal AMQP receive/optional-execution iteration. Therefore continuous queue
traffic cannot delay expired work by more than one normal task; idle,
duplicate/stale, malformed, and executed normal outcomes all schedule recovery
next. The state is not durable task state: loss on restart is safe because the
new Pod scans recovery first and PostgreSQL/RabbitMQ remain authoritative. Four
unit tests prove initial recovery, alternation, starvation prevention, and
forged/out-of-order rejection. This adds no loop, wait, connection, model,
storage, deployment, KEDA, or Kubernetes behavior. The next small task is a
single worker-cycle composition that executes only the selected cadence action
and returns its advanced state.

[`Demucs cadence-driven worker cycle`](kubernetes/services/demucs/app/worker_cycle.py)
now executes exactly that selected action once. It sends the shared RabbitMQ
channel only to the normal receive branch; recovery explicitly receives no
channel because it has no delivery acknowledgement/rejection/publish action.
It passes the same typed source, artifact, FFprobe, model, upload, retry, and
event-ID dependencies to the selected existing bounded composition, advances
cadence only after a valid result, and returns mutually exclusive compact
normal/recovery evidence with its only valid next state. Errors leave cadence
unadvanced and propagate for later supervisor policy. Four mocked tests prove
branch isolation, forwarding, error/forgery behavior, and result pairing. It
adds no loop, wait, connection lifecycle, backoff, Deployment, KEDA, or
Kubernetes behavior. The next small task is a pure supervisor decision policy
that maps cycle outcomes to explicit future wait/retry/exit actions.

[`Demucs supervisor decision policy`](kubernetes/services/demucs/app/supervisor_backoff.py)
now maps compact iteration facts to future actions without performing them.
Only a normal empty broker receive yields the fixed one-second idle wait; every
recovery fact is immediate progress because cadence schedules the normal broker
turn next. A reserved availability event produces a capped in-memory 1, 2, 4,
8, 16, then 30-second exponential backoff with bounded injected jitter, while
a configuration event visibly exits. It neither catches/classifies errors,
sleeps, reconnects, mutates durable work, nor accesses infrastructure. Eight
unit tests cover mapping, bounds/jitter, healthy reset, fatal exit, and input
guards. The next small task is a narrow supervisor-failure classifier that
authorizes only reviewed Demucs configuration and availability errors to select
those reserved failure events.

[`Demucs supervisor failure classification`](kubernetes/services/demucs/app/supervisor_failure_classification.py)
now provides that narrow authority. Bad AMQP/PostgreSQL/MinIO configuration and
missing FFprobe/Demucs executables are fatal; only safe availability wrappers
from bounded RabbitMQ, PostgreSQL, and MinIO adapters receive generic process
backoff. Source/protocol/integrity faults, FFprobe/model errors, and unknown
exceptions stay unclassified for their existing durable task policy or later
operator-visible handling. Four unit tests prove fatal, retryable, fail-closed,
and non-exception boundaries. The next small task is a one-step supervisor
composition that combines this classifier, worker cycle, and decision policy
without waiting or starting a loop.

[`Demucs supervisor step`](kubernetes/services/demucs/app/supervisor_step.py)
now runs exactly one cadence-selected cycle, maps only its matching compact
normal/recovery result to idle/progress, and obtains the next policy action and
state. A recognized retryable/fatal error has no fake completed-cycle evidence
and preserves the selected cadence action, so an outage cannot skip recovery;
unclassified errors propagate and process-control signals are not caught. Five
mocked tests cover recovery/normal idle, retry/fatal preservation, and unknown
error propagation. It neither waits, loops, reconnects, manages a channel, nor
changes Kubernetes. The next small task is a shutdown-aware action adapter
that applies one existing decision through an injected waiter.

[`Demucs supervisor action adapter`](kubernetes/services/demucs/app/supervisor_action.py)
now applies one already-made decision through an injected shutdown waiter.
`check_immediately` continues with no wait, idle/backoff waits once using the
exact bounded delay, and fatal configuration returns an explicit exit fact.
Strict boolean waiter output prevents truthiness from accidentally resuming or
stopping a worker. Five tests cover every control path and malformed waiters.
The adapter itself installs no signals, sleeps nowhere directly, reconnects no
service, and starts no loop. The next small task is a one-step runner that
joins the supervisor step and action result without creating persistence.

[`Demucs one-step supervisor runner`](kubernetes/services/demucs/app/supervisor_once.py)
now executes one supervisor step, applies precisely its returned decision, and
preserves exactly the step's next local state. Mismatched action/state evidence
is rejected; step/action failures propagate rather than becoming a false
`continue`. Four mocked tests cover forwarding, shutdown/continue results,
failure propagation, and pair guards. It is still non-persistent and creates no
loop, signal handler, service lifecycle, image, Deployment, or Kubernetes
behavior. The next small task is a focused shutdown-event adapter that owns
SIGTERM/SIGINT registration for the future injected waiter.

[`Demucs shutdown-event adapter`](kubernetes/services/demucs/app/shutdown_event.py)
now provides that scoped main-thread bridge. It maps SIGTERM/SIGINT to one
`threading.Event`, implements `wait_for_shutdown()` with the same 30-second
policy bound, and restores previous handlers after normal exit, body errors,
or partial install failures. The async handler only sets the Event. Five tests
cover finite/idempotent waits, both signals, restoration, partial cleanup, and
main-thread enforcement. It owns no worker loop, client lifecycle, model
cleanup, image, Deployment, or Kubernetes action. The next small task is the
intentional shutdown-aware supervisor loop that repeats the one-step runner
until it returns shutdown or fatal control evidence.

[`Demucs shutdown-aware supervisor loop`](kubernetes/services/demucs/app/supervisor_loop.py)
now repeats only after `continue`, returns on shutdown/fatal control evidence,
and makes a zero-delay shared-Event check before every fresh cycle. Therefore a
SIGTERM during inference cannot permit another broker receive or recovery scan.
It accepts already-created dependencies and creates, reconnects, inspects, or
closes none. Five mocked tests cover pre-cycle shutdown, continuation,
post-continue signal observation, fatal stopping, and error propagation. The
next small task is a closeable AMQP-session adapter that opens the existing
restricted connection/channel setup and closes it around this loop.

[`Demucs AMQP session`](kubernetes/services/demucs/app/amqp_session.py) now
opens one restricted private connection, obtains and prepares one channel with
prefetch-one/passive-queue verification, yields it, then closes channel before
connection on all setup/body/normal cleanup paths. A normal cleanup failure is
redacted into the existing channel-unavailable category without hiding caller
errors. The necessary direct-settings revalidator now prevents an entrypoint
from redirecting the restricted identity to a foreign host, queue, or account
before any broker I/O. Six unit tests cover validation, cleanup, redaction, and
connection failure. The next small task is a worker bootstrap entrypoint that
composes restricted database/MinIO construction, signal scope, this AMQP
session, and the supervisor loop.

[`Demucs worker bootstrap entrypoint`](kubernetes/services/demucs/app/worker_entrypoint.py)
now composes that already-tested runtime in one narrow process boundary. Before
it opens RabbitMQ, it creates the restricted PostgreSQL adapter, creates one
private MinIO client, and proves the future Pod mounted a real non-symlink
`/worker-scratch` volume instead of silently using the image filesystem. The
entrypoint narrows that one client to the source-read and planned-artifact-write
protocols, scopes SIGTERM/SIGINT and the prepared AMQP session around the
persistent supervisor, and maps only normal terminal loop outcomes to process
status: `0` for clean shutdown and `78` for classified fatal configuration.
Unexpected operational errors intentionally escape after channel-then-
connection cleanup so Kubernetes restart plus durable PostgreSQL/RabbitMQ
recovery remains observable. Four mocked tests cover lifecycle ordering,
dependency narrowing, status mapping, and absent/unsafe scratch mounts. It
does not build an image, add an entrypoint, create a Deployment, or make a
Kubernetes API call. The next small task is a thin executable wrapper that
reports only reviewed bootstrap configuration categories and returns their
visible process status.

[`Demucs executable worker wrapper`](kubernetes/services/demucs/app/worker_main.py)
now performs only that final container-process responsibility. It calls the
bootstrap entrypoint and returns any ordinary terminal status unchanged. It
converts just malformed mounted AMQP/PostgreSQL/MinIO settings or a
missing/unsafe scratch mount into one stable, non-sensitive stderr diagnostic
and status `78`; it never emits the original exception detail, which could
otherwise expose private Service or mounted-Secret context in Pod logs.
Workload, availability, and unexpected failures still propagate after existing
entrypoint cleanup, allowing Kubernetes to observe/restart the failed process
and PostgreSQL/RabbitMQ to recover durable work. Three mocked tests cover
status forwarding, diagnostic redaction, and error propagation. This adds no
image entrypoint, build, Deployment, or Kubernetes API action. The next small
task is a source-only Dockerfile update that starts this module as non-root PID
1 while preserving `/worker-scratch` for the future bounded `emptyDir` mount.

The source-only [`Demucs Dockerfile`](kubernetes/services/demucs/Dockerfile)
now has that explicit runtime process contract. Its final stage stays on the
dedicated non-root `clouddsp-demucs` account and starts the reviewed wrapper
with exec-form `ENTRYPOINT ["python", "-m", "app.worker_main"]`; therefore
Kubernetes SIGTERM reaches the scoped graceful-shutdown path directly rather
than first reaching a shell. The recipe intentionally still does not create
`/worker-scratch`, so a later bounded Pod `emptyDir` must supply it and a
misconfigured mount fails before broker work starts. Two text-only structural
tests lock those boundaries. The already-published Demucs digest predates this
source change and cannot be used as if it included the entrypoint. This task
does not build/push an image or create a Deployment. The next small task is a
local Linux/ARM64 build and inspection only; registry publication and a
workload manifest stay separate.

That source has now built successfully for Linux/ARM64 as the local-only tag
`clouddsp-demucs:0.1.1-worker-entrypoint-local-only`. Docker reports
`503,066,464` uncompressed bytes (`479.76 MiB`). The validation stage ran all
296 Demucs tests, verified FFprobe, and imported the locked Demucs 4.0.1,
Torch 2.4.0, and Torchaudio 2.4.0 runtime. An offline container inspection
confirmed the exec entrypoint, non-root UID `10003`, and that
`/worker-scratch` is absent until a future Pod mounts it; the default wrapper
without configuration produced only its controlled diagnostic and exited `78`.
No image was pushed, no `images.lock.yaml` output digest changed, and no
Kubernetes resource was created. The next small task is a local-registry push
and digest inspection only; catalog recording and a worker Deployment remain
separate.

The verified image is now pushed to the local k3d registry under
`clouddsp-registry.localhost:5001/demucs:0.1.1-worker-entrypoint-local-only`.
The registry's HTTP manifest response returned the immutable OCI index digest
`sha256:f8335a7a78108b74283d9d1d9fc46f82225d9dbe089b8fc961284c44da7f7db0`,
so the exact usable reference is
`clouddsp-registry.localhost:5001/demucs@sha256:f8335a7a78108b74283d9d1d9fc46f82225d9dbe089b8fc961284c44da7f7db0`.
This external registry fact is documented here only: the existing
`images.demucs` catalog entry still identifies the older worker-less artifact,
and no Kubernetes workload was created. The next small task is a focused
catalog update to record this reviewed immutable output; the future Demucs
Deployment stays separate.

[`images.demucs`](kubernetes/images.lock.yaml) now points to the reviewed
`0.1.1-worker-entrypoint-local-only` Linux/ARM64 CPU artifact and its registry-
confirmed immutable OCI index reference. Its provenance now records the
non-root `python -m app.worker_main` PID-1 process contract, the 296-test
validation build, pinned model artifacts, and `503,066,464`-byte local image
size. The catalog is not a Kubernetes resource and this change starts no Pod;
future manifests must copy only its immutable reference, never the readable
tag. The next small task is to prepare a Demucs Deployment manifest without
applying it.

That prepared [`Demucs Deployment`](kubernetes/services/demucs/demucs-deployment.yaml)
is now applied as one private, queue-driven controller in `clouddsp-app`:
Kubernetes defaults the intentionally omitted `replicas` field to one after an
explicit apply, while a later KEDA
`ScaledObject` can own `/scale` without a manifest conflict. It pins the
reviewed ARM64 CPU image digest, never requests CUDA or Apple GPU resources,
and runs the image's UID/GID `10003` account with a read-only root filesystem,
dropped capabilities, `RuntimeDefault` seccomp, no service-account token, and
no implicit Service-link variables. It has no Service or Ingress because the
worker initiates only private PostgreSQL, MinIO, and RabbitMQ ClusterIP
connections. The three mounted app-namespace Secrets are restricted runtime
identities rather than administrator/bootstrap credentials. Its 780-second
termination grace period covers the 720-second bounded Demucs child plus
SIGTERM/AMQP cleanup. A `2Gi` scratch `emptyDir`, `128Mi` `/tmp`, and `64Mi`
HOME mount are all disposable and accounted under a `3Gi` ephemeral-storage
request/limit; PostgreSQL/MinIO remain authoritative durable stores. Four
source-only manifest tests, the full 300-test Demucs suite, and a YAML parse
passed. The prerequisite restricted runtime Secrets and live PostgreSQL/RabbitMQ
authorities were then preflighted, and the first controller Pod was applied.

The first ready Pod exposed an important runtime fact: it had no persistent
idle AMQP socket, while exact in-Pod connection, passive queue, and empty
`basic_get` diagnostics all passed. That ruled out the private Service DNS,
outbound RabbitMQ TCP `5672`, restricted credential, queue, and NetworkPolicy
boundaries. It also clarified that Demucs has no inbound listener: a
`containerPort` or Service would be incorrect and would not configure outbound
AMQP. The new
[`Demucs session supervisor`](kubernetes/services/demucs/app/session_supervisor.py)
makes the observed idle state intentional and safe. Recovery uses PostgreSQL
only; every normal poll creates a fresh restricted/prepared AMQP session, then
closes it after its one bounded cycle. A reviewed session failure retains normal
cadence and follows interruptible local backoff without acknowledging,
requeuing, or mutating durable task state. Three isolated lifecycle tests cover
normal session scope, recovery's no-session boundary, and reconnect behavior.

The rebuilt/pushed Linux/ARM64 CPU image
`0.1.2-short-lived-amqp-sessions-local-only` passed all 304 unit/structure
tests in its Docker validation stage, FFprobe, and the pinned Demucs/Torch/
Torchaudio imports. Docker reported `503,073,487` bytes (479.77 MiB); the local
registry confirmed immutable digest
`sha256:3c721bcad886969ecf2a31b70af1a92a7d2c7058c49abceae97faea0d5c88dd9`.
`images.demucs` and the applied Deployment now use that digest. The rolling
update completed with one ready, zero-restart Pod. No browser-facing port,
Service, Ingress, GPU request, Kubernetes API credential, or cloud source
change was introduced.

The first deployed Demucs-stage smoke later found a recovery-query planning
failure before the worker could open its deliberately short-lived AMQP poll
session. This was not a missing RabbitMQ consumer attachment: zero listed
consumers is expected between `basic_get` turns. Every cadence first performs
the expired-third-attempt terminalization scan, whose prior SQL exposed a task
UUID as text for the Python adapter and then directly compared it to the UUID
Jobs key. PostgreSQL therefore rejected `uuid = text` even on an idle scan.
The prepared `0.1.3-recovery-uuid-join-fix-local-only` image makes that cast
explicit and removes an unnecessary return of `jobs.error_message`, so the
restricted Demucs role needs no broader column privilege. Its Docker validation
stage passed all 304 tests, FFprobe, and the pinned ML imports; a narrow live
query check planned and executed successfully with no eligible candidate. The
new digest is
`clouddsp-registry.localhost:5001/demucs@sha256:99e24301c4c2f37547de7fd8a7773d7e0eae6cfb5f4222db9e00ae1294cae73a`
(479.77 MiB). The image catalog and Deployment source are now pinned to that
digest, and the one-replica Deployment has completed a ready, zero-restart
rollout. A separately reviewed exact-coordinate cleanup Job then removed the
failed smoke's five literal MinIO keys and its guarded fixed Job/outbox state;
the Demucs request queue and DLQ both verified empty. No repeat smoke Job has
been started yet.

The repeat smoke then reached the actual Demucs model process, proving the
delivery, durable claim, source download, and `leased -> running` boundaries
were no longer the immediate fault. The generic Demucs console command exited
with code 132 (`SIGILL`) on the local Docker Desktop/k3d Linux/ARM64 virtual
CPU during model inference. The same fixed local WAV and baked `htdemucs`
model completed and wrote both expected two-stem artifacts after
`torch.backends.mkldnn.enabled = False` was set before `demucs.separate` was
imported. That is a narrow local CPU runtime incompatibility, not an audio
upload, PostgreSQL, RabbitMQ, MinIO, model-lock, or worker-lease issue.

The prepared `0.1.4-local-arm64-mkldnn-sigill-fix` image changes only the
reviewed child launcher: the existing shell-free worker now calls
`/usr/local/bin/python -m app.demucs_cpu_cli`, which disables and verifies the
MKLDNN setting before loading Demucs, then forwards the pre-existing fixed
arguments. The separate subprocess used for each task contains that
process-wide setting; it cannot alter PID 1 or another Pod. Docker validation
now performs a real two-second, two-stem separation and requires both output
files, in addition to all 309 unit tests, FFprobe, and pinned ML imports. The
new 479.77 MiB registry image is
`clouddsp-registry.localhost:5001/demucs@sha256:d0ebd533f66eb2c8097646477a6ba1439349add297bfde578e4a865f08c9186b`.
The image lock and Deployment source are pinned to it, but applying the
Deployment and repeating the smoke remain explicit follow-up actions. This
workaround applies only to the local CPU profile; a future Linux/NVIDIA GPU
profile needs its own validated backend configuration.

The next live smoke then exposed a separate command-startup defect, not a
second Torch failure. The model process adapter intentionally gives each child
its freshly allocated output directory as its working directory. Under that
isolation, the prior `python -m app.demucs_cpu_cli` form searched the empty
output directory rather than the image's `/app` application tree, so Python
could not import the launcher and the durable task safely became
`demucs_process_failed`. The `0.1.5-local-arm64-launcher-cwd-fix` revision
uses the same narrow launcher and MKLDNN policy through the absolute command
`/usr/local/bin/python /app/app/demucs_cpu_cli.py`. Its Docker validation
explicitly changes to the private output-directory context before executing a
real two-second, two-stem inference. All 309 tests, FFprobe, locked runtime
imports, and that exact-context inference passed. The immutable image is
`clouddsp-registry.localhost:5001/demucs@sha256:43b4c352a3c4bf077fe685a0904d368f5cb89c2f40a25b1b2a006045b2ec231c`
(479.77 MiB). The image lock and Deployment source now point to it, but the
live Deployment is deliberately unchanged pending an explicit rollout.

That renewal boundary now exists in
[`running_lease_renewal.py`](kubernetes/services/demucs/app/running_lease_renewal.py).
It requires a committed `DemucsRunningSource`, forwards only its exact task ID
and lease token to the existing short PostgreSQL renewal transaction, and
returns either matching immutable running/source evidence with a refreshed
expiry or an explicit ownership-loss stop signal. An outage or malformed
database result still raises rather than being disguised as a renewed/expired
lease. Five unit tests cover expiry-only replacement, no-row loss, capability
guards, exception propagation, and result pairing. It deliberately does not
start a timer/thread or stop a model process; the execution workspace now uses
it at the later cancellation-safe process checkpoints.

That process layer now exists in the separate renewal-aware entrypoint in
[`demucs_process.py`](kubernetes/services/demucs/app/demucs_process.py). It
requires a runner that owns the actual child process group; a plain synchronous
runner is rejected rather than being placed in an uncancellable Python thread.
The production runner waits only to the next one-minute-or-faster checkpoint,
the model deadline, or child exit. A `False` checkpoint, checkpoint exception,
or timeout terminates the full child process group *before* its stop/error
signal leaves the runner. The layer deliberately knows no PostgreSQL task,
lease token, MinIO, or RabbitMQ detail.

[`executed_separation_workspace.py`](kubernetes/services/demucs/app/executed_separation_workspace.py)
now provides that narrow task-specific wiring. It calls the committed renewal
checkpoint only while the owned model child is running, carries refreshed
immutable lease evidence into later artifact/completion guards, and converts a
runner-stopped lost lease into normal one-attempt ownership loss before the
running-failure retry/terminal policy can see it. The renewal path rejects a
plain runner, so a stale child cannot be left alive in a Python thread. Three
integration tests cover the checkpoint handoff, refreshed downstream evidence,
stopped ownership loss, and the one-attempt outcome mapping. The next small
task is atomically composing recovery claim and reconstructed evidence before
the ordinary pre-model runtime receives its committed pair.

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

### Prepared ADTOF runtime-MinIO authorization smoke boundary

[`adtof-minio-runtime-policy-smoke-job.yaml`](kubernetes/services/adtof/adtof-minio-runtime-policy-smoke-job.yaml)
was prepared, structurally tested, and successfully applied. It runs in
`clouddsp-app` and receives exactly the two values in the existing
`clouddsp-adtof-minio-credentials` Secret. It carries no MinIO administrator
or bootstrap credential, database/broker credential, ServiceAccount token,
Service, Ingress, or Kubernetes API access.

The Job uses the existing digest-pinned official AWS CLI as an S3 protocol
client, with its endpoint explicitly fixed to MinIO's private ClusterIP
Service. It proves the live narrow policy without leaving a completed object:
the allowed `midi/<unique-run-id>/drums.mid` multipart upload accepts one tiny
part and lists it, then `AbortMultipartUpload` removes its temporary server
state. This exercises the ADTOF policy's output `PutObject`,
`ListMultipartUploadParts`, and `AbortMultipartUpload` actions. The same
runtime identity then attempts `midi/<same-run-id>/vocals.mid`; MinIO must
return an explicit access denial because that filename lies outside the exact
ADTOF policy. An unexpected success, transport error, or ambiguous error fails
the Job. Exit and termination cleanup both best-effort abort the allowed upload;
the termination trap then exits, so a SIGTERM-interrupted script cannot continue
into another request.

It intentionally does not use a fabricated drums `GetObject` request: without
`ListBucket`, object storage can hide the absence of a made-up object behind a
403 response. The successful result confirmed the allowed temporary multipart
upload and mandatory foreign-key denial; the abort removed the incomplete
upload rather than retaining a completed smoke object. The ADTOF Deployment was
then rolled out successfully. Its `basic_get` polling loop creates a live
RabbitMQ connection but intentionally no `list_consumers` row. A later
end-to-end worker smoke test will verify actual read access when it owns a
known durable Demucs drums object.

### ADTOF end-to-end worker smoke fixture

[`tests/adtof-worker-smoke/`](kubernetes/tests/adtof-worker-smoke/) now holds
the source-only fixed fixture for a future production-like ADTOF worker smoke
test. Its standard-library
[`fixture module`](kubernetes/tests/adtof-worker-smoke/client/adtof_worker_smoke_fixture.py)
reserves one canonical Job, upstream synthetic-Demucs-task, and request-event
identity; limits a future smoke MinIO identity to exactly one drums input and
two ADTOF output keys; and builds a bounded deterministic four-second 120-BPM
PCM drums pattern in memory. It derives the actual size and SHA-256 at runtime
and supplies the complete Demucs stem metadata that ADTOF independently checks
before downloading its input.

The fixture is deliberately not an audio-quality benchmark. A future successful
run will prove durable publication through the generic dispatcher, one real
ADTOF claim/completion, and the validated private MIDI/tempo objects. It will
not assert an exact model note count, BPM, or confidence level. No fixture
source contacts MinIO, PostgreSQL, RabbitMQ, ADTOF/PyTorch, Docker, or
Kubernetes; no smoke object or durable row exists yet.

### Prepared ADTOF worker-smoke PostgreSQL boundary

The source-only
[`adtof-worker-smoke-database-bootstrap-job.yaml`](kubernetes/tests/adtof-worker-smoke/adtof-worker-smoke-database-bootstrap-job.yaml)
now defines the next narrow database step. It is not applied. Its ignored
Secret templates make one temporary data-namespace and one future app-namespace
copy of a dedicated `clouddsp-adtof-worker-smoke` login; the real password stays
outside Git. The short-lived administrator Job validates the v004/v006 schema
gates, removes direct table/sequence/function privileges, and grants that role
only three exact administrator-owned `SECURITY DEFINER` functions.

`prepare(size, sha256)` accepts only bounded generated-WAV evidence and
transactionally creates the one fixed 4-stems Job plus one fixed
`adtof.requested` outbox event. `observe()` reads only corresponding
publication/task facts. `cleanup()` can delete the fixed Job only after a
published first-attempt ADTOF task succeeded with no lease; failed/interrupted
evidence remains. This boundary has no MinIO, RabbitMQ, Keycloak, Kubernetes
API, or runtime-object access. The ignored local credentials were then applied
and the database-only bootstrap completed successfully; no smoke object, Job,
outbox event, task, or broker message was created.

### Prepared ADTOF worker-smoke MinIO identity

The source-only
[`adtof-worker-smoke-minio-policy-v001-configmap.yaml`](kubernetes/tests/adtof-worker-smoke/adtof-worker-smoke-minio-policy-v001-configmap.yaml)
defines one independent S3-compatible identity for the later smoke client. It
can `PutObject` only to the fixed controlled `drums.wav` input, and can
`GetObject`/`DeleteObject` only for that input plus the two fixed ADTOF output
keys. It has no wildcard, bucket-list, presign, output-write, normal-user
object, or MinIO-administration authority.

The paired
[`MinIO bootstrap Job`](kubernetes/tests/adtof-worker-smoke/minio-adtof-worker-smoke-objects-bootstrap-job.yaml)
mounts root credentials only in ordered temporary init containers. It creates
the immutable policy, creates/rotates the restricted user from a temporary
data-namespace Secret, attaches/prints the policy association, then removes its
root alias before the completion marker begins. The actual smoke client will
receive only the separate app-namespace restricted Secret. Neither new Secret,
ConfigMap, nor bootstrap Job created an object, PostgreSQL record, or RabbitMQ
event. The ignored local Secrets and ConfigMap were applied and the bootstrap
completed successfully, reporting the user enabled with exactly the reviewed
`clouddsp-adtof-worker-smoke-objects-v001` policy attached.

The first Job attempt revealed a shared manifest error rather than a MinIO
policy or deadline problem: its `docker.io/minio/mc` repository cannot resolve
the otherwise valid digest. The central image lock and every Kubernetes MinIO
Client manifest now use `quay.io/minio/mc` with the same pinned multi-platform
digest. The corrected Job completed in fourteen seconds. The next task is the
source-only end-to-end smoke client, not a runtime smoke Job.

### ADTOF worker-smoke client contract

[`adtof_worker_smoke_contract.py`](kubernetes/tests/adtof-worker-smoke/client/adtof_worker_smoke_contract.py)
is now the dependency-free first layer of that client. It centralizes the fixed
private PostgreSQL/MinIO routes, fixed bucket, restricted identity names, three
security-definer function names, and a bounded observation window. It rejects
environment-based endpoint, port, database-role, or S3-identity substitution
before any future network adapter can be created.

The contract deliberately receives high-level least-privilege interfaces—not
generic Boto3/Psycopg clients. The eventual database adapter can only prepare,
observe, and successful-clean the reserved Job/event. The eventual object
adapter can only prove the three fixed keys are clean, upload the controlled
drums WAV, verify the two ADTOF outputs, and clean exactly those keys after
success. Typed observations require broker publication plus one succeeded,
first-attempt, lease-cleared ADTOF task while the Job remains
`midi_processing`. Nine standard-library unit tests prove those boundaries.

The next small task is a source-only fixed-key MinIO adapter for clean-state
checks and controlled-WAV upload. It must not add PostgreSQL, RabbitMQ, ADTOF
output verification, an image, or a Kubernetes Job.

### ADTOF worker-smoke fixed-key MinIO input adapter

[`adtof_worker_smoke_minio_input.py`](kubernetes/tests/adtof-worker-smoke/client/adtof_worker_smoke_minio_input.py)
now implements that initial storage phase over an injected S3-shaped protocol.
It imports no Boto3 or other network client. It checks exactly the three
policy-granted keys for explicit absence, refuses to overwrite a present key,
and treats MinIO permission/transport failures as infrastructure failures rather
than a clean state. Its only write is the deterministic drums WAV at its exact
key with the complete Demucs metadata inventory; a post-write `HeadObject`
comparison establishes length/type/metadata evidence before a durable database
event may exist. Fifteen standard-library client tests prove no generic-list,
arbitrary-key, output-read, or delete capability was added.

### ADTOF worker-smoke fixed-function PostgreSQL adapter

[`adtof_worker_smoke_database.py`](kubernetes/tests/adtof-worker-smoke/client/adtof_worker_smoke_database.py)
now implements the three fixed smoke-function calls over an injected
DB-API-like connection factory. It imports no Psycopg and has no raw SQL/table
access capability. `prepare` commits only after the function returns the
reserved Job/event pair; `observe` accepts only its safe eight-column projection
and explicitly rolls back the read transaction; successful-only `cleanup`
commits a `true` deletion but rolls `false` back. Driver failures are converted
to bounded infrastructure categories and every connection closes in `finally`.
Twenty-one standard-library client tests now cover the fixture, settings,
MinIO-input, and database boundaries.

### ADTOF worker-smoke fixed-key output reader

[`adtof_worker_smoke_minio_outputs.py`](kubernetes/tests/adtof-worker-smoke/client/adtof_worker_smoke_minio_outputs.py)
now adds the narrow read-only verification boundary over an injected
S3-compatible client. It requires the real worker-created dynamic task ID from
the successful database observation and the exact input evidence from the
previous MinIO upload proof. For each exact ADTOF output key it checks both
current `HeadObject` and streamed `GetObject` headers/metadata, bounded bytes,
and SHA-256. The MIDI verifier checks complete Standard MIDI framing; the tempo
verifier checks the bounded cloud-compatible ADTOF JSON candidate schema. It
does not make an assertion about note count, tempo, or inference quality.

The adapter exposes neither a generic key/bucket method nor a mutation: it
cannot list, put, or delete objects, construct Boto3, call PostgreSQL,
publish RabbitMQ work, invoke ADTOF, build an image, or create a Job. Twenty-five
standard-library client tests now cover the fixture, contract, MinIO input,
PostgreSQL function, and output verification boundaries.

### ADTOF worker-smoke successful-only object cleanup adapter

[`adtof_worker_smoke_minio_cleanup.py`](kubernetes/tests/adtof-worker-smoke/client/adtof_worker_smoke_minio_cleanup.py)
now defines the future client's only destructive S3-compatible capability. It
requires the already observed published/succeeded first attempt with its lease
cleared, plus the fixed input proof and verified MIDI/tempo output pair, before
any deletion can begin. It then calls only `DeleteObject` for the fixed tempo,
MIDI, and controlled input keys—never a bucket, prefix, caller-chosen key, or
retry loop. A failure stops in order and is redacted, retaining the input WAV
when an earlier output deletion fails.

This operation is intentionally not a distributed transaction. It neither
opens PostgreSQL nor claims that object deletion removes the durable Job/event;
a later state-machine/composition boundary makes that cleanup ordering explicit. Twenty-nine
standard-library tests now cover the fixture, contract, MinIO input/output,
database function, and successful-only object-cleanup boundaries.

### ADTOF worker-smoke orchestration state machine

[`adtof_worker_smoke_orchestration.py`](kubernetes/tests/adtof-worker-smoke/client/adtof_worker_smoke_orchestration.py)
now makes the existing source-only adapter sequence explicit without calling a
single adapter. Its immutable phases allow only: fixed-key preflight, controlled
WAV upload, atomic durable prepare, bounded worker observation, output
verification, fixed-object cleanup, guarded database cleanup, and `passed`.
The future composition root must supply elapsed monotonic time; this pure model
does not read a clock or sleep.

Pending/running progress remains observable until the reviewed deadline. A
dead-lettered event, retry, second task attempt, task failure, failed Job, or
incoherent would-be success becomes a terminal evidence-preserving failure.
The model accurately distinguishes those outcomes from `object_cleanup_incomplete`
and `database_cleanup_incomplete`, because S3-compatible MinIO and PostgreSQL
cannot offer a shared transaction. It records the documented object-first,
guarded-database-second cleanup sequence, and has no retry or resource
creation capability. Thirty-four standard-library client tests now cover all
fixture, boundary, and pure workflow transitions.

### ADTOF worker-smoke one-step composition facade

[`adtof_worker_smoke_composition.py`](kubernetes/tests/adtof-worker-smoke/client/adtof_worker_smoke_composition.py)
now executes only the action selected by the pure state machine through the
four existing injected boundaries. It passes the deterministic input proof to
the fixed database prepare function, passes future caller-supplied elapsed
monotonic seconds only to observation, and passes only the accumulated success
proof to output verification and cleanup. It cannot call a direct RabbitMQ
publisher/consumer or a generic S3/PostgreSQL method.

The facade creates no concrete SDK/driver client, socket, clock/sleep loop,
image, Secret, or Kubernetes Job. An injected adapter error updates the stored
state to its truthful terminal outcome and raises one redacted composition
error. Thirty-nine standard-library tests now cover fixture, boundaries, pure
states, and one-action composition behavior.

The next small task is to create a hash-pinned, source-only runtime dependency
lock for the future entrypoint's minimal PostgreSQL and S3-compatible clients.
It will not construct the entrypoint, image, or Kubernetes Job.

### ADTOF worker-smoke runtime dependency lock

[`requirements.lock`](kubernetes/tests/adtof-worker-smoke/client/requirements.lock)
now pins the full CPython 3.12/Linux ARM64 S3-compatible and PostgreSQL driver
closure for the future smoke entrypoint: Boto3/Botocore, their strict
transitives, Psycopg, and the matching binary libpq wheel. Every package has
one reviewed SHA-256, so a future image must use `pip --require-hashes`; it
cannot silently resolve a mutable or unreviewed transitive package. The lock
deliberately excludes RabbitMQ, HTTP, identity, ML/GPU, and Kubernetes packages
because the smoke client observes those deployed components rather than owning
their responsibilities.

Four offline tests verify the exact closure, paired Boto3/Botocore and Psycopg
versions, binary ARM64 hash, and lack of index/direct-URL escape hatches.
Nothing was installed, no entrypoint/image/Job was created, and no cluster
resource changed. The next small task is a source-only lazy PostgreSQL
connection factory that consumes the fixed settings and lock; MinIO construction
and workflow execution remain out of scope.

### ADTOF worker-smoke lazy PostgreSQL factory

[`adtof_worker_smoke_postgresql.py`](kubernetes/tests/adtof-worker-smoke/client/adtof_worker_smoke_postgresql.py)
now returns a zero-argument connection factory for the existing three
fixed-function database adapter. Factory construction is side-effect free. Its
future invocation lazily imports Psycopg, revalidates the exact internal
Service/database/restricted-role authority, and opens a dictionary-row,
non-autocommit connection with a three-second connect and five-second
server-side statement bound. The adapter retains responsibility for each short
commit/rollback/close lifecycle, so no transaction crosses worker observation.

The focused tests use a patched driver to prove exact connection arguments,
laziness, forged-setting rejection, and redacted dependency/connection failure
categories. No SQL, MinIO client, workflow execution, image, or Job was added.
Forty-seven standard-library smoke-client tests now pass. The next small task
is an equally narrow lazy MinIO S3-compatible client factory; it will not run
the workflow or create an image/Job.

### ADTOF worker-smoke lazy MinIO factory

[`adtof_worker_smoke_minio.py`](kubernetes/tests/adtof-worker-smoke/client/adtof_worker_smoke_minio.py)
now lazily imports Boto3/Botocore and constructs the future smoke client's one
private path-style S3 client. It repeats the fixed internal Service URL, region,
and restricted access-key checks before credentials reach the SDK; explicit
credentials prevent host AWS-profile or metadata lookup. The client receives
SigV4 configuration, a three-second connect timeout, ten-second read timeout,
and two bounded SDK attempts. It performs no object request by itself.

Patched-SDK tests verify client-construction laziness, exact route/credential
arguments, settings forgery rejection, and redacted SDK failures. No PostgreSQL
call, workflow run, image, or Job was added. Fifty-one standard-library client
tests now pass. The next small task is a source-only runtime-assembly function
that combines the two factories with the existing fixed adapters/facade; it
will not add a loop, image, or Kubernetes Job.

### ADTOF worker-smoke runtime assembly

[`adtof_worker_smoke_runtime.py`](kubernetes/tests/adtof-worker-smoke/client/adtof_worker_smoke_runtime.py)
now validates the pure observation deadline, constructs the lazy PostgreSQL
connection factory, constructs one configured MinIO client, and wires that
client into the existing fixed-key input/output/cleanup adapters before
returning a preflight-state composition facade. The database adapter receives
only its zero-argument factory, so assembly opens no PostgreSQL connection;
the MinIO factory makes no object request. A future entrypoint alone may call
the facade's one-action method.

Three patched-factory tests confirm one-client wiring, no assembly-time
database/S3 operation, and early timeout rejection. The module has no SDK,
driver, clock/sleep loop, entrypoint, image, or Job capability. Fifty-four
standard-library smoke-client tests now pass.

### ADTOF worker-smoke bounded runtime entrypoint

`kubernetes/tests/adtof-worker-smoke/client/adtof_worker_smoke_entrypoint.py`
is now the future one-shot Job process boundary. It loads the fixed settings,
builds the wired composition once, and advances only its currently allowed
action. It uses monotonic elapsed time and a two-second maximum cadence solely
while observing the deployed dispatcher and ADTOF worker; no I/O failure gains
an implicit retry loop. It reports non-sensitive milestones and returns zero
only for the state-machine `passed` outcome. Every other terminal fact,
including evidence-preserving failure, timeout, or incomplete cleanup, returns
non-zero without exposing credentials or SDK details.

Five offline entrypoint tests use a fake facade, clock, and sleeper to verify
cadence, non-retry behavior, exit mapping, and error redaction. The full client
suite now has fifty-nine standard-library tests. No image, Kubernetes Job, or
cluster resource has been created.

### ADTOF worker-smoke digest-pinned image recipe

`kubernetes/tests/adtof-worker-smoke/client/Dockerfile` now defines a
two-stage, source-only recipe for the future finite verifier Job. Both stages
use the existing immutable `images.api-python-runtime` Python 3.12 digest. The
validation stage hash-installs the complete explicit Boto3/Psycopg closure,
runs every client test, and never reaches a cluster service. The runtime stage
receives only verified site-packages and the bounded client graph; it executes
the reviewed entrypoint as a dedicated non-root UID/GID 10006 process. It has
no Secret, runtime package install, AMQP/model/cloud/Kubernetes client, HTTP
listener, Service, or Ingress behavior.

The lock's Psycopg wheel is Linux/ARM64-specific, so the build explicitly used
`--platform linux/arm64`. Four static tests inspect the Dockerfile's digest,
hash enforcement, runtime contents, identity, and omitted capabilities without
calling Docker. Docker's validation stage then ran all sixty-three smoke-client
tests successfully and produced the local tag
`clouddsp-registry.localhost:5001/adtof-worker-smoke-client:0.1.0-durable-worker-path`.
Its local image ID is
`sha256:0c66126460d43e2cb5766d585055f00c7fa0dad9da7f9df3c944f21d216dfe41`
and Docker reports 66,164,217 uncompressed bytes (63.10 MiB). The local k3d
registry accepted the image and confirmed that same immutable digest. It is now
recorded as `images.adtof-worker-smoke-client`, with the exact source recipe,
ARM64-only platform, hash lock, test directory, and readable-tag provenance;
future manifests must copy only the immutable reference. No Kubernetes
Job/resource, smoke object, database event, or queue message has been created.

### ADTOF worker-smoke reproducible build/push helper

`kubernetes/scripts/build-adtof-worker-smoke-client-image.sh` now gives the
published image one non-interactive local rebuild path. It validates the exact
Docker context inputs, Docker daemon, and the named k3d registry before an
explicit `linux/arm64` Docker build. The Dockerfile itself runs the offline
client suite; only then does the helper push the dedicated repository, discover
the registry-confirmed immutable reference, and print its uncompressed local
size. It accepts no runtime credentials, never calls `kubectl`, and creates no
Kubernetes resource or pipeline evidence. A rebuild can change its digest, so
that output must be deliberately reviewed and copied into `images.lock.yaml`
before a future manifest refers to it. The next small task is the source-only
ADTOF smoke Job manifest; applying it remains separate.

### ADTOF end-to-end worker-smoke Job manifest

`kubernetes/tests/adtof-worker-smoke/adtof-worker-smoke-job.yaml` now defines
the finite verifier Pod without applying it. It executes only the immutable
`images.adtof-worker-smoke-client` ARM64 reference in `clouddsp-app`, under its
dedicated UID/GID 10006 with a read-only root filesystem, default seccomp, no
Linux capabilities, no Kubernetes API token, and no automatic Service-link
environment. It mounts only the previously applied restricted PostgreSQL
function and MinIO exact-key credentials; it has no RabbitMQ, worker,
administrator, Keycloak, browser, or cloud credential.

The entrypoint observes for twelve minutes inside a thirteen-minute Job
deadline. `backoffLimit: 0` intentionally preserves the first failure's fixed
evidence instead of risking an automatic collision. A successful path executes
the client’s reviewed object-first/database-second cleanup, while failed or
timed-out evidence remains for a later explicit cleanup task. Four offline
structural tests check the immutable image, identities, routes, finite timing,
and hardened Pod settings. The manifest has not been applied and has created no
Pod, object, database event/task, or RabbitMQ message. The next small task is
to review the explicit apply/log/inspection procedure; applying remains
separate.

### ADTOF worker-smoke reviewed run procedure

The smoke README now gives a copy/paste procedure without executing it. Its
read-only preflight checks only the two app-namespace restricted Secret names,
the generic dispatcher rollout, the ADTOF rollout, and whether an existing
fixed-name Job needs investigation. The one subsequent `kubectl apply` command
is clearly isolated as the sole resource-creating action. Follow-up commands
tail safe entrypoint milestones and inspect Job/Pod status without printing
Secret values. A failed run must not be reapplied, manually messaged, or
silently cleaned: its durable fixed evidence is the diagnosis surface for a
later narrow cleanup task. The Job is still unapplied; explicit user direction
is required before the resource-creating command is run.

## Validated Demucs worker stage smoke

The repository now has a validated stage smoke under
`kubernetes/tests/demucs-worker-smoke/`. Its tiny digest-pinned ARM64 client
creates one fixed, valid source upload through restricted PostgreSQL and MinIO
capabilities.  The ordinary generic dispatcher publishes the durable
`demucs.requested` event and the deployed Demucs worker consumes it; the smoke
client never receives RabbitMQ access.  The test accepts only a first-attempt
Demucs success with two durable downstream events and two private, streamed
hash-matched WAV stem objects.  It then waits for the two downstream Basic
Pitch tasks solely to avoid deleting stems while they are in use.

The restricted bootstrap identities and the smoke passed locally. During the
first full run, Demucs completed its CPU model work and private uploads but
PostgreSQL rejected the atomic completion CTE because it compared the CTE's
text `job_id` directly with a UUID column. The repaired
`0.1.8-completion-uuid-join` image casts that value explicitly, is digest-pinned
in `images.lock.yaml` and the Deployment, and passed all 312 image tests plus a
real two-stem CPU inference. Its clean end-to-end smoke run completed in 81
seconds, proving the Job/task/stem/outbox transition and safe downstream
cleanup barrier. A failed run retains evidence intentionally; its documented
operator recovery uses a fixed-coordinate, no-RabbitMQ cleanup Job after
scaling the worker down, rather than manual database/object deletion. This
completed correctness gate makes a Demucs KEDA `ScaledObject` the next
capacity-focused milestone.

The prepared
[`Demucs KEDA ScaledObject`](kubernetes/services/demucs/demucs-scaledobject.yaml)
now reuses the applied private RabbitMQ management ClusterIP, NetworkPolicy,
and read-only observer TriggerAuthentication used by Basic Pitch and ADTOF. It
observes only `clouddsp.demucs.requests`, has a one-message target that counts
unacknowledged work, polls every 15 seconds, and uses a five-minute cooldown.
The local Apple-Silicon CPU cap is deliberately `0 → 1`: Demucs requests
1 CPU/2 GiB (up to 2 CPU/4 GiB), so preserving queued work is safer than
starting competing model Pods on the development cluster. This source and its
structural test were then applied and observed through the ordinary restricted
Demucs smoke route. One durable request activated KEDA and scaled `0 → 1`; the
fresh worker completed the 86-second source-to-stems smoke successfully, and
the inactive queue returned the Deployment from `1 → 0` after the configured
five-minute cooldown. This validates the conservative local CPU policy only;
future threshold, resource, GPU-node, or capacity changes require a separate
review and controlled observation.

## Planned bounded six-stem end-to-end load test

The approved local load-test contract is three concurrent authenticated
six-stem jobs. That produces three capped Demucs requests, fifteen Basic Pitch
requests, and three ADTOF requests through the normal API, presigned upload,
MinIO notification, upload-intake, PostgreSQL outbox, RabbitMQ, worker, and
KEDA path. It is intentionally bounded rather than a production-throughput
claim: local CPU Demucs remains capped at one Pod, while Basic Pitch and ADTOF
may demonstrate their existing three- and two-Pod caps. One disposable
Keycloak user will own all three server-generated Jobs and use the normal
authenticated API plus constrained upload forms; it does not replace the
separate browser PKCE tests. The load client has no database, object-store,
broker, Keycloak-admin, or Kubernetes credential. Dedicated companion
observers hold only reviewed database/object/broker observation capability and
a read-only KEDA `Role`. A separate lifecycle broker verifies the temporary
owner's three API-visible Jobs before minting their exact database/object
capabilities; it records aggregate-only evidence and performs guarded cleanup
only after success. A failed run preserves all durable evidence and requires a
later marker-scoped recovery Job. The tested standard-library Keycloak
identity adapter and lifecycle-broker entrypoint now create/revoke the
temporary direct-grant client and user without a live cluster change. The
broker writes only the temporary user configuration through a bounded 0600
handoff and revokes it on a reported terminal outcome or graceful SIGTERM. The
ARM64 test-client image is built, digest-pinned, locally registry-verified,
and recorded at `images.six-stem-load-client` (48.25 MiB; tag
`0.1.9-postgresql-state-observer`, digest
`sha256:b5b8c9dcecf42ca4c0530f0575d99d40ee94f535bfddfd3e4e5bdec0fcda752b`). The ignored local
Keycloak bootstrap-credential template and a valid but suspended broker-only
Job skeleton now document the single Secret mount, the memory-backed handoff,
and graceful teardown; neither has been applied. The tested authenticated
three-concurrent-job ingress library now uses the ordinary Job API and
constrained upload forms only. It atomically returns the temporary user's
canonical subject plus the three fixed-mode, server-generated Job coordinates
through the 0600 shared handoff. The broker now verifies those untrusted claims
through normal owner-bound Job API reads before any future observer setup.

The tested broker-side owner-bound verification adapter now obtains a fresh
temporary-user token, confirms the Job API's accepted subject, and reads the
three claimed Jobs individually. It accepts only exact direct-upload snapshots
with the handed-off canonical IDs, filenames, byte sizes, audio type, and
`6-stems` mode. It deliberately has no database, MinIO, RabbitMQ, Kubernetes,
or Keycloak-administrator operation. The broker now composes that proof in a
distinct coordinate-ready phase: it waits for the complete private claim,
shares one overall deadline with the later terminal-outcome wait, writes only
a marker/mode/count verified-ingress signal, and then continues. That signal
is explicitly non-authoritative because the same Pod UID can write the shared
volume; a later broker-owned in-process step must not mint observer capability
from the marker alone. Verified ingress must never be mistaken for pipeline
completion.

The first observer-preparation artifact is now a source-only immutable
capability contract. Given only the broker's in-memory verified proof, it
revalidates the exact three canonical direct-upload coordinates and derives
the temporary owner's three database Job IDs plus only their exact source
object keys and `stems/`/`midi/` prefixes in `clouddsp-uploads`. It refuses the
writable verified-ingress marker, carries no role/user name or credential, and
performs no file, network, database, object-store, broker, or Kubernetes
operation. The next isolated task is the corresponding PostgreSQL
aggregate-only observation-function/temporary-role contract; neither a role
nor any observer permission has been created yet.

That PostgreSQL contract is now defined as source-only SQL rendering, not a
database change. It creates the blueprint for one marker-derived `LOGIN
NOINHERIT` role with a connection limit of one and a single zero-argument,
administrator-owned `SECURITY DEFINER` aggregate function. The function has
the temporary owner and exactly three verified UUIDs embedded after strict
validation; it returns only job/task/outbox counts and status maps. It takes no
arguments, reads no raw object/error/event fields, revokes default `PUBLIC`
execution, and grants the future role only `EXECUTE` on that function. The
contract includes no password, `CREATE ROLE`, database client, bootstrap Job,
or Kubernetes resource. The ignored local administrator credential template
now documents a temporary app-namespace copy of the existing PostgreSQL
administrator values; it explicitly directs the operator to copy the actual
local values rather than assume the committed example username is in use. A
settings reader accepts dedicated broker environment keys, pins the
destination to `clouddsp-postgresql.clouddsp-data.svc:5432` and database
`clouddsp`, bounds username/password inputs, and hides the credentials from
its normal representation. It is included in the newly built image.

The broker now invokes a Psycopg-backed bootstrap only after its temporary
Keycloak identity has verified all three Job snapshots. It generates a
run-scoped observer password in memory and computes its SCRAM-SHA-256 verifier
locally, so raw password text is not embedded in the SQL sent to PostgreSQL.
One administrator transaction creates the fixed-attribute observer role,
creates its marker-bound no-argument aggregate function, revokes that function's
default `PUBLIC` execute privilege, grants only its explicit `EXECUTE`, and
checks catalog ACLs/role attributes before commit. Any mismatch rolls back the
role and function together. The transaction checks direct database/schema/
function grants, memberships, function owner and `SECURITY DEFINER` search path,
and rejects effective table, sequence, or schema-create privileges.

Only after commit does the broker atomically hand the raw temporary login to a
separate 1 MiB memory-backed `emptyDir`. The authenticated load client never
mounts this second volume; the durable-state observer container must mount it
read-only. At terminal cleanup the broker revokes the marker-derived function
and role without `CASCADE`, then removes only the known credential file. This
task changed source, the pinned Psycopg requirements lock, the Dockerfile, and
the still-suspended skeleton; it did not apply Secrets or Kubernetes resources.
The offline source suite passed 40 tests, and the rebuilt ARM64 image
validation stage passed 38 tests. The image was pushed as
`0.1.9-postgresql-state-observer`, then pulled by its OCI digest to verify the
registry content. Its recorded size is 50,596,805 bytes (48.25 MiB), and its
immutable reference is
`clouddsp-registry.localhost:5001/six-stem-load-client@sha256:b5b8c9dcecf42ca4c0530f0575d99d40ee94f535bfddfd3e4e5bdec0fcda752b`.
The skeleton remains suspended. The PostgreSQL durable-state observer is now
implemented at
`kubernetes/tests/six-stem-load/client/postgresql_durable_state_observer.py`.
It reads only the broker-minted credential file, reconnects only to the fixed
PostgreSQL Service in read-only transaction mode, calls the exact
marker-derived zero-argument function with ten explicit aggregate columns,
and validates the one-row response before returning counts/status categories.
Its five offline tests use a fake connection and temporary handoff. The
adapter is packaged in the ARM64 image tagged
`0.1.9-postgresql-state-observer`, digest-pulled from the local registry, and
import-verified with Psycopg 3.2.13. The image validation stage passed 38
tests.

The source-only `postgresql_observer_report.py` defines a separate,
memory-backed observer report handoff. It atomically writes one `0600` report,
binds it to the broker's expected run marker, and accepts only a validated
aggregate snapshot or one fixed failure category. The full local source suite
passed 46 tests at that stage. This snapshot remains non-terminal: it does not
decide overall load success and does not inspect MinIO, RabbitMQ, or KEDA.

The broker now consumes that report as an intermediate step, then waits for a
separate `load_test_terminal_outcome.py` report bound to the same run marker.
The terminal outcome contract permits only `succeeded` or `failed` and a
fixed failure category; a PostgreSQL snapshot cannot satisfy the terminal
waiter or trigger temporary-identity cleanup. The broker shares one finite
deadline across both waits and revokes only the temporary PostgreSQL role and
Keycloak identity after the terminal report, timeout, or safe failure path.
The new report tmpfs is reserved for the broker and trusted future observer /
orchestrator, not the authenticated client. The offline client suite now
passes 51 tests, and the suspended skeleton test suite passes 3 tests. The
updated ARM64 broker image was built and pushed as
`0.1.10-terminal-report-handoff`, digest-pulled from the local registry, and
recorded at
`clouddsp-registry.localhost:5001/six-stem-load-client@sha256:edb5631a4a86d34fec0f974bdaeeea99df893f898d322dc1733f3c00c50d8993`.
Its local Docker size is 50,604,261 bytes (48.26 MiB); its image validation
stage passed 49 tests. No Secret, Job, or cluster resource was applied. Next,
implement the trusted observer/orchestrator that publishes the PostgreSQL
snapshot, polls processing evidence, and emits the terminal report only after
PostgreSQL, MinIO, RabbitMQ, and KEDA checks are combined.

### Current implementation status — 2026-09-23

The three-job six-stem load-test runner is now fully wired in source and a
suspended Job manifest. The Pod has a restricted root init container that
secures private `emptyDir` handoff mounts, a lifecycle broker, a normal Keycloak-authenticated
API/presigned-upload client, and an independent observer for durable
PostgreSQL state, exact MinIO object hashes, RabbitMQ queue depths, and KEDA
scale/cooldown evidence. The observer requires the exact 3/15/3 worker task
shape, 42 source/stem/MIDI objects, empty main/retry/DLQ queues, no failed task
or lease, each worker observed active without exceeding its cap, and a return
to zero. Its ServiceAccount has namespaced `get` only on the three worker
Deployments, generated HPAs, and ScaledObjects; its short-lived token is mounted
only into the observer. RabbitMQ ingress now permits the management metrics
port only to the labelled six-stem integration Pod in addition to existing
platform/bootstrap peers.

The broker is the only container given the temporary Keycloak, PostgreSQL, and
MinIO administrator copies. It verifies the authenticated user's three Jobs
before creating a function-limited PostgreSQL observer and an exact-object
MinIO reader; it revokes those temporary identities after the terminal report.
The client sees only its temporary user, and the observer sees only generated
read-only database/object credentials, the existing RabbitMQ monitoring login,
and a projected get-only Kubernetes token.

The image `clouddsp-registry.localhost:5001/six-stem-load-client:0.1.14-ingress-phase-diagnostics`
was built for `linux/arm64`, passed 75 in-image offline tests, was pushed and
pulled back from the local registry by immutable digest
`sha256:3138aba36ff1155820c0ed91e29cdcb12f4194e877685d8977ecb1d4c27d05e8`,
and measured 78,160,378 bytes (74.54 MiB). The full local source suite passes
77 client tests plus six workload-manifest tests. The digest and source
dependencies are recorded in `kubernetes/images.lock.yaml`.

The Job manifest remains `suspend: true` in Git. Two live starts on 2026-09-23
confirmed the volume-init correction and prompt failure propagation. The second
start exited `0` from init and `1` from each normal container in seconds, before
temporary identity creation or authenticated uploads. The observer rejected the
dirty RabbitMQ baseline: `clouddsp.basic-pitch.requests.dlq` already held one
message. A separate `clouddsp.source-intake.dlq` also held one message, but
is not among the nine queues checked by this observer. At the user's request
for a clean baseline, exactly those two one-message queues were purged. A
follow-up count showed every queue in `/clouddsp` empty. The failed baseline
Job and its temporary app-namespace bootstrap Secret copies were deleted. All
three KEDA ScaledObjects are ready and inactive, with worker Deployments at
zero replicas. The next run passed baseline, token issuance, `GET /auth/me`,
all three Job API creation contracts, and all three presigned MinIO uploads.
The load Job nevertheless failed before broker coordinate verification; the
temporary Keycloak identity was revoked and the observer exited on broker
failure. The application pipeline continued independently: all 3 Demucs, 15
Basic Pitch, and 3 ADTOF outbox events were published, all 21 processing tasks
reached `succeeded`, queues drained, and KEDA returned the workers to zero. The
three browser-visible Job rows remain `midi_processing`: worker completion is
deliberately task-scoped, and a future aggregate component must set the overall
Job to `completed` after all tasks finish. That aggregate is also required for
the load observer's success predicate. This run did not produce final exact
object-hash evidence and is not a successful end-to-end load test. The broker's
safe generic failure log still hides the precise coordinate-handoff error; the
latest image logs client phases without exposing credentials, coordinates, or
response bodies. Successful data Jobs and MinIO objects remain available for
inspection; the temporary Secret copies remain applied for repeated runs.

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
- Shared MIDI playback samples are a deliberately separate MinIO asset class.
  The local bootstrap mirrors the exact pinned `smplr` piano, guitar, bass,
  and drum sample set into `clouddsp-midi-samples`, records upstream SHA-256
  evidence, and grants anonymous `GetObject` only on that bucket. It grants no
  listing or write access and never changes the private `clouddsp-uploads`
  policy. The browser fetches samples through the existing MinIO S3 Ingress;
  no external sample CDN is needed after bootstrap.
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
- The local KEDA Helm release is pinned and watches only `clouddsp-app`. Its
  RabbitMQ queue-depth scaler uses the private management HTTP API with a
  dedicated read-only `monitoring` account. That account has empty AMQP
  configure/write/read regexes: it can observe metrics but cannot consume,
  acknowledge, publish, or alter topology. The monitoring tag exposes
  broker-wide operational metadata, so the identity remains separate from
  every worker and its credential is stored only in ignored local Secrets. The
  namespaced `clouddsp-rabbitmq-scaler-authentication` TriggerAuthentication
  maps only that username/password into a future scaler; each ScaledObject
  retains its own visible queue, HTTP endpoint, and replica policy.
- The management endpoint is the private
  `clouddsp-rabbitmq-management.clouddsp-data.svc:15672` ClusterIP Service.
  Its coupled broker ingress NetworkPolicy makes the selected RabbitMQ Pod
  ingress-isolated: port 15672 permits only the KEDA operator and finite,
  labelled RabbitMQ bootstrap Jobs; AMQP 5672 permits only reviewed pipeline
  components, MinIO, and labelled smoke Jobs. ClusterIP prevents host and
  Ingress exposure, while NetworkPolicy limits in-cluster sources. An
  authenticated cluster administrator can still use explicit `kubectl
  port-forward`; that administrative API path is intentionally not an
  application-network route. A later live test must prove both KEDA access
  and rejection from an unauthorized Pod before we rely on this boundary.
- The applied ADTOF RabbitMQ `ScaledObject` observes only
  `clouddsp.adtof.requests` through the private management ClusterIP and its
  read-only observer TriggerAuthentication. Its live controlled smoke proved
  the intended queue-driven `0 → 1` activation, durable worker completion, and
  post-idle `1 → 0` cooldown scale-down. The applied equivalent Basic Pitch
  `ScaledObject` observes only `clouddsp.basic-pitch.requests`; neither scaler
  consumes AMQP messages or receives a worker/admin credential. Each
  long-running Deployment retains worker identity, security settings, rollout,
  and durable acknowledgement behavior while KEDA's generated HPA owns only
  replicas, from zero to a local cap of three. A one-message target matches
  `prefetch=1` and counts unacknowledged work so active CPU inference is not
  mistaken for idleness. Basic Pitch uses five-second scale-from-zero polling,
  a one-minute cooldown/stabilization window, and up-to-three-Pod
  15-second scale-up steps: the observed local image-pull-inclusive container
  start is about seven seconds, so this favors fast short-stem bursts while
  still returning to zero when idle. The reviewed Basic Pitch burst run then
  confirmed the full local `0 → 3 → 0` lifecycle through the ordinary durable
  dispatcher/worker path. This validates only the current local CPU profile;
  a different node capacity or future GPU profile requires a separate policy
  review and observation.
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

## 2026-09-23 load-test and job-finalization corrections

The previous three-job run uploaded all three sources and then stopped its
test coordinator before owner verification: the lifecycle broker passed a
positional timeout to a keyword-only callback. The corrected broker and its
ARM64 image now pass the callback argument explicitly by name. The 75-test
offline image suite passes.

That coordinator defect was separate from the data-plane outcome. All 21
Demucs/Basic Pitch/ADTOF tasks had succeeded, but PostgreSQL had no aggregate
that moved parent jobs out of `midi_processing`, and Basic Pitch's successful
MIDI output was not registered in `jobs.midi`. Migration v007 adds the typed
Basic Pitch output-registration function and a deferred PostgreSQL aggregate
that makes a parent job `completed` only after every expected task and artifact
is present, or `failed` when a terminal stage fails. The migration was executed
against a disposable PostgreSQL instance; synthetic success and failure cases
both reached the expected terminal state.

The Job API now returns fresh owner-authorized MinIO download URLs for the
verified original, stems, MIDI, and drum-tempo outputs. An immutable MinIO v003
policy grants the API `GetObject` only for its existing source-upload prefix
and the deterministic `stems/*`/`midi/*` output prefixes. New API and
Basic-Pitch images are pushed and digest-pinned. Existing test Jobs and stored
objects have not been cleaned up.

### Live rollout and rerun diagnosis

The operator authorized rolling out the new dependencies and rerunning the
three-job load test. The v003 MinIO artifact-read policy and v007 migration
were applied successfully; the API and Basic-Pitch Deployments now use their
locked image digests. The first live run exposed a test-harness database
configuration error: the broker pointed to PostgreSQL database `clouddsp`,
while the Job API schema is in `clouddsp_job_api`. The broker, read-only
observer, and manifest were corrected to the application database, and tests
now enforce this boundary.

Inspection also found a real parent-finalization race: v007's deferred
aggregate interpreted the first completed stem task as failure if the
dispatcher had not yet inserted every expected downstream task row. Migration
v008 preserves v007 and makes an incomplete task set wait in
`midi_processing`; terminal child failures and malformed or extra task sets
still fail the parent. V008 was applied successfully and recorded in the
migration ledger. The next load-test run uses ARM64 load-client image tag
`0.1.19-minio-owned-volume-fix`, pinned at
`sha256:96b002198c81cf3f9c436a0849e05d2df0336fc647196a7a4d7a83f6a60b9edf`.

The failed runs' application Jobs and stored objects were not deleted. The
observer bootstrap defects were corrected in order: the SQL contract now
grants the required database `CONNECT` and schema `USAGE`, and the MinIO
adapter validates the root-owned `emptyDir` mount while restricting only its
broker-owned `mc-config` child. The updated ARM64 image passes 78 in-image
tests; the full local client suite passes 80, the workload-manifest suite six,
and the migration suite five. The temporary PostgreSQL observer transaction
was verified live, and no temporary observer role remained after cleanup.

The final end-to-end load-test run completed on 2026-09-23 from 18:10:34 to
18:17:07 UTC (6m33s). All three six-stem parent Jobs reached `completed`, with
3 Demucs, 15 Basic Pitch, and 3 ADTOF tasks succeeded and all corresponding
outbox events published. The observer verified 42 MinIO objects (3 source
files, 18 stems, 21 MIDI/tempo outputs) against database metadata and SHA-256
hashes. RabbitMQ's nine main/retry/DLQ queues were drained. KEDA observed
peak replicas Demucs=1, Basic Pitch=3, ADTOF=1—within configured limits—and
all workers returned to zero before the test reported success. The broker
revoked its temporary MinIO, PostgreSQL, and Keycloak identities; a follow-up
role-count check found zero temporary PostgreSQL observer roles. No application
Jobs or stored objects were deleted. The completed test Job remains until its
900-second TTL removes the Job/Pod; its Git manifest still defaults to
`suspend: true` for any future run.

## Basic Pitch Helm ownership handoff (2026-09-27)

The first worker Helm release now owns only the existing Basic Pitch
Deployment and RabbitMQ ScaledObject. Chart values pin the reviewed image
reference; the template omits `spec.replicas` so KEDA continues to write the
Deployment `/scale` subresource. The versioned release script compared source,
rendered, server-validated, and live specs before adopting. The handoff
preserved both resource UIDs, the ScaledObject spec generation, the KEDA-owned
HPA UID, and zero idle Pods. Shared TriggerAuthentication, KEDA itself,
Secrets, and the generated HPA remain outside this worker release.

The post-adoption one-request smoke published its durable event and recorded a
first-attempt succeeded Basic Pitch task, but the old client timed out waiting
for parent status `midi_processing`. The active v008 finalizer correctly
marked its intentionally incomplete two-stem parent Job `failed`. This smoke
fixture and its narrow cleanup contract need a focused update before a passing
rerun. The Helm release and KEDA idle-state verification passed after the
failed smoke; no second fixed-coordinate test was started.

### Basic Pitch worker smoke fixture repair and rerun (2026-09-27)

The expired fixed-coordinate failed run was inspected before the exact-key
MinIO and fixed-row PostgreSQL cleanup Job removed only its diagnosed evidence.
The test's database `observe` function now emits a success-eligible marker
only for migration v008's exact incomplete-stem error on this intentionally
one-stem parent; the `cleanup` function requires that error plus one published
event and one first-attempt succeeded task. The source bootstrap definitions
and versioned v002 function repair match. The rebuilt Linux/ARM64 client
image is pinned at `sha256:786d0261a5bbe1d318d807e32f2151fc678839b899c0371bebe25347a3457de0`;
nine fake-client tests pass both locally and inside the image.

The live rerun passed through the deployed generic dispatcher and Helm-owned
Basic Pitch worker. The smoke client verified MIDI framing, SHA-256, and
provenance, then removed its two fixed MinIO objects and exact database rows.
The disposable Kubernetes test Job was removed. Unrelated parent failures
still stop the client without cleanup.

## ADTOF Helm ownership and worker smoke (2026-09-27)

The ADTOF worker and its RabbitMQ ScaledObject were adopted as one Helm
release after source/render/live spec parity and API-server validation. Both
resource UIDs, the ScaledObject generation, KEDA-owned HPA UID, and zero idle
Pods were preserved. The chart keeps `spec.replicas` absent so KEDA continues
to own `/scale`; shared scaler authentication, Secrets, and the generated HPA
remain outside the worker release.

The existing one-drum smoke fixture required a test-only update for migration
v008's parent finalizer. Its restricted PostgreSQL observer now exposes a
special marker only when the synthetic four-stem parent has the exact expected
incomplete-stem error. Its cleanup function requires that error, one published
outbox event, and exactly one first-attempt succeeded ADTOF task. A versioned
v002 ConfigMap and one-shot Job installed those functions for the live cluster;
the source bootstrap has matching definitions for a fresh cluster. The
rebuilt ARM64 client image is pinned at
`sha256:cb59778c62e973151b24f9a61683334651d97d2559675e6721aa31a0ed7282aa`;
64 tests passed locally and inside the image.

The live smoke passed. KEDA activated ADTOF from zero to one Pod; the real
dispatcher and worker completed the fixed task; the client verified private
MIDI and tempo bytes, checksums, and provenance, then removed its exact
MinIO and PostgreSQL evidence. The disposable smoke Job was deleted. After
KEDA's idle cooldown, `adtof-release.rb verify` checks the zero-Pod state.

## Demucs Helm ownership and downstream smoke finding (2026-09-27)

The Demucs Deployment and its dual-trigger RabbitMQ/PostgreSQL ScaledObject
were adopted as the separate `clouddsp-demucs` Helm release after image-lock,
source/render/live spec, API schema, authentication, and KEDA HPA checks.
The takeover preserved both resource UIDs, the ScaledObject generation,
generated HPA UID, and zero idle Pods. The chart leaves `spec.replicas` absent
because KEDA continues to own the Deployment `/scale` subresource. A final
read-only `demucs-release.rb verify` passed after the smoke trial.

The fixed two-stem smoke client was updated for migration v008: it recognizes
Demucs completion before or after parent finalization, but requires both
first-attempt downstream Basic Pitch successes and a `completed` parent before
cleanup. A v002 immutable ConfigMap and one-shot database Job installed the
matching restricted cleanup function; the source bootstrap was updated for
fresh clusters. The rebuilt ARM64 smoke image is pinned at
`sha256:0d5513a288b3aa0285234d7ca2c8553570e8f0bb38d4177c9ecc6017b66f5bd6`.
Ten client tests passed locally and inside the image; six manifest tests,
Helm lint, Ruby syntax, API-server validation, and `git diff --check` passed.

The live trial proved the dispatcher published the fixed Demucs request,
KEDA activated the adopted worker, its task succeeded on the first attempt,
and the smoke client verified both private stem artifacts. One Basic Pitch
task then succeeded; the other remained `running` with an active lease while
the Basic Pitch Deployment scaled from two Pods to zero. The broker's Basic
Pitch queue had zero ready and unacknowledged messages because that worker
acknowledges after its durable claim and before model inference. Its current
RabbitMQ-only ScaledObject therefore saw no active trigger and removed the
Pod mid-task. The smoke Job failed at its deadline; its guarded client left
the fixed database rows and objects in place. This is a separate Basic Pitch
scaling and expired-running-task recovery issue. A fixed-coordinate rerun is
unsafe until the preserved evidence is reviewed and cleaned through a policy
for this exact state; the existing failed-run cleanup Job intentionally
refuses a succeeded Demucs task or downstream work.

### Basic Pitch durable-work scaling repair (2026-09-27)

The preserved first trial was inspected before the versioned
`demucs-worker-smoke-downstream-scale-recovery-v001` Job ran. Its SQL required
the exact owner/Job/event coordinates, one succeeded Demucs task, one
first-attempt succeeded Basic Pitch task, and one expired first-attempt
`running` Basic Pitch task. It deleted only that fixed Job and cascading test
rows, then five literal MinIO keys. The subsequent database count was zero
for the fixed Job, tasks, and outbox events.

The existing `clouddsp-keda-demucs` observer role already has SELECT only on
`processing_tasks.stage`, `status`, and `available_at`; it can execute a
Basic Pitch filtered count without new grants or Secrets. Basic Pitch's Helm
ScaledObject now combines its RabbitMQ queue with that PostgreSQL task metric.
The first Helm upgrade met two old `kubectl` field-owner conflicts and left
the live scaler unchanged; the reviewed script retried with explicit conflict
override after comparing installed and live baseline. Release revision 3
preserved the worker, scaler, and generated HPA UIDs and reported both
triggers Ready. During the next smoke trial the broker queue emptied while
the PostgreSQL trigger kept the worker active, confirming the missing hold
signal was restored.

That trial also revealed a separate scale-in race: when one of two Basic Pitch
tasks succeeded, HPA reduced two Pods to one while the other lease remained
`running`. Kubernetes cannot choose the idle Pod from a Deployment. Chart
0.1.2 extends the HPA scale-down stabilization window from 60 to 360 seconds,
beyond the five-minute bounded model invocation. The guarded Helm upgrade to
revision 4 changed only that scaler policy, preserved all three resource
UIDs, and the generated HPA reported `stabilizationWindowSeconds: 360`.
The second trial had already scaled two Pods to one before revision 4 landed,
so it could not validate the completed policy. Its client was stopped without
deleting test data. After the new `vocals` lease expired and process inspection
found no model child, the same exact-coordinate recovery Job again removed
the fixed Job and its task/event rows plus five literal objects; the database
count returned `0|0|0`. A fresh trial must start with revision 4 already
installed.

The fresh revision-4 trial kept two Basic Pitch Pods while the RabbitMQ queue
was empty and one task still held its PostgreSQL lease, validating the HPA
stabilization fix. It then exposed another failure: the `vocals` worker
container terminated with exit code 132 (SIGILL) during the best-effort tempo
step. The exact `vocals.wav` succeeded through the Basic Pitch CLI, but a
separate call to `app.tempo_candidate.estimate_basic_pitch_tempo_candidate`
crashed in Numba's JIT path under librosa beat tracking. The same call
completed with `NUMBA_CPU_NAME=generic`. After the failed client was stopped,
the broker queues and model subprocesses were confirmed empty; the guarded
exact-coordinate recovery Job removed the expired fixture and its five
literal object keys.

Basic Pitch chart 0.1.3 sets that portable Numba CPU target in the Pod
template. The guarded `upgrade-numba` script compared the installed and live
0.1.2 release before Helm revision 5 rolled out. The Deployment, ScaledObject,
and generated HPA UIDs were preserved, and the new Pod used `generic`. The
full Demucs smoke then passed: first-attempt Demucs completion, verified
private stems, both downstream Basic Pitch task successes, completed parent,
and fixed-coordinate cleanup. The disposable smoke Job was removed. This
validates the local ARM64 CPU path, not NVIDIA GPU throughput.

### First deployment bootstrap stage runner (2026-09-27)

`kubernetes/scripts/job-api-migrations.rb` now handles the versioned Job API
PostgreSQL schema stage independently of Helm. Its read-only `plan` compares
the applied `schema_migrations` ledger with the v001–v009 IDs and descriptions
in committed SQL and checks each live immutable ConfigMap's SQL bytes.
`verify` requires a complete ledger. `reconcile` creates only missing
versioned ConfigMaps and fixed-name migration Jobs in numeric order, waits
for each Job, and requires its exact ledger row before continuing. A ledger
gap, description drift, SQL drift, or pending Job with no ledger row blocks
all writes. It relies on the already-bootstrapped database/schema-owner
identity; at this point the preceding bootstrap and other external-service
runners remained separate work.
On the current cluster, nine matching migrations are applied, so reconcile
is an idempotent no-op.

### Job API database and role bootstrap stage (2026-09-27)

`kubernetes/scripts/job-api-database-bootstrap.rb` adds the preceding
PostgreSQL identity stage. It queries only catalog metadata for the dedicated
database, non-admin login, owner, schema owner, and public grants. Both must
be absent for `reconcile` to create the committed fixed-name bootstrap Job;
partial state, privilege drift, a pending Job, or a leftover temporary Secret
blocks any write. Before a fresh bootstrap, it compares the ignored local
bootstrap/runtime credentials and live runtime Secret in memory, never
printing their values. After the Job completes and metadata matches, it
removes the temporary Secret. The current local cluster already has the exact
database/role state, so its live reconcile is a no-op. Credential rotation and
other service database roles remain separate work.

### Job API PostgreSQL stage ordering (2026-09-27)

`kubernetes/scripts/job-api-postgresql-stage.rb` now composes the two narrow
Job API database stages in dependency order for `plan`, `verify`, and
`reconcile`. A fresh-cluster plan defers migration ledger inspection until
database bootstrap exists. Reconcile continues to schema migrations only
after the database runner reports verified role, database, owner, schema, and
grants; an error stops the later stage. The component runners remain available
for diagnosis, and other service bootstrap remains outside this slice. The
combined `plan`, `verify`, and `reconcile` passed against the current k3d
cluster with all nine migrations applied; `reconcile` performed no writes.
The temporary bootstrap Job and Secret were absent after the trial.

### RabbitMQ processing-topology stage (2026-09-27)

`kubernetes/scripts/rabbitmq-processing-topology.rb` adds a separate
RabbitMQ broker-state gate for the immutable v001 Demucs and v002 Basic
Pitch/ADTOF processing definitions. It reads only vhost names and the
exchanges, quorum queue properties/arguments, and bindings declared by those
sources. It treats broker state as durable completion evidence because
bootstrap Jobs have a TTL; a partial import, argument drift, missing applied
ConfigMap, or an ambiguous fixed-name Job blocks writes. Reconcile imports
only an entirely absent version with its existing versioned ConfigMap and Job,
then rechecks the broker before advancing. Source-intake topology and broker
users remain separate because that Job creates restricted credentials. The
runner's local unit suite passed, both versioned Jobs passed Kubernetes server
dry-run validation, and live `plan`, `verify`, and `reconcile` passed with no
pending broker changes. The existing AMQP publish/consume/acknowledge smoke
passed and removed its disposable test Job.

### RabbitMQ source-intake bootstrap stage (2026-09-27)

`kubernetes/scripts/rabbitmq-source-intake-bootstrap.rb` verifies the
source-intake v001 broker topology and the two restricted MinIO publisher and
upload-intake consumer users as one durable external-state stage. It compares
their exact permission regexes and authenticates both live credentials using
standard input to the broker Pod without logging values. Ignored local
bootstrap/runtime Secrets must match their live runtime copies, including
MinIO's encoded AMQP URL. Fresh reconcile creates the versioned ConfigMap and
Job only when topology and both users are entirely absent; it creates and
removes the temporary cross-namespace Secret around that Job. Partial state,
permission or credential drift, a lingering temporary Secret, or a fixed-name
Job without complete broker state stops the runner. Seven isolated tests pass;
the live `plan`, `verify`, and `reconcile` modes passed as no-ops because the
broker and both users already match. The Job and ignored temporary Secret
passed Kubernetes server dry-run validation, and neither temporary resource
was created by this trial.

### Root read-only deployment verification (2026-09-27)

`kubernetes/scripts/deploy-local.sh verify` now composes the existing
preflight, fourteen adopted Helm release verifiers, the Job API PostgreSQL and
RabbitMQ external-state runners, and the KEDA controller/CRD readiness checks
in a fixed dependency order. It stops at the first failing stage, reports the
specific component command for diagnosis, and does not forward child output
that might contain sensitive external-client details. Four isolated tests cover
the fixed read-only command set, ordering, failure isolation, and missing
command handling. The full root verify passed all 23 stages against the
current k3d cluster without creating or changing cluster resources. Durable
MinIO and Keycloak external-state verifiers and the remaining bootstrap
runners were still prerequisites for full-cluster reconciliation at this point.

### Root existing-cluster reconcile (2026-09-27)

`kubernetes/scripts/deploy-local.sh reconcile` uses the same fixed stage order
as root verification. It changes only the three previously audited external
state stages: Job API PostgreSQL bootstrap/migrations, RabbitMQ processing
topology, and RabbitMQ source-intake topology/users. All Helm releases and
KEDA dependencies are checked read-only; unexpected ownership or spec drift
blocks the run rather than causing implicit adoption or upgrade. Each
bootstrap runner itself refuses partial external state and confirms durable
completion before the root moves on. This is an existing-cluster stage, not
fresh-cluster installation or full Helm/MinIO/Keycloak reconciliation.
The live command passed all 23 ordered gates twice. A before/after comparison
for the repeated run found the same revisions for all fourteen CloudDSP Helm
releases, no new bootstrap Jobs, and unchanged identities/resource versions
for the three retained bootstrap ConfigMaps.

### Keycloak external-state verification gate (2026-09-27)

`kubernetes/scripts/keycloak-config-verify.rb verify` now loads the exact
non-secret desired payloads from the six versioned Keycloak bootstrap Jobs,
checks their local-only security invariants, and compares them with the live
Admin API. The gate covers realm enablement and registration, Mailpit SMTP,
the public React PKCE client and exact redirects, the Job API resource client,
the access-token-only audience mapper, the registration password form, and
the browser issuer. It reads administrator credentials in memory from the
existing Secret and never logs tokens, passwords, or full Admin responses.
Four isolated tests cover source payloads, Keycloak's omitted false client
field, safe drift labels, and safe HTTP error handling. The standalone gate
and both 24-stage root `verify` and `reconcile` runs passed on the local
cluster. This gate is read-only; versioned Keycloak write reconciliation and
MinIO external-state automation remain separate work.

### MinIO external-state verification gate (2026-09-27)

`kubernetes/scripts/minio-state-verify.rb verify` checks the MinIO state left
outside the Helm release. It compares all six committed versioned IAM policy
ConfigMaps with their live immutable data and the Admin API policy documents,
verifies exact user-to-policy mappings and enabled users, and checks that the
runtime Secrets carry the expected access-key identities. It also verifies
the two-bucket inventory, the private uploads bucket's lack of a bucket policy,
the narrow anonymous `GetObject` policy on shared MIDI samples, and the one
`uploads/` ObjectCreated AMQP notification. The MinIO admin client uses the
pinned cached image in a transient read-only container with a tmpfs config;
its credentials arrive over stdin and client output is suppressed on failure.
The S3 metadata checks use the existing local Ingress and root Secret. No
objects or notification messages are created by this gate. Six isolated
tests cover the versioned source set, policy normalization, ephemeral client
security, safe failure messages, and notification drift. The standalone gate
and both 25-stage root `verify` and `reconcile` runs passed against the current
local cluster.
This is a read-only gate; MinIO write reconciliation and full notification
delivery testing remain separate work.

### MinIO source-upload notification reconciliation (2026-09-27)

`kubernetes/scripts/minio-notification-stage.rb` adds a bounded write path
for the `uploads/` ObjectCreated AMQP rule. `plan` and `verify` read MinIO's
durable S3 metadata alongside the bucket, IAM, policy ConfigMap, and runtime
identity checks. `reconcile` creates the reviewed fixed-name notification Job
only when the notification is wholly absent and that Job does not already
exist. It validates the Job's pinned client image, credential references,
ordered commands, security settings, and ephemeral config mount, then checks
the MinIO release and RabbitMQ source-intake prerequisites before the write.
After Job completion, the exact notification metadata must be present.
Unrelated or partial notification state, an existing Job, or failed
prerequisites stop the stage without cleanup or replacement. Five isolated
tests passed, the Job passed Kubernetes server dry-run validation, and live
`plan` and `reconcile` passed as no-ops because the correct rule was already
present. Root `verify` and `reconcile` retain 25 stages; only this notification
stage now has a MinIO write path. Bucket creation, IAM user/policy repair, and
notification delivery smoke remain separate tasks.

### MinIO bucket boundary and shared-sample policy stage (2026-09-27)

`kubernetes/scripts/minio-buckets-stage.rb` adds a separate existing-cluster
gate before the MinIO notification stage. It requires exactly the private
uploads and shared MIDI sample buckets, no uploads bucket policy, and no
shared-sample notifications. It compares all 461 sample keys and sizes with
the committed lock before checking the narrow anonymous `GetObject` policy.
Only a wholly absent shared-sample policy can be restored; a changed policy,
extra object, or missing bucket stops without a write. In particular, the
runner will not hide possible private data loss by recreating an empty uploads
bucket. Five isolated tests passed and the live read-only `plan` found the
bucket boundaries and locked inventory already correct. Root `verify` and
existing-cluster `reconcile` each passed all 26 stages. Anonymous HTTP smoke
returned 200 for a shared drum sample and 403 for the private uploads bucket.
Fresh-cluster bucket creation, sample content hash verification, and IAM
reconciliation remain separate tasks.

### Fresh-cluster foundation stage (2026-09-27)

`kubernetes/scripts/deploy-local-foundation.rb` is the first standalone
fresh-bootstrap stage. It validates the pinned one-server/two-agent k3d
topology, loopback API/registry/ingress ports, and three versioned project
Namespace definitions before any write. `plan` requires both the target
cluster and registry to be absent; `bootstrap` calls the existing `cluster.sh`
creator only after those guards, waits for Ready nodes, server-validates and
creates the namespaces, then verifies registry access, exact node roles,
namespace labels, and packaged CoreDNS, Traefik, and local-path provisioner
rollouts. A failure leaves the partial cluster for inspection rather than
deleting it. Five isolated tests cover source contracts, absence guards,
ordered creation, verification, and partial failure. The current cluster
passed the read-only verifier; a fresh `plan` correctly refused it. The
Namespace manifest passed Kubernetes server dry-run validation. This stage
does not install KEDA, images, Secrets, Helm releases, or external bootstrap;
root `deploy-local.sh bootstrap` remains pending those later stages.

### Registry lifecycle separated from cluster cleanup (2026-09-28)

Normal `deploy-local.sh cleanup` now removes only the fixed CloudDSP k3d
cluster, its workloads, and local PVC data. It retains the dedicated image
registry by connecting it to the owned `clouddsp-registry-hold` network before
k3d deletion (k3d v5.9 otherwise deletes a registry connected only to the
cluster and Docker's default network). A separate
`deploy-local.sh purge-registry` refuses an active cluster
and removes that exact registry and its hold network after cluster cleanup. `cluster.sh create`
uses the checked-in registry-create configuration on a clean machine; when the
registry remains from an earlier cluster, it generates a temporary equivalent
configuration with `registries.use` and attaches it to the new nodes. The
standalone foundation stage accepts either fresh creation or healthy registry
reuse. Isolated tests cover the two cluster creation paths, normal cleanup,
registry purge, and the active-cluster guard. The existing live cluster was
not deleted to test these destructive paths.

The next image-availability stage will publish the reviewed CloudDSP images to
the public `y1ktor/clouddsp` Docker Hub repository and repopulate an empty
local registry from those published digests before Helm installs workloads.
At this stage the image bytes were not yet in Docker Hub, so the registry was
kept until publication and a clean-machine pull trial could be checked.

### Public Docker Hub image source (2026-09-28)

The public `y1ktor/clouddsp` repository was created in the requested Docker
Hub namespace. `kubernetes/scripts/image-registry-stage.rb` derives one Hub
tag for each of the 18 local Linux ARM64 image-lock entries and supports
`plan`, `publish`, `mirror`, and `verify`. Publication of all 18 images
succeeded, including the older Demucs-only dispatcher generation and optional
smoke clients. Anonymous Docker Hub manifest requests and local registry
requests returned the same locked SHA-256 digest for every tag. A Job API
image was anonymously pulled from Docker Hub and pushed to a disposable empty
`.localhost` registry without changing its digest. The disposable registry
was removed. Isolated tests cover unique tag mapping, mismatch rejection,
and the missing-local-image mirror branch. A complete empty-registry mirror
run and integration with one-command fresh bootstrap remain to be done.

### Fresh-cluster foundation and image preparation (2026-09-28)

`kubernetes/scripts/deploy-local.sh prepare` composes the existing guarded
foundation and image stages. It requires an absent target cluster, verifies
the 18 public Docker Hub image digests before creation, creates the reviewed
k3d topology and namespaces, mirrors missing images into the local registry,
and verifies both registries. The ordered runner stops on a failed stage and
does not claim Helm release or external-state installation. Isolated tests
cover the order, pre-creation source failure, and mirror failure. The live
cluster was not deleted for a fresh-path trial; an empty disposable-cluster
trial and the later full `bootstrap` remain outstanding.

### Mailpit fresh Helm install path (2026-09-28)

The Mailpit release runner now supports a guarded `install` mode for the
first stateless Helm release after cluster and image preparation. It validates
the reviewed chart, source manifests, image lock, and Kubernetes schema,
then requires both the release and all four named Mailpit objects to be
absent. Ordinary Helm install waits for readiness; the runner then checks
release ownership, manifest parity, Pod readiness, and the HTTP ingress.
This path never uses adoption takeover flags and stops on a partial prior
attempt. Isolated tests cover the ordered success path and both absence
guards. A fresh-cluster trial remains to be done without deleting the
current working cluster.

### Root Mailpit bootstrap slice (2026-09-28)

`kubernetes/scripts/deploy-local.sh bootstrap-mailpit` now composes the
guarded foundation/image preparation, fresh Mailpit Helm installation, and
Mailpit release verification in one command. The runner stops at the first
failure and names the failed stage. This is deliberately a partial bootstrap;
the data services, identity, app services, workers, and frontend still need
fresh install and bootstrap paths before the final one-command `bootstrap`.
Isolated tests cover ordered execution and both stop boundaries. The running
cluster is retained, so the clean-cluster branch still needs a disposable
trial.

### PostgreSQL fresh credential prerequisite (2026-09-28)

`kubernetes/scripts/postgresql-secret-stage.rb` adds a guarded `plan`,
`bootstrap`, and `verify` path for the PostgreSQL runtime Secret needed before
its StatefulSet can start on a clean cluster. It validates the ignored local
manifest against the committed resource/key contract, rejects the placeholder
password, performs a server dry run, creates only an absent Secret, and then
compares live and local values in memory without displaying them. The existing
cluster passed read-only `verify`. Root `verify` and `reconcile` now include
that read-only gate. Isolated tests cover absent creation, existing-resource
refusal, placeholder rejection, and drift detection. The subsequent
StatefulSet install and partial root composition are recorded below.

### PostgreSQL fresh Helm install path (2026-09-28)

The PostgreSQL release runner now opts into a guarded `install` mode. It
requires the Helm release, StatefulSet, both Services, generated claim, and
matching Pod to be absent, then verifies the ignored/local versus live
credential Secret before the Helm write. Ordinary Helm install waits for the
single StatefulSet Pod; the existing release verifier then checks chart and
live parity plus the bound PVC and PV identity. This fresh path does not run
the protected adoption backup gate or use takeover flags. Isolated tests
cover successful ordering and existing release, PVC, orphan Pod, and missing
credential stops. A clean-cluster install trial remains outstanding.

### Root PostgreSQL bootstrap slice (2026-09-28)

`kubernetes/scripts/deploy-local.sh bootstrap-postgresql` now runs the
fresh foundation and image preparation, guarded PostgreSQL credential Secret
creation, fresh StatefulSet Helm install, and release/PVC verification in one
ordered command. It stops at the first failed stage without implicitly
replacing partial resources. Isolated tests cover the success sequence and
stops after preparation, credential, and Helm failures. This command is an
alternative partial trial to `bootstrap-mailpit`: both require an absent
cluster. The final root `bootstrap` will compose component stages together;
an actual empty-cluster creation trial remains to be done.

### RabbitMQ fresh credential prerequisite (2026-09-28)

`kubernetes/scripts/rabbitmq-secret-stage.rb` now offers guarded `plan`,
`bootstrap`, and `verify` modes for the broker administrator Secret needed by
its fresh StatefulSet. It validates the ignored local manifest against the
committed identity/key contract, rejects placeholders and the reserved
`guest` account, creates only an absent Secret after a server dry run, and
compares the live data with local values in memory without displaying them.
The current live Secret passed read-only verification. Root `verify` and
existing-cluster `reconcile` now check it before the RabbitMQ release.
Isolated tests cover absent creation, existing-resource refusal, invalid
source, and drift. The fresh broker Helm install is recorded below.

### RabbitMQ fresh Helm install path (2026-09-28)

`kubernetes/scripts/rabbitmq-release.rb install` now checks that the Helm
release, five broker objects, generated claim, and matching Pod are absent.
It then verifies the administrator Secret against ignored local configuration
before an ordinary Helm install with a five-minute readiness wait. The
existing release verifier checks ownership, source spec, Ready Pod, bound
PVC/PV, and running image digest after installation. The fresh path never
runs the protected adoption backup or takeover flags. Isolated tests cover
success ordering and existing release, object, PVC, orphan Pod, and invalid
credential boundaries. The retained live cluster has not been deleted for an
empty-cluster trial; root composition is recorded below.

### Root RabbitMQ bootstrap slice (2026-09-28)

`kubernetes/scripts/deploy-local.sh bootstrap-rabbitmq` now runs fresh
foundation and image preparation, guarded administrator Secret creation,
fresh RabbitMQ Helm install, and release/PVC verification in order. It stops
at the first failed stage and leaves any partial cluster or broker release
for inspection. Isolated tests cover successful ordering and each failure
boundary. This partial command requires an absent cluster, like the separate
Mailpit and PostgreSQL trials. The final root `bootstrap` will compose their
component stages together; an empty-cluster creation trial remains pending.
The current live cluster correctly stopped `bootstrap-rabbitmq` at the
absent-cluster foundation plan before Secret or Helm writes. The RabbitMQ
AMQP smoke and all 28 root read-only verification gates passed afterward.

### MinIO fresh root credential prerequisite (2026-09-28)

`kubernetes/scripts/minio-root-secret-stage.rb` now provides guarded `plan`,
`bootstrap`, and `verify` modes for the ignored administrator Secret. It
requires the versioned identity, labels, type, and exact key set, rejects
placeholder or newline-containing credentials, creates only an absent Secret
after a server dry run, and compares live values with local values in memory
without displaying them. Root `verify` and existing-cluster `reconcile` now
run its read-only check before the MinIO release. The current live Secret
passed `verify` and correctly blocked a fresh `bootstrap`. The separate
RabbitMQ notification Secret stage is recorded below; fresh MinIO Helm install
and bucket/IAM creation still need their own stages.

The MinIO S3 create/read/delete smoke and all 29 root read-only verification
gates passed with the new Secret check in place.

### MinIO RabbitMQ notification credential prerequisite (2026-09-28)

`kubernetes/scripts/minio-amqp-secret-stage.rb` now offers guarded `plan`,
`bootstrap`, and `verify` modes for the ignored MinIO notification Secret.
It validates the committed identity, labels, exact key set, fixed broker and
vhost, and agreement between the restricted username/password fields and
their percent-encoded AMQP URL. It creates only an absent Secret after a
server dry run and compares live values without displaying the URL or
password. Root `verify` and existing-cluster `reconcile` check it before the
MinIO release. The live Secret passed `verify` and blocked a fresh
`bootstrap`. The separate RabbitMQ user/topology stage, fresh MinIO Helm
install, and bucket/IAM creation remain to be composed.

The MinIO S3 create/read/delete smoke and all 30 root read-only verification
gates passed with this Secret check in place.

### MinIO fresh Helm install path (2026-09-28)

`kubernetes/scripts/minio-release.rb install` now requires the Helm release,
four chart resources, generated claim, and matching Pod to be absent. It
checks the root and AMQP credential Secrets against their ignored sources
before the Helm write. Ordinary Helm install waits for the single StatefulSet
Pod; the existing release verifier then checks ownership, source spec,
Ready Pod, bound PVC/PV, locked running image digest, and S3 health route.
The fresh path never runs the protected adoption backup or takeover flags.
Isolated tests cover successful ordering and existing release, object, PVC,
orphan Pod, and either missing credential gate. The retained live cluster
correctly blocked `install` on the existing release, so an empty-cluster
creation trial and root composition remain pending.
The MinIO S3 create/read/delete smoke and all 30 root read-only verification
gates passed after enabling this install mode.

### Upload-intake RabbitMQ runtime credential prerequisite (2026-09-28)

`kubernetes/scripts/upload-intake-rabbitmq-secret-stage.rb` now provides
guarded `plan`, `bootstrap`, and `verify` modes for the app-namespace runtime
Secret needed by the source-intake broker bootstrap. It validates the
committed identity, labels, and key contract for both ignored runtime and
temporary data-namespace sources, and requires their username and password
to match. It creates only an absent runtime Secret after a server dry run,
then compares live values without displaying them. The existing RabbitMQ
source-intake runner still owns creation and removal of the temporary
bootstrap Secret and creation of the broker user/topology. Root `verify` and
existing-cluster `reconcile` now check this runtime Secret before that
broker state. The live runtime Secret passed `verify` and blocked a fresh
`bootstrap`. Root fresh MinIO composition remains to be implemented.
The RabbitMQ AMQP publish/consume/acknowledge smoke and all 31 root read-only
verification gates passed with this new Secret gate in place.

### Root broker-to-MinIO bootstrap slice (2026-09-28)

`kubernetes/scripts/deploy-local.sh bootstrap-minio` now composes the fresh
RabbitMQ foundation/release bootstrap with guarded MinIO root and AMQP
credential creation, upload-intake RabbitMQ runtime Secret creation, the
existing source-intake broker topology/user reconciliation and verification,
then guarded MinIO Helm installation and PVC/S3 route verification. It stops
at the first failed child without deleting partial broker state or storage.
Isolated tests cover the exact order and every failure boundary. This command
requires an absent cluster; the retained live cluster has not been removed
for a clean-cluster trial. Buckets, IAM, and remaining application releases
are outside this partial command and remain for the full root `bootstrap`.
The live cluster rejected `bootstrap-minio` at the nested foundation plan
before any Secret, broker, or Helm write. The MinIO S3 create/read/delete
smoke and all 31 root read-only verification gates passed afterward.

### Fresh MinIO bucket boundary bootstrap (2026-09-28)

`kubernetes/scripts/minio-fresh-buckets-stage.rb` now creates the two named
MinIO buckets after the fresh Helm release verifies. It first requires an
empty bucket listing, then creates private `clouddsp-uploads` and
`clouddsp-midi-samples` and checks both bucket endpoints and policy
boundaries. A partial creation is left for inspection; the stage will not
fill a missing partner on retry. `deploy-local.sh bootstrap-minio` runs this
stage and its read-only verification after MinIO installation. Isolated tests
cover the order, pre-existing and partial inventories, missing release, and
second-bucket failure. The retained live cluster has not been replaced for a
fresh trial. MinIO IAM and application releases still await their own
bootstrap stages.

### Fresh shared MIDI sample mirror (2026-09-28)

`kubernetes/scripts/minio-fresh-samples-stage.rb` now follows fresh bucket
creation in `bootstrap-minio`. It requires the exact two-bucket inventory and
an empty private sample bucket before calling the existing sample mirror in
`--fresh-bootstrap` mode. That mode reads reviewed URLs from the committed
asset lock without requiring local frontend dependencies, then checks the
same boundary again after downloading and hashing 461 files, just
before its first upload. The public policy remains absent until the upload
inventory matches; final verification checks the locked keys/sizes and the
single anonymous `GetObject` grant. Existing partial objects block a repeat
fresh mirror for inspection. The retained live cluster has not been replaced
for the clean-cluster trial. IAM identities and other application stages
remain pending.

### Job API MinIO runtime Secret preparation (2026-09-28)

`kubernetes/scripts/job-api-minio-secret-stage.rb` now validates the ignored
Job API runtime and temporary MinIO bootstrap Secret sources together. It
requires the fixed restricted access key and an identical non-placeholder
secret key, then creates only the absent `clouddsp-app` runtime Secret after a
server dry run. Its read-only mode compares the live encoded values with the
ignored local source without printing credentials. `bootstrap-minio` runs the
fresh creation after the MinIO release verifies, and root `verify` now checks
this Secret before its MinIO bucket/IAM gates. The temporary `clouddsp-data`
Secret and the two versioned Job API MinIO IAM Jobs remain separate work.
The existing-cluster read-only root verification passed all 32 gates with
this new Secret check. The restricted Job API MinIO smoke Job used that Secret
to read/write its fixed uploads marker, confirmed out-of-prefix writes and
deletion were denied, then the disposable Job was removed.

### Job API MinIO IAM fresh bootstrap (2026-09-28)

`kubernetes/scripts/minio-job-api-iam-stage.rb` now orders the two committed
Job API MinIO policy Jobs after fresh buckets and sample mirroring. It checks
that the user, both policies, temporary Secret, policy ConfigMaps, and Jobs
are absent before writing. Server dry runs precede the temporary Secret and
both Jobs; each Job's durable MinIO policy attachment verifies before the
next runs. The temporary Secret is deleted only after both exact policies and
the restricted user verify. A partial run remains inspectable and cannot be
restarted as a fresh bootstrap. Root `bootstrap-minio` includes this stage.
The root read-only verifier checks it separately before the broader MinIO
state gate. Existing-cluster `reconcile` can remove a leftover temporary
Secret only after it matches the ignored source and the IAM state is exact;
it does not create or change a user or policy. The running cluster had such a
leftover Secret from earlier manual setup, with matching values and complete
IAM. The committed `reconcile` stage removed only that Secret; a second run
was a no-op. All 33 root read-only gates then passed. The restricted Job API
MinIO smoke again confirmed permitted uploads-prefix read/write and denied
out-of-prefix write and deletion; its disposable Kubernetes Job was removed.
The fresh IAM Job sequence has not been trialed on an empty cluster.

### upload-intake MinIO runtime Secret preparation (2026-09-28)

`kubernetes/scripts/upload-intake-minio-secret-stage.rb` validates the ignored
runtime and temporary bootstrap Secret sources together, including their
restricted access-key identity and matching non-placeholder secret key. Fresh
`bootstrap-minio` creates only the absent `clouddsp-app` runtime Secret after
a server dry run; the temporary `clouddsp-data` Secret and source-read IAM Job
belong to the next task. Root read-only verification checks the live runtime
Secret against the ignored source without printing credentials. The committed
runtime template's purpose label now matches the existing live Secret and
ignored local source. The retained live cluster passed all 34 root read-only
gates; the fresh-cluster creation path remains untrialed.

### upload-intake MinIO IAM fresh bootstrap (2026-09-28)

`kubernetes/scripts/minio-upload-intake-iam-stage.rb` uses the committed
source-read policy ConfigMap and one-shot Job to provision the restricted
upload-intake identity after MinIO buckets and its runtime Secret verify. It
requires the user, policy, ConfigMap, Job, and temporary Secret to be absent
before writing; server dry runs precede every create. After Job completion it
verifies the exact policy and user attachment, then deletes the temporary
Secret. Partial state stops for inspection. Root `bootstrap-minio` and the
read-only root verifier include the stage. Existing-cluster `reconcile` can
remove only a matching temporary Secret after the durable IAM state verifies;
it cannot create or change the user or policy. The running cluster's exact IAM
state verified. The fresh Job path has not been trialed on an empty cluster.
The live fresh `plan` refused the existing policy ConfigMap, while the
read-only IAM gate and all 35 root verification gates passed. API-server dry
run accepted the committed policy ConfigMap and Job. Narrow reconcile found no
temporary Secret and made no change. The source-to-outbox smoke passed its
authenticated upload, upload-intake transition, and duplicate notification
checks; its disposable Job was removed. Both dispatcher Helm releases were
restored to their reviewed values at revision 5, their release verifiers
passed, and RabbitMQ queues were empty afterward.

### Demucs MinIO runtime Secret preparation (2026-09-28)

`kubernetes/scripts/demucs-minio-secret-stage.rb` validates the ignored
worker and temporary provisioning Secret sources together. It checks their
fixed identity, labels, keys, and matching non-placeholder secret key without
printing credentials. Fresh `bootstrap-minio` creates only the absent
`clouddsp-app` runtime Secret after a server dry run; the temporary
`clouddsp-data` Secret and worker IAM Job are separate work. Root read-only
verification compares the live Secret with the ignored source before bucket
and IAM checks. The retained cluster remains in place, so fresh creation is
covered by focused unit tests rather than a live empty-cluster trial.
The existing-cluster read-only Secret check and all 36 root verification
gates passed. Fresh `plan` refused the existing Secret, and an API-server dry
run validated the ignored runtime Secret without persisting it. No Demucs
worker Job was started for this credential-only stage.

### Demucs MinIO IAM fresh bootstrap (2026-09-28)

`kubernetes/scripts/minio-demucs-iam-stage.rb` now guards the committed
artifacts policy ConfigMap and one-shot Job. It requires the Demucs MinIO
user, policy, ConfigMap, Job, and temporary Secret to be absent before any
write, server-validates all three manifests, and checks the exact source-read
and stem-write policy attachment before deleting the temporary Secret. A
partial run remains for inspection. Root `bootstrap-minio` includes the
fresh stage and root read-only verification checks its durable IAM result.
Existing-cluster `reconcile` can remove only a matching leftover temporary
Secret after full IAM verification; it never creates or rotates the user or
policy. The retained live cluster passed the narrow IAM verifier. Fresh Job
creation remains untrialed until an empty-cluster test.
Fresh `plan` refused the existing policy ConfigMap, and API-server dry run
accepted the committed policy and Job. The focused S3 smoke used the Demucs
runtime identity to put/get one unique private stem probe while confirming
MIDI writes, deletion, and bucket listing were denied; it cleaned the probe
with the ignored MinIO administrator key. Narrow reconcile found no temporary
Secret and made no change. All 37 root read-only gates passed.

### Basic Pitch MinIO runtime Secret preparation (2026-09-28)

`kubernetes/scripts/basic-pitch-minio-secret-stage.rb` validates the ignored
worker and temporary provisioning Secret sources together, including fixed
identity, labels, keys, and matching non-placeholder secret keys. Fresh
`bootstrap-minio` creates only the absent `clouddsp-app` runtime Secret after
a server dry run; the temporary `clouddsp-data` Secret and Basic Pitch IAM
Job remain separate work. Root read-only verification checks the live Secret
against the ignored source before bucket and IAM gates. The retained cluster
has not been replaced for a fresh creation trial.
The live runtime Secret matched its ignored source, fresh `plan` refused the
existing Secret, and an API-server dry run accepted the source without
persisting it. All 38 root read-only gates passed. No Basic Pitch worker Job
was started for this credential-only stage.

### Basic Pitch MinIO IAM fresh bootstrap (2026-09-28)

`kubernetes/scripts/minio-basic-pitch-iam-stage.rb` guards the committed
artifacts policy ConfigMap and one-shot Job. It requires the Basic Pitch user,
policy, ConfigMap, Job, and temporary Secret to be absent before any write and
server-validates all three manifests before creation. Once the Job completes,
it verifies the exact stem-read/MIDI-write policy and user attachment, then
deletes the temporary Secret. A partial run remains for inspection. Root
`bootstrap-minio` now includes this fresh stage; root read-only `verify` checks
the durable IAM result. Existing-cluster `reconcile` may remove only a
matching leftover temporary Secret after full IAM verification; it cannot
create or rotate the user or policy.

The retained cluster's narrow IAM check passed, while fresh `plan` refused
the existing policy ConfigMap as designed. API-server dry run accepted the
policy and Job. The focused S3 smoke confirmed Basic Pitch could read a
random private stem and put/get a random private MIDI object; stem writes,
deletion, and bucket listing returned AccessDenied. It removed its own probes
and sent no upload notification. Fresh Job creation still awaits an empty
cluster trial. Narrow reconcile found no temporary Secret and made no change;
all 39 root read-only gates passed.

### ADTOF MinIO runtime Secret preparation (2026-09-28)

`kubernetes/scripts/adtof-minio-secret-stage.rb` validates the ignored worker
and temporary provisioning Secret sources together, including their fixed
identity, Kubernetes labels, exact data keys, and matching non-placeholder
secret keys. Fresh `bootstrap-minio` creates only the absent `clouddsp-app`
runtime Secret after a server dry run; the temporary `clouddsp-data` Secret
and ADTOF IAM Job remain separate work. Root read-only verification compares
the live Secret with the ignored source before bucket and IAM gates. The
retained cluster has not been replaced for a fresh creation trial.

The live runtime Secret matched its ignored source. Fresh `plan` refused the
existing Secret, while an API-server apply dry run accepted the source without
persisting it. A create dry run reported `AlreadyExists` because the retained
cluster already has that Secret. No ADTOF worker Job was started for this
credential-only stage. All 40 root read-only gates passed.

### ADTOF MinIO IAM fresh bootstrap (2026-09-28)

`kubernetes/scripts/minio-adtof-iam-stage.rb` guards the committed artifacts
policy ConfigMap and one-shot Job. It requires the ADTOF user, policy,
ConfigMap, Job, and temporary Secret to be absent before any write, validates
all three manifests at the API server, and checks the exact drums-read and
fixed MIDI/tempo-write policy attachment after Job completion. The runner
then removes the temporary Secret. It checks the Job's 256 MiB association
limit because an earlier ADTOF provisioning attempt exhausted a smaller
limit. A partial run remains for inspection. Root `bootstrap-minio` includes
this fresh stage; root read-only `verify` checks the durable IAM result.
Existing-cluster `reconcile` may remove only a matching leftover temporary
Secret after full IAM verification; it cannot create or rotate the user or
policy.

The retained cluster's narrow IAM check passed, while fresh `plan` refused
the existing policy ConfigMap as designed. API-server dry run accepted the
policy and Job. The focused S3 smoke confirmed ADTOF could read a private
`drums.wav` but not a planted non-drum stem; it could put/get only its fixed
`drums.mid` and `drums_bpm.json` outputs. Stem and other-MIDI writes,
deletion, and bucket listing returned AccessDenied. All unique probes were
removed without sending an upload notification. Fresh Job creation still
awaits an empty-cluster trial. Narrow reconcile found no temporary Secret
and made no change; all 41 root read-only gates passed.

### Fresh platform composition and Job API database Secret (2026-09-28)

`kubernetes/scripts/job-api-database-secret-stage.rb` now guards the ignored
Job API runtime and temporary database-role credential sources. It checks
their fixed database/role identity, Secret metadata, exact keys, and matching
non-placeholder password. Fresh `bootstrap` creates only the absent app-
namespace runtime Secret after a server dry run; the separate role Job owns
the short-lived data-namespace copy. Read-only `verify` compares the live
Secret with its ignored source without printing values.

The new `deploy-local.sh bootstrap-platform` command composes the already
guarded PostgreSQL, RabbitMQ, and MinIO child stages once. It then installs
Mailpit and orders the Job API runtime database Secret before the reviewed
role/migration reconciler, followed by RabbitMQ processing topology. Every
child must verify before the next runs. `deploy-local.sh stages` prints all
40 current stage names without touching Kubernetes. The retained cluster's
Job API Secret matched its ignored source, fresh `plan` refused the existing
Secret, and API-server dry run accepted the source. The clean-cluster platform
path has not been executed. Application identity, remaining service roles
and Secrets, KEDA authentication, and app/worker fresh Helm installers are
still needed before a full one-command browser-to-worker deployment.
