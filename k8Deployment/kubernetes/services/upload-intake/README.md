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

### Current staged state

The MinIO StatefulSet now contains the complete native AMQP target
configuration and reads its password-bearing URI from the separate local
Secret. Its enable setting remains off in this task, so MinIO does not connect
to RabbitMQ, declare the exchange, or publish notifications. A later explicit
activation task must first grant MinIO the narrow exchange-declaration
permission it needs, enable this target, and attach the bucket notification
rule. Staging the configuration separately keeps that rollout observable and
does not begin processing existing or newly uploaded objects.

MinIO natively publishes S3-compatible bucket notifications to AMQP 0-9-1
services such as RabbitMQ. The future MinIO StatefulSet revision will configure
one target identified as `intake`:

| Configuration | Contracted value | Reason |
| --- | --- | --- |
| target identifier | `intake` | Produces the stable MinIO target ARN `arn:minio:sqs::intake:amqp`. |
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
will attach the bucket rule with the target ARN and confirm it using
`mc event ls`; nobody configures it manually in the MinIO console.

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
credentials. Its policy will allow only `s3:GetObject` below
`clouddsp-uploads/uploads/*`; it grants no anonymous access, bucket
administration, deletion, or worker-output access.

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

The **first implementation stops after this state transition**. It must not
publish `demucs.requested` directly: publishing after a database commit leaves
a crash window where work is lost, while publishing first could process a job
whose state was never committed. A later small migration adds a transactional
outbox row; a dispatcher publishes that durable row to RabbitMQ.

## Reconciliation is mandatory

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

Copy both files into the ignored `k8Deployment/.local/` directory before the
later bootstrap task. The MinIO template deliberately contains the publisher
password twice: once as raw text for the bootstrap Job to create the broker
user, and once percent-encoded within MinIO's required AMQP URI. They must be
the same password in two representations. Neither template creates a broker
user or a live Kubernetes Secret until its local copy is applied.

## Prepared topology bootstrap Job

[`../rabbitmq/rabbitmq-source-intake-bootstrap-job.yaml`](../rabbitmq/rabbitmq-source-intake-bootstrap-job.yaml)
is the prepared, **unapplied** administrator Job. It uses RabbitMQ's pinned
management client inside `clouddsp-data` to import the immutable v001
definition, create or deliberately rotate both application users, assign the
reviewed regex permissions, and print only non-sensitive topology/permission
metadata. It is not a long-running controller and has no Kubernetes API
credentials.

[`../rabbitmq/rabbitmq-upload-intake-bootstrap-credentials.secret.example.yaml`](../rabbitmq/rabbitmq-upload-intake-bootstrap-credentials.secret.example.yaml)
now documents that temporary data-namespace copy. Its ignored local
materialization must exactly match the already-applied `clouddsp-app` runtime
Secret. Kubernetes prohibits the Job from mounting that application-namespace
Secret directly. After applying this temporary Secret, we can apply the
immutable v001 topology ConfigMap and bootstrap Job together, inspect the
Job’s output, then delete the temporary Secret.
