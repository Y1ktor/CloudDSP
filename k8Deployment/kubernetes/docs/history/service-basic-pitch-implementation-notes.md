# Basic Pitch worker foundation

> Historical implementation notes preserved on 2026-10-03 from
> `k8Deployment/kubernetes/services/basic-pitch/README.md`.
> This complete snapshot records earlier implementation stages and may contain
> superseded commands, filenames, image tags, and statements about unfinished work.
> Use the [current service contract](../../services/basic-pitch/README.md) and
> [operator guide](../../scripts/README.md) for deployment and operations.
> Relative Markdown links have been rebased; historical targets may no longer exist.


This directory is the Kubernetes-local Basic Pitch worker track. It does not
reuse the preserved cloud Lambda handler: later layers will explicitly compose
RabbitMQ, PostgreSQL, MinIO, and Basic Pitch dependencies for a long-running,
least-privilege Kubernetes consumer.

## Request and output contract

[`app/basic_pitch_requested_message.py`](../../services/basic-pitch/app/basic_pitch_requested_message.py)
defines the first pure safety boundary. It accepts only a persistent
`basic-pitch.requested` delivery from `clouddsp.processing-events` with a
version-1 body for one approved non-drum WAV stem:

```text
stems/{job_id}/{vocals|no_vocals|bass|other|guitar|piano}.wav
```

It rejects the ADTOF-only `drums` stem, a foreign exchange/routing key, a
non-persistent envelope, non-canonical IDs, duplicate JSON names, a different
private bucket/key, unverified byte/checksum evidence, and unexpected fields.
It does not acknowledge the delivery. RabbitMQ retry/DLQ policy and the
PostgreSQL `(job_id, basic-pitch, stem_name)` task claim must be added before a
runtime is allowed to call MinIO or ML code.

For an accepted request, the deterministic future artifact coordinate is:

```text
midi/{job_id}/{stem_name}.mid
```

This mirrors the existing cloud artifact layout. The pure plan does not claim
MIDI bytes or a checksum before Basic Pitch generates them; later output/hash,
storage, and guarded PostgreSQL-completion tasks supply that evidence.

## Planned task-table prerequisite

The applied
[`v005 processing-task migration`](../../services/api/job-api-schema-migration-v005-basic-pitch-processing-tasks-configmap.yaml)
is the completed database-only prerequisite. Its companion Job changed no
runtime component: it merely permits one `basic-pitch` task for each allowed non-drum
stem and its exact `stems/{job_id}/{stem_name}.wav` input key. It leaves the
existing `(job_id, stage, stem_name)` idempotency key and every retry/lease rule
in place. The least-privilege PostgreSQL identity and first guarded task-claim
adapter below now use that schema; a runtime, MinIO verification, and task
completion remain separate work.

## PostgreSQL identity

The completed
[`Basic Pitch database bootstrap Job`](../../services/basic-pitch/basic-pitch-database-bootstrap-job.yaml)
used the PostgreSQL administrator only long enough to create the independent
`clouddsp-basic-pitch` login. Its permanent app-namespace credential template
is [`basic-pitch-database-credentials.secret.example.yaml`](../../services/basic-pitch/basic-pitch-database-credentials.secret.example.yaml);
the temporary data-namespace duplicate is
[`basic-pitch-database-bootstrap-credentials.secret.example.yaml`](../../services/basic-pitch/basic-pitch-database-bootstrap-credentials.secret.example.yaml).
The role may create and lifecycle-manage a processing task, and read the small
Job/event fields needed to compare a delivery with durable state. It cannot
read an owner or source coordinate, update a Job, write/lease/publish an outbox
event, delete records, or run DDL. PostgreSQL requires `UPDATE` privilege for
any `SELECT … FOR UPDATE` row lock, so the bootstrap additionally grants
`EXECUTE`—and nothing else—on
`public.clouddsp_lock_basic_pitch_job_for_claim(uuid)`. That administrator-owned
`SECURITY DEFINER` function locks only the requested Job row and returns only
the existing claim projection (ID, stem mode, status, revision, retention).
It is not a general Job mutation API; direct Job `UPDATE` remains denied.
PostgreSQL column grants do not encode the stage row predicate, so the later
lease adapter must always bind both `stage = 'basic-pitch'` and the current
lease token in its guarded statements. Its grant report confirms task
creation/lease updates, limited Job/outbox reads, the exact function execute
right, and denied input replacement, owner read, direct Job update, outbox
publication update/insertion, and deletion. The temporary data-namespace
Secret is now deleted; the permanent app-namespace runtime Secret remains. No
worker Deployment or database connection is created by the identity alone.

## MinIO identity

The applied immutable
[`Basic Pitch MinIO policy`](../../services/minio/minio-basic-pitch-artifacts-policy-v001-configmap.yaml)
is the object-storage identity boundary. It permits `GetObject` on known
private `stems/*` objects and `GetObject`/`PutObject` plus multipart
inspection/cleanup on `midi/*`, so a later worker can upload and then verify
its own deterministic output. It deliberately grants no bucket listing,
deletion, upload-prefix access, bucket administration, anonymous access, or
browser presigning. Static MinIO prefix IAM cannot know which task lease the
Pod owns, so the future adapter must still compare the durable Job/event/task
identity before using an input or output key.

