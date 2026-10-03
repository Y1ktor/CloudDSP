# CloudDSP Demucs worker contract, version 1

This document defines the first Kubernetes-local Demucs worker boundary before
we create its PostgreSQL migration, credentials, Python consumer, container
image, Deployment, or KEDA scaler. It extends the already-published
[`demucs.requested` contract](../dispatcher/README.md); it does not change the
RabbitMQ topology, send a message, run Demucs, or create a Kubernetes resource.

The worker consumes a private, at-least-once RabbitMQ request and makes
PostgreSQL—not the broker delivery—the source of truth for exactly which
Demucs task may execute. It must work correctly if RabbitMQ redelivers, a Pod
crashes, a lease expires, a Deployment has more than one replica, or MinIO is
temporarily unavailable.

## Scope and durable outcome

The version-1 input is a source object that upload-intake has verified and the
dispatcher has published as one `demucs.requested` event:

```text
RabbitMQ demucs.requested
  -> short PostgreSQL task claim
  -> acknowledge the broker delivery
  -> private MinIO read + FFprobe validation + Demucs
  -> private MinIO stem writes
  -> short PostgreSQL result + per-stem downstream-outbox commit
  -> later dispatcher publishes Basic Pitch / ADTOF work
```

The important point is that the AMQP acknowledgement comes after the durable
task claim, not after an arbitrary amount of CPU/GPU work. If the Pod dies
after acknowledgement, the lease expires and a worker recovery scan resumes
the durable task. RabbitMQ is therefore a wake-up/delivery mechanism, while
PostgreSQL owns stage state and recovery.

The current Mac/k3d profile will eventually run the same contract using CPU.
That validates correctness but does not demonstrate CUDA throughput. A future
native Linux/NVIDIA profile may use the same contract with a GPU-requesting
worker image; it must not change this data model or acknowledgement order.

## Accepted RabbitMQ request

The worker accepts messages only from the existing private route in virtual
host `/clouddsp`:

| Field | Required version-1 value |
| --- | --- |
| Exchange | `clouddsp.processing-events` |
| Routing key | `demucs.requested` |
| Queue | `clouddsp.demucs.requests` |
| Body | Exact JSON body frozen by the dispatcher contract |
| `content_type` | `application/json` |
| `content_encoding` | `utf-8` |
| `delivery_mode` | `2` (persistent) |
| `type` | `demucs.requested` |
| `message_id` | Canonical lowercase UUID for `outbox_events.event_id` |
| `correlation_id` | Canonical lowercase UUID matching body `job_id` |

The body must have exactly these fields and no browser/API extras:

```json
{
  "schema_version": 1,
  "job_id": "3a1f329f-04f7-4b4f-842a-f2b2a7c9e3c3",
  "source": {
    "bucket": "clouddsp-uploads",
    "object_key": "uploads/3a1f329f-04f7-4b4f-842a-f2b2a7c9e3c3/mix.wav"
  },
  "stem_mode": "4-stems"
}
```

The first pure
[`Demucs request parser`](app/demucs_requested_message.py) now enforces this
boundary before a future worker makes any database or object-storage call. It
accepts only the exact exchange/routing key and AMQP properties above,
canonical lowercase UUID text, one UTF-8 JSON object at most 4 KiB, and the
version-1 field/source-key shape. It rejects duplicate JSON members, unknown
fields, malformed UTF-8, non-persistent delivery, wrong correlation, another
bucket, and nested/other-job source paths through one safe error category. Its
unit tests use no broker or cluster; the later AMQP adapter will turn that
specific malformed-message error into `basic_nack(requeue=False)`.

The pure [`PostgreSQL task-lease adapter`](app/task_lease.py) now supplies the
next boundary, still without opening a connection or committing a transaction.
For a first parsed delivery it locks an existing canonical task, locks the Job,
then locks the task again before it inserts a 15-minute lease. The repeated task
read closes the concurrent first-claim gap while another worker waits for the
Job lock. A matching existing task is a safe duplicate; a missing, expired, or
terminal Job is safe stale history; a Job/outbox mismatch remains explicitly
unsafe for the later terminal-result adapter to record. Its recovery query uses
`FOR UPDATE SKIP LOCKED` to re-lease one due retry or expired active task, and
renewal requires both the current token and an unexpired lease. It never grants
a fourth attempt: an expired third attempt is reserved for a separate guarded
terminal-failure transition.

The pure [`MinIO HeadObject verifier`](app/source_object.py) now validates one
already-claimed private source without downloading audio. It requires the
task's own `uploads/{job_id}/` coordinate, the 256 MiB storage-side size limit,
one canonical CloudDSP audio MIME type, and matching `job-id`/`stem-mode`
metadata. A definite missing object or verified mismatch produces a bounded
permanent category; an authorization/network/service fault remains retryable.
It does not inspect codec bytes or duration—those require the later FFprobe
download boundary—and it does not write a stem or mutate a lease.

Before it creates or claims a task, the worker validates canonical UUID text,
the fixed bucket, the `uploads/{job_id}/` object-key prefix, and a stem mode of
`2-stems`, `4-stems`, or `6-stems`. It then re-reads the authoritative
PostgreSQL `jobs` row and requires all of the following to match:

- a first task claim requires a retained job with `source_uploaded = TRUE` and
  `status = 'source_uploaded'`; a duplicate delivery may instead observe an
  already-owned canonical task and must not mutate that task or Job state;
- `input_bucket`, `input_object_key`, and `stem_mode` equal the message;
- the matching `outbox_events` row has the same `event_id`, `job_id`, stage
  `demucs`, empty `stem_name`, type `demucs.requested`, and is `published`.

The worker never trusts a broker body as permission to read another object's
key, revive an expired job, or replace the source selection stored by the Job
API. It never receives an owner subject, password, access token, original
filename, or presigned URL.

## Versioned `processing_tasks` ownership record

The prepared immutable
[`v003 processing-task migration`](../api/job-api-schema-migration-v003-processing-tasks-configmap.yaml)
and its separate one-shot
[`migration Job`](../api/job-api-schema-migration-v003-processing-tasks-job.yaml)
will introduce a generic durable `public.processing_tasks` table when applied.
Demucs is the first stage, but using a stage column avoids creating a new table
for every later MIDI/DSP stage. Version 1 allows only the canonical Demucs key:

```text
(job_id, stage, stem_name) = (job UUID, 'demucs', '')
```

Its key columns and invariants are planned as follows; this is a contract, not
SQL yet:

| Planned field | Purpose and invariant |
| --- | --- |
| `task_id` | Application-generated UUID primary key for operational correlation. |
| `job_id` | Foreign key to `jobs`, `ON DELETE CASCADE`; the job remains authoritative. |
| `stage`, `stem_name` | `demucs`, `''`; unique with `job_id`, forming the required idempotency key. |
| `request_event_id` | The immutable AMQP `message_id`; unique so one outbox event maps to one task record. |
| `input_bucket`, `input_object_key`, `stem_mode` | Validated stable input copied only after equality with `jobs`; lets recovery execute without needing a new RabbitMQ delivery. |
| `status` | One of `leased`, `running`, `retry_scheduled`, `succeeded`, or `failed`. There is no ambiguous in-memory-only “working” state. |
| `attempt_count`, `available_at` | Counts real task leases, not arbitrary broker redeliveries; supports bounded retry and due-work scans. |
| `lease_token`, `lease_expires_at` | Random UUID ownership token and expiry. Active states require both; inactive/terminal states clear both. |
| `started_at`, `completed_at` | UTC operational timestamps. A successful/failed task has `completed_at`; an active task does not. |
| `last_error_code` | Fixed, non-sensitive category only—never stderr, a model traceback, object key, URL, or credential. |
| `created_at`, `updated_at` | UTC audit timestamps, with a truthful `updated_at` trigger like the existing tables. |

The migration adds a unique constraint on `(job_id, stage, stem_name)`, a
unique constraint on `request_event_id`, and partial indexes for due retries
and expired leases. A second worker replica or a duplicate RabbitMQ delivery
therefore cannot create a second logical Demucs task.

## Claim, lease, and recovery state machine

```text
first valid delivery
  -> leased -> running -> succeeded
      |          |
      |          +-> retry_scheduled --(available time/recovery scan)--> leased
      |          |
      |          +-> failed
      |
      +-> expired lease --(recovery scan)--> leased
```

The initial worker implementation will use a 15-minute lease and renew it at
least once per minute while Demucs/FFprobe/stem upload is in progress. This is
long enough for normal local scheduling but still bounded; an Apple-Silicon CPU
run may take much longer than a GPU run, and the periodic renewal rather than
an extremely long one-shot lease keeps ownership accurate.

The claim is one short PostgreSQL transaction:

1. Lock or insert the canonical task record with `FOR UPDATE SKIP LOCKED`.
2. Re-check the authoritative job and published outbox event in that same
   transaction.
3. If no current worker owns it, assign a new lease token/expiry, increment
   `attempt_count`, and commit.
4. Only after that commit, `basic_ack` the corresponding RabbitMQ delivery.

There is deliberately no database transaction held while downloading audio,
running FFprobe/Demucs, or uploading stems. A task's lease token must be
included in every renewal and final update, so a stale/crashed worker cannot
overwrite a recovery worker's result.

When no queue delivery is available, each worker replica also performs a
bounded due-task scan. It selects only `retry_scheduled` rows whose
`available_at` has arrived and `leased`/`running` rows whose lease expired,
again with `FOR UPDATE SKIP LOCKED`. This is why an acknowledged but crashed
task remains recoverable without inventing a new RabbitMQ message.

The initial retry budget is three real Demucs task leases. A transient failure
after a durable claim records `retry_scheduled` with `available_at` 30 seconds
in the future. After the third failed lease, the worker records terminal
`failed` with a fixed category and updates the Job to `failed` in the same
short result transaction. The existing RabbitMQ retry queue is reserved for a
future reviewed broker-retry publisher; version 1 does not use it because a
database-only retry schedule avoids adding another publish-confirmation crash
window to the worker's task recovery path.

## Acknowledgement rules

| Situation | Durable action before broker action | AMQP outcome |
| --- | --- | --- |
| Valid request wins a new task lease | Commit task lease and validated input identity | `basic_ack`; process under the lease. |
| Valid duplicate while task is active, retrying, successful, or failed | Read/lock the existing canonical task without changing its owner/result | `basic_ack`; the durable task or recovery scanner already owns outcome. |
| Valid request for deleted, expired, or already-terminal Job | Commit no state change after authoritative lookup | `basic_ack` as a harmless stale delivery. |
| Valid request but job/outbox/source identity is inconsistent | Commit a fixed terminal task/job category when the job can be safely identified | `basic_ack` only after that commit. |
| Malformed body or incompatible AMQP properties | No unsafe guess is possible | `basic_nack(requeue=False)`; RabbitMQ sends it to the configured DLQ. |
| PostgreSQL or MinIO unavailable before a durable claim | No task state is committed | Do not acknowledge. Close/reconnect with bounded backoff so the broker redelivers; the quorum queue's delivery limit eventually preserves a repeatedly failing delivery in the DLQ for diagnosis. |

