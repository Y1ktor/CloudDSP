# CloudDSP outbox dispatcher contracts

This document records versioned internal processing-request contracts. The
imported v001 topology and current dispatcher runtime handle only
`demucs.requested`; the source-only v002 post-Demucs contract below is prepared
before its topology or generic dispatcher integration exists. This Markdown is
documentation, not a Kubernetes resource, so applying it is neither possible
nor intended.

## Why a dispatcher exists

When upload-intake verifies an object, its one PostgreSQL transaction changes
the job to `source_uploaded` **and** inserts one `pending` outbox row. It does
not publish to RabbitMQ directly because PostgreSQL and RabbitMQ cannot share
one transaction. Publishing directly after the database commit could lose work
if the Pod crashes between those two systems; publishing first could run Demucs
for a job whose database state never committed.

The later dispatcher is a small internal Deployment that bridges that gap:

```text
PostgreSQL outbox row
  -> short database lease
  -> persistent RabbitMQ publish with broker confirmation
  -> mark the same leased row published
  -> Demucs queue
```

The confirmation-to-database-update gap can still yield a duplicate publish if
the dispatcher crashes at exactly that point. That is why the queue is
at-least-once and why the worker must be idempotent; the dispatcher does not
promise impossible exactly-once delivery across two independent systems.

## Version 1 broker route

The future topology/bootstrap task must create these exact durable resources in
RabbitMQ virtual host `/clouddsp`. No browser-facing Service or Ingress is part
of this contract.

| Contract field | Version 1 value | Reason |
| --- | --- | --- |
| Exchange | `clouddsp.processing-events` | A dedicated processing boundary keeps worker requests separate from untrusted MinIO source notifications. |
| Exchange type | `direct` | Version 1 has one exact route per stage; a consumer cannot receive a wildcard class of work accidentally. |
| Routing key | `demucs.requested` | Identifies a request to perform the job-wide Demucs stage. |
| Queue | `clouddsp.demucs.requests` | Holds durable stage work until a future KEDA/Demucs consumer accepts it. |
| Publisher | Internal dispatcher only | Upload-intake, the browser, MinIO, and Demucs workers do not publish this request. |
| Consumer | Future Demucs worker only | The worker validates the body and claims durable stage state before audio processing. |

The applied immutable
[`rabbitmq-processing-topology-v001 ConfigMap`](../rabbitmq/rabbitmq-processing-topology-v001-configmap.yaml)
declares the delivery paths below. It did not change RabbitMQ by itself; the
separately applied administrator-only
[`rabbitmq-processing-topology-bootstrap Job`](../rabbitmq/rabbitmq-processing-topology-bootstrap-job.yaml)
mounted and imported that exact definition. Their names and retry timing are
intentionally not part of the worker payload contract: a worker consumes the
same versioned request body after redelivery.

| Purpose | Exchange/routing key | Queue | Behavior |
| --- | --- | --- | --- |
| Ordinary Demucs request | `clouddsp.processing-events` / `demucs.requested` | `clouddsp.demucs.requests` | Durable quorum queue; it accepts at most 1,000 waiting jobs, then rejects new publisher requests so the dispatcher leaves its authoritative outbox row for retry. |
| Delayed retry | `clouddsp.processing-retry` / `demucs.retry` | `clouddsp.demucs.requests.retry.30s` | Holds a worker-published retry for 30 seconds, then dead-letters it back to the ordinary request route. |
| Dead letter | `clouddsp.processing-dead-letter` / `demucs.dead-letter` | `clouddsp.demucs.requests.dlq` | Preserves a request after the main queue reaches its five-delivery limit for explicit diagnosis. |

## Message body

The RabbitMQ body is the outbox row's `payload` JSON **without transformation**.
The dispatcher validates its shape before publication but must not append a
presigned URL, token, password, owner identity, original filename, raw error,
or a second copy of database delivery state.

```json
{
  "job_id": "3a1f329f-04f7-4b4f-842a-f2b2a7c9e3c3",
  "schema_version": 1,
  "source": {
    "bucket": "clouddsp-uploads",
    "object_key": "uploads/3a1f329f-04f7-4b4f-842a-f2b2a7c9e3c3/mix.wav"
  },
  "stem_mode": "4-stems"
}
```

