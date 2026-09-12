# Basic Pitch worker foundation

This directory is the Kubernetes-local Basic Pitch worker track. It does not
reuse the preserved cloud Lambda handler: later layers will explicitly compose
RabbitMQ, PostgreSQL, MinIO, and Basic Pitch dependencies for a long-running,
least-privilege Kubernetes consumer.

## Request and output contract

[`app/basic_pitch_requested_message.py`](app/basic_pitch_requested_message.py)
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
[`v005 processing-task migration`](../api/job-api-schema-migration-v005-basic-pitch-processing-tasks-configmap.yaml)
is the completed database-only prerequisite. Its companion Job changed no
runtime component: it merely permits one `basic-pitch` task for each allowed non-drum
stem and its exact `stems/{job_id}/{stem_name}.wav` input key. It leaves the
existing `(job_id, stage, stem_name)` idempotency key and every retry/lease rule
in place. The least-privilege PostgreSQL identity and first guarded task-claim
adapter below now use that schema; a runtime, MinIO verification, and task
completion remain separate work.

## PostgreSQL identity

The completed
[`Basic Pitch database bootstrap Job`](basic-pitch-database-bootstrap-job.yaml)
used the PostgreSQL administrator only long enough to create the independent
`clouddsp-basic-pitch` login. Its permanent app-namespace credential template
is [`basic-pitch-database-credentials.secret.example.yaml`](basic-pitch-database-credentials.secret.example.yaml);
the temporary data-namespace duplicate is
[`basic-pitch-database-bootstrap-credentials.secret.example.yaml`](basic-pitch-database-bootstrap-credentials.secret.example.yaml).
The role may create and lifecycle-manage a processing task, and read the small
Job/event fields needed to compare a delivery with durable state. It cannot
read an owner or source coordinate, update a Job, write/lease/publish an outbox
event, delete records, or run DDL. PostgreSQL column grants do not encode the
stage row predicate, so the later lease adapter must always bind both
`stage = 'basic-pitch'` and the current lease token in its guarded statements.
Its grant report confirmed task creation/lease updates and limited Job/outbox
reads while reporting denied input replacement, owner read, Job update, outbox
publication update/insertion, and deletion. The temporary data-namespace
Secret is now deleted; the permanent app-namespace runtime Secret remains. No
worker Deployment or database connection is created by the identity alone.

## MinIO identity

The applied immutable
[`Basic Pitch MinIO policy`](../minio/minio-basic-pitch-artifacts-policy-v001-configmap.yaml)
is the object-storage identity boundary. It permits `GetObject` on known
private `stems/*` objects and `GetObject`/`PutObject` plus multipart
inspection/cleanup on `midi/*`, so a later worker can upload and then verify
its own deterministic output. It deliberately grants no bucket listing,
deletion, upload-prefix access, bucket administration, anonymous access, or
browser presigning. Static MinIO prefix IAM cannot know which task lease the
Pod owns, so the future adapter must still compare the durable Job/event/task
identity before using an input or output key.

The permanent app-namespace credential template is
[`basic-pitch-minio-credentials.secret.example.yaml`](basic-pitch-minio-credentials.secret.example.yaml).
The deliberately temporary data-namespace duplicate is
[`minio-basic-pitch-artifacts-bootstrap-credentials.secret.example.yaml`](../minio/minio-basic-pitch-artifacts-bootstrap-credentials.secret.example.yaml).
The completed bootstrap Job created/rotated the user and attached the policy;
the temporary data-namespace Secret has been deleted, while the app runtime
Secret remains. No worker connection or object is created by that bootstrap.

The completed
[`MinIO artifacts bootstrap Job`](../minio/minio-basic-pitch-artifacts-bootstrap-job.yaml)
runs six ordered administrative init containers: configure a root alias, wait
for MinIO, import the immutable policy, create/rotate the restricted user,
attach/inspect the policy, then delete the temporary root alias before the Job
can complete. It has no worker credentials beyond the one restricted pair and
does not access PostgreSQL, RabbitMQ, or objects.

## MinIO client configuration