At-least-once delivery means a duplicate `demucs.requested` message remains
possible if a dispatcher publishes successfully but crashes before recording
`outbox_events.published`. The worker must never treat duplicate AMQP delivery
as a request to run Demucs twice. The `processing_tasks` key and guarded lease
updates provide that protection.

## Source validation and private artifact rules

After claiming, but before allocating the model, the worker uses its own
least-privilege MinIO identity to `HeadObject` the stored input. It must verify
the same durable limits and metadata enforced by intake:

- bucket/key match the claimed task and `jobs` row;
- object metadata `job-id` and `stem-mode` match the canonical job/stage;
- exact encoded byte cap is at most 256 MiB; and
- FFprobe finds an audio stream and a duration at most 500 seconds.

[`audio_probe.py`](app/audio_probe.py) is the pure interpretation boundary for
that later FFprobe command. It accepts at most 1 MiB of successful-process JSON
output, rejects duplicate JSON members and non-standard numeric values, requires
at least one `codec_type: audio` stream, and requires a finite, positive
container `format.duration` no greater than 500 seconds. It preserves the
duration as an exact decimal so the 500-second boundary is not affected by
binary floating-point rounding. Malformed FFprobe output is a process-adapter
protocol concern; known media problems use bounded permanent categories. The
module does not run FFprobe, download an object, choose an audio stream, or
change database/AMQP state.

[`ffprobe_process.py`](app/ffprobe_process.py) is the small composition adapter
around that pure parser. It accepts only an existing non-symlink regular file
below the worker's own Pod-local work directory, then runs the fixed shell-free
`ffprobe -v error -show_format -show_streams -of json -i <file>` command. The
child receives no stdin, writes no retained stderr, has a 30-second deadline,
and is killed as a process group if it times out or exceeds the one-MiB JSON
output allowance. The adapter does not download the file or create a work
directory: the next bounded MinIO-download task will own that lifecycle and the
future Deployment will mount it as a size-limited `emptyDir`.

[`source_download.py`](app/source_download.py) now provides that bounded MinIO
download boundary. Given a `HeadObject`-verified source, it makes one private
`GetObject` request, compares its declared length and every streamed byte to
the verified size, and writes only a generic `source.media` file in a random
temporary child of Pod scratch. It yields that path only inside a `with` scope
and removes the child directory on every normal or exceptional exit. It never
uses the browser filename or S3 key as a local path, and it closes the stream
even when MinIO changes or truncates the object. A MinIO outage remains
retryable; a size disagreement is explicitly not allowed to reach FFprobe.

[`source_preflight.py`](app/source_preflight.py) composes the three completed
source checks for one already-claimed task: exact private-coordinate metadata,
exact streamed bytes, then FFprobe audio/duration evidence. Its return value
contains only durable-safe object and probe evidence—never the temporary local
path—because the download context removes the source before the function
returns. It preserves the individual exception categories for the future
token-guarded result adapter; it does not transition PostgreSQL state or make a
RabbitMQ acknowledgement decision.

The companion `opened_validated_demucs_source_workspace()` context manager
performs the same three checks but yields the resulting generic scratch path
only while its `with` block is active. The source body and random scratch child
are removed after every normal or exceptional exit, so a later Demucs command
can reuse FFprobe's exact verified bytes without persisting a path or making a
second MinIO download. `opened_acknowledged_demucs_source_workspace()` adds the
manual-ack lease gate: only an `ACKNOWLEDGED_LEASE` result can obtain that
temporary file capability. Its `preflight` property exposes only durable-safe
evidence to the existing guarded `leased → running` database transition.

[`minio_client.py`](app/minio_client.py) supplies the eventual real client for
that preflight composition. It validates that a Pod has only internal `.svc`
MinIO routing, the fixed uploads bucket/region, path-style addressing, and its
dedicated Demucs S3 Secret. Its lazy Boto3 factory explicitly supplies those
credentials, Signature V4, and bounded connect/read/retry settings, so it does
not consult ambient AWS credential providers. This code creates no client until
called. [`requirements.lock`](requirements.lock) now pins the complete first
local-CPU runtime closure—Boto3, Psycopg, Pika, Demucs 4.0.1, its Dora/ML
dependencies, and the Linux/ARM64 CPU Torch/TorchAudio 2.4.0 wheels—with
SHA-256 hashes. No package is installed by this file. The Dockerfile uses
`pip --require-hashes` and installs locked build tooling before the source-only
packages. Its first verified output is now pinned by immutable digest under
[`images.demucs`](../../images.lock.yaml); a readable image tag is never used
by a future Deployment.

The new [`postgresql.py`](app/postgresql.py) is the matching private Psycopg
connection boundary for the restricted `clouddsp-demucs` role. It accepts only
the authoritative database and internal PostgreSQL Service DNS, hides the
mounted password from normal representations, and yields one short
dictionary-row write transaction with bounded connection and statement
timeouts. It intentionally contains no task-claim SQL, broker acknowledgement,
source read, model invocation, or result update. Seven fake-driver tests prove
its configuration, transaction, rollback, and safe outage behavior without a
database connection. The already-pushed image predates this source-only task;
the later executable-runtime build must produce a new digest before deployment.

[`first_claim.py`](app/first_claim.py) now combines those two deliberately
separate layers for exactly one already-parsed `demucs.requested` delivery. It
opens the bounded PostgreSQL write scope, invokes the pure first-claim decision,
and returns only after normal exit has committed—or an unsafe inconsistency has
rolled back and escaped. It still has no RabbitMQ receive/acknowledgement,
MinIO request, media work, result transition, or Kubernetes resource. Four
recording-context tests prove duplicate/stale commit behavior, inconsistency
rollback, and that an unavailable database prevents the pure claim query. The
image also predates this source-only composition; no Deployment may use its old
digest as if it contained this code.

[`task_maintenance.py`](app/task_maintenance.py) provides the matching bounded
transaction wrapper for the existing recovery and renewal decisions. Recovery
claims one due/expired task and reconstructs its matching published request on
the same cursor, then commits only the resulting `DemucsRecoveredTask` pair or
a normal idle scan. Before a later scan tries reclaimable work, terminalization
atomically records one expired third attempt as `failed` on both the Demucs
task and its still-retained `source_uploaded` Job using the bounded
`demucs_lease_expired_attempts_exhausted` code—Demucs is job-wide, unlike a
single downstream stem stage. If the post-claim evidence read loses ownership,
a private sentinel rolls back the fresh lease before returning `None`; no bare
lease can be committed for future MinIO/model work. Renewal still commits one
matching token's new expiry or a durable `None` loss signal. Neither function
holds a PostgreSQL lock while it waits, touches MinIO, runs a model, or makes a
broker decision. Eleven recording-context tests cover terminalization, idle/
pair commits, rollback on lost or invalid evidence, malformed-row rollback,
renewal, outage ordering, and direct mixed-pair rejection. This remains
source-only; the pinned image predates both transaction-composition modules.

[`delivery_claim.py`](app/delivery_claim.py) now joins the strict request parser
to the committed first-claim composition for one delivery, in that order. It
returns only parsed identifiers plus PostgreSQL's durable claimed/duplicate/stale
result; it intentionally contains no delivery tag, Pika import, acknowledge,
nack, retry/DLQ publish, connection, or consumer loop. This keeps acknowledgement
policy explicit in the next Pika-only task: malformed requests, transient
database failures, and successful durable claims need different broker actions.
Four mocked-boundary tests prove parse-before-claim, parser-error isolation,
database-error propagation, and that the bridge does not invent an
acknowledgement boolean. The pinned image does not contain this source yet.

[`amqp_manual_ack.py`](app/amqp_manual_ack.py) is now that next Pika-shaped,
one-delivery task. It uses `basic_get(..., auto_ack=False)` only on the fixed
Demucs request queue, calls the bridge, then acknowledges a committed
claimed/duplicate/stale result. A malformed request is nacked with
`requeue=False` for the configured DLQ path; a database outage, durable
inconsistency, or unexpected bridge error remains unacknowledged and escapes
for a later reconnect/backoff supervisor. It has no connection settings,
topology control, retry publisher, consumer loop, source validation, model
work, or Kubernetes resource. Its returned result now retains the exact
committed lease only after a `claimed` delivery was acknowledged; duplicate,
stale, idle, and rejected results deliberately contain no lease. Eight
mocked-channel/result tests prove this exact state machine, including ack/nack
channel failures and a missing-lease guard. The image still predates it.

[`acknowledged_lease_preflight.py`](app/acknowledged_lease_preflight.py) is the
next intentionally narrow handoff. It accepts only the manual-ack adapter's
`ACKNOWLEDGED_LEASE` outcome, retains that exact PostgreSQL lease token beside
the existing durable-safe source evidence, and then calls the completed
HeadObject/GetObject/FFprobe composition. Idle, duplicate/stale no-work, and
malformed-DLQ outcomes raise before a MinIO call or FFprobe process can start.
It preserves source-preflight exception categories for a later token-guarded
result transition; it adds no supervisor loop, connection, task-state write,
model invocation, image rebuild, Deployment, or Kubernetes action. Three
mocked-boundary tests prove the handoff gate and exception propagation.

[`task_lease.py`](app/task_lease.py) now also contains the pure
`start_leased_demucs_task` statement for the next step after that handoff
returns valid evidence. In one short transaction supplied later by a
composition layer, it changes only the still-current `leased` task to
`running`, guarded by task ID, job ID, the exact lease token, and PostgreSQL's
unexpired-clock condition. It retains the first `started_at` timestamp across
recovery, while no returned row means the worker lost ownership and must not
start Demucs. Three fake-cursor tests cover successful start, ownership loss,
and an invalid returned timestamp. This remains pure SQL: it does not itself
invoke source preflight, commit, run the model, renew, update a result, build an
image, or change the cluster.

[`preflight_task_start.py`](app/preflight_task_start.py) now supplies that one
short transaction composition. It accepts only acknowledged, validated source
evidence; commits the pure `leased`-to-`running` guard; and returns the lease,
source evidence, and start timestamp only after commit. A committed no-row
result exposes no model-eligible value, so an expired/recovered worker has to
stop before Demucs starts. A database outage or malformed result rolls back and
propagates for later policy. Four in-memory transaction tests prove commit,
ownership-loss, rollback, and outage ordering. It does not receive another
broker delivery, renew ownership, run the model, write artifacts, build an
image, create a Deployment, or change the cluster.