The permanent app-namespace credential template is
[`basic-pitch-minio-credentials.secret.example.yaml`](../../services/basic-pitch/basic-pitch-minio-credentials.secret.example.yaml).
The deliberately temporary data-namespace duplicate is
[`minio-basic-pitch-artifacts-bootstrap-credentials.secret.example.yaml`](../../services/minio/minio-basic-pitch-artifacts-bootstrap-credentials.secret.example.yaml).
The completed bootstrap Job created/rotated the user and attached the policy;
the temporary data-namespace Secret has been deleted, while the app runtime
Secret remains. No worker connection or object is created by that bootstrap.

The completed
[`MinIO artifacts bootstrap Job`](../../services/minio/minio-basic-pitch-artifacts-bootstrap-job.yaml)
runs six ordered administrative init containers: configure a root alias, wait
for MinIO, import the immutable policy, create/rotate the restricted user,
attach/inspect the policy, then delete the temporary root alias before the Job
can complete. It has no worker credentials beyond the one restricted pair and
does not access PostgreSQL, RabbitMQ, or objects.

## MinIO client configuration

[`app/minio_client.py`](../../services/basic-pitch/app/minio_client.py) is the next pure runtime boundary.
It reads the future Pod's non-secret internal Service configuration and the
existing `clouddsp-basic-pitch-minio-credentials` Secret, then lazily constructs
one explicit Boto3-compatible S3 client. It accepts only
`http://clouddsp-minio.clouddsp-data.svc:9000`, the private
`clouddsp-uploads` bucket, `us-east-1`, and path-style S3 addressing. The
factory passes the restricted access/secret key directly, so Boto3 cannot use
ambient AWS credentials, a host profile, or browser-facing Traefik routing.
Creating the client performs no S3 request; Boto3 imports only when the factory
is called, and the later dependency/image task will pin and install it. The
lease-bound MinIO `HeadObject` verifier in [`app/stem_object.py`](../../services/basic-pitch/app/stem_object.py)
now makes that one metadata-only request. It permits only the claimed
`stems/{job_id}/{stem_name}.wav` coordinate, compares its exact WAV type and
byte length with the durable request, and requires the complete immutable
Demucs metadata inventory—including job/stem/mode, size, and SHA-256—to agree.
An absent object is a bounded permanent result; an outage remains retryable;
malformed metadata is never treated as valid media. It does not download a
byte, renew/start a task, acknowledge RabbitMQ, invoke Basic Pitch, write MIDI,
or update PostgreSQL.

[`app/stem_download.py`](../../services/basic-pitch/app/stem_download.py) is the next bounded I/O layer.
Given only that verified evidence, it requests the same private object once,
rechecks the returned size/type headers, streams at most 64 KiB at a time into
a private random child under the future Pod's scratch `emptyDir`, and hashes
every byte. It yields `stem.wav` only if the final byte count and SHA-256 match
the `HeadObject` evidence; the file and directory are removed on all exits.
It accepts no arbitrary bucket/key, no symlinked scratch root, and no stem
larger than 256 MiB. It still does not start/renew a task, acknowledge a broker
delivery, invoke Basic Pitch, upload MIDI, or write PostgreSQL. The next small
task is a guarded `leased → running` PostgreSQL transition before model work.

That pure transition now lives beside the claim SQL in
[`app/task_lease.py`](../../services/basic-pitch/app/task_lease.py). It uses one parameterized PostgreSQL
statement that binds the task ID, Job ID, Basic Pitch stage, exact stem name,
and lease token, and requires the PostgreSQL clock to show that the lease is
still active. A timestamp returned from the committed future transaction is
the only permission a later runtime may use to start the model. A zero-row
result means the lease was expired, recovered, or changed, so the worker must
stop without producing MIDI. It opens no connection or transaction itself;
the separate PostgreSQL client/composition task will supply that short scope.

[`app/postgresql.py`](../../services/basic-pitch/app/postgresql.py) now supplies the client half of that
scope. It accepts only the private
`clouddsp-postgresql.clouddsp-data.svc:5432` Service, authoritative
`clouddsp_job_api` database, and the restricted `clouddsp-basic-pitch` login
from the existing permanent app Secret. It lazily imports Psycopg and opens a
fresh dictionary-row connection per action with a three-second connect timeout
and five-second PostgreSQL statement timeout. `write_cursor()` commits on a
normal exit and rolls back on any error; it ends before MinIO or model work.
It creates no Deployment, no connection at import time, and no task/model
transition by itself.

[`app/stem_task_start.py`](../../services/basic-pitch/app/stem_task_start.py) now composes the temporary
verified stem, that short transaction, and the pure start statement. It first
downloads/hashes the exact claimed private stem while the task remains
`leased`, then commits `running` with the same token. Only after the transaction
commits does its context manager yield the generic temporary `stem.wav` to a
future Basic Pitch process. If ownership is gone, it deletes the scratch stem
before yielding `None`; database or protocol failures also propagate only after
cleanup.

[`app/stem_task_terminal_failure.py`](../../services/basic-pitch/app/stem_task_terminal_failure.py) now
defines the complementary **pre-model** terminal path. A later failure
classifier may give it only one finite, non-sensitive stem-validation code:
object missing, size/type/metadata mismatch, or a streamed-download checksum
mismatch. Its one guarded statement changes only the current unexpired
`leased` task to `failed`, clears the lease, records PostgreSQL's completion
time, and stores that reviewed code in `last_error_code`. It does not update
the overall Job, retry or dead-letter the already-acknowledged RabbitMQ
delivery, call MinIO, run Basic Pitch, open a transaction, or create a
Kubernetes resource. A no-row result is a normal ownership-loss signal. The
completed
[`app/stem_task_terminal_failure_commit.py`](../../services/basic-pitch/app/stem_task_terminal_failure_commit.py)
now supplies the one short commit-or-rollback scope around that statement, and
[`app/stem_failure_classification.py`](../../services/basic-pitch/app/stem_failure_classification.py)
maps only the known permanent `HeadObject` or streamed-download consistency
errors to those finite codes. MinIO outages, protocol errors, database errors,
and model failures are deliberately not terminalized here. Those modules do
not themselves catch errors in the execution coordinator, receive RabbitMQ
messages, or run a worker loop; the later post-ack execution boundary described
below connects only the reviewed terminal handler.