| Field | Required value and validation | Why it is present |
| --- | --- | --- |
| `schema_version` | Integer exactly `1` | Lets a worker reject an unknown incompatible body instead of guessing at its meaning. |
| `job_id` | Lowercase canonical UUID | Correlates every durable state change and MinIO prefix to one job. |
| `source.bucket` | `clouddsp-uploads` in the local profile | Identifies the private MinIO bucket; it is not a public URL. |
| `source.object_key` | Non-empty stable key under `uploads/{job_id}/` | Lets the worker re-read authoritative storage without trusting an expired upload contract. |
| `stem_mode` | One of `2-stems`, `4-stems`, or `6-stems` | Carries the API-authorized Demucs output selection. |

The body deliberately does not include `event_id`. One event ID can be carried
as a message property and redelivered many times; copying it into both places
would create two sources of truth. The `outbox_events` unique key remains
`(job_id, stage, stem_name)`, where Demucs uses stage `demucs` and stem name
`''` because it processes the source as one job-wide stage.

## Required AMQP properties

The dispatcher must publish every version 1 body with these properties. They
are metadata for RabbitMQ and consumers; they are not browser API fields.

| AMQP property | Required value | Purpose |
| --- | --- | --- |
| `content_type` | `application/json` | Reject accidental non-JSON payloads before a worker interprets them. |
| `content_encoding` | `utf-8` | Makes the JSON byte encoding explicit. |
| `delivery_mode` | `2` (persistent) | Asks RabbitMQ to persist the message in the durable queue. Publisher confirmation remains required. |
| `type` | `demucs.requested` | Matches the fixed event kind without re-parsing the JSON body. |
| `message_id` | `outbox_events.event_id` as canonical UUID text | Provides one immutable publication correlation ID across retries. |
| `correlation_id` | `job_id` as canonical UUID text | Lets logs/metrics correlate work without inspecting an object key. |

No AMQP header may carry a credential, presigned URL, Keycloak token, full
database row, owner `sub`, or an arbitrary exception message. RabbitMQ's own
redelivery/death headers are broker-owned operational metadata and must not be
trusted as application authorization.

## Prepared version 2 downstream routes

After a complete Demucs transaction commits its v004 outbox rows, the later
generic dispatcher will use the dependency-free
[`downstream outbox request contract`](app/downstream_outbox_request.py) to
validate and render the matching persistent AMQP request. The mapping is fixed
in code and must be mirrored by a later immutable RabbitMQ topology version:

| Durable outbox triple | Exchange / routing key | Planned queue | Status now |
| --- | --- | --- | --- |
| `basic-pitch`, any approved non-drum stem, `basic-pitch.requested` | `clouddsp.processing-events` / `basic-pitch.requested` | `clouddsp.basic-pitch.requests` | Live: the v002 import Job verified this queue, binding, retry queue, and DLQ. |
| `adtof`, `drums`, `adtof.requested` | `clouddsp.processing-events` / `adtof.requested` | `clouddsp.adtof.requests` | Live: the v002 import Job verified this queue, binding, retry queue, and DLQ. |

The downstream body retains exactly its durable payload, normalized as compact
UTF-8 JSON:

```json
{
  "job_id": "3a1f329f-04f7-4b4f-842a-f2b2a7c9e3c3",
  "schema_version": 1,
  "stem_name": "bass",
  "stem": {
    "bucket": "clouddsp-uploads",
    "content_type": "audio/wav",
    "object_key": "stems/3a1f329f-04f7-4b4f-842a-f2b2a7c9e3c3/bass.wav",
    "sha256": "<64 lowercase hexadecimal characters>",
    "size_bytes": 1234
  }
}
```

Its AMQP properties use `application/json`, `utf-8`, persistent
`delivery_mode=2`, the exact outbox event type, `message_id=event_id`, and
`correlation_id=job_id`. Neither identifier appears in the body. The validator
rejects crossed stage/stem/type combinations, extra payload fields, a non-WAV
type, different bucket/key, unsafe size, or a malformed SHA-256 before any
future Pika call. Its five pure tests do not access RabbitMQ or PostgreSQL.

