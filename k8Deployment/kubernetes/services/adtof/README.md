# CloudDSP ADTOF worker contract, version 1

This document defines the first Kubernetes-local ADTOF boundary before we add
an ADTOF PostgreSQL migration, service identities, Python worker, container
image, Deployment, smoke test, or KEDA scaler. It preserves the existing
CloudDSP product result: ADTOF accepts only a Demucs **drums** stem, produces
drum MIDI and a tempo-candidate JSON artifact, and keeps PostgreSQL—not a
RabbitMQ delivery or a Pod—as the durable authority.

It deliberately does not import or reuse the cloud Lambda handler. The cloud
implementation is a useful product-behaviour reference, but the Kubernetes
worker must use local PostgreSQL, MinIO, and RabbitMQ adapters with the same
durable ownership rules as the existing Demucs and Basic Pitch workers.

## Scope and durable flow

ADTOF is a CPU-only drum-transcription stage. It is not a general audio
processor, an API server, or a Kubernetes-job creator. Its single approved
input is the durable per-stem event that Demucs wrote transactionally after it
had stored the verified drum WAV.

```text
RabbitMQ adtof.requested
  -> strict delivery parser
  -> short PostgreSQL `(job_id, adtof, drums)` task claim
  -> acknowledge the claimed/duplicate broker delivery
  -> private MinIO HeadObject + streamed checksum-verified download
  -> CPU ADTOF inference + local MIDI/tempo validation
  -> private MinIO MIDI and tempo-object writes
  -> short guarded PostgreSQL completion commit
  -> later job-level MIDI aggregate and browser polling observe the result
```

The message acknowledgement intentionally occurs after a durable claim, not
after inference. If a Pod stops after the commit but before the acknowledgement,
RabbitMQ can redeliver the same event. The unique task coordinate makes that a
safe duplicate instead of a second independent ADTOF job. If a Pod stops after
acknowledgement, the PostgreSQL retry/expired-lease scan recovers the durable
task; recovery never needs a new broker message.

## Input message contract

The existing dispatcher topology already provides this route. The future ADTOF
parser must enforce it again rather than trusting a queue name or an environment
variable supplied to a Pod.

| Item | Required value | Why it is fixed |
| --- | --- | --- |
| Exchange | `clouddsp.processing-events` | Keeps internal processing work separate from MinIO source notifications. |
| Routing key | `adtof.requested` | The direct exchange selects the dedicated ADTOF queue. |
| Queue | `clouddsp.adtof.requests` | The queue owns normal backlog handling; it is not a storage coordinate. |
| AMQP type | `adtof.requested` | Repeats the stage identity in persistent message metadata. |
| AMQP message ID | v004 outbox `event_id` UUID | Correlates delivery to one immutable durable event. |
| AMQP correlation ID | Job UUID | Correlates logs/metrics safely at the job level without becoming an authorization claim. |
| Content type / encoding / mode | `application/json` / `utf-8` / persistent (`2`) | Prevents implicit binary payloads and makes broker restart survival explicit. |

The JSON body is the existing schema-version-1 downstream-stem request. It is
metadata only—never audio bytes, a presigned URL, model arguments, credentials,
or an arbitrary MinIO key.

```json
{
  "schema_version": 1,
  "job_id": "canonical-lowercase-uuid",
  "stem_name": "drums",
  "stem": {
    "bucket": "clouddsp-uploads",
    "object_key": "stems/{job_id}/drums.wav",
    "content_type": "audio/wav",
    "size_bytes": 1,
    "sha256": "64-lowercase-hex-characters"
  }
}
```

The parser must reject every other stem name, bucket, key prefix, MIME type,
UUID spelling, duplicate JSON field, extra field, oversized body, non-finite
JSON value, or AMQP property mismatch. In particular, `2-stems` jobs never
produce `drums`, so no ADTOF event is valid for that mode. `4-stems` and
`6-stems` jobs have exactly one possible ADTOF input: `stems/{job_id}/drums.wav`.

## PostgreSQL ownership and idempotency

The eventual additive migration will extend `processing_tasks` to admit only
the stage/stem/key combination below. It will be a new versioned migration
after v005; it must not edit historic schema ConfigMaps.

| Durable field | Required value |
| --- | --- |
| Task idempotency key | `(job_id, 'adtof', 'drums')` |
| Event idempotency key | the one `adtof.requested` outbox `event_id` |
| Input coordinate | `clouddsp-uploads` / `stems/{job_id}/drums.wav` |
| Eligible Job state | retained `midi_processing` Job with `4-stems` or `6-stems` mode |
| Task states | `leased`, `running`, `retry_scheduled`, `succeeded`, `failed` |
| Maximum durable attempts | 3, matching the existing generic task bound |

The future first-claim transaction must lock the exact retained Job and read
the matching published v004 outbox row. It may create or recover only this
coordinate, with a fresh lease token and expiry. It must not change the Job's
owner, source, retention timestamp, stem map, or overall status. A duplicate
message that finds an already-active, retry-scheduled, or succeeded task is an
idempotency outcome, not permission to execute a second transcription.

The completion transaction requires the current, unexpired lease token before
it changes `running` to `succeeded`, clears the lease, writes the drum entry in
`jobs.midi`, increments the Job revision, and records the durable tempo
candidate. The Job API's v007 migration installs a deferred PostgreSQL
aggregate trigger that runs after this transaction's writes are visible. It
waits for every mode-required Basic Pitch and ADTOF task to become terminal,
then marks the whole Job `completed` only when every expected output is
registered; exhausted failures or broken durable task/output invariants become
a bounded terminal `failed` state. No separate status-polling Deployment or
RabbitMQ message is needed for this database-authoritative transition.

## Private input verification

Before ADTOF reads a byte, its worker must compare MinIO `HeadObject` evidence
with both the parsed event and its current PostgreSQL lease. The object must
have all of the following fixed Demucs metadata fields:

```text
schema-version=1    producer=demucs
job-id={job_id}     task-id={demucs_task_id}
stem-name=drums     stem-mode=4-stems or 6-stems
size-bytes={size}   sha256={digest}
```

Its `ContentType` must be `audio/wav`, and its `ContentLength`, job ID, stem
name, stem mode, and SHA-256 must equal the durable message. The worker then
downloads to a bounded scratch volume and hashes the streaming bytes; matching
metadata alone is not proof that the downloaded file is the intended WAV.

An unavailable MinIO request is a bounded retryable condition. A missing
object, content-type/size mismatch, malformed metadata, or downloaded-byte
hash mismatch is a durable terminal input-validation failure after the exact
failure-code vocabulary is reviewed. Neither case exposes raw MinIO responses,
object metadata, credentials, URLs, or model stderr in public logs or Job data.

## Outputs and provenance

One task attempt may write only the two stable private keys below. Attempt
numbers never enter the key, so a safe recovery overwrites only the same
logical artifact—not an unbounded number of browser-visible artifacts.

| Artifact | Fixed key | Content type | Purpose |
| --- | --- | --- | --- |
| Drum MIDI | `midi/{job_id}/drums.mid` | `audio/midi` | The five-voice General MIDI drum transcription used by the editor. |
| Tempo candidate | `midi/{job_id}/drums_bpm.json` | `application/json` | Evidence-backed ADTOF tempo observation for later job-level aggregation. |

Both uploads must carry immutable provenance and integrity metadata: schema
version, `producer=adtof`, Job ID, ADTOF task ID, request event ID, stem name,
stem mode, output byte count, output SHA-256, input-stem SHA-256, and the
versioned ADTOF model/configuration identity. A subsequent `HeadObject` check
must prove each stored object matches its upload plan before PostgreSQL records
success.

The source-only
[`output-object planner`](app/output_object_plan.py) now names the two keys
from committed `RunningADTOFStem` evidence and gives each a frozen *base*
metadata tuple. It includes the input-stem digest, artifact kind, and the
cloud-default-compatible ADTOF configuration identity: pinned
`adtof-pytorch` revision `85c192e78f716ea0b111cc8a5ee4a8f6a3a4f8a9`, CPU,
100 FPS, and thresholds `0.22,0.24,0.32,0.22,0.30`. It intentionally cannot
add `size-bytes` or output `sha256`: no local MIDI or JSON bytes exist yet.
A following artifact-verification task must produce those two integrity facts,
join them to this base tuple, and only then make an upload plan. This prevents
invented output checksums from appearing as provenance.