[`app/stem_task_retry_schedule.py`](../../services/basic-pitch/app/stem_task_retry_schedule.py) now
provides the separate **transient pre-model** SQL boundary.  It accepts only a
current, unexpired Basic Pitch `leased` task that has retries remaining and the
one reviewed `basic_pitch_stem_storage_unavailable` code.  Its guarded
statement clears the lease, records a PostgreSQL-clock `available_at` time,
and changes only that task to `retry_scheduled`; it does not mark the task or
Job complete, start the model, call MinIO/RabbitMQ, open a transaction, or
create a Kubernetes resource.  The bounded durable delay is distinct from a
future Pod's short reconnect backoff, so a restart cannot erase it.  A no-row
result deliberately covers loss of ownership, expiry, another state change,
or the final allowed attempt; a later explicit exhaustion/recovery policy will
own those outcomes and arrange a new delivery for a successfully scheduled
retry.  The completed
[`app/stem_task_retry_schedule_commit.py`](../../services/basic-pitch/app/stem_task_retry_schedule_commit.py)
now supplies the one short commit-or-rollback scope around that statement. It
returns retry evidence only after normal transaction exit, while preserving the
same no-row outcome and no authority to classify exceptions, sleep, invoke
MinIO/Basic Pitch, touch RabbitMQ, or update the Job.

[`app/task_lease.py`](../../services/basic-pitch/app/task_lease.py) now also owns the pure due-retry
recovery claim. Its one indexed `FOR UPDATE SKIP LOCKED` statement selects at
most one due Basic Pitch `retry_scheduled` task with attempts remaining,
increments its attempt count, clears the old error code, and grants a fresh
`leased` token. It does not use a RabbitMQ delivery or recover an expired
active task, because a possibly started model run requires a separate policy.
The returned lease is not model permission by itself: a later recovery boundary
must reconstruct strict request evidence before normal preflight can start.

[`app/stem_retry_classification.py`](../../services/basic-pitch/app/stem_retry_classification.py) now
recognizes exactly two temporary input-stem MinIO wrappers: unavailable initial
`HeadObject` and unavailable `GetObject`/streaming download. Both map to the
same finite storage-unavailable retry code. Permanent input mismatches,
protocol failures, database/model failures, and MIDI upload/verification
failures are deliberately excluded: the latter occur after the model starts
and require their own later policy. The adjacent
[`app/stem_retry_handling.py`](../../services/basic-pitch/app/stem_retry_handling.py) joins that finite
classification to the committed retry-schedule wrapper. Its explicit result is
either `unclassified` (no SQL), `retry_scheduled` (committed first/second
attempt evidence), `retry_exhausted` (committed third-attempt terminal
evidence), or `no_durable_result` (a stale/recovered/different-state guard
miss). It still does not catch around worker I/O, rethrow an unclassified
error, or send/receive RabbitMQ messages.

[`app/stem_task_retry_exhaustion.py`](../../services/basic-pitch/app/stem_task_retry_exhaustion.py) now
defines the separate final-attempt outcome for that reviewed temporary storage
failure. It accepts only a current unexpired Basic Pitch `leased` task whose
attempt count is exactly three, then changes it to `failed`, clears its lease,
sets PostgreSQL's completion time, and stores the finite
`basic_pitch_stem_storage_retry_exhausted` code. It leaves `started_at` and the
overall Job unchanged because the model never began. A no-row result is normal
ownership loss; this pure adapter does not open a transaction, classify an
exception, touch MinIO/RabbitMQ, or create a Kubernetes resource.
The completed
[`app/stem_task_retry_exhaustion_commit.py`](../../services/basic-pitch/app/stem_task_retry_exhaustion_commit.py)
now supplies its short commit-or-rollback scope. It returns terminal evidence
only after normal transaction exit, preserves the no-row ownership-loss result,
and has no authority to classify errors, retry, touch MinIO/RabbitMQ, or update
the Job.

[`app/basic_pitch_process.py`](../../services/basic-pitch/app/basic_pitch_process.py) now builds and runs
the fixed Basic Pitch CLI command
`/usr/local/bin/basic-pitch <fresh-output-directory> <temporary-stem.wav>`.
It makes a mode-0700 `midi-output` sibling in the worker-owned temporary
directory, permits no caller-selected model/flags/paths, starts a shell-free
process group with no stdin or retained stdout/stderr, and enforces a default
five-minute CPU deadline. A zero exit returns only the expected local
`stem_basic_pitch.mid` coordinate; it is not yet artifact proof.

[`app/midi_artifact.py`](../../services/basic-pitch/app/midi_artifact.py) is the completed local artifact
proof boundary. It accepts only that fixed process command/output coordinate,
opens one non-symlink regular file, limits it to 16 MiB, validates its Standard
MIDI File header and exact declared `MTrk` chunk layout, then streams SHA-256
in 64 KiB pieces. Its returned local path, byte count, `audio/midi` MIME type,
and checksum are evidence for a later MinIO upload boundary—not proof of an
uploaded object, a completed task, or a RabbitMQ acknowledgement.