[`running_source_workspace.py`](app/running_source_workspace.py) joins that
guarded transaction to the already-open temporary source workspace without
widening either boundary. It yields a model-eligible path only after the
transaction committed a `running` result with the same lease and source
evidence; a committed ownership loss yields `None`, so the caller must leave
the source context without starting CPU/GPU work. The file remains local only
for the enclosing context and is not stored in PostgreSQL. This source-only
handoff does not yet build a Demucs command, invoke the model, renew a lease,
write a stem, or process another RabbitMQ delivery.

[`demucs_command.py`](app/demucs_command.py) now fixes the next local process
boundary's CPU-only argument vector without starting a child process. It keeps
the cloud model choices (`htdemucs` for two/four stems and `htdemucs_6s` for
six), sets `--device cpu`, names the baked read-only `--repo`, and accepts only
the generic `source.media` file plus a fresh empty output directory below
worker-owned scratch. The tuple contains no shell syntax or user-selected CLI
options; expected output is always the predictable `model/source` child that a
later artifact verifier must still inspect. Three temporary-file tests cover
all stem modes and reject invalid modes, host/symlink/user-named inputs, and
non-empty output directories. It does not download input, execute Demucs,
renew, write artifacts, build an image, or change the cluster.

[`demucs_process.py`](app/demucs_process.py) now adds the bounded runner for
that exact command request. It rebuilds and compares the dataclass just before
execution, so a hand-built request cannot replace the executable or add an
option; then its production runner uses `shell=False`, no stdin, discarded
stdout/stderr, and a private process group. The default 12-minute deadline is
intentionally within the current 15-minute lease, leaving room for the future
orchestrator to renew and clean up; timeout terminates the whole group and
returns a safe category. A zero exit merely returns the expected output path—
it does not trust any stem or upload it. Four fake-runner tests prove approval,
tamper rejection, failure propagation, and deadline validation without running
Demucs/Torch. No model command was executed, and this adds no image, Deployment,
or cluster change.

[`executed_separation_workspace.py`](app/executed_separation_workspace.py) now
composes the committed `running` source workspace with that exact CPU command
and bounded runner. It creates one new random, empty `demucs-output-` directory
under validated Pod scratch, derives the stem mode solely from the committed
lease, and yields a zero-exit command/output pairing only while both source and
output contexts remain open. The next task must validate that output before any
MinIO call; a zero exit is not a completed task and no stem is inspected,
hashed, uploaded, or written to PostgreSQL here. Four fake-runner tests prove
the four-stem and six-stem fixed model choices, source-cleanup gate, failure
propagation, and output cleanup without starting Demucs/Torch. This remains a
source-only change: it does not rebuild an image, deploy a worker, or change
the cluster.

[`validated_stem_inventory_workspace.py`](app/validated_stem_inventory_workspace.py)
now nests the existing strict local stem validator inside that still-open
execution workspace. It yields evidence only when the zero-exit output tree has
exactly the current mode's expected non-empty regular WAV files, and it keeps
the exact command object identity paired with those private paths. Missing,
extra, empty, or unsafe entries stop before a hashing or MinIO boundary; the
outer execution scope removes the output on every exit path. Four fake-runner
tests prove complete inventory, incomplete-output rejection, outer cleanup, and
substituted-command rejection. It performs no hash, upload, PostgreSQL update,
lease renewal, RabbitMQ action, image rebuild, Deployment, or cluster change.

[`hashed_stem_inventory_workspace.py`](app/hashed_stem_inventory_workspace.py)
now nests the existing streaming SHA-256 boundary inside the validated-inventory
scope. It revalidates every exact WAV artifact before reading it, preserves the
same command and per-stem name/path/byte-count pairing, and yields only current
digest evidence while the outer temporary output directory exists. A changed,
missing, or unsafe artifact cannot reach a future object-plan/upload boundary;
the outer execution scope cleans the whole tree after any outcome. Four
fake-runner tests prove complete digest evidence, post-inventory change
rejection, cleanup, and cloned-command rejection. It makes no MinIO request,
PostgreSQL update, lease decision, RabbitMQ action, image rebuild, Deployment,
or cluster change.

[`stem_output_plan_workspace.py`](app/stem_output_plan_workspace.py) now nests
the deterministic private object planner inside that hashed workspace. It
derives only stable `stems/{job_id}/{stem_name}.wav` plans, retains each exact
current local path/byte-count/digest alongside the committed lease, and checks
the fixed bucket, type, key, and complete provenance metadata before yielding.
A later uploader must still stream and rehash the same temporary file; no
MinIO request occurs merely by making a plan. Four fake-runner tests prove the
complete stable plan set, post-hash mutation rejection, outer cleanup, and a
substituted planner result. This adds no upload, PostgreSQL update, lease
action, RabbitMQ action, image rebuild, Deployment, or cluster change.

[`planned_stem_upload.py`](app/planned_stem_upload.py) now invokes the existing
restricted streaming/hash MinIO adapter for exactly one plan instance from that
open workspace. It forbids cloned or arbitrary plans, requires the returned
receipt to match the selected bucket/key/length/SHA-256 evidence, and leaves
the lower adapter to rehash before and during the actual private `PutObject`.
Three fake-client tests prove one selected upload, clone/byte-change rejection,
and receipt matching; a fourth proves an arbitrary workspace cannot select a
storage coordinate. It does not aggregate all stems, write PostgreSQL, renew or
finish a lease, publish/acknowledge RabbitMQ work, rebuild an image, deploy a
worker, or change the cluster.

[`complete_stem_upload.py`](app/complete_stem_upload.py) now calls that
one-stem handoff sequentially for every fixed plan, returning the existing
receipt-only `PublishedDemucsStemSet` type only after every upload succeeds in
deterministic stem order. If an upload fails, later writes stop and earlier
private retry-overwritable objects remain unexposed; no complete receipt set or
PostgreSQL update is made. Three fake-client tests prove complete ordered
receipts, a later transport failure that stops the sequence, and arbitrary
workspace rejection. It does not run Demucs, plan objects, write PostgreSQL,
renew/finish a lease, publish/acknowledge RabbitMQ work, rebuild an image,
deploy a worker, or change the cluster.

[`complete_stem_upload_commit.py`](app/complete_stem_upload_commit.py) now
joins that complete receipt set to the existing token-guarded PostgreSQL
completion transaction. It verifies transaction and outbox-ID-factory
capabilities before MinIO writes, uploads the full fixed set with no database
transaction open, then calls the existing completion path that atomically marks
the task/Job complete and inserts downstream outbox rows. A committed no-row
lease loss returns `None`, so it exposes no durable success or downstream
publish action. Three mocked-boundary tests prove upload-before-transaction
ordering, ownership loss, and no uploads when the database capability is
missing. It does not directly publish or acknowledge RabbitMQ, rebuild an
image, deploy a worker, or change the cluster.

[`task_runtime_once.py`](app/task_runtime_once.py) now nests the completed
boundaries into one single-task attempt for an already acknowledged AMQP result:
source verification, committed running transition, CPU process, exact local
stem evidence, private uploads, and guarded completion. It validates the
narrow source/artifact/database/outbox-ID capabilities before source or model
work, holds no PostgreSQL transaction across storage or CPU work, and unwinds
all temporary directories on every exit path. A start or completion lease loss
returns `None`; exceptions remain for a later supervisor to classify. Three
mocked-boundary tests prove exact nesting/unwinding, no-model ownership loss,
and the database-capability gate. It receives no new AMQP message, loops or
retries nothing, directly publishes/acknowledges no RabbitMQ action, rebuilds
no image, and changes no Deployment or cluster resource.

[`source_failure_classification.py`](app/source_failure_classification.py) now
defines the narrow pre-model exception vocabulary needed by the next durable
result layer. Immutable HeadObject metadata/source-limit, FFprobe media-limit,
and HeadObject/GetObject consistency mismatches map to finite terminal codes;
only the already-redacted HeadObject/GetObject availability wrappers map to a
single retry code. MinIO protocol, FFprobe process/protocol, model, artifact,
database, and unknown failures intentionally remain unclassified. The module
does not write PostgreSQL yet: a following task must consume its result with a
lease-token-guarded `leased -> retry_scheduled` or `leased -> failed` update,
and atomically fail the authoritative Job for every terminal result, including
the third transient failure. Five pure tests prove the exact mapping without a
worker loop, image, Deployment, or cluster side effect.

[`pre_model_failure_transition.py`](app/pre_model_failure_transition.py) now
owns that pure, parameterized PostgreSQL decision. It locks the retained
`source_uploaded` Job and requires every current task coordinate, attempt,
lease UUID, and PostgreSQL-clock expiry. A first/second known MinIO outage
clears the active lease and sets `retry_scheduled` at a database-clock time 30
seconds later; a permanent source rejection, or third outage, atomically marks
the task and Job `failed`, increments the Job revision, and stores only the
reviewed category. A no-row outcome is normal lease/retention/state loss, not
a permission to invent another result. The adapter opens no connection or
transaction and makes no broker, model, image, Deployment, or cluster change;
the next small task is the short commit/rollback wrapper around it. Five fake
cursor tests prove the two atomic paths, bounded exhaustion, race, and
unclassified-error rejection.

[`pre_model_failure_transition_commit.py`](app/pre_model_failure_transition_commit.py)
now supplies the missing short transaction scope. It obtains one restricted
`write_cursor()`, calls the guarded retry/terminal decision exactly once, and
does not return its evidence until normal context exit has committed. A no-row
race commits normally as ownership loss; a database or protocol exception
escapes so the context rolls back. It adds no exception catch, retry sleep,
recovery scan, RabbitMQ action, model work, image rebuild, Deployment, or
cluster change. Four in-memory tests prove commit, normal no-row handling,
rollback, and the early database-capability guard. The next small task can
connect classified source exceptions from the one-task runtime to this committed
decision.

[`pre_model_failure_runtime.py`](app/pre_model_failure_runtime.py) now makes
that connection without widening the worker into a supervisor. It wraps one
already-acknowledged one-task attempt, preserves successful completion and
normal ownership loss, and catches only the reviewed source-preflight failure
wrappers. A committed terminal/retry result becomes an explicit safe outcome;
a no-row transition race becomes ownership loss. Model, output-upload,
database, source-protocol, and unknown errors remain their original exceptions
and receive no source-failure SQL. It neither receives/acknowledges RabbitMQ,
renews/recover tasks, sleeps, rebuilds an image, changes a Deployment, nor
alters KEDA. Five mocked-boundary tests prove success, terminal source failure,
retry/race outcomes, unclassified model failure propagation, and normal lease
loss. The next small task is to define the separate policy for failures after
the task has entered `running`.