The source-only [`local output-artifact verifier`](app/output_artifact.py) now
accepts one of those exact plans and a future model runner's controlled local
output path. It rejects symlinks, non-regular files, wrong filenames, oversized
files, malformed Standard MIDI framing, duplicate/non-finite/wrong-shape JSON,
and inconsistent ADTOF tempo evidence. It streams a SHA-256 while it validates
the artifact and returns only bounded local evidence. The MIDI check deliberately
validates the file container rather than ADTOF's musical-note semantics; that
belongs to the later model runner/editor boundary. No model invocation, MinIO
request, PostgreSQL mutation, RabbitMQ action, image, or Kubernetes resource is
introduced.

The source-only [`CPU inference-command builder`](app/adtof_inference_command.py)
now reserves a fresh `0700` `adtof-output` sibling of the temporary verified
drums WAV and builds only one no-shell Python-module argv. The command fixes
the cloud-default-compatible `adtof-pytorch` revision, CPU device, 100 FPS,
and all five thresholds; no deployment environment, browser input, object key,
or model choice can alter it. Its MIDI and tempo filenames exactly match the
local artifact verifier and MinIO output plans. It reserves paths only—it does
not import or run ADTOF/PyTorch, and a future execution boundary must still
handle timeouts/process cleanup before artifact validation.

The source-only [`CPU inference entrypoint`](app/adtof_cpu_inference_entrypoint.py)
now accepts only that exact argument sequence, rechecks the fresh sibling
scratch tree, lazily invokes the pinned `adtof_pytorch` CPU API, and writes only
the two reserved outputs. Its beat/tempo calculation preserves the cloud
candidate fields and safe low-confidence fallback. It validates the generated
JSON through the same parser used by the later local-artifact verifier before
writing it. It intentionally has no timeout/supervisor, storage, database,
broker, or Kubernetes code; the next boundary must execute this child command
under a finite process deadline and clean up its full process group.

The source-only [`CPU process runner`](app/adtof_cpu_process.py) now repeats
the fixed command/path/configuration checks immediately before it starts one
shell-free child process. It makes a new process session, gives ADTOF a normal
ten-minute CPU deadline (twelve minutes maximum within the current fifteen
minute lease), then sends `SIGTERM` and, if needed, `SIGKILL` to the entire
process group. A zero exit remains only local process evidence; it does not
prove a valid artifact, a stored MinIO object, or a completed PostgreSQL task.

The source-only [`local task execution composition`](app/local_task_execution.py)
now joins one already-running temporary drums WAV, fixed output plans, the CPU
process runner, and both local artifact checks. It returns MIDI and tempo
evidence together only after the child exits successfully and both bounded
files validate. Every returned path remains inside the existing running-stem
scratch context; there is still no MinIO call or PostgreSQL task completion.

The source-only [`upload-object planner`](app/upload_object.py) now rechecks
both current local files and joins their exact size/SHA-256 values with the
frozen base provenance. Its two plans carry the only permitted private MinIO
keys, MIME types, model identity, input hash, and complete immutable metadata.
They are still not upload receipts; the following streaming uploader must prove
that MinIO consumed these same bytes before stored-object verification.

The [`MinIO streaming uploader`](app/minio_upload.py) now accepts only that
exact MIDI/tempo pair. Before either `PutObject` request, it independently
revalidates both local artifacts, rebuilds every fixed bucket/key/type/metadata
constraint, and streams from a non-symlink regular file. A wrapper hashes the
exact bytes the S3-compatible client consumes, so a client that returns without
reading the promised `ContentLength` cannot produce a receipt. The pair is
sequential rather than a fictional cross-object transaction: if MIDI succeeds
and tempo has a temporary transport failure, a recovery safely overwrites the
same deterministic MIDI key before retrying tempo. This still does not make a
task successful; the next boundary must prove stored MinIO metadata and bytes
with `HeadObject` before a PostgreSQL completion transaction can run. The
uploader creates no client, changes no database row, handles no RabbitMQ
delivery, runs no model, and creates no Kubernetes resource.

The [`stored-output verifier`](app/output_artifact_head_object.py) now follows
the two upload receipts with exactly two metadata-only `HeadObject` requests.
It independently rechecks every fixed coordinate and receipt, requires the
MIDI/tempo plans to share the same Job, ADTOF task, request event, drums-input
digest, model configuration, and stem mode, then compares MinIO's current
content type, size, and complete user-metadata mapping. A missing object or
stored mismatch becomes one bounded permanent failure code; an unavailable
MinIO request stays retryable and hides raw SDK details. It does not re-read
the object body because the preceding streaming uploader already computed the
byte-level digest as its client consumed it. The returned pair is evidence for,
not a substitute for, the next guarded PostgreSQL completion transaction.

The [`guarded completion adapter`](app/task_completion.py) now accepts only a
canonical current ADTOF lease, the stored MIDI/tempo pair, and a tempo candidate
that round-trips through the same strict JSON parser used for local output. It
calls one typed database function rather than requesting direct Job-table
authority. The function locks the task and Job, requires the current unexpired
lease and a retained `midi_processing` Job, records only the fixed
`midi.drums` keys/tempo candidate, increments the Job revision, and changes
the task to `succeeded`. It never changes the overall Job status. A missing row
is safe ownership loss rather than success.

The [`completion-commit composition`](app/task_completion_commit.py) now keeps
that single completion call inside the existing short `write_cursor()` context.
It returns a non-`None` completion only after the context exits normally and
commits. A no-row stale lease also exits normally because no change occurred;
any malformed result or database error escapes the context so its transaction
rolls back before a future supervisor can choose retry, failure, or broker
acknowledgement. This composition performs no model/storage/broker work and
does not apply the prepared bootstrap Job.

The [`post-inference finalization composition`](app/task_finalization.py) now
joins the completed local result to the remaining durable happy path: it rebuilds
both upload plans, streams the MIDI/tempo pair, verifies both current MinIO
objects, then invokes the completion-commit composition. No long database
transaction surrounds MinIO work. If the final lease guard returns no row, the
function returns `None`; the deterministic private objects may remain, but a
stale Pod cannot call the task successful or acknowledge a message. Storage and
database exceptions intentionally propagate to a later supervisor. This module
does not parse a delivery, download/start a task, run ADTOF, choose retry
policy, or create Kubernetes resources.

The [`post-claim success coordinator`](app/claimed_task_success.py) now joins
the already-reviewed boundaries for one *committed* ADTOF lease: private drums
metadata proof, bounded download plus the guarded `running` transition, CPU
output production, and finalization. It keeps CPU work and output finalization
inside the temporary running-stem context, so the scratch files cannot be
cleaned before their last required use. Its only normal results are
`succeeded` (with completion evidence) and `ownership_lost`; MinIO, database,
CPU, and validation failures intentionally propagate to a later supervisor.
It has no RabbitMQ delivery/acknowledgement, retry, Deployment, or Kubernetes
responsibility.

The JSON tempo artifact must retain the cloud-compatible fields
`extractor`, `bpm`, `beat_count`, `duration_seconds`, `interval_consistency`,
`drum_event_count`, `credible`, `confidence`, and `source=adtof_drums`.
`bpm` may be `null` when the evidence is not credible; a valid but low-confidence
candidate is not a model failure. The final Job-level tempo selection is a
separate aggregation task and will prefer a credible ADTOF drum candidate as
the preserved product behaviour requires.

On a guarded completion, the `jobs.midi.drums` record will preserve the
cloud-compatible durable shape:

```json
{
  "status": "ready",
  "extractor": "adtof",
  "s3_key": "midi/{job_id}/drums.mid",
  "bpm_key": "midi/{job_id}/drums_bpm.json",
  "tempo_candidate": { "...": "reviewed ADTOF fields" }
}
```

Stable object keys—not presigned URLs—belong in PostgreSQL. The owner-checked
Job API will generate fresh short-lived URLs only when the browser polls its
snapshot endpoint.