[`app/midi_output_object.py`](../../services/basic-pitch/app/midi_output_object.py) is the completed
object-plan boundary. It joins the strict request, matching current task lease,
and freshly revalidated local MIDI evidence to form only
`midi/{job_id}/{non-drum-stem}.mid`. Its immutable S3 metadata records the
schema/producer, Job/task/request IDs, stem and mode, MIDI size/checksum, and
input-stem checksum. It does not create a MinIO client or send a request.

[`app/midi_artifact_upload.py`](../../services/basic-pitch/app/midi_artifact_upload.py) is the completed
restricted MinIO write boundary. It revalidates the fixed private key and all
metadata, repeats MIDI framing/hash proof, hashes the current file once before
upload and again as the Boto3-compatible client consumes it, and sends exactly
one `PutObject` request. A success receipt exists only when the client consumed
the planned byte count and SHA-256. It intentionally excludes raw SDK output
and ETags, and does not update PostgreSQL, acknowledge RabbitMQ, or create
Kubernetes resources.

[`app/midi_artifact_head_object.py`](../../services/basic-pitch/app/midi_artifact_head_object.py) is the
completed stored-object boundary. It makes exactly one MinIO `HeadObject` call
for the upload receipt's fixed key and requires the stored length, `audio/midi`
type, and complete case-normalized provenance metadata to match the immutable
plan. It returns only stable bucket/key/length/SHA-256 evidence; raw S3
responses, paths, credentials, and ETags remain out of later task state.

[`app/midi_task_completion.py`](../../services/basic-pitch/app/midi_task_completion.py) now calls the
administrator-owned `clouddsp_complete_basic_pitch_task` function installed by
the Job API's versioned v007 migration (with its tempo-aware overload added by
v009). PostgreSQL rechecks the current, unexpired lease, records the verified
MIDI key/byte-count/SHA-256 and BPM candidate under `jobs.midi[stem_name]`, and
succeeds that one task in the same transaction.
The worker receives `EXECUTE` on that typed function, not direct `UPDATE`
access to the parent Job. `None` remains the normal stale-owner result.

[`app/tempo_candidate.py`](../../services/basic-pitch/app/tempo_candidate.py) mirrors cloud Basic Pitch's
librosa beat-tracker candidate. It records estimated BPM, beat count, stem
duration, interval consistency, and a credible/low-confidence decision. BPM
analysis is best-effort: a tempo-estimation error creates a non-credible
candidate without discarding a valid MIDI result. Migration v009 derives the
browser-visible `jobs.tempo` field from all ready candidates in the same
PostgreSQL write; credible ADTOF drums take priority, otherwise the strongest
agreeing non-vocal candidate cluster is resolved with the cloud weighted-
median rule.

The same migration installs a deferred PostgreSQL aggregate trigger. When a
Basic Pitch or ADTOF task becomes terminal, the trigger runs at commit and
locks the parent Job. It checks the mode's exact expected stem/task set, waits
while any task can still retry or run, and then sets the parent to `completed`
only if every task succeeded and every deterministic MIDI/tempo output is
registered. If all child tasks are terminal and one failed—or a durable task or
output invariant is broken—it sets a bounded safe `failed` state. This keeps
RabbitMQ as transport and PostgreSQL as the state authority; no extra polling
Deployment is required.

[`app/midi_task_completion_commit.py`](../../services/basic-pitch/app/midi_task_completion_commit.py)
is that completed transaction composition. It opens the existing restricted
PostgreSQL `write_cursor()` only after all model and MinIO work has finished,
calls the pure completion function inside it, and returns a success result only
after normal context exit commits. A normal stale-owner `None` commits no
mutation; any database or evidence exception leaves the scope and rolls back.
It does not make MinIO/RabbitMQ/model/Kubernetes calls; the deferred database
trigger owns the overall Job transition.

[`app/basic_pitch_task_execution.py`](../../services/basic-pitch/app/basic_pitch_task_execution.py) now
defines the post-claim execution order without becoming an AMQP consumer. A
future consumer may call it only after its separate parser/first-claim layer
has committed a `claimed` lease and acknowledged the corresponding delivery.
For that lease it checks the private Demucs stem with `HeadObject`, downloads
and hashes it while committing `leased → running`, runs the fixed Basic Pitch
CLI, proves the local MIDI, plans/uploads/rechecks the deterministic
`midi/{job_id}/{stem_name}.mid` object, and commits the guarded
`running → succeeded` transition. The temporary WAV/MIDI scope surrounds all
model and object work, but neither PostgreSQL transaction does. A lost lease
stops the sequence; MinIO/database/process errors escape for the later
retry/recovery supervisor. It receives no delivery tag and cannot parse,
acknowledge, reject, or retry RabbitMQ messages.

## RabbitMQ consumer identity

