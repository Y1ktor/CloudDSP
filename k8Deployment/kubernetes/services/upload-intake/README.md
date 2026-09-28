# CloudDSP MinIO AMQP upload-intake contract

## Scope of this first task

This document defines the event contract for the component that notices a
completed source-object upload. It creates **no** Kubernetes resource, RabbitMQ
topology, MinIO target, database migration, or container image yet. Defining
the transport first prevents an S3 notification from being mistaken for proof
that audio is valid, owned, or ready for Demucs.

The existing direct-upload path is already live:

```text
authenticated browser
  -> Job API creates one PostgreSQL upload_pending job
  -> Job API returns one constrained presigned POST form
  -> browser uploads directly to MinIO
```

The next path starts only after MinIO has accepted the object:

```text
MinIO native S3 ObjectCreated notification
  -> RabbitMQ clouddsp.source-intake queue
  -> upload-intake Deployment consumer
  -> authoritative PostgreSQL transition
  -> (later) transactional outbox -> RabbitMQ demucs.requested
```

MinIO's notification is a trigger, not the source of truth. PostgreSQL owns
the job state, RabbitMQ provides durable asynchronous delivery, and MinIO
remains private object storage.

## Deployment boundary

The future `upload-intake` application is a long-running **Deployment** in
the `clouddsp-app` namespace. Its Pods make outbound AMQP connections to
RabbitMQ and outbound S3 `HeadObject` calls to MinIO. They accept no inbound
application traffic, so this consumer needs **no Kubernetes Service and no
Ingress**. A Service only gives other clients a stable inbound address; it is
not required for a Pod that initiates its own connections.

The eventual Deployment may be scaled manually at first and by KEDA later.
Multiple consumer Pods may receive different messages from the same queue, so
the database transition below is deliberately idempotent. No consumer Pod gets
Kubernetes API credentials, MinIO root credentials, or the RabbitMQ
administrator credential.

## AMQP topology and identities

The future RabbitMQ topology is created **before** MinIO is allowed to publish.
An administrator-only bootstrap Job first creates the dedicated `/clouddsp`
virtual host, then creates these durable resources inside it. A RabbitMQ
virtual host is a namespace within one broker: clients on `/clouddsp` cannot
accidentally use queues from the broker's default `/` virtual host.

| AMQP resource | Contracted name and setting | Purpose |
| --- | --- | --- |
| source exchange | `clouddsp.source-events`, durable `direct` exchange | Stable ingress point for source-created notifications. |
| intake queue | `clouddsp.source-intake`, durable quorum queue | Holds each source notification until one intake consumer acknowledges it. A one-broker local cluster has one quorum member; it is not high availability. |
| binding | `clouddsp.source-events` + routing key `source.upload.created` → `clouddsp.source-intake` | Ensures MinIO source events reach only intake consumers. |
| retry path | A named, durable delayed retry queue plus a bounded-attempt policy | Prevents a transient MinIO/PostgreSQL outage from becoming a hot `requeue` loop. Its exact delay and five-attempt limit will be implemented with the consumer. |
| dead-letter queue | `clouddsp.source-intake.dlq`, durable | Retains a poisoned or exhausted event for inspection; it never silently disappears. |

The topology bootstrap also creates two least-privilege RabbitMQ accounts:

| Identity | Permitted action | Explicitly not permitted |
| --- | --- | --- |
| `clouddsp-minio-events` | Idempotently declare and publish only to `clouddsp.source-events`. | Read queues, consume messages, configure queues/bindings/other exchanges, manage RabbitMQ, or publish Demucs work. |
| `clouddsp-upload-intake` | Consume only `clouddsp.source-intake`; later publish only to its reviewed retry/dead-letter exchange. | Read unrelated queues, publish Demucs work, declare arbitrary topology, or administer RabbitMQ. |

The administrator account already bootstraps the local broker and is never
mounted into MinIO or the consumer. The publisher and consumer credentials are
created from separate ignored `.local` Secret files. This prevents possession
of MinIO's AMQP URL from also granting a Pod authority to consume or manage
CloudDSP queues.

MinIO performs a passive-safe, idempotent declaration of its configured
exchange before publishing. It therefore needs configure permission for that
one exchange in addition to write permission; this is not authority to create
queues or bindings, and its matching regular expression excludes every other
RabbitMQ resource.

## Native MinIO AMQP notification target

### Current enabled-target state

The MinIO StatefulSet now contains the complete native AMQP target
configuration and reads its password-bearing URI from the separate local
Secret. Its enable setting is on, so a restarted MinIO server registers the
target. MinIO opens its AMQP connection and idempotently declares the one
permitted source exchange lazily, when it has an event to deliver. The
already-completed RabbitMQ bootstrap granted exactly the configure/write
permission required for that exchange and nothing else.