## Failure, retry, and Kubernetes boundary

Malformed pre-claim AMQP deliveries go to the existing ADTOF dead-letter route;
they must not create a task. A storage/broker/database outage after a valid
claim uses the reviewed bounded retry policy and durable lease recovery. A
terminal input/protocol/model failure records a short safe error code against
the owned task, leaves its private objects unavailable to the browser, and is
later considered by job-level aggregation. Raw audio, stack traces, secrets,
and presigned URLs are never persisted as error text.

The eventual worker is a long-running, single-message consumer with RabbitMQ
prefetch `1`, manual acknowledgements, a bounded scratch `emptyDir`, no
Kubernetes API access, no Service/Ingress, and least-privilege PostgreSQL,
MinIO, and RabbitMQ identities. It will be CPU-only for the local Apple-silicon
profile; ADTOF must not request CUDA or claim that the Mac cluster validates
NVIDIA throughput.

The first reviewed KEDA policy now lives in
[`adtof-scaledobject.yaml`](adtof-scaledobject.yaml). It uses the measured
CPU-only image envelope, prefetch-one flow control, ten-minute task timeout,
and two-agent local topology to scale the long-running ADTOF Deployment from
zero to at most two Pods. One ready-or-unacknowledged request targets one Pod;
KEDA polls the private RabbitMQ management API every fifteen seconds and waits
three idle minutes before requesting scale-to-zero. Its generated HPA owns only
the Deployment's replica count, never RabbitMQ delivery, a PostgreSQL lease,
or output persistence. The manifest remains unapplied until the dedicated
KEDA apply-and-observe task.

## PostgreSQL runtime identity

The applied v006 migration permits the task coordinate but does not give a Pod
unrestricted access to it. The existing
[`ADTOF database bootstrap Job`](adtof-database-bootstrap-job.yaml) uses the
short-lived PostgreSQL administrator login and a temporary data-namespace copy
of the ADTOF credential to create/reconcile the separate `clouddsp-adtof` role.
Its runtime Secret template is
[`adtof-database-credentials.secret.example.yaml`](adtof-database-credentials.secret.example.yaml);
the matching bootstrap-only template is
[`adtof-database-bootstrap-credentials.secret.example.yaml`](adtof-database-bootstrap-credentials.secret.example.yaml).

The role can create/read/recover only generic task lifecycle fields, read the
minimum Job/outbox fields needed to cross-check one `adtof.requested` event,
and execute two narrow administrator-owned functions: one locks a Job for a
claim, and one atomically records the fixed ADTOF drums result. The latter
accepts only canonical identifiers, deterministic keys, and strict tempo JSON;
it revokes PostgreSQL's default `PUBLIC` execute grant and grants execution
only to `clouddsp-adtof`. The role still cannot directly update a Job, read an
owner/source/artifact map, write an outbox event, administer PostgreSQL, or use
any MinIO/RabbitMQ credential. The updated fixed-name Job was deliberately
reconciled successfully, activating the completion function without widening
table privileges. Its temporary
`clouddsp-data` Secret was deleted again immediately after the Job's
non-secret permission checks passed.

## MinIO runtime identity

The applied immutable
[`ADTOF MinIO policy`](../minio/minio-adtof-artifacts-policy-v001-configmap.yaml)
and completed
[`MinIO bootstrap Job`](../minio/minio-adtof-artifacts-bootstrap-job.yaml)
created the distinct S3 identity `clouddsp-adtof`. The policy permits only
`GetObject` for `stems/*/drums.wav`, plus `GetObject`/`PutObject` and multipart
completion helpers for `midi/*/drums.mid` and `midi/*/drums_bpm.json`. It does
not permit a bucket listing, delete, browser presign, uploads-prefix access,
or any non-drum stem/MIDI object.

The future runtime credential template is
[`adtof-minio-credentials.secret.example.yaml`](adtof-minio-credentials.secret.example.yaml);
the matching temporary data-namespace template is
[`adtof-minio-bootstrap-credentials.secret.example.yaml`](adtof-minio-bootstrap-credentials.secret.example.yaml).
Only the bootstrap Job mounts MinIO root credentials. After it succeeds, delete
the real temporary data-namespace Secret; the later worker uses only the
restricted app-namespace pair through private MinIO Service DNS.

## RabbitMQ runtime identity

The applied
[`ADTOF RabbitMQ runtime Secret template`](adtof-rabbitmq-credentials.secret.example.yaml),
temporary
[`bootstrap Secret template`](../rabbitmq/rabbitmq-adtof-consumer-bootstrap-credentials.secret.example.yaml),
and completed one-shot
[`RabbitMQ bootstrap Job`](../rabbitmq/rabbitmq-adtof-consumer-bootstrap-job.yaml)
created the separate untagged broker user `clouddsp-adtof` in vhost `/clouddsp`, with empty `configure` and
`write` permissions and an exact `read` permission for only
`clouddsp.adtof.requests`. In RabbitMQ, that read permission permits receiving
and manually acknowledging a delivery; it does not permit publishing retries,
declaring queues/exchanges, consuming retry/DLQ queues, or opening the
management UI.

The future worker receives only the app-namespace runtime Secret and connects
through RabbitMQ's normal internal AMQP Service. The bootstrap Job separately
uses the broker administrator Secret plus a temporary data-namespace copy of
the restricted pair because Kubernetes does not allow a Pod to mount a Secret
from another namespace. Delete that temporary copy after a successful
bootstrap. This boundary creates no worker Deployment or audio workload.

## Request-parser boundary

[`app/adtof_requested_message.py`](app/adtof_requested_message.py) now
implements the pure, strict entry boundary for the future consumer. It accepts
only the existing v004 `adtof.requested` delivery: the fixed direct-exchange
route, persistent AMQP properties, canonical outbox and Job UUIDs, bounded
UTF-8 JSON, and exactly `clouddsp-uploads/stems/{job_id}/drums.wav` with its
byte-count/SHA-256 evidence. It deliberately rejects every non-drums stem,
alternate bucket/key/content type, unknown field, duplicate JSON field, and
non-persistent or mismatched AMQP property before any PostgreSQL, MinIO, or ML
operation exists. The accompanying dependency-free unit tests prove this
boundary without a cluster or broker connection.

## PostgreSQL first-claim boundary

[`app/task_claim.py`](app/task_claim.py) now contains the future worker's pure
PostgreSQL first-claim decision. Inside one caller-owned, short transaction it
locks the canonical `(job_id, 'adtof', 'drums')` task coordinate, uses the
administrator-owned narrow Job-lock function, re-reads the immutable published
outbox event, then inserts an initial `leased` task only when all durable
evidence agrees. An existing matching task is a no-mutation `duplicate`; a
missing, expired, or terminal Job is a no-mutation `stale` result. A conflict
raises without inserting, changing Job state, publishing, or acknowledging.

The lease is 15 minutes by default (bounded to one minute through one hour) and
is created from PostgreSQL's clock. It remains `leased`, not `running`, until a
future MinIO preflight and model-start boundary independently prove the worker
still owns the token. Tests use a scripted in-memory cursor and prove first
claim, duplicate delivery, stale Jobs, a concurrent insertion race, durable
evidence conflicts, and direct-object construction rejection.

The same module now has the pure
`claim_next_expired_adtof_task()` recovery candidate query. It claims at most
one expired active (`leased` or `running`) drums task with a new token and
incremented attempt, using PostgreSQL's clock and `FOR UPDATE SKIP LOCKED` so
overlapping Pods cannot recover the same task concurrently. It handles only
crash/post-ack active-lease recovery through attempt three. Before each
recovery claim, the separate
`finalize_next_expired_exhausted_adtof_task()` query locks at most one expired
active third attempt and records the terminal task fact `failed` with the safe
code `lease_expired_attempts_exhausted`; it clears the stale lease but does not
change `jobs.status`, delete an object, or issue a fourth attempt. A future
job-level aggregate remains responsible for the wider Job result. The returned
recovery lease contains the durable task and published-event coordinates, but
not an outbox payload. A following read-only boundary must rebuild strict request evidence
inside the same short transaction before MinIO or CPU work may begin.