[`running_failure_classification.py`](app/running_failure_classification.py)
now defines that after-model policy without changing a task row. A Demucs
process start/timeout/nonzero failure, temporary invalid output tree, local
post-model artifact-integrity problem, or private MinIO stem-write outage maps
to one finite retry category. These categories are safe to retry because a
fresh worker uses the same validated source and deterministic private stem
keys; attempt three will use the explicitly paired terminal exhaustion code.
Image/command/output-plan contracts, database/completion faults, and unknown
errors remain unclassified, so an operator-visible defect cannot silently
become a browser Job failure. Four pure tests prove all retry mappings, the
fail-closed exclusions, and complete retry-to-exhaustion coverage. The next
small task is a token-guarded `running -> retry_scheduled/failed` database
transition that consumes this vocabulary.

[`running_failure_transition.py`](app/running_failure_transition.py) now owns
that pure PostgreSQL decision. Its SQL locks the retained `source_uploaded`
Job and requires the complete task coordinate, current `running` state, exact
attempt, lease UUID, and unexpired PostgreSQL lease. Attempts one/two clear the
lease and become `retry_scheduled` after 30 database-clock seconds. On attempt
three, the paired exhaustion code atomically marks both the task and Job
`failed` while retaining `started_at` as truthful evidence that Demucs began.
Private partial stem objects stay inaccessible and are retry-overwritable at
their stable keys. Four fake-cursor tests prove retry scheduling, terminal
task/Job atomicity, ownership loss, and unclassified-category rejection.

[`running_failure_transition_commit.py`](app/running_failure_transition_commit.py)
now supplies that short commit/rollback scope. It opens one restricted
`write_cursor()`, calls the running retry/exhaustion decision exactly once, and
returns evidence only after normal exit commits. A no-row ownership/state race
commits normally as a stop signal; database/SQL/protocol errors escape and roll
back. It adds no exception classifier, retry sleep, recovery scan, lease
renewal, AMQP/MinIO/model operation, image/Deployment/KEDA change. Four tests
prove normal commit, no-row commit, exception rollback, and capability guard.

[`running_failure_runtime.py`](app/running_failure_runtime.py) now makes that
connection without turning a single attempt into a supervisor. It wraps the
pre-model handoff, passes its success/ownership/source-failure outcomes through
unchanged, and catches only later exceptions that the fail-closed running
classifier recognizes. A reviewed model/output/private-artifact failure uses
the same acknowledged lease in the committed `running` transition; a no-row
race becomes ownership loss, while database, source-protocol, image-contract,
completion, and unknown errors preserve their original exception. The shared
compact outcome retains the concrete pre-model *or* running transition instead
of flattening their different state predicates. Six mocked-boundary tests prove
pass-through, retry, terminal exhaustion, ownership loss, and fail-closed
propagation. It does not receive/acknowledge AMQP, loop/sleep/recover, itself
schedule renewal, rebuild an image, change a Deployment, or alter KEDA. Its
nested execution workspace owns short renewal checkpoints while the model runs.

[`receive_execute_once.py`](app/receive_execute_once.py) now supplies the
first bounded supervisor building block: one manual-ack RabbitMQ receive and,
only for an acknowledged current lease, one call to the completed one-attempt
policy. Idle, duplicate/stale, and malformed-DLQ outcomes return compact
non-execution evidence and cannot reach MinIO, FFprobe, or Demucs. The channel
does not cross into processing, so the post-ack task path cannot make another
broker action. Receive and unclassified runtime errors remain exceptions for a
later reconnect/backoff policy. Four mocked-boundary tests prove exact
dependency forwarding, the no-work gate, failure propagation, and result
pairing. It adds no loop, sleep, recovery scan, signal handling, client
lifecycle, image/Deployment change, or KEDA action; its nested one-attempt
runtime owns short renewal checkpoints.

[`recovery_request.py`](app/recovery_request.py) now reconstructs the strict
request evidence that a due retry or expired active task no longer has in an
AMQP delivery. Immediately after PostgreSQL grants a fresh attempt-two/three
lease, its caller can read only the matching *published* immutable
`demucs.requested` outbox record in that same short transaction. The SQL
rebinds the full task/job/event/source/mode/attempt/token coordinate and checks
the PostgreSQL lease clock. The reader then requires the exact version-1 JSON
payload before reconstructing the ordinary `DemucsRequestedMessage` using the
same source-coordinate validator as a new AMQP delivery. A missing pair is
normal ownership loss; malformed evidence raises so the fresh lease rolls back
instead of authorizing MinIO or Demucs. Four fake-cursor tests cover successful
reconstruction, stale ownership, first-attempt rejection before any query, and
row/payload/publication mismatches. This read-only boundary does not claim or
start work, commit a transaction, acknowledge/publish RabbitMQ, contact MinIO,
run Demucs, loop, alter a Deployment, or change KEDA.

[`task_maintenance.py`](app/task_maintenance.py) now composes that evidence
read with its due/expired lease claim in the one short transaction required for
safe recovery. It returns a `DemucsRecoveredTask` only after normal commit; if
the fresh lease has no current evidence, a private sentinel forces rollback and
the caller receives normal no-safe-work instead. The frozen pair also rejects
first attempts, mixed coordinates, and unsafe direct construction. It neither
starts the model nor changes the normal pre-model guards.

[`recovered_task_execution.py`](app/recovered_task_execution.py) now provides
the recovery-execution gate. It accepts only the committed pair, then feeds its
lease into the established one-task pre-model/running-failure policy through a
data-only compatibility result. That result has no AMQP channel, delivery tag,
body, properties, or acknowledgement method; constructing it cannot contact
RabbitMQ, and the recovery pair remains the authorization. The reused path
still revalidates private source evidence, guards `leased -> running`, renews
while the model runs, and returns its existing success/loss/retry/terminal
outcomes. Three mocked tests prove correct lease forwarding, forged-input
rejection before the runtime, and unchanged downstream error propagation. The
next small task was terminalizing an expired third-attempt task, which recovery
must not grant a fourth lease.

[`task_lease.py`](app/task_lease.py) and
[`task_maintenance.py`](app/task_maintenance.py) now provide that terminal
path. PostgreSQL uses `FOR UPDATE SKIP LOCKED` to select one active
attempt-three Demucs lease whose database-clock expiry has passed, locks its
still-processable Job, and atomically fails both records. It clears the token
and expiry, sets completion time, increments Job revision, and writes only the
fixed expiry-exhaustion code. A no-row result is normal: another scanner may
own it, or Job retention/deletion/later state may have made it ineligible. Two
pure SQL tests and three transaction tests prove success, no work, forged
return rejection, and rollback behavior. The next small task is a single
bounded recovery iteration that terminalizes first, then claims and executes
at most one reclaimable recovered task; it will not yet be a loop.

The terminalization result intentionally exposes `task_id` and `job_id` as
canonical text to the Python adapter. Its Job-update CTE must cast that returned
task `job_id` back to PostgreSQL `UUID` before comparing it to `jobs.job_id`.
Without the explicit cast, PostgreSQL rejects an idle terminalization scan at
planning time (`uuid = text`); because recovery precedes every normal AMQP
poll, a healthy worker would then back off forever without attaching to queued
work. The returned task-side fixed error code is also sufficient evidence, so
the query deliberately does not return `jobs.error_message` or require this
worker to read broader Job error history. This compatibility repair preserves
the same transaction, state guards, and least-privilege grants; it only makes
the existing UUID relationship unambiguous to PostgreSQL.

[`recovery_execute_once.py`](app/recovery_execute_once.py) now provides that
bounded iteration. It terminalizes first; only when no final attempt changed
state does it claim one committed recovery pair and send it through the
recovered-task execution gate. Its three outcomes—`idle`, `terminalized`, and
`executed`—retain exactly their matching compact durable evidence, so a later
supervisor cannot mistake task/Job finalization for model work. Recovery or
runtime errors propagate unchanged instead of being disguised as idle. It has
no AMQP channel/action, loop, sleep, backoff, signal handler, client lifecycle,
or Kubernetes action. Five mocked tests prove idle, execution, terminalization
priority, error propagation, and result evidence pairing. The next small task
is a pure recovery-cadence policy that decides how often this bounded step runs
alongside normal broker receives, without starting a loop.

[`recovery_cadence.py`](app/recovery_cadence.py) now provides that pure local
policy. A new Pod starts with recovery, then strictly alternates one recovery
scan and one normal manual-ack receive/optional-execution iteration. This
limits an expired task's wait to at most one normal task even when the broker
never becomes idle; duplicate and malformed delivery outcomes cannot starve
recovery either. Its frozen state is deliberately not durable task state, so a
restart safely begins with recovery again while PostgreSQL and RabbitMQ retain
the authoritative facts. Four unit tests prove the initial scan, strict
alternation, no normal-outcome bypass, and forged/out-of-order guards. It has
no loop, wait, connection, storage, model, Deployment, KEDA, or Kubernetes
action. The next small task is a one-cycle composition that performs exactly
the action selected by this cadence and returns the advanced state.

[`worker_cycle.py`](app/worker_cycle.py) now supplies that one-cycle
composition. It accepts the shared channel only for the normal branch; a
recovery-selected cycle explicitly never passes it onward because durable
recovery has no AMQP delivery to acknowledge, reject, or publish. It forwards
the same typed source, artifact, FFprobe, Demucs, upload, retry, and event-ID
dependencies to the selected existing bounded composition, advances cadence
only after a valid result, and returns mutually exclusive normal/recovery
evidence with its only valid next state. Errors leave state unadvanced and
propagate for a later supervisor policy. Four mocked tests prove branch
isolation, dependency forwarding, error/forgery handling, and result pairing.
It is still not a loop, wait, connection lifecycle, backoff, Deployment, KEDA,
or Kubernetes action. The next small task is a pure supervisor decision policy
that maps compact cycle outcomes to explicit future wait/retry/exit actions.

[`supervisor_backoff.py`](app/supervisor_backoff.py) now provides that pure
decision policy. Only an empty *normal* broker iteration yields the fixed
one-second idle wait; every recovery result is immediate progress because its
following cadence turn is the normal broker receive. Its reserved availability
failure event creates a capped in-memory exponential backoff of 1, 2, 4, 8,
16, then 30 seconds (with bounded injected jitter), while a static
configuration event chooses a visible exit instead of an infinite retry. The
policy neither catches nor classifies exceptions, sleeps, reconnects, mutates
tasks, or accesses any infrastructure. Eight unit tests prove outcome mapping,
backoff bounds/jitter, healthy-state reset, fatal exit, and invalid-input
guards. The next small task is a narrow supervisor-failure classifier that
allows only reviewed Demucs configuration and dependency-availability errors
to choose the two reserved failure events.