The completed broker boundary uses a separate `clouddsp-basic-pitch` RabbitMQ user;
it is independent of the same-named PostgreSQL and MinIO identities. The
permanent [`runtime template`](../../services/basic-pitch/basic-pitch-rabbitmq-credentials.secret.example.yaml)
belongs in `clouddsp-app`, while the temporary
[`bootstrap template`](../../services/rabbitmq/rabbitmq-basic-pitch-consumer-bootstrap-credentials.secret.example.yaml)
belongs in `clouddsp-data`. The ignored local copies are applied. A later
administrator bootstrap Job gave this user empty configure/write regular
expressions and read access only to `clouddsp.basic-pitch.requests` in
`/clouddsp`, so it can consume and acknowledge its own requests but cannot
alter topology, publish a message, read retry/DLQ queues, or use the management
API.
The completed
[`RabbitMQ consumer bootstrap Job`](../../services/rabbitmq/rabbitmq-basic-pitch-consumer-bootstrap-job.yaml)
waits for both the private management API and the already-imported main request
queue before it declares the untagged user and applies those exact vhost regex
permissions. It then lists only queue/permission metadata for review. Its
successful report confirmed the exact main Basic Pitch queue and empty
configure/write rules. The temporary data-namespace Secret is deleted, while
the app runtime Secret remains for a future worker.

[`app/amqp_connection.py`](../../services/basic-pitch/app/amqp_connection.py) now turns that permanent
runtime Secret into one bounded Pika connection configuration. It permits only
the private `clouddsp-rabbitmq.clouddsp-data.svc:5672` ClusterIP listener,
`/clouddsp`, the fixed `clouddsp.basic-pitch.requests` queue, and the
restricted `clouddsp-basic-pitch` identity; it hides the password from normal
representations and rechecks direct dataclass construction before importing
Pika. The current local profile has plain private AMQP only: the module
deliberately supplies no TLS option because no AMQPS listener/certificate
configuration exists yet. It opens no channel, consumes no message, declares
no topology, and makes no acknowledgement decision.

[`app/amqp_channel.py`](../../services/basic-pitch/app/amqp_channel.py) now provides that setup. It calls
`basic_qos(prefetch_count=1)`, so one CPU-bound worker Pod never holds several
unacknowledged delivery/lease candidates while processing a single stem, then
uses `queue_declare(..., passive=True)` to read-check the exact existing Basic
Pitch queue. Passive mode never creates, binds, deletes, or changes queue/DLQ
arguments. A failure is a bounded channel-unavailable category for a future
supervisor; this code still has no receive, acknowledgement, retry, database,
MinIO, or model action.

## First durable task-claim adapter

[`app/task_lease.py`](../../services/basic-pitch/app/task_lease.py) is the first pure PostgreSQL adapter.
Inside a later short transaction, it locks the exact `(job_id, basic-pitch,
stem_name)` task; locks the Job and rechecks the task to close an insertion
race; validates the retained `midi_processing` Job/stem-mode coordinate; and
compares the full durable JSONB outbox evidence with the parser-validated AMQP
message. Only then does it insert a PostgreSQL-timed `leased` task. A duplicate
or stale delivery changes nothing; a disagreement raises rather than becoming
safe to acknowledge. It has no database connection, transaction commit,
RabbitMQ acknowledgement, MinIO call, model invocation, or Kubernetes action.
The later start, renewal/recovery, output verification, and completion
transitions remain separate tasks.

[`app/first_claim.py`](../../services/basic-pitch/app/first_claim.py) now adds only the missing short
transaction scope around that pure adapter. It returns a `claimed`, duplicate,
or stale result only after the restricted PostgreSQL `write_cursor()` exits
normally and commits; inconsistencies, malformed rows, and database failures
escape the context so it rolls back. It has no AMQP frame, delivery tag,
acknowledgement, MinIO call, model work, or Kubernetes responsibility. The
next narrow boundary is the completed parser-plus-first-claim bridge below.

[`app/delivery_claim.py`](../../services/basic-pitch/app/delivery_claim.py) now joins the strict raw AMQP
parser with that completed first-claim transaction. It returns only the
validated request identifiers and PostgreSQL's committed `claimed`, duplicate,
or stale result—never raw body/properties, a delivery tag, database cursor, or
broker client. Parsing completes before PostgreSQL is touched, so malformed
deliveries cannot create or inspect a task. The bridge itself has no Pika
import, acknowledgement/rejection/retry operation, MinIO call, model action,
or Kubernetes behavior.

[`app/amqp_manual_ack.py`](../../services/basic-pitch/app/amqp_manual_ack.py) now applies the Pika-only
manual acknowledgement policy for at most one `basic_get(..., auto_ack=False)`
delivery. After the bridge commits a new lease, duplicate, or stale result, it
acknowledges the delivery; an acknowledged new lease carries its same
parser-validated message, while all other normal results carry neither. A
permanently malformed request is `basic_nack(..., requeue=False)` so RabbitMQ's
configured DLQ retains it. Database, durable-identity, malformed claim-result,
receive, and acknowledgement failures make no successful broker decision: they
propagate for a future reconnect supervisor and leave work eligible for
at-least-once redelivery. It starts no loop and makes no MinIO, model, or
Kubernetes call.

[`app/acknowledged_lease_execution.py`](../../services/basic-pitch/app/acknowledged_lease_execution.py)
now gates the existing post-claim execution coordinator behind exactly that
`ACKNOWLEDGED_LEASE` result. It passes the matching durable lease and strict
request message to the coordinator, which then owns the established
HeadObject/download/start/model/upload/completion ordering. Idle, duplicate,
stale, and malformed outcomes are stopped before any MinIO or model operation;
one known permanent pre-model input mismatch is instead classified and
committed as a terminal `failed` task before returning its compact terminal
result. The original RabbitMQ message was already acknowledged after the
durable lease, so this path neither retries nor DLQs it. A reviewed temporary
stem `HeadObject` or `GetObject` outage now records `retry_scheduled` on
attempts one/two or terminal `retry_exhausted` on attempt three; a no-row write
becomes normal ownership loss. Protocol errors, database/model errors,
post-model MIDI-store failures, and every other unclassified exception still
propagate without any second acknowledgement decision. This handoff accepts no
channel or delivery tag and makes no new broker decision or re-delivery.