## First-claim transaction composition

[`app/first_claim.py`](app/first_claim.py) now provides the small caller-owned
transaction composition around that pure SQL decision. It accepts only a
structural database object with `write_cursor()`, calls the first-claim adapter
inside that context, and returns the result only after normal context exit has
committed. Any database/outbox/task inconsistency escapes the context and
therefore rolls back before a future RabbitMQ adapter could acknowledge a
delivery. The module contains no channel, delivery tag, broker client, MinIO
client, ADTOF model, or Kubernetes dependency. Its in-memory tests prove commit
for claimed/duplicate/stale outcomes and rollback for a durable conflict.

## Concrete PostgreSQL connection boundary

[`app/postgresql.py`](app/postgresql.py) now provides the concrete
`PsycopgADTOFDatabase.write_cursor()` implementation required by the
first-claim composition. It accepts only the internal
`clouddsp-postgresql.clouddsp-data.svc:5432` Service, `clouddsp_job_api`
database, and `clouddsp-adtof` runtime role supplied by the already-applied
app-namespace Secret. Each durable action gets one fresh dictionary-row
transaction with a three-second connect timeout and five-second statement
timeout; normal exit commits and exceptional exit rolls back before a later
broker decision. Psycopg remains lazily imported until a future dependency/image
task installs it. [`requirements.lock`](requirements.lock) now pins the complete
Python 3.11/Linux ARM64 CPU closure with hashes; unit tests still use a fake
driver and open no socket. It now includes Pika, restricted MinIO's Boto3
closure, cloud-compatible ADTOF/PyTorch/audio/MIDI dependencies, and explicit
source archives for the pinned ADTOF revision and PrettyMIDI 0.2.10. The next
container-recipe task can therefore install a complete reviewed closure rather
than resolving dependencies from the network at Pod start.

## RabbitMQ connection-settings boundary

[`app/amqp_connection.py`](app/amqp_connection.py) now loads and validates the
future worker's only broker configuration: private
`clouddsp-rabbitmq.clouddsp-data.svc:5672`, vhost `/clouddsp`, queue
`clouddsp.adtof.requests`, prefetch one, bounded connection/heartbeat settings,
and the `clouddsp-adtof` app-namespace runtime Secret. Prefetch one is the
important flow-control boundary: this CPU Pod must not reserve several
unacknowledged broker deliveries and durable task leases while it can process
only one drums stem. [`requirements.lock`](requirements.lock) pins Pika's
pure-Python AMQP client wheel, and the same module now imports it lazily only
when `open_adtof_rabbitmq_connection()` is called. That factory uses the fixed
private plain-AMQP listener with three bounded attempts, timeout/heartbeat
limits, and one redacted retryable failure category. It still creates no
channel, delivery, acknowledgement, queue/exchange declaration, or worker loop.
The same complete lock now contains the MinIO, ADTOF/PyTorch, and audio/MIDI
closures needed by the future CPU worker recipe; this source module still does
not itself build an image or open a socket until a worker process starts.

## RabbitMQ passive-channel boundary

[`app/amqp_channel.py`](app/amqp_channel.py) now owns the next narrow broker
layer. It applies `prefetch_count=1` to one Pika-shaped channel, then uses a
passive declaration to verify the pre-existing
`clouddsp.adtof.requests` queue. The first setting limits a CPU ADTOF worker to
one unacknowledged drums-stem candidate; the second is a topology read, so the
restricted runtime identity cannot create, bind, delete, or modify the queue.

The adapter validates the complete fixed ADTOF AMQP settings before channel I/O
and exposes one redacted retryable category for QoS or passive-lookup failure.
It receives no delivery, makes no acknowledgement/retry decision, and calls no
PostgreSQL, MinIO, model, Docker, or Kubernetes component. Its mocked tests
prove those limits and that a direct attempt to widen the queue fails before
the channel is touched.

## Parser-to-first-claim bridge

[`app/delivery_claim.py`](app/delivery_claim.py) composes the two preceding
application boundaries in their required order: it first strictly parses one
`adtof.requested` envelope and only then opens the short first-claim
transaction. Its normal return contains the validated private drums-stem
evidence and PostgreSQL's already-committed `claimed`, `duplicate`, or `stale`
fact—never raw AMQP bytes/properties, a delivery tag, database cursor, or a
broker client.

Malformed AMQP input therefore cannot inspect or create durable task state.
Conversely, a database outage or durable-state conflict escapes without any
RabbitMQ action. This lets the following manual-acknowledgement boundary map
permanent malformed input to the DLQ while leaving transient state failures
unacknowledged for at-least-once redelivery. The bridge calls no MinIO client,
ADTOF model, Docker/image tool, or Kubernetes API.

## RabbitMQ manual-acknowledgement boundary

[`app/amqp_manual_ack.py`](app/amqp_manual_ack.py) now performs one explicit
manual-ack attempt against the reviewed `clouddsp.adtof.requests` queue. It
receives with `auto_ack=False`, passes the raw envelope only to the
parser-to-first-claim bridge, and acknowledges only after that bridge returns
a committed `claimed`, `duplicate`, or `stale` fact. A newly claimed task is
returned with its validated drums-stem evidence only after its acknowledgement
succeeds; duplicate or stale deliveries are acknowledged but expose no work.

A malformed contract is `basic_nack(..., requeue=False)`, letting RabbitMQ's
preconfigured DLQ own its permanent-error path. Database, integrity, bridge,
or acknowledgement failures deliberately have no success acknowledgement and
no retry publication: RabbitMQ can redeliver and PostgreSQL turns a prior
committed claim into a no-work duplicate. The adapter starts no loop, makes no
MinIO/model call, and does not build an image or create Kubernetes resources.

## Acknowledged-lease execution gate

[`app/acknowledged_lease_execution.py`](app/acknowledged_lease_execution.py)
is the narrow handoff from the completed RabbitMQ action to the post-claim
success coordinator. It admits only the manual-ack adapter's
`ACKNOWLEDGED_LEASE` result, which contains both PostgreSQL's committed ADTOF
lease and its matching strictly parsed request. `idle`, acknowledged
duplicate/stale, and malformed-DLQ outcomes stop before MinIO, scratch files,
or CPU work; defensive type checks also reject a forged handoff.

The gate does not call RabbitMQ itself. It therefore cannot make a second
acknowledgement, requeue/reject a delivery, choose a retry, open a transaction,
or start a loop. Once it has proven the handoff, it delegates only to the
existing success coordinator, which owns the reviewed object/database/model
ordering.

## One receive-and-execute iteration

[`app/receive_execute_once.py`](app/receive_execute_once.py) joins one
prepared Pika channel, the reviewed manual-ack receive operation, and the
acknowledged-lease gate once. It maps `idle`, acknowledged duplicate/stale, and
malformed-DLQ outcomes to non-executing results. Only an
`ACKNOWLEDGED_LEASE` passes into the existing success coordinator through the
gate. The result retains neither raw AMQP evidence, client objects, credentials,
scratch paths, nor artifact data.

An AMQP, PostgreSQL, MinIO, or ADTOF error still propagates unchanged rather
than being silently interpreted as no work. This is important: the following
supervisor must choose its reconnect/retry/backoff action explicitly. This
composition has no receive loop, sleep, connection lifecycle, retry schedule,
lease recovery, signal handler, image entrypoint, or Kubernetes behavior.

## Supervisor decision policy

[`app/supervisor_backoff.py`](app/supervisor_backoff.py) now supplies that
next-step policy without becoming a runtime. A normal `idle` iteration maps to
a fixed one-second wait. An acknowledged duplicate/stale result, a
malformed-DLQ rejection, or a completed execution maps to an immediate next
check so the worker can drain any backlog. Either normal outcome resets the
local dependency-failure streak.

Future reviewed failure classification can provide `retryable_failure`, which
uses a capped exponential 1, 2, 4, 8, 16, 30-second sequence with at most 25%
caller-provided jitter. Static configuration failures map to an immediate,
visible exit. The state is deliberately in-memory—not task retry state—and the
decision does not sleep, reconnect, write PostgreSQL, call RabbitMQ/MinIO, run
ADTOF, or interact with Kubernetes.