[`supervisor_failure_classification.py`](app/supervisor_failure_classification.py)
now provides that narrow authority. Invalid AMQP/PostgreSQL/MinIO settings and
missing FFprobe or Demucs executables are fatal configuration faults. Only the
safe availability wrappers emitted by bounded RabbitMQ, PostgreSQL, and MinIO
adapters receive generic process backoff. Source/protocol/integrity errors,
FFprobe/model faults, and unknown exceptions return `None`, preserving their
existing durable task policy or operator-visible handling. Four unit tests
prove the fatal, retryable, fail-closed, and non-exception boundaries. The
next small task is a one-step supervisor composition that combines this
classifier, the worker cycle, and the pure decision policy without waiting or
starting a loop.

[`supervisor_step.py`](app/supervisor_step.py) now provides that composition.
It runs exactly one cadence-selected cycle, converts only its matching compact
branch result into an idle/progress event, and asks the policy for the next
action/state. A recognized retryable or fatal error carries no fake cycle
evidence and preserves the previously selected cadence action, so an outage
cannot skip recovery. Unclassified exceptions escape unchanged, and process
control signals are not caught. Five mocked tests cover recovery/normal idle,
retry/fatal preservation, and unclassified propagation. It neither waits,
loops, reconnects, manages a channel, nor changes Kubernetes. The next small
task is a shutdown-aware action adapter that applies one existing decision
through an injected waiter.

[`supervisor_action.py`](app/supervisor_action.py) now applies that one
decision. `check_immediately` continues without waiting; idle/backoff invokes
the injected shutdown waiter once with the exact policy delay; and fatal
configuration returns an explicit exit fact without waiting. A strict boolean
waiter result prevents truthiness from accidentally stopping or resuming a
worker. Five unit tests prove each control path and malformed-waiter rejection.
This adapter installs no signal handler, sleeps nowhere directly, reconnects
no service, and starts no loop. The next small task is a one-step runner that
joins the supervisor step and this action result without creating persistence.

[`supervisor_once.py`](app/supervisor_once.py) now supplies that runner. It
executes one supervisor step, applies exactly that returned decision through
the action adapter, and preserves the step's exact next local state. The
result rejects mismatched action/state evidence and propagates step/action
failures instead of manufacturing `continue`. Four mocked tests prove
forwarding, shutdown/continue results, failure propagation, and pair guards.
It remains non-persistent and creates no loop, signal handler, service
lifecycle, image, Deployment, or Kubernetes action. The next small task is a
focused shutdown-event adapter that owns SIGTERM/SIGINT registration for the
future injected waiter.

[`shutdown_event.py`](app/shutdown_event.py) now provides that scoped bridge.
On the main thread it maps SIGTERM and SIGINT to one `threading.Event`, exposes
the existing `wait_for_shutdown()` protocol, validates the 30-second maximum
policy delay, and restores all prior signal handlers even after body or partial
installation failures. Its asynchronous handler only sets the Event. Five
tests prove finite/idempotent waits, both signals, restoration, partial-failure
cleanup, and main-thread enforcement. It has no worker loop, broker/database/
storage client lifecycle, model cleanup, image, Deployment, or Kubernetes API
action. The next small task is the intentional shutdown-aware supervisor loop
that repeatedly invokes the one-step runner until it returns shutdown or fatal
control evidence.

[`supervisor_loop.py`](app/supervisor_loop.py) now provides that persistent
control loop. It repeats only after `continue`, stops on shutdown/fatal control
evidence, and checks the shared shutdown Event with zero delay before every
new cycle—so SIGTERM during inference cannot permit another broker receive or
recovery scan. It accepts already-created dependencies and creates, reconnects,
inspects, or closes none of them. Five mocked tests prove pre-cycle shutdown,
continue behavior, post-continue signal observation, fatal stopping, and error
propagation. The next small task is a closeable AMQP-session adapter that opens
the existing restricted connection/channel setup and closes it around this loop.

[`amqp_session.py`](app/amqp_session.py) now supplies that lifecycle scope. It
creates one restricted private connection, obtains one channel, applies only
prefetch-one/passive-queue verification, yields the prepared channel, then
closes channel before connection on setup, body, or normal cleanup paths. A
normal cleanup failure becomes the existing redacted channel-unavailable
category without hiding caller errors. As a required prerequisite, direct
`DemucsAMQPSettings` construction is now revalidated before broker I/O, so it
cannot redirect the restricted identity to another host, queue, or account.
Six unit tests cover settings revalidation, setup/body cleanup, cleanup
redaction, and connection failure. The next small task is a worker bootstrap
entrypoint that composes restricted database/MinIO construction, signal scope,
this AMQP session, and the supervisor loop.

[`worker_entrypoint.py`](app/worker_entrypoint.py) now provides that one
process-boundary composition. It creates the restricted PostgreSQL adapter and
the one private MinIO client before opening RabbitMQ, verifies that Kubernetes
mounted a real non-symlink `/worker-scratch` directory, narrows that S3 client
to the source-read and artifact-write protocols, then scopes SIGTERM/SIGINT
and the prepared AMQP session around the persistent supervisor. A clean
termination returns status `0`; an explicitly classified fatal configuration
outcome returns `78`. Unexpected operational failures deliberately propagate
after channel-then-connection cleanup so durable PostgreSQL/RabbitMQ recovery
remains visible to Kubernetes. Four fully mocked tests prove dependency
ordering, signal/session lifetime nesting, status mapping, and no silent
scratch-directory fallback. It creates no image, Deployment, Kubernetes
resource, or network listener. The next small task is a thin executable worker
wrapper that owns controlled diagnostics and turns reviewed bootstrap
configuration failures into the same visible configuration exit status.

[`worker_main.py`](app/worker_main.py) now provides that final executable
boundary. It calls the bootstrap entrypoint and returns the entrypoint's normal
status unchanged. Before the supervisor starts, malformed mounted RabbitMQ,
PostgreSQL, or MinIO configuration—or a missing/unsafe scratch mount—produces
only `demucs worker has invalid or incomplete configuration.` on stderr and
returns `78`; its original detail never enters ordinary Pod logs. Workload,
availability, and unknown failures deliberately propagate after the
entrypoint's AMQP cleanup so Kubernetes can observe the failed process and
durable state can recover work. Three mocked tests prove normal status
forwarding, detail-free configuration failure, and unreviewed-error
propagation. This task does not modify the image, start a container, or create
a Kubernetes resource. The next small task is to update the source-only
Demucs Dockerfile so its non-root runtime starts this module directly as PID 1
while still leaving `/worker-scratch` for the future bounded `emptyDir` mount.

[`Dockerfile`](Dockerfile) now records that source-only runtime contract. Its
final stage retains the dedicated `clouddsp-demucs` non-root account and uses
the exec-form `ENTRYPOINT ["python", "-m", "app.worker_main"]`, so Kubernetes
delivers SIGTERM directly to the reviewed shutdown path rather than to a shell.
It deliberately does not create `/worker-scratch`; the later Deployment must
mount a bounded writable `emptyDir` there or the bootstrap entrypoint fails
safely. Two structural tests prove the non-root exec entrypoint and the absent
scratch-directory fallback. The existing published Demucs image predates this
source change, so it must be rebuilt, tested, and pushed under a new immutable
digest before any future Deployment can use this worker revision. This task did
not build, push, or deploy an image. The next small task is that local ARM64
image build and inspection only; a registry push and workload manifest remain
separate decisions.

That reviewed source has now built locally for Linux/ARM64 as the deliberately
non-deployable tag `clouddsp-demucs:0.1.1-worker-entrypoint-local-only`.
Docker's uncompressed size is `503,066,464` bytes (`479.76 MiB`). Its
validation stage ran all 296 Demucs tests, `ffprobe -version`, and the pinned
Demucs/Torch/Torchaudio import check. An offline runtime inspection confirmed
the exact exec entrypoint, UID `10003`, and an absent `/worker-scratch`; the
default wrapper run, without any mounted configuration, emitted only its safe
configuration diagnostic and exited `78`. The tag has not been pushed, no
image-lock digest has changed, and no Kubernetes resource was created. The
next small task is registry push and digest inspection only; updating the image
catalog and creating a worker Deployment remain separate tasks.

The same verified ARM64 bytes are now present in the local k3d registry as
`clouddsp-registry.localhost:5001/demucs:0.1.1-worker-entrypoint-local-only`.
The registry's HTTP manifest response confirmed this immutable OCI index:

```text
clouddsp-registry.localhost:5001/demucs@sha256:f8335a7a78108b74283d9d1d9fc46f82225d9dbe089b8fc961284c44da7f7db0
```

This push does not make the tag a workload reference: `images.demucs` still
records the older worker-less image and remains deliberately unchanged. No
Deployment was created. The next small task is to review and update that image
catalog record with this verified immutable reference; a Demucs Deployment is
still a separate task.

[`images.demucs`](../../images.lock.yaml) now records the reviewed
`0.1.1-worker-entrypoint-local-only` build, its `503,066,464`-byte local size,
the Linux/ARM64 CPU profile, and the exact immutable OCI index reference above.
It also records `python -m app.worker_main` as the source image's PID-1
contract. A future manifest must use only that immutable reference, never the
readable tag. This catalog update creates no workload and does not imply that a
worker Pod is running. The next small task is a prepared Demucs Deployment
manifest; applying it remains a separate, explicit action.

[`demucs-deployment.yaml`](demucs-deployment.yaml) was prepared separately and
is now applied as the one-replica local controller. Kubernetes defaults the
intentionally omitted `spec.replicas` field to one; keeping it omitted also
means a later KEDA `ScaledObject` can own the Deployment scale subresource
without an ordinary manifest continually overwriting KEDA's choice. The
Deployment selects only Linux/ARM64 nodes and uses the reviewed `images.demucs`
immutable CPU digest, not a mutable tag, CUDA request, or Apple-GPU claim. It
runs the image's dedicated UID/GID `10003` account with a read-only root
filesystem, dropped capabilities, `RuntimeDefault` seccomp, no service-account
token, and no legacy Service-link environment variables.

The worker deliberately creates no Service or Ingress because it accepts no
inbound application traffic. Its only three dependency routes are the private
PostgreSQL, MinIO, and RabbitMQ ClusterIP DNS names. The three mounted Secrets
are the already-prepared, restricted runtime identities; administrator and
bootstrap credentials are absent. A 780-second termination grace period gives
the 720-second bounded Demucs child enough time to react to SIGTERM and close
its AMQP session before Kubernetes must force it down. All permitted writes are
bounded disposable `emptyDir` volumes: `2Gi` for one source/stem workspace,
`128Mi` for `/tmp`, and `64Mi` for a nonpersistent HOME. The `3Gi`
ephemeral-storage request/limit accounts for those volumes and runtime
headroom; PostgreSQL and MinIO remain the only durable task/artifact stores.
The first controller's `1` CPU request, `2` CPU limit, `2Gi` memory request,
and `4Gi` memory limit are an intentionally conservative local CPU profile.