[`app/receive_execute_once.py`](../../services/basic-pitch/app/receive_execute_once.py) now joins the
manual-ack adapter and post-ack gate for exactly one worker iteration. It
returns a compact `idle`, `acknowledged_no_work`, `malformed_rejected`, or
`executed` result; only `executed` contains the coordinator's result. It does
not catch a receive, acknowledgement, MinIO, database, or model failure, so a
later supervisor owns reconnection/retry/recovery policy without hiding an
incomplete action. This file still has no loop, sleep, connection lifecycle,
or Kubernetes behavior.

[`app/supervisor_backoff.py`](../../services/basic-pitch/app/supervisor_backoff.py) now provides only the
pure timing policy the future long-running worker will use. Normal progress
checks the next delivery immediately; an idle queue pauses for one second; a
runtime-classified retryable failure follows a bounded `1, 2, 4, 8, 16, 30`
second exponential sequence with up to 25% injected jitter; and a runtime-
classified fatal configuration error exits without a retry loop. A normal
iteration resets the local failure streak. This policy does not sleep, create
entropy, reconnect, catch an exception, inspect error types, or mutate any
RabbitMQ/PostgreSQL/MinIO/Kubernetes state. The next small task is to classify
real worker exceptions and connect this policy to a long-running runtime.

[`app/recovery_request.py`](../../services/basic-pitch/app/recovery_request.py) now rebuilds the same
strict `BasicPitchRequestedMessage` from the matching immutable, published
outbox event after PostgreSQL has granted a retry-scheduled task a fresh lease.
Its read-only query binds the full lease identity and PostgreSQL's current
expiry time while joining the task to its event, then repeats the exact JSONB
schema, input bucket/key, content type, byte count, and SHA-256 checks used by
the normal AMQP parser. A missing current pair is normal ownership loss; a
malformed or inconsistent row stops recovery. It claims no task, opens or
commits no transaction, contacts no broker/MinIO/model, and creates no
Kubernetes resource.

[`app/due_retry_recovery.py`](../../services/basic-pitch/app/due_retry_recovery.py) now provides the
missing short transaction scope. It claims at most one due retry, then reads
that fresh lease's durable request evidence before the same restricted
PostgreSQL context commits. `None` means the indexed claim found no due task.
If the reader unexpectedly cannot find a matching event after a claim, the
composition raises so the new lease rolls back rather than becoming stranded.
Only a matching committed lease/request pair returns; it neither polls nor
publishes RabbitMQ, calls MinIO, runs Basic Pitch, sleeps, or creates a
Kubernetes resource. The next narrow task is to pass that committed pair into
the existing post-claim execution path with the reviewed retry/failure policy.

[`app/recovered_retry_execution.py`](../../services/basic-pitch/app/recovered_retry_execution.py) now
does that one transport-free handoff. It accepts only a committed due-retry
lease/request pair and calls the shared post-lease policy also used after an
acknowledged RabbitMQ delivery. The policy retains the same sequence and
outcomes: verify/download the stem, guard `leased → running`, run Basic Pitch,
verify/upload MIDI, and record success; permanent input mismatches become a
terminal task result, while temporary pre-model storage outages are scheduled
or exhausted by attempt count. No second broker delivery or acknowledgement is
created. The gate itself does not claim work, touch MinIO/model directly, open
a transaction, sleep, loop, or create Kubernetes resources. The next narrow
task was fair selection between normal RabbitMQ work and PostgreSQL-scheduled
recovery work.

[`app/work_schedule.py`](../../services/basic-pitch/app/work_schedule.py) now supplies that fair choice as
a pure round-robin policy. Each bounded selection alternates between an
ordinary RabbitMQ delivery attempt and a due PostgreSQL retry-recovery attempt,
so sustained backlog on either source cannot starve the other. The preference
advances even when the selected source is idle: the runtime can immediately
check the other one and should use the supervisor's idle delay only after both
are empty. The state is a disposable in-memory preference, never task truth;
RabbitMQ and PostgreSQL remain authoritative after a Pod restart. This policy
does not poll, sleep, open a connection/transaction, invoke MinIO/Basic Pitch,
or create Kubernetes resources. The next narrow task is one bounded runtime
iteration that follows this selection and returns whether a source was idle or
made normal progress.

[`app/work_source_iteration.py`](../../services/basic-pitch/app/work_source_iteration.py) now provides
that bounded composition. It executes the selected RabbitMQ delivery or due
retry-recovery path at most once; if that path is empty, it immediately checks
the other source once. It reports `idle` only when both sources are empty, and
otherwise returns compact normal broker or recovered-execution evidence along
with the next fair schedule state. It catches no broker, database, MinIO, or
model failures, so the future supervisor can preserve their types for its
backoff/reconnect policy. It has no loop, sleep, connection lifecycle, or
Kubernetes behavior. The next narrow task is to adapt the existing supervisor
event/backoff policy to this new two-source iteration result.

[`app/supervisor_backoff.py`](../../services/basic-pitch/app/supervisor_backoff.py) now has the matching
fair-iteration event classifier. A `progress` result checks immediately and
resets a local backoff streak. Its `idle` event is emitted only after the fair
iteration confirmed both RabbitMQ and due-retry recovery were empty, so the
one-second idle delay cannot hold back ready retries behind an empty broker
poll. The classifier does not change schedule state, sleep, poll, reconnect,
or inspect/catch exceptions. The next narrow task is to classify real runtime
exceptions into the existing retryable-versus-fatal supervisor events.