## Supervisor failure classification

[`app/supervisor_failure_classification.py`](app/supervisor_failure_classification.py)
supplies only the decision policy's safe event inputs. Invalid AMQP, PostgreSQL,
or MinIO configuration and a missing worker image entrypoint are
`fatal_configuration`. The established availability wrappers from RabbitMQ,
PostgreSQL, and the ADTOF MinIO boundaries are `retryable_failure`. The
classifier relies on exception types—not messages, causes, endpoints, object
keys, or credentials.

All integrity/contract/checksum errors, model failures/timeouts, and unknown
exceptions remain unclassified. The classifier does not catch an operational
exception, acknowledge a delivery, change a task, or cause a retry; a later
supervisor must combine the event with explicit current-lease recovery and
shutdown behavior before it performs any action.

## One supervisor decision step

[`app/supervisor_step.py`](app/supervisor_step.py) composes one
receive-and-execute iteration with the normal-result mapper, narrow failure
classifier, and pure backoff policy. A normal iteration carries its compact
result and resets/uses the policy's delay. A classified retryable or fatal
failure carries no iteration evidence and yields only its corresponding next
decision/state; an unknown failure propagates exactly as raised.

The returned local state contains only the bounded in-memory failure streak,
not a task lease or durable retry record. This step does not perform the chosen
action—it does not sleep, close/reopen RabbitMQ, loop, modify PostgreSQL beyond
the invoked iteration, or interact with Kubernetes. The next focused boundary
will apply one decision through a shutdown-aware waiter.

## Supervisor action adapter

[`app/supervisor_action.py`](app/supervisor_action.py) now applies one already
validated next-step decision through an injected `wait_for_shutdown(timeout)`
primitive. Immediate checks continue without waiting; idle and backoff actions
call that primitive exactly once; and fatal configuration returns an explicit
exit result without a wait. A true waiter result stops a future loop before it
attempts another broker receive, which is how a later SIGTERM-aware entrypoint
can exit promptly during an idle/backoff delay.

The adapter intentionally does not own signals, use `time.sleep`, loop,
reconnect RabbitMQ, close resources, alter a task, run ADTOF, or interact with
Kubernetes. Its next-step result is a control fact for a future runtime, not a
process exit or a completed retry.

## MinIO client-settings boundary

[`app/minio_client.py`](app/minio_client.py) now reads only the restricted
ADTOF runtime S3 pair plus fixed private MinIO configuration: the internal
`clouddsp-minio.clouddsp-data.svc:9000` Service, `clouddsp-uploads` bucket,
`us-east-1`, and path-style addressing. Its lazy Boto3 factory passes that key
pair explicitly, preventing ambient AWS environment variables, host profiles,
or EC2 metadata from becoming another credential source. MinIO is S3
compatible; this factory talks only to MinIO, never AWS.

It revalidates direct settings construction before Boto3 loads, so a future
call site cannot redirect the restricted key pair to an arbitrary endpoint.
Connection/read timeouts and SDK retry attempts are bounded, but the factory
opens no connection and reads/writes no object. A later lease-bound
`HeadObject` verifier owns the first concrete storage request.

## Drums `HeadObject` verifier

[`app/stem_object.py`](app/stem_object.py) now accepts only a parser-validated
`drums` request and matching, claimed ADTOF lease. It validates both direct
dataclass inputs before contacting MinIO, then makes exactly one metadata-only
lookup for `stems/{job_id}/drums.wav`. It requires the current object byte
length, WAV type, and complete immutable Demucs metadata inventory to match
the event and lease—including schema, producer, Job, drums name, stem mode,
size, and SHA-256.

A missing object or known size/type/metadata disagreement has one bounded
permanent failure code; an unavailable MinIO call is retryable; malformed
headers are a safe protocol error. No object bytes are downloaded here, so a
future stream-download boundary must still compare a computed SHA-256 with the
returned evidence before ADTOF runs. This verifier never changes PostgreSQL,
acknowledges RabbitMQ, writes an output, invokes a model, builds an image, or
uses Kubernetes.

## Bounded drums-download boundary

[`app/stem_download.py`](app/stem_download.py) accepts only the verified,
canonical private drums-WAV evidence. It performs one `GetObject`, verifies its
headers against the prior `HeadObject`, writes at most 256 MiB into a random
child of the worker's mounted scratch directory, and computes SHA-256 while it
streams. The local file has mode `0600`, never incorporates an object key into
its name, and exists only during the caller's context-manager scope.

Short, grown, malformed, changed-type, or checksum-mismatched streams never
yield a path to ADTOF and the temporary directory is removed. Storage transport
faults remain retryable; malformed client/evidence is a safe protocol error.
The adapter does not start the task, invoke the model, alter PostgreSQL,
acknowledge RabbitMQ, upload an object, build an image, or use Kubernetes.

## Guarded task-start boundary

[`app/task_claim.py`](app/task_claim.py) now also provides the pure
`start_leased_adtof_task()` transition. It accepts only a canonical ADTOF
drums-task lease, then uses a parameterized update whose predicate includes the
task and Job IDs, fixed `adtof`/`drums` coordinate, current `leased` state,
exact lease token, and PostgreSQL's `lease_expires_at > CURRENT_TIMESTAMP`.
A returned timezone-aware `started_at` proves the row committed as `running`;
no row is a normal ownership-loss outcome and must stop later model work.

The first-claim and storage preflight remain separate. This adapter opens no
connection or transaction, does not renew a lease, call MinIO/RabbitMQ/ADTOF,
write an output, build an image, or use Kubernetes.

## Verified-stem-to-running composition

[`app/stem_task_start.py`](app/stem_task_start.py) composes the completed
download and task-start boundaries without adding a model implementation. It
downloads the already verified drums WAV while the task is still `leased`, then
opens the short PostgreSQL transaction. Only after the guarded transition
commits does it yield the temporary path plus lease and `started_at` evidence.

If ownership is lost, it first closes the download scope and removes the local
WAV, then yields `None`; a stale replica therefore cannot run ADTOF on it.
Storage/database/protocol errors propagate after cleanup so a later supervisor
can apply durable retry or terminal policies. This composition receives no
RabbitMQ delivery, invokes no ADTOF model, creates no output, and makes no
image, Deployment, or Kubernetes change.

## Recovery request reader

[`app/recovery_request.py`](app/recovery_request.py) rebuilds normal strict
`adtof.requested` evidence after a prior RabbitMQ delivery has already been
acknowledged. It accepts only a fresh second- or third-attempt lease returned
by the expired-active-lease claim, then re-reads the matching immutable
published outbox event in that same short PostgreSQL transaction. The query
rebinds the task, Job, event, private drums-object coordinates, stem mode,
attempt number, token, and PostgreSQL-clock lease expiry before returning any
row. It also checks the event's exact published payload shape and routes the
rebuilt message through the shared direct-request validator.

An absent row is normal ownership loss: the caller must stop without MinIO or
ADTOF work. A malformed lease or durable event is a protocol error, so the
surrounding transaction must roll back rather than commit an unusable recovery
lease. The reader opens and commits no transaction itself; it neither claims a
task nor contacts RabbitMQ, MinIO, ADTOF, or Kubernetes.

## Expired-lease recovery transaction

[`app/recovery.py`](app/recovery.py) composes the expired-active-lease claim
and recovery request reader using exactly one `write_cursor()` context. A scan
with no candidate commits normally because it changed nothing. A recovered
task returns only after that context commits both the fresh lease and matching
strict request evidence. The claim and reader receive the same cursor, so the
claim's task-row lock remains in force while PostgreSQL proves current token,
attempt, private input, and published-outbox evidence.

If the claim succeeds but the reader returns normal ownership loss, the
composition raises an internal sentinel inside the transaction context. The
concrete database adapter rolls back the fresh lease; only then does the
composition return `None`. Unsafe durable evidence and database failures also
propagate through the context's rollback path. The committed pair is not a
RabbitMQ delivery or acknowledgement and still must pass the ordinary MinIO
preflight, guarded task start, inference, and finalization steps. This module
does not contact RabbitMQ, MinIO, or ADTOF, sleep, loop, build an image, or use
Kubernetes.