This does not yet publish source notifications: MinIO has no bucket event rule
for this target until the next root-only, idempotent bootstrap Job attaches
one. Enabling connectivity separately keeps the broker rollout observable and
does not retroactively start intake for existing or newly uploaded objects.

MinIO natively publishes S3-compatible bucket notifications to AMQP 0-9-1
services such as RabbitMQ. The StatefulSet configures one enabled target
identified as `intake`:

| Configuration | Contracted value | Reason |
| --- | --- | --- |
| target identifier | `INTAKE` | Produces the stable MinIO target ARN `arn:minio:sqs::INTAKE:amqp`. MinIO derives it from the uppercase environment-variable suffix. |
| AMQP URL | Secret-backed URI for `clouddsp-minio-events` on `/clouddsp` at `clouddsp-rabbitmq.clouddsp-data.svc:5672` | Stays on cluster DNS and keeps the password out of the manifest. |
| exchange/type/routing key | `clouddsp.source-events` / `direct` / `source.upload.created` | Sends only source-created events to the declared intake binding. |
| mandatory delivery | `on` | Makes an absent binding a visible publishing failure instead of silently dropping the event. |
| broker and message durability | durable exchange/queue plus delivery mode `2` | Allows RabbitMQ to restore accepted source events after its Pod restarts. |
| MinIO event store | A bounded queue directory on MinIO's existing `/data` PVC, never `emptyDir` | Holds events MinIO cannot currently deliver to RabbitMQ and replays them after recovery. |
| bucket rule | `clouddsp-uploads`, prefix `uploads/`, object-created events | Excludes artifact outputs and unrelated objects from source intake. |

MinIO uses environment settings named
`MINIO_NOTIFY_AMQP_*_INTAKE`; the AMQP URL itself comes from a Secret through
`valueFrom.secretKeyRef`. MinIO applies those environment settings when the
server process starts, so changing them requires a deliberate MinIO
StatefulSet rollout. A separate root-credential-bearing `mc` bootstrap Job
([`../minio/minio-source-intake-notification-bootstrap-job.yaml`](../minio/minio-source-intake-notification-bootstrap-job.yaml))
has attached the bucket rule with the target ARN and confirmed it using `mc
event list`; nobody configures it manually in the MinIO console. The Job uses
`--ignore-existing`, so deleting a completed Job and reapplying it preserves
the exact existing rule rather than creating a duplicate. It intentionally
does not remove any separately configured notification rule.

The prepared [`MinIO-to-RabbitMQ transport smoke Job`](../../tests/minio-smoke/minio-source-intake-rabbitmq-smoke-job.yaml)
first proves the source-intake queue is empty, creates one fixed harmless S3
`PutObject` marker through the restricted Job API storage identity, and then
validates/acknowledges its one resulting AMQP event with the restricted intake
identity. It deliberately creates no PostgreSQL job. The fixed marker remains
because the restricted application identity correctly lacks `DeleteObject`;
it is overwritten on the next intentional smoke run. Do not run this check
while normal uploads are active because an unexpected message is requeued and
causes the test to fail safely.

The `put` selector includes the object-created family: browser presigned forms
produce `s3:ObjectCreated:Post`, while a later yt-dlp worker will produce
`s3:ObjectCreated:Put`. The intake consumer independently allowlists the
exact event names below—it does not trust the bucket filter as a security
boundary.

The target uses broker publisher confirms and MinIO's persistent event store
where supported by the pinned MinIO release. A publisher confirm only means
RabbitMQ accepted the event; it does **not** mean an intake Pod processed it.
We will not enable MinIO's global synchronous-events mode because it would
make a browser upload wait on RabbitMQ availability. Reconciliation remains
the correctness guarantee if MinIO reaches its bounded local event queue.