[`app/supervisor_failure_classification.py`](../../services/basic-pitch/app/supervisor_failure_classification.py)
now performs that narrow mapping. Bad static RabbitMQ/PostgreSQL/MinIO
configuration or a missing Basic Pitch executable is fatal because waiting
cannot fix a Secret, topology, dependency, or worker image. Bounded RabbitMQ
connection/channel/receive and PostgreSQL availability wrappers are retryable.
Stem storage, model, MIDI-output, protocol, integrity, and unknown failures
remain unclassified: they need a task-specific lifecycle decision and must not
be hidden by a generic worker restart. The next narrow task is a small runtime
composition that calls one fair iteration, maps its normal result or one
classified exception to the existing supervisor decision, and leaves the
actual loop/sleep/reconnect behavior separate.

[`app/supervisor_step.py`](../../services/basic-pitch/app/supervisor_step.py) now provides that one-step
composition. A normal fair iteration advances its round-robin state, produces
an immediate-progress or both-sources-idle decision, and resets local backoff
as appropriate. A classified worker-level fault preserves fair preference,
then produces bounded retry backoff or fatal exit; unclassified task failures
still propagate unchanged. The step does not sleep, loop, reconnect, close a
channel, or create Kubernetes resources. The next narrow task is to provide
the injectable interruptible wait/action boundary needed by a later real
worker loop.

[`app/supervisor_action.py`](../../services/basic-pitch/app/supervisor_action.py) now provides that
injected action boundary. It does not call `sleep`; instead a future entrypoint
provides a shutdown-aware waiter (for example, a `threading.Event`) that says
whether termination arrived before the reviewed idle/backoff timeout. Immediate
progress continues without waiting, a timed-out wait continues, an interrupted
wait requests clean shutdown, and fatal configuration requests visible exit.
It starts no worker loop, reconnects no service, closes no resource, and makes
no Kubernetes change. The next narrow task is to compose a real entrypoint
loop around the existing step and action boundaries with explicit resource
lifecycle and shutdown semantics.

[`app/worker_runtime.py`](../../services/basic-pitch/app/worker_runtime.py) now provides that real,
testable worker loop over already-constructed restricted dependencies. It
checks shutdown before opening RabbitMQ, opens one private connection, creates
one `prefetch=1` passively verified channel, and repeatedly applies the
supervisor step/action boundaries until interrupted shutdown or fatal
configuration. The connection closes on every post-open normal and exceptional
path; task-specific errors still propagate after cleanup. A classified
retryable failure closes the connection *before* its bounded backoff. That
causes RabbitMQ to release any unacknowledged `prefetch=1` delivery for
at-least-once redelivery; once the delay ends, the runtime reopens and
passively verifies a new private channel. This avoids holding one delivery in
an old channel indefinitely after a temporary PostgreSQL/RabbitMQ outage. It
does not change PostgreSQL grants, task SQL, RabbitMQ topology, or task-state
policy. It deliberately does not read environment variables, construct
clients, install signal handlers, or call `sys.exit`.

[`app/worker_entrypoint.py`](../../services/basic-pitch/app/worker_entrypoint.py) now provides that
bootstrap composition. It validates fixed AMQP/PostgreSQL/MinIO configuration
from mounted environment values, creates only the restricted database and
MinIO clients, requires the fixed `/worker-scratch` volume to already exist as
a real directory, and delegates to the worker runtime. Clean shutdown maps to
status `0`; a runtime-detected configuration exit maps to `78`. It does not
catch configuration errors, call `sys.exit`, install signal handlers, or alter
Kubernetes resources. The next narrow task is a thin executable wrapper that
installs reviewed SIGTERM/SIGINT handling and returns this entrypoint status.

[`app/worker_main.py`](../../services/basic-pitch/app/worker_main.py) is that thin executable wrapper.
It turns Kubernetes `SIGTERM` and interactive `SIGINT` into a single
in-memory `threading.Event`, passes that event through the worker's
shutdown-wait protocol, and returns the bootstrap's explicit status. Its
signal handler sets only the event: normal runtime code owns connection
closure and durable task behavior. Known static bootstrap configuration errors
produce the non-sensitive container diagnostic and status `78`; task-specific
and unexpected runtime errors still propagate after their existing cleanup.
The next narrow task is to select this wrapper explicitly in the Basic Pitch
image command; no image or Deployment change has been made here.

[`requirements.lock`](../../services/basic-pitch/requirements.lock) now records the complete
hash-verified Linux/ARM64/Python 3.11 dependency closure for the future Basic
Pitch image: Basic Pitch, TensorFlow CPU, MIDI/audio libraries, restricted
MinIO/RabbitMQ/PostgreSQL clients, and all transitive packages. Basic Pitch
0.4.0 requires TensorFlow 2.15 on Linux, which needs Python 3.11 rather than
the other services' Python 3.12 runtime. The TensorFlow selector uses a wheel
called `tensorflow-cpu-aws` on Linux/ARM64; it is an upstream CPU binary name,
not an AWS integration or credential source. The adjacent
[`image source lock`](../../images.lock.yaml) pins the separate official
Python 3.11.16 base-image digest. The reviewed published worker is now pinned
as `images.basic-pitch`; the Deployment separately records which immutable
worker build is live in the cluster.