The current `amqp_publisher.py`, `dispatch_once.py`, supervisor, image, and
Deployment remain Demucs-only. This is intentional: although the lease adapter
now has a separate generic v004 claim helper, no running process calls it until
the next route-aware composition task can select the matching strict publisher.
That prevents an otherwise valid Basic Pitch/ADTOF lease from being handed to
the existing Demucs-only AMQP adapter.

The immutable
[`v002 downstream topology ConfigMap`](../rabbitmq/rabbitmq-processing-topology-v002-downstream-configmap.yaml)
supplied that queue source, and its administrator-only import Job has now
imported it into the local broker. It adds separate quorum main/retry/DLQ queues
for Basic Pitch and ADTOF, each with a 30-second retry delay, five-delivery
limit, bounded 1,000-message capacity, and the existing processing exchanges.
The completed Job at
[`rabbitmq-processing-topology-v002-downstream-bootstrap-job.yaml`](../rabbitmq/rabbitmq-processing-topology-v002-downstream-bootstrap-job.yaml),
verified the existing v001 exchanges before import, then verified all six
new queues and bindings afterward.

## Publisher and consumer safety rules

The future dispatcher must follow this order for one outbox row:

1. Atomically claim a due `pending` row, or recover an expired `leased` row,
   using a new lease token and bounded expiry.
2. Validate the row's fixed stage, event type, and version 1 payload before
   sending anything to RabbitMQ. If it is incompatible, terminally mark that
   same lease `dead_lettered` with a bounded safe reason; do not publish it.
3. Publish a valid body persistently to `clouddsp.processing-events` with routing key
   `demucs.requested`, then wait for publisher confirmation.
4. Only after confirmation, mark the same `event_id` and lease token
   `published`. A competing dispatcher may never publish a row it does not
   currently lease.
5. On a known pre-confirmation failure, release/schedule the row for bounded
   retry. On an uncertain confirmation outcome, let lease recovery publish it
   again; the downstream stage must tolerate that duplicate.

The future Demucs worker must independently validate the body, re-read its
authoritative PostgreSQL job state and private MinIO object, and claim its own
stage lease before it runs audio processing. It must not assume one broker
delivery means one execution.

## Implemented lease boundary

[`app/outbox_lease.py`](app/outbox_lease.py) now implements the first pure
PostgreSQL adapter and [`tests/test_outbox_lease.py`](tests/test_outbox_lease.py)
proves its contract without requiring a database or broker. It atomically
claims one due pending event or expired lease with `FOR UPDATE SKIP LOCKED`,
generates/requires a lease token, increments the attempt count, and returns no
event when the outbox is idle. Its guarded completion helpers can mark only the
same lease `published`, or schedule a retry only after a *known* pre-confirmed
broker failure. It can also terminally mark only that lease `dead_lettered`
with the fixed `invalid_event_contract` category before broker publication. An
uncertain publisher-confirmation result deliberately relies on lease expiry,
where a later dispatcher may duplicate-publish safely.

Alongside the unchanged Demucs-specific helper, it now exposes
`claim_due_dispatchable_outbox_event`. Its `LeasedOutboxEvent` data model
retains the finite `(stage, stem_name, event_type)` tuple and its SQL claim
predicate admits only the reviewed Demucs, Basic Pitch, and ADTOF combinations.
It has the same atomic lease/recovery behavior, but is intentionally unused by
the current runtime. The next composition task will validate the returned row
with the corresponding strict Demucs/downstream request builder before it can
publish to RabbitMQ.

That pure selection step now exists in
[`app/dispatchable_outbox_request.py`](app/dispatchable_outbox_request.py).
It accepts only a `LeasedOutboxEvent`, preserves the existing Demucs request
validator, and otherwise delegates to the Basic Pitch/ADTOF validator. Both
paths return the same publisher-ready fields—fixed exchange, routing key,
compact JSON body, persistent AMQP properties, event ID, and job correlation
ID—without importing Pika or contacting a broker. It rejects crossed triples
or malformed private-object evidence through one safe terminal category. The
generic Pika boundary now publishes that common request record through
`publish_dispatchable_amqp_request` in
[`app/amqp_publisher.py`](app/amqp_publisher.py). It accepts only immutable
Demucs/downstream request models, rechecks the finite exchange/routing/type
allowlist and persistent AMQP metadata, then performs the same mandatory
publisher-confirmed operation used by the Demucs wrapper. A forged route is
rejected before Pika is loaded. A known nack or unroutable result remains a
bounded retry case; an uncertain confirmation still leaves PostgreSQL lease
recovery to make a duplicate-safe later attempt. The current Demucs-only
runtime is still untouched.