## Recovered-task execution gate

[`app/recovered_task_execution.py`](app/recovered_task_execution.py) is the
delivery-free counterpart to the normal acknowledged-lease gate. It accepts
only a committed `ADTOFRecoveredTask` pair, rechecks canonical UUIDs, recovery
attempt two/three, a timezone-aware lease timestamp, fixed `adtof`/`drums`
coordinates, the private drums WAV key, and all shared event/Job/object
identity between the lease and strict request. This stops a direct dataclass
construction or cross-wired outbox event from reaching MinIO or CPU work.

It then delegates to the existing post-claim success coordinator. It does not
invent or require a RabbitMQ acknowledgement: the original delivery was
acknowledged before the crash, while recovery authority comes from PostgreSQL.
The gate does not scan, open a transaction, contact RabbitMQ/MinIO/ADTOF,
sleep, loop, build an image, or use Kubernetes.

## Recovery execute-once composition

[`app/recovery_execute_once.py`](app/recovery_execute_once.py) first makes the
third-attempt terminal transition, then joins one reclaimable expired-lease
recovery transaction to the delivery-free execution gate. Its `idle` outcome
means PostgreSQL found no safe expired lease; `terminalized` carries only the
task-level exhausted-lease fact and deliberately enters no MinIO or model path;
its `executed` outcome contains only the existing coordinator's compact success
or ownership-loss result. An invalid recovery result or an operational failure
propagates rather than being hidden as idle, leaving later supervisor policy
able to distinguish an outage from an empty scan.

This is intentionally not an AMQP consumer: it takes no channel and performs
no receive, acknowledgement, rejection, or publish. It has no loop, sleep,
backoff, signal handler, image, Deployment, or Kubernetes action. A following
small scheduler policy will decide when to call this bounded recovery attempt
so continual normal queue traffic cannot starve crash recovery.

## Normal-work and recovery cadence

[`app/recovery_cadence.py`](app/recovery_cadence.py) is the pure fairness
policy for a future worker loop. A new Pod begins with a recovery scan, then
strictly alternates one bounded recovery attempt and one bounded normal AMQP
iteration. This deliberate one-to-one cadence means an expired task waits for
at most one normal iteration—even if the normal queue is continuously busy or
contains duplicate/malformed traffic. It is a stronger and easier-to-audit
bound than a large message-count interval; the empty recovery scan is only a
short PostgreSQL no-mutation query.

The immutable state records only the next action, not a task lease, retry, or
broker message. It verifies that callers advance in the right order and that
the reported normal/recovery iteration facts have a valid execution shape. It
does not call PostgreSQL, MinIO, RabbitMQ, sleep, loop, build an image, or use
Kubernetes.

## Cadence-driven worker cycle

[`app/worker_cycle.py`](app/worker_cycle.py) performs precisely one action
selected by that cadence. For a normal action it passes the AMQP channel only
to the existing normal receive/execute composition. For recovery it deliberately
does not pass the channel anywhere, because no raw delivery remains to
acknowledge or reject. The returned cycle result contains one compact branch
result and the only valid next cadence state; it cannot report a normal result
as recovery or advance to the wrong branch.

Operational errors propagate without advancing the frozen input state, so a
later supervisor can apply backoff and retry the same selected action. This
composition owns no loop, wait, connection lifecycle, image, Deployment, or
Kubernetes action.

## Cadence-aware supervisor step

[`app/supervisor_step.py`](app/supervisor_step.py) now owns both bounded
process-local states: the existing retry-backoff count and the cadence's next
action. It invokes one worker cycle. A completed cycle advances cadence and
maps to the established supervisor event vocabulary. A normal AMQP `idle`
still requests the one-second idle wait; a recovery `idle` maps to immediate
progress because it says nothing about whether the *next* normal AMQP poll has
work.

If a recognized retryable or fatal error prevents a cycle from completing, the
step changes only backoff state and retains the prior cadence action. Thus an
outage cannot accidentally skip a selected recovery scan or normal iteration.
The step does not apply the decision, sleep, loop, reconnect, build an image,
or use Kubernetes.

## One-step supervisor runner

[`app/supervisor_once.py`](app/supervisor_once.py) joins exactly one
cadence-aware supervisor step to the existing shutdown-aware decision-action
adapter. It returns the exact step, applied action result, and next state as
one immutable fact. The action must match the step's decision and the state
must match the step's advanced state, preventing a future loop from resuming
with unrelated local state. `continue`, `shutdown_requested`, and `exit_fatal`
remain visible to a later entrypoint rather than causing another receive here.

The runner has no persistent loop, signal handler, service connection
lifecycle, image, Deployment, or Kubernetes action. Step/action failures
propagate unchanged instead of being presented as a false `continue` result.

## Scoped shutdown event

[`app/shutdown_event.py`](app/shutdown_event.py) provides the main-thread
bridge from Kubernetes `SIGTERM` (and interactive `SIGINT`) to the existing
shutdown-waiter protocol. Both handlers do only one idempotent in-memory
`threading.Event.set()`. The waiter exposes a bounded 0–30 second interruptible
wait, matching the maximum reviewed supervisor delay. A signal context manager
installs both handlers together, restores previous handlers on normal/error
exit, and rolls back any partially installed handler if setup fails.

This makes it safe for a future loop to finish its current bounded operation,
then stop before a new receive/recovery cycle. The signal callback itself does
not log, call MinIO/PostgreSQL/RabbitMQ, run ADTOF, close clients, build an
image, or use Kubernetes.

## Shutdown-aware supervisor loop

[`app/supervisor_loop.py`](app/supervisor_loop.py) is the intentional
persistent worker-control loop. It repeats the one-step runner only after a
`continue` result, returns on `shutdown_requested` or `exit_fatal`, and checks
the shutdown event with a zero-second wait before every new worker cycle. That
extra check prevents a SIGTERM received during a CPU-bound task from allowing
another RabbitMQ receive or recovery scan when the completed step selected
immediate progress.

The loop accepts already-created channel, PostgreSQL, and MinIO dependencies
but does not create, reconnect, inspect, or close them. It also does not
install signal handlers, build an image, create a Deployment, or use
Kubernetes. A following lifecycle boundary must own opening and closing the
AMQP session around this loop.

## Closeable AMQP session

[`app/amqp_session.py`](app/amqp_session.py) now owns that AMQP lifecycle
boundary. It opens the existing restricted connection, obtains one channel,
applies prefetch-one/passive queue verification, and yields only the prepared
channel. On setup failure, normal completion, an exception from the supervisor
loop, or cleanup failure, it attempts channel-then-connection closure. A normal
cleanup failure becomes the reviewed redacted channel-unavailable category;
cleanup never hides an error raised by the caller's work.

The session does not receive, acknowledge, reject, requeue, publish, or create
broker topology. It creates no PostgreSQL/MinIO client, does not install signal
handlers or start the loop itself, and makes no image, Deployment, or
Kubernetes change.

## Worker bootstrap entrypoint

[`app/worker_entrypoint.py`](app/worker_entrypoint.py) now composes the
restricted runtime into one testable process boundary. It constructs the
existing ADTOF PostgreSQL adapter, reads the fixed private MinIO settings and
creates its Boto3 client, and requires the future Pod's `/worker-scratch`
`emptyDir` mount to already exist as a real non-symlink directory. It then
installs the scoped SIGTERM/SIGINT event, opens one prepared AMQP session, and
runs the existing supervisor loop only inside both scopes. This ordering means
a termination request can interrupt an idle/backoff wait, the loop stops before
fresh work, and the session always closes its channel before its connection.

The entrypoint returns an immutable supervisor result plus an explicit process
status: `0` for clean shutdown and `78` for a supervisor-classified fatal
configuration outcome. It does not call `sys.exit`, hide unexpected operational
errors, build an image, create a Deployment, or apply a manifest. A missing or
unsafe scratch mount fails before signal or RabbitMQ setup; malformed mounted
service/credential configuration likewise remains a reviewed error rather than
being reported as a successful worker exit.