[`app/minio_client.py`](app/minio_client.py) is the next pure runtime boundary.
It reads the future Pod's non-secret internal Service configuration and the
existing `clouddsp-basic-pitch-minio-credentials` Secret, then lazily constructs
one explicit Boto3-compatible S3 client. It accepts only
`http://clouddsp-minio.clouddsp-data.svc:9000`, the private
`clouddsp-uploads` bucket, `us-east-1`, and path-style S3 addressing. The
factory passes the restricted access/secret key directly, so Boto3 cannot use
ambient AWS credentials, a host profile, or browser-facing Traefik routing.
Creating the client performs no S3 request; Boto3 imports only when the factory
is called, and the later dependency/image task will pin and install it. The
lease-bound MinIO `HeadObject` verifier in [`app/stem_object.py`](app/stem_object.py)
now makes that one metadata-only request. It permits only the claimed
`stems/{job_id}/{stem_name}.wav` coordinate, compares its exact WAV type and
byte length with the durable request, and requires the complete immutable
Demucs metadata inventory—including job/stem/mode, size, and SHA-256—to agree.
An absent object is a bounded permanent result; an outage remains retryable;
malformed metadata is never treated as valid media. It does not download a
byte, renew/start a task, acknowledge RabbitMQ, invoke Basic Pitch, write MIDI,
or update PostgreSQL.

[`app/stem_download.py`](app/stem_download.py) is the next bounded I/O layer.
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
[`app/task_lease.py`](app/task_lease.py). It uses one parameterized PostgreSQL
statement that binds the task ID, Job ID, Basic Pitch stage, exact stem name,
and lease token, and requires the PostgreSQL clock to show that the lease is
still active. A timestamp returned from the committed future transaction is
the only permission a later runtime may use to start the model. A zero-row
result means the lease was expired, recovered, or changed, so the worker must
stop without producing MIDI. It opens no connection or transaction itself;
the separate PostgreSQL client/composition task will supply that short scope.

[`app/postgresql.py`](app/postgresql.py) now supplies the client half of that
scope. It accepts only the private
`clouddsp-postgresql.clouddsp-data.svc:5432` Service, authoritative
`clouddsp_job_api` database, and the restricted `clouddsp-basic-pitch` login
from the existing permanent app Secret. It lazily imports Psycopg and opens a
fresh dictionary-row connection per action with a three-second connect timeout
and five-second PostgreSQL statement timeout. `write_cursor()` commits on a
normal exit and rolls back on any error; it ends before MinIO or model work.
It creates no Deployment, no connection at import time, and no task/model
transition by itself.

[`app/stem_task_start.py`](app/stem_task_start.py) now composes the temporary
verified stem, that short transaction, and the pure start statement. It first
downloads/hashes the exact claimed private stem while the task remains
`leased`, then commits `running` with the same token. Only after the transaction
commits does its context manager yield the generic temporary `stem.wav` to a
future Basic Pitch process. If ownership is gone, it deletes the scratch stem
before yielding `None`; database or protocol failures also propagate only after
cleanup.

[`app/basic_pitch_process.py`](app/basic_pitch_process.py) now builds and runs
the fixed Basic Pitch CLI command
`/usr/local/bin/basic-pitch <fresh-output-directory> <temporary-stem.wav>`.
It makes a mode-0700 `midi-output` sibling in the worker-owned temporary
directory, permits no caller-selected model/flags/paths, starts a shell-free
process group with no stdin or retained stdout/stderr, and enforces a default
five-minute CPU deadline. A zero exit returns only the expected local
`stem_basic_pitch.mid` coordinate; it is not yet artifact proof.

[`app/midi_artifact.py`](app/midi_artifact.py) is the completed local artifact
proof boundary. It accepts only that fixed process command/output coordinate,
opens one non-symlink regular file, limits it to 16 MiB, validates its Standard
MIDI File header and exact declared `MTrk` chunk layout, then streams SHA-256
in 64 KiB pieces. Its returned local path, byte count, `audio/midi` MIME type,
and checksum are evidence for a later MinIO upload boundary—not proof of an
uploaded object, a completed task, or a RabbitMQ acknowledgement.

