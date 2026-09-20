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
commits either one due task's new lease or a normal idle result; renewal commits
one matching token's new expiry or a durable `None` signal that ownership was
lost. A future runtime must stop work on that `None`, and neither function can
hold a PostgreSQL lock while it waits, touches MinIO, runs a model, or makes a
broker decision. Five recording-context tests cover the normal recovery/renewal
paths, malformed-row rollback, and the database-outage ordering. This remains
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
runtime owns short renewal checkpoints. The next small task is reconstructing
strict execution evidence for a due/expired recovered Demucs task.

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
one-attempt ownership mapping. The next small task is reconstructing strict
execution evidence for a due/expired recovered Demucs task, which has no new
RabbitMQ delivery to parse.

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