The completion, retry, and terminal-invalid-event helpers now also use
stage-neutral names: `mark_outbox_event_published`,
`schedule_outbox_event_retry`, and `mark_outbox_event_dead_lettered`. Their SQL
has always guarded only the durable `(event_id, lease_token)` ownership pair,
so the new names make that true scope explicit for downstream events without
changing the state transition. The old Demucs-named symbols remain thin
compatibility wrappers for the current `dispatch_once` runtime. Three isolated
tests confirm a downstream event receives the same ownership, bounded retry,
and safe terminal-state guarantees.

The complete generic one-attempt composition is now in
[`app/dispatch_dispatchable_once.py`](app/dispatch_dispatchable_once.py). It
commits a finite-vocabulary claim, validates/selects a route before opening
RabbitMQ, waits for publisher confirmation, and then uses a second short
transaction to update that exact lease. A malformed event becomes terminal
before any broker connection; a known pre-confirmation failure receives bounded
retry; an uncertain confirmation leaves its lease untouched for duplicate-safe
recovery. It shares only safe outcome categories with the current runtime and
is not imported by the deployed Demucs-only supervisor.

[`app/dispatcher_generic_runtime.py`](app/dispatcher_generic_runtime.py) is
the deliberately separate process entrypoint for that composition. It reuses
the existing non-secret setting parser, bounded idle/database waits, and
SIGTERM/SIGINT handling, but injects `dispatch_dispatchable_once` into the
shared supervisor. The ordinary `app.dispatcher_runtime` entrypoint remains
unchanged. A future Docker command and Deployment rollout must explicitly use
`python -m app.dispatcher_generic_runtime`; importing this file has no database
or broker side effect.

The module does not open PostgreSQL, publish RabbitMQ work, read credentials,
or run a loop. Those responsibilities remain separate so the next tasks can
give its runtime Pod exactly the database and broker permissions it needs.

## Restricted PostgreSQL boundary

The completed
[`dispatcher-database-bootstrap Job`](dispatcher-database-bootstrap-job.yaml)
created a distinct `clouddsp-dispatcher` PostgreSQL login. It was intentionally
an administrator-only, short-lived Job in `clouddsp-data`; the temporary
bootstrap Secret was deleted after it passed. The future long-running
dispatcher belongs in `clouddsp-app` and its separate restricted runtime
database Secret is already applied there.

The committed [database runtime Secret template](dispatcher-database-credentials.secret.example.yaml)
records the three keys read by both dispatcher Deployments. The separate
[RabbitMQ runtime Secret template](dispatcher-rabbitmq-credentials.secret.example.yaml)
records their two publisher login keys. These are name/key contracts with
placeholders, not deployed credentials. Each ignored local runtime copy must
match the corresponding temporary bootstrap identity, and the two backends
must retain separate passwords.

The role is restricted to the adapter's exact `public.outbox_events` boundary:

- `SELECT` only the claim filters, order columns, durable payload, and
  `RETURNING` fields required by `app/outbox_lease.py`.
- `UPDATE` only publication state, lease fields, delivery attempts,
  `available_at`, `published_at`, and bounded error category.
- No `INSERT`, `DELETE`, schema/data-definition, role administration, table
  ownership, or access to `public.jobs`.

This split matters: upload-intake alone creates a source-verified outbox event
inside its source-state transaction; a dispatcher can lease/publish/complete
that promise but cannot invent, erase, or alter its processing request.

## Runtime dependency boundary

[`requirements.lock`](requirements.lock) now pins Pika `1.4.4` by its package
hash and Psycopg `3.2.13` plus its binary and typing dependencies by exact
hash. Pika is the future dispatcher's AMQP 0-9-1 publisher client; Psycopg is
the PostgreSQL client used by the later runtime composition. Neither package is
a RabbitMQ broker, Kubernetes controller, background loop, or database role by
itself. The later image task must use `pip --require-hashes` against this file.