Four source-only structural tests lock this private, non-root, bounded
manifest shape. They do not inspect a live cluster or Secret. The prerequisite
runtime Secrets and live PostgreSQL/RabbitMQ authorities were separately
preflighted before application; the completed bootstrap Jobs had already been
removed by their intentional TTLs.

The initial applied `0.1.1` worker remained process-healthy but had no durable
idle AMQP socket, despite the exact restricted in-Pod AMQP connection, passive
queue check, and empty `basic_get` all succeeding. This proved the required
outbound route—private RabbitMQ Service DNS on TCP `5672`—and the credentials,
queue, and policy were correct; it was not an inbound-port problem. Demucs
does **not** declare a `containerPort` or Kubernetes Service because it listens
on no port. `containerPort: 5672` would incorrectly say that RabbitMQ runs
inside the worker and would not enable outbound networking.

[`session_supervisor.py`](app/session_supervisor.py) now avoids the stale-idle
session condition: a PostgreSQL-only recovery turn opens no broker socket, and
each normal `basic_get` turn opens a newly authenticated, prefetch-one,
passively verified AMQP session, then closes it after that one bounded cycle.
A reviewed AMQP open/close fault becomes interruptible local reconnect backoff
with unchanged normal cadence; it never becomes an acknowledgement, requeue,
or durable task mutation. The rebuilt Linux/ARM64 runtime is
`0.1.2-short-lived-amqp-sessions-local-only`, pinned in
[`images.demucs`](../../images.lock.yaml) to
`sha256:3c721bcad886969ecf2a31b70af1a92a7d2c7058c49abceae97faea0d5c88dd9`.
Its validation stage passed 304 tests plus FFprobe and locked ML-runtime
imports; the 479.77 MiB image was registry-confirmed and rolled out as one
ready, zero-restart local Pod. An absent AMQP socket between normal polls is
now intentional, not a readiness signal.

### Prepared recovery-query repair (not rolled out)

The first Demucs-stage smoke exposed a separate recovery-path issue, not an
AMQP attachment failure. A RabbitMQ `list_consumers` result of zero is normal
between polls because this worker intentionally uses a short-lived
`basic_get` session. Before it can reach that poll, however, every cadence runs
the expired-third-attempt terminalization scan. That SQL returned
`failed_task.job_id` as text for the Python result adapter, then compared it
directly with the UUID `jobs.job_id`; PostgreSQL rejects `uuid = text` while
planning the statement even when there is no expired task. The repair makes
that boundary explicit with `failed_task.job_id::uuid`. It also stops returning
`jobs.error_message`, which the restricted Demucs database role does not need
and is not permitted to select. The fixed image is
`0.1.3-recovery-uuid-join-fix-local-only`, pinned to
`sha256:99e24301c4c2f37547de7fd8a7773d7e0eae6cfb5f4222db9e00ae1294cae73a`.
Its Docker validation stage passed all 304 tests, FFprobe, and the pinned ML
imports; the targeted live SQL check planned and executed with no eligible
task. The Deployment has now rolled out this immutable reference to one ready,
zero-restart local Pod. The failed test's fixed object keys, Job, and dependent
outbox event were then removed through a reviewed exact-coordinate cleanup
Job; the Demucs request queue and DLQ were independently verified empty. The
same smoke test is therefore ready to repeat from its documented clean state.

### Prepared local ARM64 CPU Demucs repair (not rolled out)

The repeated smoke moved past the repaired recovery SQL and reached real model
execution. Its durable task then entered `retry_scheduled` with the safe
`demucs_process_failed` category because the fixed Demucs child ended with
exit code 132 (`SIGILL`). The fault was isolated inside the local Linux/ARM64
Torch model backend: it happened with a fixed local WAV and the baked model
files, before any stem upload or downstream outbox change. It was not caused
by RabbitMQ delivery, PostgreSQL state, MinIO, or the smoke client's upload.

The local-only fix is intentionally narrow. The command builder now starts
`/usr/local/bin/python -m app.demucs_cpu_cli` instead of the generic `demucs`
console script. That launcher sets and verifies
`torch.backends.mkldnn.enabled = False` **before** importing
`demucs.separate`, then delegates the same fixed arguments to Demucs. A
separate child process is created for every task, so this process-wide Torch
setting cannot leak into the PID-1 worker supervisor or another Pod. It is not
a CUDA/NVIDIA or cloud policy and must not be copied into a future GPU image.

The new Docker validation deliberately runs more than imports: it generates a
two-second WAV and executes `htdemucs --two-stems vocals` through the launcher,
requiring both `vocals.wav` and `no_vocals.wav`. The rebuilt local ARM64 image
passed all 309 tests, FFprobe, pinned Demucs/Torch/Torchaudio imports, and that
real inference check. Its immutable reference is
`clouddsp-registry.localhost:5001/demucs@sha256:d0ebd533f66eb2c8097646477a6ba1439349add297bfde578e4a865f08c9186b`
(479.77 MiB). `images.lock.yaml` and the Deployment source are updated to that
digest but no Deployment has been applied or rolled out by this repair task.

### Prepared launcher working-directory repair (not rolled out)

The deployed `0.1.4` image correctly avoids the local ARM64 MKLDNN `SIGILL`,
but the next live smoke exposed a distinct startup boundary. The process
runner deliberately calls every model child with its private output directory
as `cwd`. Therefore `python -m app.demucs_cpu_cli` searches that empty
directory for `app` instead of the image's `/app` working tree and ends before
it can load Demucs. That safe but opaque child failure was recorded as
`demucs_process_failed`; it was not a model, audio, MinIO, RabbitMQ, or memory
failure. A direct command from `/app` could succeed, which is why the former
image validation did not detect it.

`0.1.5-local-arm64-launcher-cwd-fix` changes only the reviewed child command
to `/usr/local/bin/python /app/app/demucs_cpu_cli.py`. It preserves the exact
MKLDNN setting and fixed Demucs arguments, but the absolute script remains
resolvable when the process runner isolates the child in its output directory.
The Docker validation now explicitly changes into that empty output directory
before running the real two-second, two-stem inference. All 309 source tests,
FFprobe, locked runtime imports, and that exact-context inference passed. The
new immutable image is
`clouddsp-registry.localhost:5001/demucs@sha256:43b4c352a3c4bf077fe685a0904d368f5cb89c2f40a25b1b2a006045b2ec231c`
(479.77 MiB). The image lock and Deployment source are updated, but the live
Deployment remains on the earlier digest until an explicit rollout.

[`running_lease_renewal.py`](app/running_lease_renewal.py) now supplies that
one-shot renewal boundary. It accepts only a committed `DemucsRunningSource`,
uses its exact task/token in the existing short PostgreSQL renewal transaction,
and returns either an identical running/source coordinate with only its expiry
refreshed or an explicit ownership-loss stop signal. A database/protocol error
still escapes; it is not misreported as a renewal or lease loss. Five unit
tests prove expiry-only replacement, no-row loss, input guards, error
propagation, and result pairing. This adapter intentionally starts no timer or
thread and cannot stop a child process by itself; the execution workspace uses
it at the later cancellation-safe process checkpoints.

[`demucs_process.py`](app/demucs_process.py) now provides that cancellation-
safe process layer as the separate `run_demucs_separation_with_lease_renewal()`
entrypoint. It requires a runner that owns the real child process group; a
plain synchronous runner is rejected instead of being abandoned in an
uncancellable Python thread. The production subprocess runner waits only until
the next one-minute-or-faster renewal tick, timeout, or child exit. A renewal
checkpoint result of `False`, a checkpoint exception, or a process timeout
terminates the entire child group before the caller receives the corresponding
signal. This process boundary still knows no database, task, or RabbitMQ fact:
[`executed_separation_workspace.py`](app/executed_separation_workspace.py) now
wires the committed running-lease checkpoint into it. Each renewal runs in its
own short transaction while the child waits; a refreshed lease is carried to
later upload/completion guards, while a stopped lost-lease child becomes normal
task ownership loss without entering retry/terminal SQL. The caller cannot use
a plain runner when renewal is active. Three integration tests cover the
renewal handoff, downstream refreshed evidence, child-stop loss, and the
one-attempt ownership mapping. The next small task is atomically composing
recovery claim and reconstructed evidence before the ordinary pre-model runtime
receives the committed pair.

[`demucs_artifacts.py`](app/demucs_artifacts.py) now performs the next strict
local-output check before an upload layer may exist. It requires exactly
`vocals`/`no_vocals` for two stems, the four normal hybrid stems for four, or
those four plus guitar/piano for six; each must be one non-empty regular WAV
file in the expected private `model/source` directory. Missing, extra, hidden,
directory, symlinked, or empty entries are rejected, and the returned inventory
contains only stem names, ephemeral paths, and byte counts. Four temporary-file
tests prove all modes plus mismatch/path/tamper rejections. It does not inspect
audio samples, hash, upload, update PostgreSQL, or change an image, Deployment,
or cluster resource.

[`demucs_artifact_hash.py`](app/demucs_artifact_hash.py) now adds the next
local-only evidence boundary. It repeats the exact inventory validation rather
than trusting a caller's dataclass, then reads each current regular WAV file in
64 KiB chunks to produce an immutable SHA-256 digest alongside its fixed stem
name and byte count. A changed size, symlink, non-regular file, or altered
hand-built inventory is rejected before future MinIO code can receive it. Three
temporary-file tests prove stable evidence plus stale-size, symlink, and
metadata-tamper rejection. This layer does not upload, call MinIO, update
PostgreSQL, acknowledge AMQP, build an image, or change the cluster.

[`demucs_output_object.py`](app/demucs_output_object.py) now converts only a
current re-hashed inventory plus its durable Demucs lease into a deterministic
private upload plan. Each stem always maps to
`stems/{job_id}/{stem_name}.wav`, preserving the cloud-compatible job prefix
across recovery attempts. The immutable plan fixes the bucket, `audio/wav`
content type, byte length, Pod-local source path, and ordered S3 metadata:
schema version, producer, job/task IDs, stem name/mode, byte count, and
SHA-256. It re-hashes before comparison so even a same-size byte substitution
or forged frozen dataclass is rejected. Three temporary-file tests cover the
normal plan, mismatched/malformed leases, and stale or forged hash evidence.
It does not call MinIO, upload data, update PostgreSQL, acknowledge AMQP,
build an image, or change the cluster.