[`Dockerfile`](../../services/basic-pitch/Dockerfile) now builds the local CPU worker in two stages. Its
validation stage installs the complete lock with `--require-hashes`, runs all
worker unit tests, verifies the fixed `/usr/local/bin/basic-pitch` executable,
and confirms the wheel bundles a TFLite model rather than allowing a running
Pod to fetch one. Its final stage copies only validated runtime packages, the
fixed CLI, and worker code; it runs as an unprivileged UID/GID `10004` and does
not create `/worker-scratch`, so a missing future `emptyDir` mount fails safely
at startup. The exec-form `ENTRYPOINT ["python", "-m", "app.worker_main"]`
makes the signal-aware wrapper PID 1. The reviewed published CPU image is now
recorded as `images.basic-pitch` in [`../../images.lock.yaml`](../../images.lock.yaml),
and the live Deployment below is pinned to that exact digest. KEDA owns the
replica count independently of the image version.

[`../../scripts/build-basic-pitch-image.sh`](../../scripts/build-basic-pitch-image.sh)
is that non-interactive local build-and-push boundary. It accepts no runtime
credentials, verifies the dedicated `clouddsp-registry` container exists,
builds the Dockerfile specifically for the local Linux/ARM64 nodes, and prints
both the pushed immutable repository digest and Docker's uncompressed size.
Its Docker build reruns the unit/model validation stage; the script itself does
not call `kubectl`, modify an image lock, or create a workload. The separately
reviewed image-lock record captures its output, and the Deployment consumes
that immutable reference in the local cluster.

The current `0.1.2-tempo-resolution` CPU image passed all 213 worker unit tests
plus its TensorFlow/TFLite model checks. Its immutable local-registry reference
is
`clouddsp-registry.localhost:5001/basic-pitch@sha256:e4ef1fc639a3571b3b8d4b2ab9cd0dacf3bb62ae1d194e3fad48c10066b0648a`
and its uncompressed Docker size is 520,356,879 bytes (496.25 MiB). Along with
the prior verified MIDI artifact evidence, this image records a best-effort
librosa BPM candidate from the hash-verified stem. PostgreSQL v009 resolves
that candidate together with ADTOF evidence and updates the parent Job's tempo
atomically with MIDI task completion. The candidate is advisory: failure to
estimate BPM does not fail MIDI extraction.

## Basic Pitch Deployment manifest

[`basic-pitch-deployment.yaml`](../../services/basic-pitch/basic-pitch-deployment.yaml) is the applied
internal-only worker controller. It uses the locked CPU-only image digest,
KEDA-managed replicas, one-message RabbitMQ prefetch, private
PostgreSQL/MinIO/RabbitMQ Service DNS, and only the three existing
app-namespace runtime Secrets. The
Pod uses no ServiceAccount token, no Service or Ingress, no CUDA resource, and
no administrator/bootstrap Secret. Its root filesystem is read-only; bounded
`emptyDir` volumes at `/worker-scratch`, `/tmp`, and the worker HOME contain
all transient local files. The 330-second termination grace period is longer
than the five-minute fixed Basic Pitch CLI deadline so a normal SIGTERM can let
the current bounded operation clean up before RabbitMQ/PostgreSQL recovery is
needed.

The v0.1.2 tempo-resolution image is installed in the local cluster through
the Basic Pitch Helm release. Its dual-trigger scaler is Ready and normally
inactive, so the Deployment intentionally has zero Pods when the queue and
durable task count are both empty. A new Basic Pitch delivery or due retry
will scale up a Pod from the digest-pinned image.

## KEDA queue and durable-task scaling policy

[`basic-pitch-scaledobject.yaml`](../../services/basic-pitch/basic-pitch-scaledobject.yaml) is the
installed KEDA policy for this worker. It leaves the long-running Deployment
responsible for the worker process and Pod security, while KEDA's generated HPA
owns only the Deployment replica count. The policy observes the private
`clouddsp.basic-pitch.requests` queue through RabbitMQ's private management
ClusterIP using the dedicated read-only monitoring identity. It also counts
active and due Basic Pitch tasks through the existing read-only PostgreSQL
observer role. Neither trigger receives the Basic Pitch worker's credentials.

The policy has a five-second scale-from-zero polling interval, a one-message
target that matches the worker's `prefetch=1`, a zero-to-three replica range,
and a one-minute idle cooldown. Its HPA may add up to three Pods per
15-second control interval, so a five-stem Demucs burst does not wait through
several 30-second one-Pod increments. HPA holds the previous desired count
for six minutes before scale-in, longer than the worker's five-minute model
deadline; it cannot know which Pod owns a remaining task. The worker ACKs
after committing its lease, before CPU inference. RabbitMQ depth can be zero
during active tasks; the PostgreSQL trigger keeps their Pods present and wakes
the worker for due database retries. The Deployment omits a handwritten
`replicas` field so KEDA alone controls `/scale`. Subsequent delivery changes
must use the Helm chart rather than applying this source manifest directly.

An earlier queue-only policy and its three-request burst Job ran on
2026-09-20. It confirmed the full `0 → 3 → 0` lifecycle: three valid durable
requests reached the configured three-Pod ceiling, completed through the normal
worker path, then returned to zero after the idle cooldown. Future policy
changes still require an explicit apply-and-observe task; this source never
applies itself.

Run the isolated contract tests from the repository root:

```bash
PYTHONPATH=k8Deployment/kubernetes/services/basic-pitch \
  python3 -m unittest discover \
  --start-directory k8Deployment/kubernetes/services/basic-pitch/tests \
  --pattern 'test_*.py' \
  --verbose
```