## Implemented publisher boundary

[`app/amqp_publisher.py`](app/amqp_publisher.py) now owns the narrow AMQP
publisher side of one `demucs.requested` event. It uses only the private
RabbitMQ Service, fixed vhost/exchange/routing key, restricted publisher
credentials, `mandatory=True`, persistent `delivery_mode=2`, and explicit
publisher confirmation. It performs no topology declaration because the
publisher identity lacks configure permission.

The adapter rejects an incompatible version-1 durable body before a broker
call. It maps a failed connection before any publish, a negative confirmation,
and a mandatory unroutable return to the bounded failure codes the outbox
adapter permits. A connection failure while waiting for a publish confirmation
is instead explicitly *uncertain*: the later runtime must leave the PostgreSQL
lease to expire rather than scheduling an eager duplicate retry.

[`tests/test_amqp_publisher.py`](tests/test_amqp_publisher.py) uses a fake Pika
module and channel to prove the exact message/properties, fixed route, no
topology declaration, configuration rejection, and known-versus-uncertain
failure boundary without opening a broker connection.

## Implemented PostgreSQL transaction boundary

[`app/postgresql.py`](app/postgresql.py) now provides the dispatcher's private,
short Psycopg write-cursor scope. It uses the restricted
`clouddsp-dispatcher` login, the private PostgreSQL Service, dictionary rows,
an explicit transaction, and a bounded statement timeout. Each scope commits
or rolls back before returning to its caller; a future runtime must never hold
one open while waiting for RabbitMQ confirmation.

[`tests/test_postgresql.py`](tests/test_postgresql.py) uses a fake Psycopg
driver to prove its Secret/configuration handling, explicit transaction
boundary, rollback propagation, and safe unavailable category. This module
does not decide which outbox SQL action to run and has no polling loop or AMQP
publish capability.

## Implemented one-attempt dispatch composition

[`app/dispatch_once.py`](app/dispatch_once.py) now composes the reviewed pieces
for exactly one event: it claims and commits a PostgreSQL lease, opens a
confirming publisher channel, publishes the event, then opens a second database
transaction to mark the same lease published. A known pre-confirmation broker
failure instead schedules the same lease for a 30-second retry.

An uncertain confirmation loss performs no second database write, because
RabbitMQ may already have accepted the message. The lease expires and a later
attempt may safely duplicate-publish it. An incompatible durable body is never
published: a second guarded PostgreSQL transaction clears its lease and records
the terminal `dead_lettered` state with only `invalid_event_contract` as the
safe reason. This PostgreSQL state is distinct from RabbitMQ's worker-message
DLQ because no malformed message reaches RabbitMQ at all.

The function does not poll, sleep, install signal handlers, or create a long-
lived connection. [`tests/test_dispatch_once.py`](tests/test_dispatch_once.py)
uses fake cursors and broker calls to prove those paths without live services.

## Implemented bounded dispatcher supervisor

[`app/dispatcher_runtime.py`](app/dispatcher_runtime.py) now adds the
long-running process boundary around `dispatch_once`. It constructs the
restricted private PostgreSQL and RabbitMQ settings once, then performs one
short outbox attempt at a time. It immediately checks for more work after a
published, stale, terminal, retry-scheduled, or confirmation-unknown outcome,
which lets a finite backlog drain without inserting an artificial delay after
every event. Only an empty outbox sleeps for one bounded second by default.

A temporary PostgreSQL availability failure is the only exception the
supervisor retries: it emits a fixed safe message and waits five bounded
seconds before the next attempt. The short database scope has already rolled
back, so no partial state is retained. Contract, schema, and programming
errors intentionally end the process rather than being hidden in an infinite
retry loop; a later Kubernetes Deployment can restart the bad Pod visibly.

The runtime installs small SIGINT/SIGTERM handlers that only set an in-memory
stop flag. Kubernetes can therefore terminate the Pod cooperatively after its
current bounded attempt or wait. If termination happens after publisher
confirmation but before PostgreSQL records it, the durable lease-expiry query
preserves the existing duplicate-safe recovery behavior. The module now runs
inside the reviewed local image below, but no Deployment exists yet.