[`app/midi_output_object.py`](app/midi_output_object.py) is the completed
object-plan boundary. It joins the strict request, matching current task lease,
and freshly revalidated local MIDI evidence to form only
`midi/{job_id}/{non-drum-stem}.mid`. Its immutable S3 metadata records the
schema/producer, Job/task/request IDs, stem and mode, MIDI size/checksum, and
input-stem checksum. It does not create a MinIO client or send a request.

[`app/midi_artifact_upload.py`](app/midi_artifact_upload.py) is the completed
restricted MinIO write boundary. It revalidates the fixed private key and all
metadata, repeats MIDI framing/hash proof, hashes the current file once before
upload and again as the Boto3-compatible client consumes it, and sends exactly
one `PutObject` request. A success receipt exists only when the client consumed
the planned byte count and SHA-256. It intentionally excludes raw SDK output
and ETags, and does not update PostgreSQL, acknowledge RabbitMQ, or create
Kubernetes resources.

[`app/midi_artifact_head_object.py`](app/midi_artifact_head_object.py) is the
completed stored-object boundary. It makes exactly one MinIO `HeadObject` call
for the upload receipt's fixed key and requires the stored length, `audio/midi`
type, and complete case-normalized provenance metadata to match the immutable
plan. It returns only stable bucket/key/length/SHA-256 evidence; raw S3
responses, paths, credentials, and ETags remain out of later task state.

[`app/midi_task_completion.py`](app/midi_task_completion.py) now defines that
pure completion statement. It validates that stored output evidence names the
same Job and non-drum stem as the Basic Pitch task lease, then updates only a
current unexpired `running` task with the same lease token to `succeeded`. It
clears the active lease fields and records PostgreSQL's completion timestamp;
`None` is the normal stale-owner result. It deliberately does **not** change
the Job from `midi_processing`: a later aggregate must wait for every Basic
Pitch and ADTOF task.

[`app/midi_task_completion_commit.py`](app/midi_task_completion_commit.py)
is that completed transaction composition. It opens the existing restricted
PostgreSQL `write_cursor()` only after all model and MinIO work has finished,
calls the pure completion statement inside it, and returns a success result
only after normal context exit commits. A normal stale-owner `None` commits no
mutation; any database or evidence exception leaves the scope and rolls back.
It does not make MinIO/RabbitMQ/model/Kubernetes calls or update the overall
Job.

[`app/basic_pitch_task_execution.py`](app/basic_pitch_task_execution.py) now
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
permanent [`runtime template`](basic-pitch-rabbitmq-credentials.secret.example.yaml)
belongs in `clouddsp-app`, while the temporary
[`bootstrap template`](../rabbitmq/rabbitmq-basic-pitch-consumer-bootstrap-credentials.secret.example.yaml)
belongs in `clouddsp-data`. The ignored local copies are applied. A later
administrator bootstrap Job gave this user empty configure/write regular
expressions and read access only to `clouddsp.basic-pitch.requests` in
`/clouddsp`, so it can consume and acknowledge its own requests but cannot
alter topology, publish a message, read retry/DLQ queues, or use the management
API.
The completed
[`RabbitMQ consumer bootstrap Job`](../rabbitmq/rabbitmq-basic-pitch-consumer-bootstrap-job.yaml)
waits for both the private management API and the already-imported main request
queue before it declares the untagged user and applies those exact vhost regex
permissions. It then lists only queue/permission metadata for review. Its
successful report confirmed the exact main Basic Pitch queue and empty
configure/write rules. The temporary data-namespace Secret is deleted, while
the app runtime Secret remains for a future worker.

[`app/amqp_connection.py`](app/amqp_connection.py) now turns that permanent
runtime Secret into one bounded Pika connection configuration. It permits only
the private `clouddsp-rabbitmq.clouddsp-data.svc:5672` ClusterIP listener,
`/clouddsp`, the fixed `clouddsp.basic-pitch.requests` queue, and the
restricted `clouddsp-basic-pitch` identity; it hides the password from normal
representations and rechecks direct dataclass construction before importing
Pika. The current local profile has plain private AMQP only: the module
deliberately supplies no TLS option because no AMQPS listener/certificate
configuration exists yet. It opens no channel, consumes no message, declares
no topology, and makes no acknowledgement decision.

