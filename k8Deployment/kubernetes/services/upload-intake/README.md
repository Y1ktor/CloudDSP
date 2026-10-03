# Upload intake

The implemented internal consumer verifies direct browser uploads after MinIO
notifications and atomically records the durable Demucs handoff in PostgreSQL.
It has no HTTP Service/Ingress; the Job API owns authentication, upload
contracts, and browser responses. Local quota enforcement remains future work.

## Runtime interfaces

```text
Job API authorizes private multipart POST -> MinIO source object
  -> native MinIO notification -> RabbitMQ source-intake queue
  -> retained pending Job + HeadObject verification
  -> source_uploaded + one Demucs outbox event in PostgreSQL
  -> dispatcher -> Demucs
```

| Interface | Current contract |
| --- | --- |
| Runtime | `python -m app.consumer_runtime`, one outbound consumer Deployment in `clouddsp-app`. |
| MinIO | Private `clouddsp-uploads`, notification prefix `uploads/`, target `arn:minio:sqs::INTAKE:amqp`. |
| Broker | Vhost `/clouddsp`; durable direct exchange `clouddsp.source-events`, key `source.upload.created`, quorum queue `clouddsp.source-intake`, retained DLQ. |
| Event | Bounded S3-compatible `Records` envelope; direct-upload parser accepts `s3:ObjectCreated:Post` and decodes the key exactly once. |
| Source | Stored PostgreSQL coordinate `uploads/{job_id}/<one filename>`, never a URL or an arbitrary message-selected path. |
| Durable result | `source_uploaded=true`, Job status `source_uploaded`, incremented revision, and one pending `demucs.requested` outbox row keyed `(job_id, 'demucs', '')`. |

A notification is an untrusted hint. The [parser](app/minio_event.py) narrows
bucket, event, path, canonical Job ID, and encoding before any I/O. The
[database adapter](app/database_transition.py) selects only retained,
non-deleted `direct_upload` Jobs still `upload_pending` with `source_uploaded=false`
and an exact matching bucket/key. A missing, expired, deleted, already-advanced,
or unrelated Job is a safe no-op; the event cannot revive or replace it.

[HeadObject verification](app/object_storage.py) compares the actual stored
object's approved MIME type, positive byte count within 256 MiB, and `job-id` /
`stem-mode` metadata with the authoritative Job. A permanent mismatch can record
a guarded failed state with a fixed category. Network/service authorization
failures remain operational failures, not fabricated invalid-media results.
Codec and 500-second duration validation happen in Demucs after download.

## Transactions, ACK, and duplicates

The [message handler](app/message_handler.py) reads durable state in a short
scope, performs MinIO HEAD outside the transaction, then opens a short write
transaction with the previously read revision as an optimistic guard. Success
and the initial outbox insert commit together. The named unique outbox constraint
prevents a second logical Demucs event under duplicate notification or race.
A newer Job state cannot be overwritten by a delayed verification response.

- The [AMQP adapter](app/amqp_consumer.py) uses manual ACK and prefetch one.
  **ACK occurs only after the handler returns with every actionable record's
  durable outcome committed.** Intake has no long ML task claim to ACK early.
- Invalid/irrelevant records and authoritative no-work outcomes are safely ACKed;
  repeatedly retrying them would not create valid work. Permanent verification
  failures are ACKed only after their guarded failure outcome commits.
- If a later record fails transiently, the message stays unacknowledged. Earlier
  committed records can be redelivered without creating duplicate state/outbox.
- The runtime closes/reconnects with bounded delays on reviewed service failures.
  RabbitMQ requeues unacknowledged deliveries and its delivery-limit/DLQ policy
  retains repeated failures. The consumer does not publish delayed retries.
- The consumer never publishes processing requests directly. The
  [dispatcher](../dispatcher/README.md) leases the committed outbox and uses
  publisher confirmation, preserving the PostgreSQL-to-broker crash boundary.

MinIO's native notification queue is persisted on its data PVC so unavailable
RabbitMQ does not require an in-memory-only handoff. Events and that queue remain
local storage, not backups; deleting the cluster's data removes them.

## Security and image

MinIO uses a publish-only broker identity; intake has a separate consume-only
identity. Its restricted PostgreSQL role can perform only the needed guarded
source-state/outbox operations. Its MinIO identity reads known private source
objects without listing, writing, deleting, managing buckets, or presigning
browser access. Exact Job/coordinate checks remain required above prefix IAM.