[`demucs_artifact_upload.py`](app/demucs_artifact_upload.py) now provides the
one-stem private MinIO `PutObject` boundary for those plans. It rechecks every
fixed bucket/key/metadata field, hashes the current regular WAV before opening
MinIO, then streams the request body while hashing again. It returns a small
receipt only when the client consumed the planned content length and SHA-256;
an ETag is deliberately not treated as a portable integrity proof. A same-size
replacement fails before the client call, partial body consumption fails after
the call, and raw SDK diagnostics become one safe unavailable category. Three
fake-client tests prove those paths. It does not create the client, list/delete
objects, update PostgreSQL, acknowledge AMQP, build an image, or change the
cluster.

[`demucs_stem_set_publish.py`](app/demucs_stem_set_publish.py) now composes the
approved local process, exact-output inventory, SHA-256 evidence, private
object-plan, and one-stem upload boundaries. Starting with a matching running
lease and command, it returns a complete in-memory receipt set only after every
expected stem is uploaded in deterministic order and every receipt re-matches
its plan. A failed process, incomplete output tree, task-mode mismatch, or
forged injected receipt stops before a full result exists. Earlier successful
objects can remain private at retry-overwritable keys, but this composition
does not record them in PostgreSQL or expose them to a browser. Three fake
process/client tests prove those paths. It does not renew a lease, mutate task
or Job state, create outbox events, acknowledge AMQP, build an image, or change
the cluster.

[`task_lease.py`](app/task_lease.py) now also contains the one guarded Demucs
completion statement. It locks only a retained `source_uploaded` Job, then
transitions only that Job’s `running`, unexpired task token to `succeeded`,
records the complete JSONB stem map, advances the Job to `midi_processing`, and
increments its revision. In that *same statement*, it inserts one durable
post-Demucs request for every expected stem: `drums` routes to ADTOF and every
other reviewed stem routes to Basic Pitch. A no-row result changes neither task,
Job, nor outbox; an outbox constraint/permission failure rolls back all three.
The statement is fully parameterized. Four fake-cursor cases cover success,
ownership loss, malformed evidence, and a mismatched inserted-event count.

[`demucs_task_completion.py`](app/demucs_task_completion.py) is the matching
short-transaction composition. It accepts all and only the expected uploaded
stem receipts, preserves the cloud-compatible `status: ready` and `s3_key`
fields, adds bucket/type/byte-count/SHA-256 evidence, and returns a completion
only after the transaction commits. It builds the exact finite downstream event
set and passes it into that same guarded SQL statement, so no later component
has to infer work from mutable Job state. Invalid receipt/event evidence never
opens a transaction; an SQL failure rolls it back. Three in-memory transaction
tests prove those guarantees. It does not touch MinIO, publish/acknowledge
RabbitMQ, renew a lease, build an image, or change the cluster.

[`amqp_connection.py`](app/amqp_connection.py) now provides the separate
connection boundary. It accepts only the private RabbitMQ Service DNS, AMQP
port 5672, `/clouddsp` vhost, fixed Demucs queue, and restricted
`clouddsp-demucs` Secret username; it redacts the mounted password and bounds
connection/heartbeat timing. Its lazy Pika factory opens one socket without
creating a channel, declaring topology, receiving a delivery, or acknowledging
anything. Six mocked settings/driver tests cover rejection of widened config,
safe outage handling, and exact Pika parameters. This source-only task still
does not alter the pinned image, a Deployment, or the live cluster.

[`amqp_channel.py`](app/amqp_channel.py) now adds the distinct safe setup of a
channel from that connection. It applies `prefetch_count=1`, so one CPU/GPU
worker holds at most one unacknowledged request, then passively checks only the
already-imported `clouddsp.demucs.requests` queue. Passive verification cannot
create or change queue/DLQ/quorum arguments; an inaccessible queue becomes a
bounded reconnectable error rather than an invitation for the restricted user
to alter topology. Four mocked-channel tests cover exact setup, failures, and
the repeated fixed-queue guard. It still receives no delivery or starts no loop.

The resulting local image recipe is now documented in
[`image-recipe.md`](image-recipe.md). It deliberately uses a CPU-only
Linux/ARM64 build for the current Apple-Silicon k3d cluster while preserving
the cloud worker's PyTorch 2.4.0 and `htdemucs`/`htdemucs_6s` compatibility
baseline. It is not a CUDA image and does not claim Metal/MPS acceleration.
The document also makes model-weight bytes an explicit, separately checksummed
build input rather than an untracked network download at Pod startup.
[`model-artifacts.lock.yaml`](model-artifacts.lock.yaml) now pins the two
CloudDSP model names' actual weight and local-descriptor bytes, so the
Dockerfile can make an offline `--repo` bundle after it verifies their full
SHA-256 checksums. The lock is source metadata only; it does not add the 132.7
MiB model bytes to Git or create an image.

[`Dockerfile`](Dockerfile) is now the reproducible local CPU image definition.
Its first `linux/arm64` build successfully verified all four model artifacts,
ran all 49 isolated tests, and was pushed to the local registry as the
digest-pinned `images.demucs` entry. Its final size is 479.50 MiB, expected
because it includes CPU Torch, FFmpeg, and the 132.7 MiB verified model bundle.
It deliberately has no `ENTRYPOINT` yet: the AMQP consumer loop is a later
small task, so a Deployment cannot accidentally start an unfinished worker.

It then writes only stable private artifact keys under `stems/{job_id}/`. The
version-1 output sets are fixed by `stem_mode`:

| Mode | Expected stem names |
| --- | --- |
| `2-stems` | `vocals`, `no_vocals` |
| `4-stems` | `drums`, `bass`, `vocals`, `other` |
| `6-stems` | `drums`, `bass`, `vocals`, `other`, `guitar`, `piano` |

Partial/private MinIO objects are not exposed to the browser. Only after every
expected stem is present and validated may a guarded PostgreSQL transaction
record the stem keys in `jobs.stems`, mark the Demucs task `succeeded`, update
the Job from `source_uploaded` to `midi_processing`, and atomically create the
per-stem downstream outbox events. This worker still must not call Basic
Pitch/ADTOF, RabbitMQ, or Kubernetes directly.

## Transactional post-Demucs outbox boundary

The prepared immutable
[`v004 downstream-outbox migration`](../api/job-api-schema-migration-v004-downstream-outbox-configmap.yaml)
and its separate [`migration Job`](../api/job-api-schema-migration-v004-downstream-outbox-job.yaml)
extend v002's single Demucs-only outbox vocabulary without editing history. The
migration permits only these combinations:

| Output stem | Durable stage and event type | Why |
| --- | --- | --- |
| `drums` | `adtof`, `adtof.requested` | ADTOF extracts drum onsets/features from the dedicated drum stem. |
| `vocals`, `no_vocals`, `bass`, `other`, `guitar`, `piano` | `basic-pitch`, `basic-pitch.requested` | Each non-drum Demucs output may carry pitched musical content. |

The completion payload has the exact schema-version, Job ID, stem name, and
private WAV bucket/key/type/byte/SHA-256 evidence. It contains no lease token,
presigned URL, browser identity, credential, scratch path, or exception text.
`outbox_events` retains v002's `(job_id, stage, stem_name)` uniqueness, so a
task cannot produce duplicate durable downstream work.

The separate prepared
[`Demucs downstream-outbox permission bootstrap Job`](demucs-downstream-outbox-permissions-bootstrap-job.yaml)
must run only after v004 and the original Demucs role bootstrap. It grants the
restricted role INSERT only for event identity/routing/payload columns; the
dispatcher retains every publication, retry, lease, and error-state field.
Neither prepared Job is applied by this documentation/source change.

## Least-privilege boundaries

Demucs uses three separate credentials. The prepared manifests do not create
any identity until their individual bootstrap Jobs are deliberately applied:

| System | Demucs worker authority | Explicitly excluded |
| --- | --- | --- |
| RabbitMQ | Consume/ack `clouddsp.demucs.requests`; no topology access. | Browser/API ingress, source queue, management API, dispatcher/outbox publishing. |
| PostgreSQL | Create/claim/renew/finish canonical Demucs tasks; guarded stage/job updates and later approved outbox inserts. | Role administration, schema changes, arbitrary jobs, other users’ data, deletion. |
| MinIO | Read the task's validated `uploads/{job_id}/…` source and write that job's `stems/{job_id}/…` artifacts. | Bucket administration, broad listing, browser presigning, unrelated source/artifact prefixes. |

The worker requires no Kubernetes API permission and never creates another Pod
or Job. Kubernetes schedules/restarts/scales the worker process; durable stage
handoff remains PostgreSQL and RabbitMQ's responsibility.

## Validated Demucs worker stage smoke

`kubernetes/tests/demucs-worker-smoke/` contains the end-to-end stage test for
this deployed worker. It uses a fixed two-second source WAV,
the ordinary PostgreSQL outbox and generic dispatcher path, then proves that
the worker completed its canonical task and produced two privately readable,
hash-matched WAV stems.  The smoke client has no AMQP credential, so it cannot
fake either dispatch or consumption.  See that directory's `README.md` for
the reviewed bootstrap, apply, inspection, and evidence-preserving cleanup
procedure.

## Durable-work KEDA scaling policy

[`demucs-scaledobject.yaml`](demucs-scaledobject.yaml) is the prepared KEDA
policy for the local CPU Demucs worker. The long-running Deployment remains the
owner of the Pod template, digest-pinned image, least-privilege identities,
resource limits, rollout behavior, and SIGTERM handling. KEDA's generated HPA
owns only its standard `/scale` subresource; it is not a second consumer and
never creates a Kubernetes Job.

KEDA watches `clouddsp.demucs.requests` through RabbitMQ's private management
ClusterIP on port 15672, using the existing read-only monitoring identity.
That wakes a worker for a new request. The worker acknowledges RabbitMQ as
soon as its PostgreSQL task lease is committed, *before* long CPU inference.
Consequently, the queue may be empty while an active model still needs its
Pod. A second KEDA PostgreSQL trigger counts only Demucs tasks that are
`leased`, `running`, or `retry_scheduled` and due. It keeps the Pod alive
after the broker ACK and wakes a zero-replica Deployment for a database-only
retry. Neither trigger consumes work or changes task state.

The PostgreSQL observer is a separate, read-only `clouddsp-keda-demucs` role.
The versioned [bootstrap Job](../postgresql/postgresql-keda-demucs-bootstrap-job.yaml)
grants SELECT on only the task columns `stage`, `status`, and `available_at`;
it does not expose job IDs, MinIO keys, event payloads, or write privileges.
The [TriggerAuthentication](../../helm/scaling-auth/templates/keda-demucs-postgresql-trigger-authentication.yaml)
reads its password from an ignored app-namespace Secret. The temporary
bootstrap Secret is removed from the data namespace after the role is created.

