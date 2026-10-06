# Outbox dispatchers

The implemented dispatchers bridge PostgreSQL's transactional outbox to private
RabbitMQ processing queues. Upload intake and Demucs commit the next stage's
outbox row with their durable result; dispatchers publish it later with broker
confirmation. Delivery is at-least-once, and workers claim idempotent durable tasks.

## Current releases and runtime interfaces

The full bootstrap installs both releases, normally with one Pod each in
`clouddsp-app`. They share the restricted publisher identity and outbox but keep
separate Helm ownership and reviewed image-lock entries.

| Release | Entrypoint | Routes | Image-lock entry |
| --- | --- | --- | --- |
| `clouddsp-dispatcher` | `python -m app.dispatcher_runtime` | Demucs only | `dispatcher-demucs-only` |
| `clouddsp-generic-dispatcher` | `python -m app.dispatcher_generic_runtime` | Demucs, Basic Pitch, ADTOF | `dispatcher` |

The generic Deployment explicitly selects its entrypoint; the image's default
entrypoint remains Demucs-only. Concurrent claims use row leases and
`SKIP LOCKED`, so the overlapping Demucs route does not require a singleton
publisher or create two logical outbox rows.

There is no HTTP Service/Ingress or inbound request interface. The runtime
connects to PostgreSQL and RabbitMQ through private Service DNS names. It does
not read MinIO, run models, consume worker deliveries, or create Kubernetes Jobs.
The [Dockerfile](Dockerfile) and [requirements.lock](requirements.lock) define
the local Python image. [images.lock.yaml](../../images.lock.yaml) and each
chart's values pin current digests; old readable tags are not deployment inputs.

## Processing request contract

All routes use the durable direct exchange `clouddsp.processing-events` in
virtual host `/clouddsp`:

| Durable stage / stem | Routing key | Queue |
| --- | --- | --- |
| `demucs` / empty string | `demucs.requested` | `clouddsp.demucs.requests` |
| `basic-pitch` / approved non-drum stem | `basic-pitch.requested` | `clouddsp.basic-pitch.requests` |
| `adtof` / `drums` | `adtof.requested` | `clouddsp.adtof.requests` |

The immutable outbox key is `(job_id, stage, stem_name)`. A request carries the
validated outbox payload without adding browser fields, credentials, URLs,
owner identity, or publication state. The finite contract builders reject
unsupported stage/event/stem combinations; queue names cannot be supplied by a
browser or arbitrary event body.

| Payload | Required version-1 shape |
| --- | --- |
| Demucs | `schema_version=1`, canonical `job_id`, `source={bucket,object_key}`, and `stem_mode` (`2-stems`, `4-stems`, or `6-stems`). The fixed bucket is `clouddsp-uploads`; the key is `uploads/{job_id}/<one filename>`. |
| Basic Pitch / ADTOF | `schema_version=1`, canonical `job_id`, `stem_name`, and `stem={bucket,object_key,content_type,size_bytes,sha256}`. The input is private `stems/{job_id}/{stem_name}.wav`, type `audio/wav`, with positive size and lowercase SHA-256 evidence registered by Demucs. |

Non-drum stems are `vocals`, `no_vocals`, `bass`, `other`, `guitar`, and `piano`.
ADTOF accepts only `drums` from a four- or six-stem Job. The
[request selection](app/dispatchable_outbox_request.py) and
[downstream contract](app/downstream_outbox_request.py) are the exact source
boundaries; worker READMEs document their independent durable checks.

| AMQP property | Value |
| --- | --- |
| `content_type`, `content_encoding` | `application/json`, `utf-8` |
| `delivery_mode` | `2` (persistent) |
| `type` | Exactly the event routing key |
| `message_id` | Canonical `outbox_events.event_id` |
| `correlation_id` | Canonical Job ID matching the body |

Main processing queues are durable quorum queues with bounded backlog,
publisher rejection on overflow, delivery limits, and dead-letter retention.
Declared delayed-retry queues are topology resources; current worker task
retries are PostgreSQL-driven rather than publications to those queues.

## Publication and crash recovery

```text
pending/due outbox or expired publication lease
  -> commit short row claim -> persistent mandatory publish + broker confirm
  -> guarded published update
```

The default publication lease and definite-failure retry delay are each 30
seconds. No database transaction remains open during broker I/O. A result update
requires the same event/lease token; a stale publisher cannot overwrite the
current owner. A definite connection/publish failure schedules a later attempt.
An invalid permanent event contract is retained as a `dead_lettered` outbox row
with a fixed category, distinct from a broker queue's DLQ.

An uncertain post-send confirmation leaves the lease for expiry recovery.
Likewise, a crash after confirmation but before the `published` commit can
publish again. A confirmation proves broker acceptance, not worker completion.
Workers therefore ACK only after their own durable claim and reject duplicate
execution through the canonical task key. There is no exactly-once transaction
across PostgreSQL and RabbitMQ.