This contract follows MinIO's [AMQP notification guide](https://docs.min.io/aistor/administration/bucket-notifications/publish-events-to-amqp/),
[AMQP settings reference](https://docs.min.io/aistor/reference/aistor-server/settings/notifications/amqp/),
and [bucket-notification delivery guidance](https://docs.min.io/aistor/administration/bucket-notifications/).

## Consumer message contract

RabbitMQ delivers the MinIO S3-compatible JSON event envelope as the AMQP
message body. A message can contain a `Records` array, and a useful record
contains `eventName`, `eventTime`, `s3.bucket.name`, `s3.object.key`,
`s3.object.size`, `s3.object.eTag`, and a sequencer.

The message holds no Keycloak owner identity and cannot request a PostgreSQL
change by itself. The consumer URL-decodes each S3 object key exactly once,
rejecting malformed percent escapes before it compares the result with durable
state. It processes each record independently; one message is acknowledged
only after every actionable record has reached a durable outcome.

The first implementation permits only these records:

| Field | Required value | Why |
| --- | --- | --- |
| `eventName` | `s3:ObjectCreated:Post` | This first consumer handles browser direct uploads only. The yt-dlp `Put` transition is a later explicit task. |
| bucket name | `clouddsp-uploads` | Source data has one dedicated private bucket. |
| decoded object key | `uploads/{canonical-lowercase-job-uuid}/{one-filename}` | Matches the Job API's key and database constraint; nested paths, traversal-like text, and worker prefixes are rejected. |
| database job | A retained row with exactly matching `input_bucket` and `input_object_key` | An object event can never create or select a job solely from its key. |
| source type/status | `direct_upload` + `upload_pending` | Stops this direct-upload consumer from advancing linked or terminal jobs. |

No browser or HTTP client calls this consumer. RabbitMQ account permissions,
private Service DNS, and later NetworkPolicy are its network/authentication
boundary. The receiver no longer has a webhook authorization header because
there is no HTTP receiver.

## Acknowledgement, retry, and failure rules

RabbitMQ delivery is at-least-once. The consumer uses manual acknowledgement
and follows these rules:

| Outcome | Queue action | Reason |
| --- | --- | --- |
| Matching job was already durably accepted or advanced by a later stage | Acknowledge without mutation. | Duplicate delivery must never roll state backward or create another job. |
| Event is from another bucket/prefix, has an unsupported event type, lacks a retained matching job, or is malformed | Acknowledge and emit a safe operational category. | Retrying cannot turn an invalid object notification into a valid job. |
| Matching pending job fails durable object verification | Atomically mark the job `failed`, then acknowledge. | A permanently invalid object must not remain pending forever. |
| MinIO `HeadObject`, PostgreSQL, or a required state write fails transiently | Republish through the bounded retry path, wait for broker confirmation, then acknowledge the original. | Avoids an unbounded immediate redelivery loop while retaining recoverable work. |
| Retry limit is exhausted | Publish to `clouddsp.source-intake.dlq`, wait for broker confirmation, then acknowledge. | Keeps the original evidence for operator review instead of dropping it. |

The consumer must not acknowledge before the PostgreSQL outcome is durable. A
process crash after a database commit but before an acknowledgement causes
normal duplicate delivery; the conditional transition turns that duplicate
into a harmless acknowledgement. It must never log AMQP URLs/passwords, raw
event JSON, presigned fields, full object keys, access keys, or database
exception text.

## Durable verification and state transition

Before PostgreSQL changes a job, upload intake performs an internal S3
`HeadObject` through `clouddsp-minio.clouddsp-data.svc`. This requires a future
least-privilege **read-only intake identity**, separate from the Job API's
presigning identity, the MinIO AMQP publisher account, and MinIO root
credentials. Its immutable
[`v001 policy ConfigMap`](../minio/minio-upload-intake-source-read-policy-v001-configmap.yaml)
allows only `s3:GetObject` below `clouddsp-uploads/uploads/*`; it grants no
anonymous access, bucket administration, deletion, listing, upload, or
worker-output access. MinIO authorizes S3 `HeadObject` through this
`s3:GetObject` action, so the consumer verifies metadata without downloading
the audio bytes. The prepared
[`MinIO source-read bootstrap Job`](../minio/minio-upload-intake-source-read-bootstrap-job.yaml)
will create the separate access key and attach this policy after a temporary
data-namespace copy of the ignored runtime Secret is supplied.

The consumer compares the durable row with the `HeadObject` result:

- bucket/key must exactly equal the job's stored input location;
- `x-amz-meta-job-id` must equal the canonical job UUID;
- `x-amz-meta-stem-mode` must equal the job's stored stem mode;
- content type must equal the canonical type stored when the Job API created
  the presigned form; and
- object size must be positive and no greater than the preserved 256 MiB
  source limit. It may also be checked against the user-declared database size,
  but that declaration does not replace the hard maximum.

This does **not** decode audio or prove duration/audio streams. The later
Demucs worker performs those byte-level `ffprobe` checks before GPU work. The
intake step proves only that MinIO holds the object the durable job contract
expected.

For a verified direct-upload job, one PostgreSQL transaction will use a
conditional update equivalent to this behavior:

```text
only when job_id/key/source_type/status/source_uploaded still equal
the expected upload_pending state:
    source_uploaded = true
    status = source_uploaded
    revision = revision + 1
    error_message = null
```

If an earlier delivery made this exact change, the later delivery is a
successful no-op. If a later worker has already advanced a verified source,
the consumer performs no rollback. The `expires_at` value is unchanged, so an
old queue message can never resurrect an expired record.

The upload-intake source now makes the successful transition and its initial
`demucs.requested` outbox insert in the same PostgreSQL transaction. It must
not publish `demucs.requested` directly: publishing after a database commit
leaves a crash window where work is lost, while publishing first could process
a job whose state was never committed. The applied Job API v002 migration
provides the transactional `outbox_events` schema, and the applied
[`upload-intake outbox permission bootstrap Job`](upload-intake-outbox-permissions-bootstrap-job.yaml)
grants the restricted intake login the six required insert columns plus SELECT
only on the three-column idempotency key required by PostgreSQL's named
`ON CONFLICT` constraint. A later dispatcher will lease and publish the durable
event to RabbitMQ according to the versioned
[`demucs.requested` contract](../dispatcher/README.md).

The rolled-out upload-intake Deployment now uses the reviewed ARM64 image that
contains this source change. It is owned by the dedicated
[`../../helm/upload-intake/`](../../helm/upload-intake/README.md) Helm release.
Adoption preserved its Deployment UID, Pod UID, and running digest; the raw
manifest is now a comparison baseline and must not be reapplied. A later
update must repeat the same digest-pinned build, chart review, and explicit
rollout rather than changing a mutable tag.

## Source-to-outbox integration smoke

[`../../tests/source-intake-smoke/source-to-outbox-smoke-job.yaml`](../../tests/source-intake-smoke/source-to-outbox-smoke-job.yaml)
is a one-time end-to-end assertion for the rolled-out worker. It
creates a temporary Keycloak direct-grant client/user, calls the normal
authenticated Job API `POST /jobs` route, posts a minimal valid WAV through the
returned presigned form to private MinIO, and polls the owner-bound Job API
until intake reports `source_uploaded`. It then uses bounded PostgreSQL
metadata assertions to require exactly one `pending demucs.requested` outbox
event, publishes one duplicated source notification to RabbitMQ, waits for the
source queue to drain, and requires the same one-event result again.

The disposable test Pod runs in `clouddsp-data` because its cleanup needs the
already-scoped data-namespace administrator Secrets. It never prints those
values, the temporary token, presigned form, object key, job ID, or SQL rows.
On either success or failure it removes only the object/job/outbox row and
Keycloak client/user it created. It does not create a dispatcher, a Demucs
queue, a worker Pod, or a browser-facing endpoint. The
[`smoke runbook`](../../tests/source-intake-smoke/README.md) pauses both
dispatcher Helm releases during the pending-row assertion and restores them
after the test's cleanup.

On 2026-09-26, the first run reached the duplicate notification but RabbitMQ's
NetworkPolicy blocked the test Pod from the management port. A fixed-name,
label-constrained exception was added to the versioned policy. The retry
passed: the native notification caused `source_uploaded` and one pending
Demucs outbox event, and the deliberate duplicate did not create a second.
The test cleaned up its temporary resources; both dispatchers were restored
to one replica and source/Demucs queues were empty afterward.

## Reconciliation is mandatory

S3-compatible event keys use form-style URL encoding: a space becomes `+`
and a literal plus becomes `%2B`. Intake now decodes those distinctly before
matching the exact PostgreSQL-generated object key. This matters for ordinary
filenames with spaces (including Unicode names); otherwise the notification
can be safely acknowledged as non-actionable even though the uploaded object
exists. The [S3 event message format](https://docs.aws.amazon.com/AmazonS3/latest/userguide/notification-content-structure.html)
documents the plus-for-space representation; parser and handler regression
tests cover that path locally.

For a retained upload that was already acknowledged before this correction,
[`../../scripts/reconcile-one-upload-intake-job.sh`](../../scripts/reconcile-one-upload-intake-job.sh)
accepts only its canonical job UUID. It runs a one-shot module inside the
digest-pinned upload-intake Pod. The module reads the pending row's bucket/key
using the existing restricted database role, then calls the normal parser,
private HeadObject verifier, and atomic source/outbox transition. It does not
overwrite the user object, publish a broker message, or change a completed
job. The command is idempotent and prints only a fixed outcome category:

```bash
./k8Deployment/kubernetes/scripts/reconcile-one-upload-intake-job.sh JOB_UUID
```

This is a focused operator repair, not the eventual periodic reconciler.
Future lost notifications still require an operator to identify and reconcile
pending rows until that bounded background component exists.

MinIO notifications accelerate the normal path but are not authoritative. A
future reconciler periodically selects retained direct-upload rows still in
`upload_pending`, performs the same private `HeadObject` verification, and
calls the same idempotent transition. Therefore a lost notification, bounded
MinIO queue overflow, broker outage, consumer rollout, or duplicate message
cannot strand a valid object forever. It uses bounded batches and backoff so
recovery cannot overload MinIO or PostgreSQL.

The consumer and reconciler share one verification/transition implementation;
they must not grow subtly different security rules. A reconciler never scans
all bucket objects or creates a job from storage—it begins with PostgreSQL's
authoritative job rows.

## Current pure parsing boundary

The side-effect-free implementation now lives in
[`app/minio_event.py`](app/minio_event.py). Its one public function,
`parse_direct_upload_event`, accepts only UTF-8 JSON AMQP body bytes/text and
returns both accepted candidates and explicit ignored-record categories. It
imports only Python's standard library and has no queue, storage, database,
HTTP, filesystem, Kubernetes, or logging operation.

Its focused tests are in [`tests/test_minio_event.py`](tests/test_minio_event.py).
From the repository root, run them without installing an application runtime:

```sh
PYTHONPATH=k8Deployment/kubernetes/services/upload-intake \
  python3 -m unittest discover \
  -s k8Deployment/kubernetes/services/upload-intake/tests \
  -p 'test_*.py' -v
```

The tests prove at least:

1. a valid single `ObjectCreated:Post` record becomes one normalized candidate;
2. URL-encoded filenames decode once without changing the allowed key shape;
3. non-upload prefixes, `Put` events, malformed records, and malformed UUIDs
   never become database queries or processing requests; and
4. multiple records are processed independently without silently accepting a
   malformed message.

## Current PostgreSQL transition boundary

[`app/database_transition.py`](app/database_transition.py) is the next narrow
boundary. It accepts a parser-produced candidate and a caller-owned database
cursor, so it opens no network connection, receives no database password, and
cannot acknowledge an AMQP message by itself. Its parameterized operations:

1. select only an unexpired `direct_upload` job whose stored bucket/key exactly
   match the candidate and whose status is still `upload_pending`;
2. after a later private `HeadObject` check, atomically change that exact row
   to `source_uploaded` only when its original revision still matches; or
3. record one fixed safe permanent-object category as `failed` under the same
   pending/revision predicates.

An update that returns no row is intentionally a successful idempotent no-op:
another delivery, deletion, expiry, or later stage won the race first. The
helper performs no broad follow-up query and never rolls a state backward.
It returns only a small result object, never a job row or a raw database error.

Its isolated tests are in [`tests/test_database_transition.py`](tests/test_database_transition.py).
They use a mock cursor—no PostgreSQL data changes are made—and can run with the
existing parser tests:

```sh
PYTHONPATH=k8Deployment/kubernetes/services/upload-intake \
  python3 -m unittest discover \
  -s k8Deployment/kubernetes/services/upload-intake/tests \
  -p 'test_*.py' -v
```

## Current MinIO HeadObject boundary

[`app/object_storage.py`](app/object_storage.py) verifies a selected pending
row with one private S3-compatible `HeadObject` request. HeadObject returns the
object's headers/metadata but not its audio body, so this intake stage can
prove storage identity without downloading or decoding media. It calls only
the internal `clouddsp-minio.clouddsp-data.svc:9000` endpoint, never the
browser-facing `minio.localhost` Traefik host.

The future runtime loads its isolated MinIO key from the ignored application
Secret and its pinned Boto3 dependency from [`requirements.lock`](requirements.lock).
The policy permits only `s3:GetObject` below `clouddsp-uploads/uploads/*`;
MinIO authorizes HeadObject through that same read action. The module compares
the resulting `ContentLength`, `ContentType`, `job-id` metadata, and
`stem-mode` metadata against PostgreSQL's restricted row. It requires the
completed length to equal the direct-upload job's declared `File.size` when
present and never exceed 256 MiB. It performs no `GetObject`, listing, upload,
deletion, or browser-facing request.

A known missing object or metadata/type/size mismatch becomes one fixed
permanent-failure category. Network, authorization, malformed-response, and
other service faults remain retryable categories: they must not cause a job to
be marked failed merely because MinIO may be temporarily unavailable. The
focused [`tests/test_object_storage.py`](tests/test_object_storage.py) injects
a fake S3 client, so it makes no network call and changes no MinIO object.

## Current transaction-aware message handler

[`app/message_handler.py`](app/message_handler.py) composes the three reviewed
boundaries without introducing a RabbitMQ client yet. For every parser-accepted
record, it:

1. opens a short read scope to select the restricted pending row, then closes
   it before any MinIO network request;
2. calls the private HeadObject verifier;
3. opens a short write transaction to conditionally record either
   `source_uploaded` or a fixed permanent-failure category; and
4. returns only record indexes and fixed outcomes that are safe for a future
   AMQP adapter to count or log.

The split read/HeadObject/write scopes avoid holding a PostgreSQL transaction
open while waiting on storage. The selected revision guards the final UPDATE,
so a competing consumer cannot overwrite a newer state during that interval.
An absent/advanced row becomes a no-op. A transient storage or database error
raises instead of returning an acknowledgement-safe result; the later AMQP
adapter will leave that message unacknowledged for the bounded retry path.

The handler never acknowledges a RabbitMQ delivery, publishes Demucs work,
opens a PostgreSQL connection, or stores object bytes. Its isolated
[`tests/test_message_handler.py`](tests/test_message_handler.py) use fake
database transaction contexts and a fake S3 client to prove the durable order
without changing any live resource.

## Current PostgreSQL transaction adapter

[`app/postgresql.py`](app/postgresql.py) is the concrete implementation of the
message handler's `read_cursor()` and `write_cursor()` interface. It reads the
dedicated `clouddsp-upload-intake` database Secret values at runtime and opens
connections only through the private
`clouddsp-postgresql.clouddsp-data.svc:5432` Service address.

Its read cursor uses PostgreSQL `default_transaction_read_only=on` with
autocommit, so the one pending-job SELECT ends before MinIO I/O. Its write
cursor uses an explicit Psycopg transaction: normal context exit commits the
conditional state update; an exception rolls it back. The adapter maps only
driver and network faults to a safe retryable category and preserves other
application-contract failures for the handler to stop safely.

Psycopg is lazily imported so the pure unit tests do not require a database
driver or a connection. The pinned binary client is now in
[`requirements.lock`](requirements.lock) and will be installed by the later
image task. The focused [`tests/test_postgresql.py`](tests/test_postgresql.py)
use a fake driver to prove the connection settings, read-only option,
transaction context, rollback path, and safe outage category without touching
the live PostgreSQL Pod.

## Current RabbitMQ manual-ack adapter

[`app/amqp_consumer.py`](app/amqp_consumer.py) is the narrow transport adapter
for the existing `clouddsp.source-intake` quorum queue. It reads the restricted
RabbitMQ credentials from the ignored application Secret and connects only to
the private `clouddsp-rabbitmq.clouddsp-data.svc:5672` ClusterIP Service on the
fixed `/clouddsp` virtual host. It rejects a changed vhost/queue configuration
instead of becoming an arbitrary broker reader.

The adapter passively checks the pre-created queue and uses `prefetch_count=1`:
one Pod holds at most one unacknowledged delivery while it performs MinIO and
PostgreSQL work. Its `consume_one_source_intake_delivery` function calls
RabbitMQ `basic_get` with `auto_ack=False`, invokes the message handler, and
calls `basic_ack` only after the handler returns its durable-safe result.

If the handler or acknowledgement fails, it does not acknowledge or
immediately requeue the message. Immediate requeue would make a hot loop
during a MinIO/PostgreSQL outage. The later long-running entrypoint will close
and reconnect so RabbitMQ retains/redelivers the at-least-once message; a
separate retry task will implement the reviewed delayed-retry and DLQ publish
route. The adapter has no deployment loop, auto-ack, topology creation, or
Demucs-publishing capability. Its fake-channel
[`tests/test_amqp_consumer.py`](tests/test_amqp_consumer.py) proves the
receive → handle → acknowledge order without calling a live broker.

## Current long-running consumer runtime

[`app/consumer_runtime.py`](app/consumer_runtime.py) is the composition root
that the later worker image will start with `python -m app.consumer_runtime`.
It creates one restricted PostgreSQL transaction adapter, one private MinIO
HeadObject client, and one RabbitMQ manual-ack connection cycle. It has two
deliberately separate loops:

1. a **connection cycle** opens RabbitMQ, passively verifies the reviewed
   queue, polls one message at a time, then always closes the connection; and
2. a **supervisor loop** waits a bounded interval after a failed cycle before
   reconnecting.

The connection cycle’s `finally` block is important: when a MinIO, PostgreSQL,
or RabbitMQ operation fails before `basic_ack`, closing the AMQP connection
returns the unacknowledged delivery to RabbitMQ for normal at-least-once
redelivery. It does not issue `basic_nack(requeue=True)`, which would make an
unhealthy dependency create a hot redelivery loop. It also avoids logging raw
event data, keys, AMQP URLs, database diagnostics, or secrets; its one failure
line is a fixed operational category.

An idle `basic_get` sleeps for one second by default, so an empty queue does
not consume a CPU core. `SIGTERM` and `SIGINT` set an in-memory stop flag; this
will let a future Kubernetes Deployment terminate a Pod cooperatively during
its grace period, after which the connection closes normally. This module does
not create a Kubernetes object, expose a network listener, publish retry/DLQ
messages, publish Demucs work, or call the Kubernetes API.

[`tests/test_consumer_runtime.py`](tests/test_consumer_runtime.py) uses fake
connections and sleep functions to prove process composition, idle backoff,
connection closing after a processing failure, and reconnect backoff. It makes
no live broker, database, storage, Docker, or Kubernetes call.

## Worker container image

[`Dockerfile`](Dockerfile) packages this exact worker as a two-stage Linux
container image. Its validation stage installs only the hash-locked Boto3,
Psycopg, and Pika runtime dependencies and executes the full focused Python
test suite. Its final stage starts again from the same digest-pinned official
Python 3.12 slim image, copies only the validated packages and `app/` source,
then runs the worker as numeric non-root account `10001`. It exposes no port:
this worker makes only outbound connections to its private RabbitMQ, MinIO, and
PostgreSQL Services, so it needs no Service or Ingress.

[`../../scripts/build-upload-intake-image.sh`](../../scripts/build-upload-intake-image.sh)
builds `linux/arm64` for the current k3d nodes, runs the test validation,
pushes the result to the dedicated local registry, and prints its immutable
digest. The subsequent Deployment task must copy that digest into
[`../../images.lock.yaml`](../../images.lock.yaml) and use only the immutable
reference. The image build never receives a Kubernetes Secret, does not call a
live dependency, and does not create a Kubernetes workload by itself.

## Prepared upload-intake Deployment

[`upload-intake-deployment.yaml`](upload-intake-deployment.yaml) now defines
one non-root `clouddsp-upload-intake` Pod in `clouddsp-app`. It uses only the
immutable image reference from `images.lock.yaml`, has no Service or Ingress,
and disables its automatically mounted Kubernetes ServiceAccount token. It
receives three separate runtime Secrets from its own namespace: the restricted
PostgreSQL login, MinIO source-read access key, and RabbitMQ source-queue
consumer login. It never receives a root/admin/bootstrap credential.

The manifest deliberately starts with one replica and applies CPU, memory, and
ephemeral-storage requests/limits. The internal database, S3, and AMQP Service
DNS names are normal Deployment configuration; they are not browser addresses.
It has no placeholder health probe because this processor offers no inbound
endpoint and a superficial `exec` probe would incorrectly claim success while
an outbound dependency was unavailable. A later observability task will add a
meaningful worker-health signal before KEDA scaling is introduced.

This file is prepared only; it is not applied by creating it. Before applying,
verify that the three same-namespace runtime Secrets already exist and that the
image digest remains available in the local registry.

## Versioned RabbitMQ topology definition

[`../rabbitmq/rabbitmq-source-intake-topology-v001-configmap.yaml`](../rabbitmq/rabbitmq-source-intake-topology-v001-configmap.yaml)
now records the reviewed broker structure as RabbitMQ definitions JSON. It
creates no queue by itself because the current RabbitMQ StatefulSet does not
mount or import this ConfigMap yet. The later bootstrap Job will import it
only after a separate task has supplied the publisher/consumer Secret values.

The definition creates `/clouddsp`, its three durable direct exchanges, and
three durable quorum queues:

| Queue | Role | Important behavior |
| --- | --- | --- |
| `clouddsp.source-intake` | Normal MinIO source-created events | A five-delivery safety cap dead-letters repeatedly unacknowledged messages to the DLQ. |
| `clouddsp.source-intake.retry.30s` | Bounded delayed retry | A consumer republishes a transient failure here; its 30-second TTL returns the message to the normal intake exchange. |
| `clouddsp.source-intake.dlq` | Exhausted/poison-event evidence | Retains messages for inspection; it has no automatic consumer or retry route. |

Each source/retry dead-letter route requests RabbitMQ quorum queues'
at-least-once dead-letter strategy and `reject-publish` overflow behavior. A
bounded `x-max-length` keeps a local broker outage visible rather than allowing
unbounded laptop disk growth; the PostgreSQL-driven reconciler remains the
fallback for events that cannot be retained.

## Application credential templates

Two committed templates now describe separate RabbitMQ identities without
containing a live password:

| Template | Namespace and eventual consumer | Role |
| --- | --- | --- |
| [`../minio/minio-source-intake-rabbitmq-credentials.secret.example.yaml`](../minio/minio-source-intake-rabbitmq-credentials.secret.example.yaml) | `clouddsp-data`; MinIO StatefulSet | Publish-only `clouddsp-minio-events` identity. It includes the URI MinIO's native AMQP setting requires. |
| [`upload-intake-rabbitmq-credentials.secret.example.yaml`](upload-intake-rabbitmq-credentials.secret.example.yaml) | `clouddsp-app`; upload-intake Deployment | Consume-only `clouddsp-upload-intake` identity with raw username/password fields for the future AMQP client. |

The two RabbitMQ templates must be copied into the ignored
`k8Deployment/.local/` directory before their bootstrap task. The MinIO
publisher template deliberately contains its RabbitMQ password twice: once as
raw text for the bootstrap Job to create the broker user, and once
percent-encoded within MinIO's required AMQP URI. They must be the same
password in two representations. The upload-intake MinIO runtime Secret has
an ignored local copy at
`k8Deployment/.local/upload-intake-minio-credentials.secret.yaml`. It uses a
distinct S3 secret key. Its committed
[`upload-intake-minio-credentials.secret.example.yaml`](upload-intake-minio-credentials.secret.example.yaml)
template now records the exact runtime key names without exposing that key;
the ignored copy must match the temporary MinIO bootstrap identity. None of
these templates creates a live Kubernetes Secret until its local copy is
applied.

## PostgreSQL identity boundary

The upload-intake consumer shares the existing `clouddsp_job_api` database
because it must atomically transition the authoritative `jobs` row that the
Job API created. Sharing a database does **not** mean sharing the Job API
login. The one-shot
[`upload-intake-database-bootstrap Job`](upload-intake-database-bootstrap-job.yaml)
uses PostgreSQL's administrator credential only to create the separate
`clouddsp-upload-intake` login, prove that login works, and grant this exact
initial authority:

| Capability | Allowed columns/actions | Not allowed |
| --- | --- | --- |
| Read one candidate job | `job_id`, source type/location/metadata, stem mode, source state, revision, expiry | `owner_sub`, source filename, artifact maps, timestamps, full error text, or any other application table |
| Mark the verified intake outcome | `source_uploaded`, `status`, `revision`, `error_message` | insert/delete rows, alter source bucket/key/metadata, alter ownership, expiry, artifacts, schemas, roles, or databases |
| Create the initial Demucs promise *(after the separate v002 permission Job succeeds)* | Insert only `event_id`, `job_id`, `stage`, `stem_name`, `event_type`, and `payload`; read only the `job_id`/`stage`/`stem_name` conflict key required by PostgreSQL `ON CONFLICT` | read event payloads or dispatcher state, update/delete outbox rows, set publication/lease/retry/error fields, create any other event type, or publish RabbitMQ messages |

The role has no `CREATE`, `DELETE`, database-owner, role-management, or
PostgreSQL-superuser permission. It has no `INSERT` permission on `jobs` or
other application tables, and no broad `INSERT` on `outbox_events`: the v002
extension permits only the six columns in the table above, leaving PostgreSQL
defaults to establish the safe initial pending state. Its column privileges are
intentionally stronger than trusting a future application query alone: even a
compromised consumer process cannot expose owner identities, rewrite completed
artifacts, impersonate the dispatcher, or create arbitrary database work.

Two committed templates make the namespace boundary explicit:

| Template | Namespace and use | Lifetime |
| --- | --- | --- |
| [`upload-intake-database-credentials.secret.example.yaml`](upload-intake-database-credentials.secret.example.yaml) | `clouddsp-app`; eventual upload-intake Deployment | Retained for the consumer runtime after it exists. |
| [`upload-intake-database-bootstrap-credentials.secret.example.yaml`](upload-intake-database-bootstrap-credentials.secret.example.yaml) | `clouddsp-data`; administrator-only bootstrap Job | Temporary duplicate of the runtime values; delete after the Job passes. |

Copy both templates into `k8Deployment/.local/` with their documented local
filenames and use the same database name, username, and password in both.
The database password must be unique to PostgreSQL; a same-named RabbitMQ or
MinIO account is a separate identity and must not reuse it. The initial
bootstrap requires the already-applied Job API v001 schema, but it never
migrates or changes that schema itself. The follow-up outbox permission Job
requires the already-applied v002 schema and intentionally needs only the
PostgreSQL administrator Secret; its fixed application role name and database
name are not secrets, so it does not duplicate or mount the runtime password.

## Prepared topology bootstrap Job

[`../rabbitmq/rabbitmq-source-intake-bootstrap-job.yaml`](../rabbitmq/rabbitmq-source-intake-bootstrap-job.yaml)
is the completed administrator Job. It used RabbitMQ's pinned management
client inside `clouddsp-data` to import the immutable v001 definition, create
both application users, assign the reviewed regex permissions, and print only
non-sensitive topology/permission metadata. It is not a long-running
controller and has no Kubernetes API credentials.

[`../rabbitmq/rabbitmq-upload-intake-bootstrap-credentials.secret.example.yaml`](../rabbitmq/rabbitmq-upload-intake-bootstrap-credentials.secret.example.yaml)
documents that temporary data-namespace copy. Its ignored local materialization
matched the already-applied `clouddsp-app` runtime Secret because Kubernetes
prohibits the Job from mounting that application-namespace Secret directly.
The temporary Secret was deleted after the completed Job verified the
topology; only the runtime Secret remains.

The versioned
[`rabbitmq-source-intake-bootstrap.rb`](../../scripts/rabbitmq-source-intake-bootstrap.rb)
runner now verifies that durable broker topology, both least-privilege users,
their permissions, and their live credentials. Its fresh-cluster reconcile
creates the temporary Secret and fixed-name Job only when both topology and
users are absent, then removes the temporary Secret after verification.