Reassert the reviewed configuration in an already-deployed, idle cluster with the
[versioned script](../../scripts/reconcile-demucs-scaling.sh):

```bash
./k8Deployment/kubernetes/scripts/reconcile-demucs-scaling.sh
```

The script first verifies the existing PostgreSQL and RabbitMQ scaler
identities and their restricted permissions. It then runs the existing Helm
release checks, which lint and render the charts, validate the Kubernetes API
schema, compare installed and live configuration with the reviewed sources,
and require Helm ownership and an idle Demucs worker.

After those gates pass, it upgrades `clouddsp-scaling-auth` and then
`clouddsp-demucs` in `clouddsp-app`, using the explicit
`k3d-clouddsp-local` context and checked-in chart defaults. The shared
scaling-auth release includes both PostgreSQL and RabbitMQ authentications;
the Demucs release includes its Deployment and ScaledObject. Final checks
verify both authentications, scaler readiness, HPA ownership, and idle state.

This maintenance command requires both releases to be deployed and already
match the reviewed configuration. Missing releases, failed releases, source
or ownership drift, and active Demucs work stop it before Helm writes. Use
`deploy-local.sh bootstrap-platform` for a fresh cluster; intentional chart
changes need their own reviewed rollout. Existing runtime credentials under
`k8Deployment/.local/` are verified without applying Secrets, creating a
database bootstrap Job, or rotating credentials. If a Helm upgrade fails,
inspect the release in place before retrying.

This local policy uses a 15-second polling interval, one-message target,
`minReplicaCount: 0`, `maxReplicaCount: 1`, and a five-minute cooldown. The
cap is deliberate: one CPU-only Demucs Pod now requests 2 CPU/2 GiB and
can use 4 CPU/4 GiB, so a second local model could crowd out PostgreSQL,
RabbitMQ, and MIDI workers. RabbitMQ retains backlog safely. A future NVIDIA
GPU node profile may define a separate capacity policy after measuring GPU
memory and node capacity. The reviewed local run applied this policy and used
the ordinary restricted Demucs smoke route as its one-request backlog. KEDA
observed that request, scaled the Deployment from `0 → 1`, and the fresh Pod
completed the full source-to-stems route. The smoke finished successfully in
86 seconds and self-cleaned its evidence. Once the queue became inactive, the
same ScaledObject returned Demucs from `1 → 0` after the configured five-minute
cooldown for a short source. A 150-second MP3 later exposed the flaw in that
queue-only rule: once its message was acknowledged, KEDA saw zero work and
terminated the running Pod. The PostgreSQL metric closes that gap. Future
threshold, resource, node, or GPU-profile changes still need
their own explicit apply-and-observe task.

### Yosemite local CPU benchmark (2026-09-24)

The first real-file check used the existing authenticated six-stem job for
`Yosemite.mp3`: MP3, 6,006,016 bytes, 150.048 seconds of audio. Its first
attempt had been interrupted under the old queue-only scaling policy and
recorded a process timeout, so that attempt is **not** a valid two-CPU
throughput baseline. After deploying the dual RabbitMQ/PostgreSQL scaler and
the two-CPU request/four-CPU limit, a due PostgreSQL-only retry activated
Demucs from zero replicas with an empty request queue.

The worker acquired its running lease at `04:43:15.993 UTC`, logged
`model_complete` at `04:50:00.130 UTC` (404.137 seconds, about 6 minutes
44 seconds), and committed completion at `04:50:01.100 UTC` (405.107 seconds
from lease acquisition). During inference, `kubectl top` sampled about
3.4–4.0 CPU cores and 1.2–1.4 GiB of container memory. Those are sampled
values, not peak CPU or memory claims. KEDA kept the Pod Running beyond the
previous five-minute scale-down point, PostgreSQL recorded Demucs attempt 2
as `succeeded`, and the job subsequently reached `completed` with six durable
stem records. Read-only MinIO `HeadObject` checks found all six expected WAV
objects (drums, bass, other, vocals, guitar, piano), each 26,468,546 bytes.
Once the task metric became inactive, the five-minute idle cooldown elapsed
and KEDA returned the Demucs Deployment to zero replicas with no Pod left.
This establishes the local four-CPU outcome but cannot isolate
how much speedup came from extra CPU versus the scaling fix; a separate
controlled two-CPU run would be required for that comparison.

## Prepared PostgreSQL identity

The prepared
[`demucs-database-bootstrap Job`](demucs-database-bootstrap-job.yaml) creates
the separate `clouddsp-demucs` PostgreSQL login only after the v003 migration
has succeeded. It preflights every required table/column with zero-row queries
first; therefore applying it to an older database fails before it can create a
partially useful runtime role.

Its two credential templates make Kubernetes namespace boundaries explicit:

| Template | Namespace and lifetime | Purpose |
| --- | --- | --- |
| [`demucs-database-credentials.secret.example.yaml`](demucs-database-credentials.secret.example.yaml) | `clouddsp-app`; retained | Future Demucs runtime's restricted database name, username, and password. |
| [`demucs-database-bootstrap-credentials.secret.example.yaml`](demucs-database-bootstrap-credentials.secret.example.yaml) | `clouddsp-data`; temporary | The same restricted identity values for the administrator-only bootstrap Job; delete after it passes. |

The role can create/claim/renew/complete `processing_tasks`, re-read the
minimal Job/outbox identity, and make guarded updates to Job status, revision,
stems, and a reviewed error category. The separate v004 extension described
above adds only downstream-outbox INSERT columns. It still cannot manage
roles/schema, delete records, change ownership/source coordinates/retention,
read owner identity or outbox payload, or update dispatcher-owned publication
state. PostgreSQL grants control columns, not which rows may change; the
adapter must still use the contract's `job_id`, revision, status, and
lease-token predicates on every write. The MinIO identity remains separate.

### Recovery payload-verification repair

An expired task must rebuild its original `demucs.requested` authorization
without asking RabbitMQ to redeliver the message. The worker therefore needs
to prove the immutable outbox payload still exactly matches the new lease.
Granting `SELECT` on `outbox_events.payload` would solve that mechanically but
would let the Demucs role inspect payloads for unrelated stages and jobs.

[`demucs-recovery-verifier-bootstrap-job.yaml`](demucs-recovery-verifier-bootstrap-job.yaml)
installs an idempotent PostgreSQL `SECURITY DEFINER` function instead. It
accepts the complete current lease coordinate, verifies the matching published
v1 JSON payload inside PostgreSQL, and returns only `true` or `false`. Public
function execution is revoked; only `clouddsp-demucs` receives `EXECUTE`.
The worker keeps raw payload reads denied. The baseline
[`demucs-database-bootstrap Job`](demucs-database-bootstrap-job.yaml) creates
the same verifier for a fresh cluster, while the dedicated repair Job lets an
already-running local cluster add it without recreating the database role or
mounting the temporary Demucs credential Secret.

The local PostgreSQL repair Job has been applied successfully: its safe
bootstrap log reports `can_verify_own_recovery_event = t` and
`can_read_outbox_payload = f`, without printing passwords, payloads, or object
keys. A later end-to-end smoke run exposed one separate completion defect: the
task CTE returns `job_id` as text for the Python contract, while the next CTE
compared that text directly with PostgreSQL's UUID column. PostgreSQL correctly
rejected `uuid = text`, so the worker safely retried after CPU work rather than
committing a partial result.

The current local Linux/ARM64 worker image,
`0.1.8-completion-uuid-join`, explicitly casts that CTE value back to UUID in
the guarded completion transaction. It is pinned as
`clouddsp-registry.localhost:5001/demucs@sha256:e6cab988fa3786d66dcfd7f608dfa6479582a4acd3bfb9b47f948d637cf6fd59`.
Docker validation passed all 312 unit/structural tests, FFprobe, locked ML
imports, and a real two-stem CPU inference. The deployed one-replica worker
then passed the complete restricted smoke route in 81 seconds, including the
atomic Job/task/downstream-outbox commit and its downstream cleanup barrier.

The prepared RabbitMQ templates and
[`RabbitMQ consumer bootstrap Job`](../rabbitmq/rabbitmq-demucs-consumer-bootstrap-job.yaml)
create a separate `clouddsp-demucs` AMQP user. It has empty configure/write
patterns and an exact read pattern for `clouddsp.demucs.requests` in
`/clouddsp`, which permits consumption and acknowledgement but not topology,
publication, retry/DLQ consumption, management access, or direct downstream
dispatch. Its app-namespace runtime template lives at
[`demucs-rabbitmq-credentials.secret.example.yaml`](demucs-rabbitmq-credentials.secret.example.yaml);
the matching temporary data-namespace template is
[`rabbitmq-demucs-consumer-bootstrap-credentials.secret.example.yaml`](../rabbitmq/rabbitmq-demucs-consumer-bootstrap-credentials.secret.example.yaml).
The broker topology must already exist, and neither template/Job is applied by
this documentation change.

The prepared immutable
[`Demucs MinIO policy`](../minio/minio-demucs-artifacts-policy-v001-configmap.yaml)
and its [`bootstrap Job`](../minio/minio-demucs-artifacts-bootstrap-job.yaml)
create a third, separate `clouddsp-demucs` identity for S3 requests. Its
policy permits `GetObject` on known private `uploads/*` sources and
`GetObject`/`PutObject` plus multipart cleanup/inspection only under
`stems/*`; it has no bucket administration, browser presigning, object
deletion, or bucket-list permission. Its retained app-namespace template is
[`demucs-minio-credentials.secret.example.yaml`](demucs-minio-credentials.secret.example.yaml),
while the matching data-namespace bootstrap template is
[`minio-demucs-artifacts-bootstrap-credentials.secret.example.yaml`](../minio/minio-demucs-artifacts-bootstrap-credentials.secret.example.yaml).

MinIO IAM can scope an identity to a static prefix, but it cannot express the
dynamic rule “only the one job currently leased by this worker.” The later
adapter must therefore obtain its job/object identity from PostgreSQL, bind its
lease token in every state update, and write only that `stems/{job_id}/` prefix.
This is defense in depth: storage policy prevents source/bucket administration,
while durable task ownership prevents a worker from treating an arbitrary
object as authorized work.

## Out of scope for this contract task

This task intentionally does **not** apply the prepared `processing_tasks`
migration or any prepared identity, add the remaining consumer/database/FFprobe
artifact-writing implementation, a CPU/GPU image build or push, Deployment, KEDA scaler,
Basic Pitch/ADTOF worker/AMQP contracts, or smoke Job.