## Executable worker wrapper

[`app/worker_main.py`](app/worker_main.py) is the final minimal Python process
boundary. It invokes the bootstrap entrypoint and returns its exact status. If
a mounted AMQP, PostgreSQL, or MinIO setting—or the required scratch mount—is
invalid before the supervisor starts, it writes only `adtof worker has invalid
or incomplete configuration.` to stderr and returns `78`. It deliberately does
not print the underlying exception text, which could expose private service or
Secret context in ordinary Pod logs. Workload-specific, availability, and
unexpected failures still propagate after the entrypoint's AMQP cleanup, so a
future Deployment can restart the container and durable task ownership remains
recoverable.

## Complete CPU dependency lock

[`requirements.lock`](requirements.lock) is now the complete hash-verified
CPython 3.11/Linux ARM64 closure for this local CPU worker. It preserves the
cloud ADTOF model revision `85c192e78f716ea0b111cc8a5ee4a8f6a3a4f8a9` as a
fixed GitHub source archive, rather than a mutable branch or a VCS checkout.
That package's own metadata requires only Torch, Librosa, PrettyMIDI, and NumPy;
the lock lists those exact archives as well as Boto3/MinIO, Pika, Psycopg, and
every required transitive package. The ARM64 PyTorch 2.5.1 CPU wheel has no
CUDA/NVIDIA dependencies, keeping the local worker compatible with k3d on
Apple Silicon.

Two source distributions require a small build-time exception to an otherwise
wheel-only closure: the ADTOF archive and cloud-compatible `pretty-midi==0.2.10`.
Both have SHA-256 pins, and the lock includes reviewed `setuptools` and `wheel`
versions for the later Dockerfile to install before running pip with
`--no-build-isolation --no-deps --require-hashes`. The lock deliberately omits
Audioread's `standard-aifc` and `standard-sunau` dependencies because their own
metadata limits them to Python 3.13+, while this worker is explicitly Python
3.11. A separate future Python/platform profile must generate its own lock.

## Source-only CPU container recipe

[`Dockerfile`](Dockerfile) is the source recipe for the local Linux/ARM64 CPU
ADTOF worker. Its validation stage begins from the same digest-pinned official
Python 3.11 image as the existing local Basic Pitch worker. It first installs
the lock's independently hash-pinned `setuptools` and `wheel` build tools, then
installs the complete closure with `--require-hashes`, `--no-deps`, and
`--no-build-isolation`. The two source distributions therefore build only with
the reviewed inputs; a missing transitive requirement or changed archive fails
the image build rather than being silently resolved.

The lock intentionally retains `setuptools==79.0.1`: the cloud-compatible
`pretty-midi==0.2.10` package imports its legacy `pkg_resources` module, which
the newer reviewed Setuptools 84 release no longer provides. The exact 79.0.1
wheel is hash-pinned and present in the final runtime closure, so this is an
explicit compatibility dependency rather than an accidental mutable base-image
tool.

That throwaway stage runs the complete unit suite and imports the reviewed
ADTOF, Torch, Librosa, and PrettyMIDI packages. It also verifies that the ADTOF
package contains bundled `.pth` weights, without running inference or fetching
any runtime model. The final stage installs only Debian's `libsndfile1` native
audio decoder: the hash-pinned Python `soundfile` package used by Librosa links
to this operating-system library when it decodes a WAV. It then copies only
verified site packages and `app/` onto the identical base. It excludes tests,
locks, credentials, cloud configuration, Kubernetes clients, and a public
listener.

The runtime uses the unprivileged UID/GID `10005` and starts
`python -m app.worker_main` in exec form as PID 1, so Kubernetes can send its
cooperative SIGTERM directly to the reviewed wrapper. It deliberately does
not create `/worker-scratch`: the future Deployment must mount a bounded,
writable `emptyDir` at that exact location (and at `/tmp`/the non-root home for
runtime caches) before the entrypoint will start. This task records source
only—it does not itself build/push an OCI image or apply a Kubernetes resource.

## Image-source provenance

[`images.lock.yaml`](../../images.lock.yaml) now has a
`buildSources.adtof` record. It fixes the source Dockerfile and application
directory, complete requirements lock, matching pinned Python 3.11 base, local
Linux/ARM64 CPU target, intended registry repository
`clouddsp-registry.localhost:5001/adtof`, ADTOF source revision, direct runtime
packages, test directory, and exec-form `app.worker_main` entrypoint. It is a
build input record, and now links to the separately reviewed immutable output
record `images.adtof`.

The source record still is not a workload image field: a future manifest must
use `images.adtof.immutableReference`, never the source record's intended
repository or a readable tag.

## Local ARM64 build result

The reviewed recipe has now built successfully in the local Docker daemon as
`clouddsp-registry.localhost:5001/adtof:0.1.4-exhausted-lease-recovery`.
Docker reported an uncompressed image size of `346,446,624` bytes (`330.40
MiB`) and Linux/ARM64 platform. Its runtime configuration has the expected exec-form
`["python", "-m", "app.worker_main"]` entrypoint and UID/GID `10005:10005`.
An offline runtime check, executed as that non-root user, imported ADTOF,
Librosa, PrettyMIDI, and CPU Torch `2.5.1`, and confirmed that
`/worker-scratch` is absent until a future Pod mounts it.

The build also verified the hash-pinned dependency closure and worker test
suite. Its narrow service-only context does not contain the repository-level
`images.lock.yaml`, so exactly the two catalog-structure assertions skip during
the Docker validation stage; they run normally in the source checkout.

## Local registry push result

The same verified bytes are now pushed to the dedicated k3d registry as
`clouddsp-registry.localhost:5001/adtof:0.1.4-exhausted-lease-recovery`. The
registry's own `Docker-Content-Digest` response confirms the immutable
manifest reference:

```text
clouddsp-registry.localhost:5001/adtof@sha256:c8706bb0b814ed3a0c1e58455a7c153848ddf7808e65d4e3f4ed52a37b521b91
```

This rebuild also corrects the CPU-process output-directory validator for
Kubernetes `fsGroup` volumes. An `emptyDir` mounted with `fsGroup: 10005` is
setgid, so the worker's owner-only output directory inherits mode `02700`
rather than bare `0700`. The validator now requires the same private `0700`
permission bits, permits that harmless inherited setgid bit, and still rejects
setuid, sticky, group-readable, group-writable, or any other-access modes.

The CPU child now uses `/app` as its fixed working directory. This matters
because `python -m app.adtof_cpu_inference_entrypoint` resolves the image's
`app` package from that directory; using disposable output scratch as CWD made
the child unable to import its own module. The WAV and output paths remain
absolute, reviewed command arguments, so changing CWD does not redirect media
I/O or turn scratch into a code-import location.

The latest rebuild also installs Debian's minimal `libsndfile1` system library
in the final runtime stage. ADTOF's `librosa.load` reaches the hash-pinned
Python `soundfile` package during real WAV transcription, and that package
loads `libsndfile.so` dynamically. The prior image had all Python imports and
model weights but lacked that native decoder, so it failed only once a real
audio file reached inference. The current build proves the package is present;
the end-to-end smoke rerun is the separate proof that model inference succeeds
through the deployed worker.

This release also makes exhausted active leases inspectable rather than
stranded: before ordinary recovery, PostgreSQL can lock one expired ADTOF
third attempt and terminalize only its task with
`lease_expired_attempts_exhausted`. It clears the stale lease, sets completion
time, and leaves Job-level aggregation to a later dedicated policy. The worker
therefore never grants a fourth lease or runs MinIO/model work for that task.

## Immutable image lock

`images.adtof` now records that registry digest as the only approved image
reference, its Linux/ARM64 platform, source Dockerfile/lock/base-image links,
CPU policy, ADTOF model revision, PID 1 entrypoint, local image size, and
offline runtime-import verification. A readable tag remains only build
convenience; a future Deployment must use the immutable reference. No workload
manifest exists or has been applied yet.

## Prepared ADTOF worker Deployment