[`tests/test_dispatcher_runtime.py`](tests/test_dispatcher_runtime.py) injects
the one-attempt function, clock, and stop flag to prove empty-outbox backoff,
backlog draining, temporary database recovery, fail-fast unexpected errors,
and cooperative signal handling without live services.

## Container-image boundary

[`Dockerfile`](Dockerfile) now builds the dispatcher as a two-stage,
Linux/ARM64 local-profile image from the same digest-pinned Python 3.12 base as
the other Kubernetes-local Python services. Its validation stage installs only
the hash-enforced Pika/Psycopg lock and runs every dispatcher unit test. The
final stage copies only those validated packages and `app/`, then runs as
numeric non-root UID/GID `10001` with `app.dispatcher_runtime` as its entrypoint.

The image contains no Secret, PostgreSQL/RabbitMQ endpoint, Kubernetes client,
HTTP listener, MinIO access, Demucs binary, GPU dependency, or test source.
The future Deployment supplies the two least-privilege runtime Secrets and
private-Service configuration. The Dockerfile deliberately retains
`app.dispatcher_runtime` as its default entrypoint, which keeps the established
Demucs-only behavior unless a future generic Deployment explicitly overrides
the command with `python -m app.dispatcher_generic_runtime`. The reviewed ARM64
generic-capable build passed all 71 isolated tests, was pushed through
[`../../scripts/build-dispatcher-image.sh`](../../scripts/build-dispatcher-image.sh),
and is locked as
`clouddsp-registry.localhost:5001/dispatcher@sha256:cc2d36bd78ceb7e8ff8cd58d0d39d312c3325dda5e3d540dfcfad94e0dfdb487`
in [`../../images.lock.yaml`](../../images.lock.yaml). Its local uncompressed
Docker layers measure 48.68 MiB. Building and locking this image did not
create, update, or apply a Kubernetes workload. A separate Deployment task
must use this immutable reference rather than the readable build tag.

## Prepared dispatcher Deployment

[`dispatcher-deployment.yaml`](dispatcher-deployment.yaml) now defines one
outbound-only `clouddsp-dispatcher` Pod in `clouddsp-app`. It has no Service or
Ingress because it accepts no inbound request; it only reaches private
PostgreSQL and RabbitMQ Service DNS names. The Pod uses the locked image digest,
the two separate least-privilege runtime Secrets, a non-root/read-only security
context, disabled Kubernetes API token mounting, and modest CPU/memory limits.

The initial replica count is one for clear local observation. The PostgreSQL
lease protects future additional replicas and the RollingUpdate overlap, but
autoscaling is intentionally deferred until metrics/operational boundaries are
implemented. This existing manifest deliberately still pins the prior
Demucs-only image and has not been modified by the generic-capable image build.
The following manifest provides the separate generic controller identity and
explicit generic runtime command.

## Prepared generic dispatcher Deployment

[`dispatcher-generic-deployment.yaml`](dispatcher-generic-deployment.yaml)
defines that second, outbound-only controller. It pins the generic-capable
image digest and, crucially, uses Kubernetes `command` to override the image's
Demucs-only default entrypoint with `python -m app.dispatcher_generic_runtime`.
It receives the same already-applied restricted PostgreSQL and RabbitMQ runtime
Secrets: its finite Python route allowlist—not a broader credential—selects
Demucs, Basic Pitch, or ADTOF requests.

The legacy `clouddsp-dispatcher` manifest is unchanged. A deliberate temporary
overlap is safe because both controllers use the same PostgreSQL lease and
lease-token-guarded completion protocol; they may race to *claim* a Demucs row
but only one current lease holder can record its result. After a smoke test, a
separate rollout task can scale the legacy controller to zero. This new
manifest is now applied: its one replica was verified Running with zero
restarts, the pinned image digest, and the explicit generic runtime command.

## Prepared Basic Pitch routing-smoke identity

A future generic-dispatcher smoke Job must observe one controlled
`basic-pitch.requested` delivery without giving either the real dispatcher or
the test Job broad RabbitMQ access. The prepared
[`Basic Pitch smoke-reader bootstrap Job`](../rabbitmq/rabbitmq-basic-pitch-smoke-consumer-bootstrap-job.yaml)
therefore creates the no-tag `clouddsp-basic-pitch-smoke` user with exactly:

| Permission | Regular expression | Effect |
| --- | --- | --- |
| Configure | `^$` | Cannot create, bind, delete, or alter RabbitMQ resources. |
| Write | `^$` | Cannot publish an event, retry, or dead-letter message. |
| Read | `^clouddsp\.basic-pitch\.requests$` | Can consume and acknowledge only the Basic Pitch request queue. |

The later app-namespace smoke Job uses the
[`runtime Secret template`](../../tests/generic-dispatcher-smoke/basic-pitch-smoke-rabbitmq-credentials.secret.example.yaml).
The bootstrap Job temporarily needs the matching ignored
[`data-namespace Secret template`](../rabbitmq/rabbitmq-basic-pitch-smoke-bootstrap-credentials.secret.example.yaml),
because Secrets cannot cross namespaces. The templates contain no real
password; local copies were applied, and the bootstrap Job completed with the
expected `^$` configure, exact Basic Pitch read, and `^$` write permissions.
The permanent app-namespace test Secret remains for the later smoke Job. The
temporary data-namespace bootstrap Secret remains only until its cleanup is
explicitly approved.

## Prepared restricted PostgreSQL routing-smoke identity

The RabbitMQ reader proves the final queue delivery, but a routing smoke test
also needs one legal, disposable downstream outbox row for the live generic
dispatcher to publish. Giving that test Pod raw `jobs` or `outbox_events`
table privileges would let it create arbitrary work. The prepared
[`PostgreSQL bootstrap Job`](../../tests/generic-dispatcher-smoke/generic-dispatcher-smoke-database-bootstrap-job.yaml)
instead creates the no-inherit `clouddsp-generic-dispatcher-smoke` role with no
table, sequence, or schema-create privilege. It may execute only these
security-definer functions:

| Function | Narrow capability |
| --- | --- |
| `clouddsp_generic_dispatcher_smoke_create(job_id, event_id)` | Creates one short-lived synthetic Job and its valid `basic-pitch`/`vocals` outbox event. |
| `clouddsp_generic_dispatcher_smoke_status(job_id)` | Reads only that synthetic event's non-sensitive publication state. |
| `clouddsp_generic_dispatcher_smoke_cleanup(job_id)` | Deletes that synthetic Job only after the event is published; its outbox row then cascades. |

The synthetic payload follows the normal private-WAV contract but intentionally
does not require a real MinIO object, because this test proves dispatcher
routing—not Basic Pitch object retrieval. Its permanent app-namespace and
temporary data-namespace Secret templates are adjacent to the Job. Their
ignored local copies were applied after the v004 downstream-outbox migration,
and the bootstrap Job completed: its privilege report confirms the role has no
direct `jobs`/`outbox_events` access and execute permission only on the three
functions above.

The unit-tested
[`generic Basic Pitch smoke client`](../../tests/generic-dispatcher-smoke/client/generic_dispatcher_basic_pitch_smoke.py)
is the next narrow layer above those identities. It creates one opaque
synthetic event through `create`, waits through `status` until the generic
dispatcher records `published`, verifies/acknowledges one exact persistent
`basic-pitch.requested` delivery, then calls `cleanup`. It has no direct SQL
statement against application tables, no RabbitMQ publish/configure operation,
no MinIO client, no Keycloak credential, and no Kubernetes API access. If a
foreign delivery appears it requeues it and fails; if verification fails it
does not request cleanup, leaving the 10-minute synthetic record as bounded
diagnostic evidence.