[`app/amqp_channel.py`](app/amqp_channel.py) now provides that setup. It calls
`basic_qos(prefetch_count=1)`, so one CPU-bound worker Pod never holds several
unacknowledged delivery/lease candidates while processing a single stem, then
uses `queue_declare(..., passive=True)` to read-check the exact existing Basic
Pitch queue. Passive mode never creates, binds, deletes, or changes queue/DLQ
arguments. A failure is a bounded channel-unavailable category for a future
supervisor; this code still has no receive, acknowledgement, retry, database,
MinIO, or model action.

## First durable task-claim adapter

[`app/task_lease.py`](app/task_lease.py) is the first pure PostgreSQL adapter.
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

[`app/first_claim.py`](app/first_claim.py) now adds only the missing short
transaction scope around that pure adapter. It returns a `claimed`, duplicate,
or stale result only after the restricted PostgreSQL `write_cursor()` exits
normally and commits; inconsistencies, malformed rows, and database failures
escape the context so it rolls back. It has no AMQP frame, delivery tag,
acknowledgement, MinIO call, model work, or Kubernetes responsibility. The
next narrow boundary is the completed parser-plus-first-claim bridge below.

[`app/delivery_claim.py`](app/delivery_claim.py) now joins the strict raw AMQP
parser with that completed first-claim transaction. It returns only the
validated request identifiers and PostgreSQL's committed `claimed`, duplicate,
or stale result—never raw body/properties, a delivery tag, database cursor, or
broker client. Parsing completes before PostgreSQL is touched, so malformed
deliveries cannot create or inspect a task. The bridge itself has no Pika
import, acknowledgement/rejection/retry operation, MinIO call, model action,
or Kubernetes behavior.

[`app/amqp_manual_ack.py`](app/amqp_manual_ack.py) now applies the Pika-only
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

[`app/acknowledged_lease_execution.py`](app/acknowledged_lease_execution.py)
now gates the existing post-claim execution coordinator behind exactly that
`ACKNOWLEDGED_LEASE` result. It passes the matching durable lease and strict
request message to the coordinator, which then owns the established
HeadObject/download/start/model/upload/completion ordering. Idle, duplicate,
stale, and malformed outcomes are stopped before any MinIO or model operation;
coordinator failures still propagate for the future supervisor. This handoff
accepts no channel or delivery tag and makes no new broker decision.

[`app/receive_execute_once.py`](app/receive_execute_once.py) now joins the
manual-ack adapter and post-ack gate for exactly one worker iteration. It
returns a compact `idle`, `acknowledged_no_work`, `malformed_rejected`, or
`executed` result; only `executed` contains the coordinator's result. It does
not catch a receive, acknowledgement, MinIO, database, or model failure, so a
later supervisor owns reconnection/retry/recovery policy without hiding an
incomplete action. This file still has no loop, sleep, connection lifecycle,
or Kubernetes behavior.

[`app/supervisor_backoff.py`](app/supervisor_backoff.py) now provides only the
pure timing policy the future long-running worker will use. Normal progress
checks the next delivery immediately; an idle queue pauses for one second; a
runtime-classified retryable failure follows a bounded `1, 2, 4, 8, 16, 30`
second exponential sequence with up to 25% injected jitter; and a runtime-
classified fatal configuration error exits without a retry loop. A normal
iteration resets the local failure streak. This policy does not sleep, create
entropy, reconnect, catch an exception, inspect error types, or mutate any
RabbitMQ/PostgreSQL/MinIO/Kubernetes state. The next small task is to classify
real worker exceptions and connect this policy to a long-running runtime.

Run the isolated contract tests from the repository root:

```bash
PYTHONPATH=k8Deployment/kubernetes/services/basic-pitch \
  python3 -m unittest discover \
  --start-directory k8Deployment/kubernetes/services/basic-pitch/tests \
  --pattern 'test_*.py' \
  --verbose
```