[`adtof-deployment.yaml`](adtof-deployment.yaml) is the prepared controller for
one internal, long-running CPU ADTOF worker. It uses only
`images.adtof.immutableReference`, disables ServiceAccount-token and Service
link injection, requires Linux/ARM64 scheduling, and supplies no Service or
Ingress because RabbitMQ drives work from inside the cluster.

The Pod runs UID/GID `10005` with a read-only root filesystem, dropped Linux
capabilities, and the runtime-default seccomp profile. It receives exactly the
three established restricted app-namespace Secrets: one each for PostgreSQL,
MinIO, and RabbitMQ. Fixed environment values point only at the private Service
DNS names and the one ADTOF queue, with AMQP prefetch pinned at one.

Its `emptyDir` mounts are intentionally disposable: `512Mi` for the required
`/worker-scratch` input/output workspace, `128Mi` for `/tmp`/Numba cache, and
`64Mi` for the non-root home. The `660`-second termination grace period exceeds
the worker's ten-minute default CPU process limit so SIGTERM can finish one
bounded operation and close cleanly before lease recovery is needed. The
manifest is prepared only: it is **not applied** by this task.

## Read-only pre-apply verification

The local cluster was inspected without reading Secret values, creating a Pod,
or changing any Kubernetes resource. All three long-lived app-namespace Secret
objects exist with their expected key counts: PostgreSQL has three keys and
MinIO/RabbitMQ each have two. The three historical one-shot bootstrap Jobs are
gone, as are their temporary data-namespace administrator Secrets; this is the
intended cleanup outcome, but it means their old completion logs cannot be
read again.

The live backend state supplies stronger current evidence for two services.
PostgreSQL is Ready and contains login role `clouddsp-adtof` with superuser,
database-creation, role-creation, replication, and inheritance all disabled.
That role has execution permission on both guarded functions:
`clouddsp_lock_adtof_job_for_claim` and `clouddsp_complete_adtof_task`.
RabbitMQ is Ready, its durable ADTOF request/retry/DLQ queues exist, and the
ADTOF identity's permission row is exactly no configure, no publish, and read
only `^clouddsp\.adtof\.requests$`. A zero current consumer count is expected:
the Deployment has not been applied.

MinIO is Ready behind its private ClusterIP Service, and the retained
`clouddsp-adtof-artifacts-policy-v001` ConfigMap contains only the approved
drums-WAV `GetObject` and deterministic drum-MIDI/tempo read/write actions.
However, a read-only cluster inspection cannot prove the live MinIO
user-to-policy attachment: the hardened MinIO server container includes no
admin client, and the deliberately removed bootstrap credentials cannot be
reused. Therefore this preflight does **not** claim final MinIO authorization
proof. The prepared runtime-credential smoke Job below provides that missing
live evidence, but is intentionally still not applied.

## Prepared ADTOF MinIO runtime-policy smoke Job

[`adtof-minio-runtime-policy-smoke-job.yaml`](adtof-minio-runtime-policy-smoke-job.yaml)
is a one-shot `clouddsp-app` Job that mounts exactly the existing
`clouddsp-adtof-minio-credentials` Secret. It has neither a MinIO root or
bootstrap Secret nor PostgreSQL/RabbitMQ credentials, and it has no
ServiceAccount token, Service, Ingress, or Kubernetes API dependency.

It uses the already locked official AWS CLI image only as an S3-compatible
client pointed at MinIO's private ClusterIP Service. “AWS” here names the
protocol client and environment-variable convention; the Job does not contact
or provision AWS.

The job cannot safely prove `GetObject` on a made-up drums key: without a
bucket-list permission, S3-compatible storage may intentionally return an
indistinguishable denial for a non-existent object. Instead it proves the
allowed ADTOF **output** boundary without retaining test data: it creates one
multipart upload at `midi/<unique-run-id>/drums.mid`, uploads and lists its one-byte
part, and then requires `AbortMultipartUpload` to remove the incomplete upload
and part. Those calls exercise the policy's `PutObject`,
`ListMultipartUploadParts`, and `AbortMultipartUpload` permissions.

In the same run it attempts only to create a multipart upload at the sibling
`midi/<same-run-id>/vocals.mid` key. That exact final filename is not in the
ADTOF policy, so an explicit MinIO `AccessDenied` response is required. An
unexpected success means the live policy is too broad; a non-authorization
failure is also rejected rather than mistaken for proof. The shell installs an
exit/signal trap to best-effort abort the permitted upload after an error or
SIGTERM, and successful completion aborts it explicitly. It never completes a
multipart upload, so no object is retained. Its termination trap exits after
cleanup instead of allowing a SIGTERM-interrupted script to continue into a
new request.

The source was structurally tested and then applied successfully with only the
existing ADTOF runtime MinIO Secret. Its completion proves the live identity
can perform the temporary allowed ADTOF output operation, must abort it, and
cannot create the foreign `vocals.mid` object. The ADTOF Deployment was then
rolled out successfully. Its normal worker loop uses RabbitMQ `basic_get`
polling, so `rabbitmqctl list_consumers` correctly shows no persistent consumer
row even while RabbitMQ reports the live `clouddsp-adtof` connection.

This authorization check is still not a full worker test: actual read access to
a private Demucs drums WAV is verified only later as part of the end-to-end
ADTOF worker smoke flow, where an authorized durable stem coordinate exists.

## Prepared ADTOF end-to-end smoke fixture

[`../../tests/adtof-worker-smoke/`](../../tests/adtof-worker-smoke/) now fixes
the future smoke test's one `4-stems`/`drums` input coordinate, one synthetic
upstream Demucs-task identity, one downstream request-event identity, and its
two ADTOF outputs. Its standard-library fixture builds a deterministic,
bounded, four-second 120-BPM PCM drum pattern in memory and derives its
run-time byte length/SHA-256 plus the exact Demucs metadata inventory that the
deployed worker independently enforces. It does not create an object, durable
row, broker message, image, or Kubernetes resource.

The later test will prove pipeline correctness—not model quality. It must
observe generic-dispatcher publication, the real worker-created ADTOF task,
both stored outputs and their provenance/checksums, and one successful guarded
completion. It must not require a particular note count, BPM, or confidence
level from the CPU model.

## Prepared ADTOF worker-smoke PostgreSQL boundary

[`../../tests/adtof-worker-smoke/adtof-worker-smoke-database-bootstrap-job.yaml`](../../tests/adtof-worker-smoke/adtof-worker-smoke-database-bootstrap-job.yaml)
now defines the database-only boundary for this fixed ADTOF smoke fixture. It
is source only and remains unapplied. Its ignored credential templates describe
a dedicated temporary data-namespace copy and a later app-namespace copy of a
restricted role; no password is versioned.

The short-lived administrator Job verifies the existing v004/v006 schema gates,
removes the role's direct table/sequence/function authority, and grants only
three administrator-owned fixed-coordinate `SECURITY DEFINER` functions:
`prepare`, `observe`, and successful-only `cleanup`. Its local credentials were
applied and bootstrap completed successfully; that action created no smoke
object, durable Job/event, or broker message.

The companion
[`MinIO smoke identity`](../../tests/adtof-worker-smoke/adtof-worker-smoke-minio-policy-v001-configmap.yaml)
is a separate fixed-key policy and root-key bootstrap Job. Its future client may
write/read/delete only the controlled drums WAV and read/delete only the ADTOF
MIDI/tempo outputs—never list a bucket, write an output, access another job, or
administer MinIO. Its ignored local credentials and immutable policy were
applied, and the bootstrap reported the restricted user enabled with exactly
`clouddsp-adtof-worker-smoke-objects-v001` attached. This provisioning action
created no object, PostgreSQL event, or broker message, so it is still distinct
from a full worker smoke run.

The MinIO client comes from MinIO's official `quay.io/minio/mc` release. The
saved image archive available during local-registry migration contained only
its ARM64 child, so that reviewed image is published at
`y1ktor/clouddsp:minio-mc-release-2025-08-13` and pinned by digest in the image
lock. Fresh deployment verifies the public tag, mirrors it into the local
registry, and uses that local digest in MinIO Jobs. This lock entry currently
supports Linux ARM64 only; it does not claim AMD64 coverage.