Its adjacent
[`requirements.lock`](../../tests/generic-dispatcher-smoke/client/requirements.lock)
pins only the Pika reader, Psycopg/binary PostgreSQL client, and Psycopg's
typing helper with SHA-256 hashes. It was resolved with `--require-hashes` for
the local Linux/ARM64 CPython 3.12 profile. Its adjacent
[`Dockerfile`](../../tests/generic-dispatcher-smoke/client/Dockerfile): a
temporary validation stage installs the lock and runs the isolated tests, while
the final stage copies only verified packages and the one client module under a
numeric non-root account. The local Linux/ARM64 build passed all 16 tests and
is pushed under this immutable reference:
`clouddsp-registry.localhost:5001/generic-dispatcher-basic-pitch-smoke-client@sha256:886bdb2d29b29a6aa6eee674283c769524e0e51f86b6e310e03e437f25af9eb3`.
The prepared, unapplied
[`one-shot Job`](../../tests/generic-dispatcher-smoke/generic-dispatcher-basic-pitch-routing-smoke-job.yaml)
uses that digest in `clouddsp-app`, mounts only the two restricted app-namespace
Secrets, disables the ServiceAccount token, and runs as the image's non-root
UID/GID `10002`. It creates no Service, Ingress, PVC, or Kubernetes API client.
Its `backoffLimit: 0` preserves a failed synthetic event for inspection instead
of rerunning automatically against an active 10-minute test record.

## Prepared smoke-test RabbitMQ identity

The later dispatcher smoke Job must observe one controlled message in
`clouddsp.demucs.requests`, but the real dispatcher identity must never gain
queue-consumer authority merely for testing. The prepared administrator-only
[`RabbitMQ smoke-reader bootstrap Job`](../rabbitmq/rabbitmq-dispatcher-smoke-consumer-bootstrap-job.yaml)
therefore creates a separate no-tag `clouddsp-dispatcher-smoke` user with only:

| Permission | Regular expression | Effect |
| --- | --- | --- |
| Configure | `^$` | Cannot declare, bind, delete, or alter RabbitMQ topology. |
| Write | `^$` | Cannot publish source, processing, retry, or dead-letter messages. |
| Read | `^clouddsp\.demucs\.requests$` | Can consume/ack only the exact Demucs request queue during a controlled smoke test. |

The app-namespace
[`runtime Secret template`](dispatcher-smoke-rabbitmq-credentials.secret.example.yaml)
and data-namespace
[`temporary bootstrap Secret template`](../rabbitmq/rabbitmq-dispatcher-smoke-bootstrap-credentials.secret.example.yaml)
intentionally duplicate this one restricted username/password because Secrets
cannot cross namespaces. The second is deleted after the bootstrap Job passes;
neither template is applied nor contains a real password.

## Restricted RabbitMQ publisher boundary

The completed
[`RabbitMQ dispatcher publisher bootstrap Job`](../rabbitmq/rabbitmq-dispatcher-publisher-bootstrap-job.yaml)
created a separate `clouddsp-dispatcher` RabbitMQ user. It has no RabbitMQ tags
and only these `/clouddsp` vhost permissions:

| Permission | Regular expression | Effect |
| --- | --- | --- |
| Configure | `^$` | Cannot declare, bind, delete, or otherwise configure broker resources. |
| Write | `^clouddsp\.processing-events$` | Can publish only to the exact existing processing exchange. |
| Read | `^$` | Cannot consume, inspect, or acknowledge any queue. |

The ignored temporary
`clouddsp-dispatcher-rabbitmq-bootstrap-credentials` Secret existed in
`clouddsp-data` only while that administrator Job ran, then was deleted. It
duplicated the app-namespace identity solely because Kubernetes Secrets cannot
cross namespaces. The separate restricted RabbitMQ runtime Secret is already
applied in `clouddsp-app` and contains no administrator credential.

## Versioning boundary

Version 1 is immutable once a dispatcher can publish it. Adding optional
fields, changing a field's meaning, changing the routing key, or changing the
allowed stem modes requires a deliberate compatibility decision and a new
versioned contract. The safe default is a new event type/routing key and a
worker that accepts both versions during migration; never silently reinterpret
an existing durable outbox row.

## Scope established so far

The contract, imported RabbitMQ topology, restricted PostgreSQL and RabbitMQ
identities, applied app-namespace runtime Secrets, pure outbox lease adapter,
AMQP publisher adapter, private transaction adapter, both Demucs-only and
generic one-attempt composition paths, and a generic runtime entrypoint now
exist. The generic-capable ARM64 image and its separate generic Deployment are
prepared and digest-locked, but that Deployment has not been applied; therefore
the live Demucs-only dispatcher behavior has not changed. KEDA scaling and all
processing workers remain separate small tasks.