The Pod mounts only restricted runtime Secrets, uses private Service DNS,
runs non-root with a read-only root filesystem and capabilities dropped, and
has no ServiceAccount API token. RabbitMQ's ingress NetworkPolicy admits the
selected consumer to AMQP; it does not establish complete Pod egress or
PostgreSQL/MinIO network isolation. The [operator guide](../../scripts/README.md)
initializes ignored local credentials; server secrets never enter Helm values
or the frontend bundle.

The [Dockerfile](Dockerfile) and [requirements.lock](requirements.lock) package
the local Python consumer. [images.lock.yaml](../../images.lock.yaml) and
[chart values](../../helm/upload-intake/values.yaml) pin its immutable image.
The [Helm release](../../helm/upload-intake/README.md) owns only the Deployment;
MinIO notification settings, broker topology, identities, and database migrations
are ordered dependencies managed outside that release.

## Install and verify

Run from the repository root. The fresh full command installs dependencies,
notification/broker configuration, policies, schema, and runtime credentials:

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-platform
./k8Deployment/kubernetes/scripts/deploy-local.sh verify
```

On a prepared cluster where this release/resources are absent, install the
component; use `verify` for an existing Helm release:

```bash
./k8Deployment/kubernetes/scripts/upload-intake-release.rb install
./k8Deployment/kubernetes/scripts/upload-intake-release.rb verify
```

The helper checks prerequisite identity/topology/IAM stages and rejects conflicts.
Verification checks the locked image, rendered/source/live specs, Helm ownership,
stored manifest, and ready running Pod. It does not submit a new upload.
Raw workload manifests are comparison baselines and must not be applied over
Helm-owned objects. `plan`/`adopt` are the historical pre-adoption paths.

## Source-to-outbox smoke

The [smoke runbook](../../tests/source-intake-smoke/README.md) is the complete
ordered procedure, including fixture cleanup and versioned Helm pause/restore
commands. It requires an empty source-intake queue and **both dispatchers paused**;
otherwise they can drain the pending outbox assertion and send the tiny fixture
toward real workers. Use that runbook's preflight and pause first, then run:

```bash
kubectl --context k3d-clouddsp-local -n clouddsp-data create -f k8Deployment/kubernetes/tests/source-intake-smoke/source-to-outbox-smoke-job.yaml
kubectl --context k3d-clouddsp-local -n clouddsp-data wait job/source-to-outbox-smoke --for=condition=complete --timeout=300s
kubectl --context k3d-clouddsp-local -n clouddsp-data logs job/source-to-outbox-smoke
```

The test uses the normal authenticated Job API and presigned multipart POST,
checks source verification and exactly one pending outbox event, then deliberately
repeats the notification. Its cleanup removes only its generated user/client,
object, and database row. After a passing result and reported cleanup, delete
only the test Job and restore both dispatchers using the runbook. After failure,
inspect cleanup state before restoration or rerun; a retained fixture may become
publishable when dispatchers resume. The release helper has no `smoke` mode.

## One-job reconciliation and limitations

If a retained pending direct upload exists but its notification was missed, the
implemented operator command reuses the running intake Pod's restricted clients
and the normal verification/transaction handler:

```bash
./k8Deployment/kubernetes/scripts/reconcile-one-upload-intake-job.sh CANONICAL_JOB_UUID
```

It reads the exact stored source key from PostgreSQL; it accepts no bucket/path
argument, creates no Secret/Job, and performs no bucket scan. Repeated calls are
idempotent after the source transition. It cannot restore a missing object or
recover deleted/expired Jobs.

Automatic periodic bounded reconciliation and a reviewed delayed-retry publisher
remain future work; the current runtime must not be described as implementing
them. This consumer's implemented scope is direct uploads, not a yt-dlp service.
Fresh deployment creates working empty state and does not restore earlier data.
For diagnosis without exposing credentials:

```bash
kubectl --context k3d-clouddsp-local -n clouddsp-app logs deployment/clouddsp-upload-intake --tail=100
./k8Deployment/kubernetes/scripts/upload-intake-release.rb verify
```

The complete initial contract, staged adapter implementation, and historical
bootstrap/trial records are preserved in
[historical implementation notes](../../docs/history/service-upload-intake-implementation-notes.md).