See [outbox leases](app/outbox_lease.py),
[one generic dispatch attempt](app/dispatch_dispatchable_once.py), and
[AMQP publication](app/amqp_publisher.py). Normal diagnostics use fixed safe
categories, not raw broker messages, database rows, or credentials.

## Security and deployment

The publisher database role can read the needed immutable outbox fields and
update publication/lease columns. It cannot mutate Job ownership/results or run
DDL. Its RabbitMQ identity writes only the processing exchange; the application
restricts routing keys. It has no worker-queue consume permission or
broker-administrator tag. No MinIO credential is mounted. Pods run non-root with
a read-only root filesystem, capabilities dropped, and no Kubernetes API token.
RabbitMQ's ingress NetworkPolicy permits the selected publisher's AMQP access;
it does not provide complete Pod egress or database network isolation. Secrets
come from ignored local initialization, not Helm values or image layers.

Run from the repository root. The ordered fresh bootstrap installs dependencies,
identities, schema, and both releases:

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-platform
./k8Deployment/kubernetes/scripts/deploy-local.sh verify
```

On an already prepared cluster, install an absent component separately; for an
existing Helm release use `verify`:

```bash
./k8Deployment/kubernetes/scripts/releases/dispatcher-release.rb install
./k8Deployment/kubernetes/scripts/releases/generic-dispatcher-release.rb install
./k8Deployment/kubernetes/scripts/releases/dispatcher-release.rb verify
./k8Deployment/kubernetes/scripts/releases/generic-dispatcher-release.rb verify
```

The helpers guard prerequisites, image locks, rendered/source/live specs, and
Helm ownership/stored manifests. `plan`/`adopt` are pre-adoption maintenance
paths, not an existing-release check. Use the
[legacy chart](../../helm/dispatcher/README.md) and
[generic chart](../../helm/generic-dispatcher/README.md) for reviewed changes;
raw Deployment manifests are comparison baselines.

## Smoke and troubleshooting

Neither dispatcher release helper has a `smoke` mode. On a normal full cluster,
the [Demucs](../../tests/demucs-worker-smoke/README.md),
[Basic Pitch](../../tests/basic-pitch-worker-smoke/README.md), and
[ADTOF](../../tests/adtof-worker-smoke/README.md) worker smokes exercise publication
through actual workers without a competing test queue consumer.

The narrower transport tests below require their documented test identities,
empty target queues, and a controlled run where the corresponding worker is not
consuming. Their reader ACKs only its expected message and requeues an unexpected
one. Do not run them against normal queued work or race an active ML worker.
See the [normal-path client runbook](../../tests/dispatcher-smoke/client/README.md)
and [generic-routing Job prerequisites](../../tests/generic-dispatcher-smoke/generic-dispatcher-basic-pitch-routing-smoke-job.yaml).

```bash
kubectl --context k3d-clouddsp-local -n clouddsp-data create -f k8Deployment/kubernetes/tests/dispatcher-smoke/dispatcher-normal-path-smoke-job.yaml
kubectl --context k3d-clouddsp-local -n clouddsp-data wait job/dispatcher-normal-path-smoke --for=condition=complete --timeout=300s
kubectl --context k3d-clouddsp-local -n clouddsp-data logs job/dispatcher-normal-path-smoke

kubectl --context k3d-clouddsp-local -n clouddsp-app create -f k8Deployment/kubernetes/tests/generic-dispatcher-smoke/generic-dispatcher-basic-pitch-routing-smoke-job.yaml
kubectl --context k3d-clouddsp-local -n clouddsp-app wait job/generic-dispatcher-basic-pitch-routing-smoke --for=condition=complete --timeout=180s
kubectl --context k3d-clouddsp-local -n clouddsp-app logs job/generic-dispatcher-basic-pitch-routing-smoke
```

Inspect failure/fixture cleanup before any rerun. After a passing result and
reported fixture cleanup, remove only the corresponding disposable Job; remove
the normal-path test's temporary data-namespace credential copy as its runbook
requires. Never purge ordinary queues to make a smoke pass. For safe runtime logs:

```bash
kubectl --context k3d-clouddsp-local -n clouddsp-app logs deployment/clouddsp-dispatcher --tail=100
kubectl --context k3d-clouddsp-local -n clouddsp-app logs deployment/clouddsp-generic-dispatcher --tail=100
```

## Limitations and history

The generic release has not retired the legacy Demucs-only release. Broker
acceptance and component verification are separate from end-to-end processing.
Dead-letter replay is an explicit diagnosis/maintenance action; these helpers do
not automatically replay failures. Fresh cluster bootstrap does not restore data.

The full event examples, staged development notes, and earlier trials are in
[historical implementation notes](../../docs/history/service-dispatcher-implementation-notes.md).
