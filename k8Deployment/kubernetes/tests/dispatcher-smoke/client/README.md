# Dispatcher smoke-client source

This directory contains the disposable smoke client for one controlled
outbox-dispatcher handoff: its source, unit tests, Dockerfile, and built
digest-pinned local image. It creates no Kubernetes Job, Secret, RabbitMQ
topology change, or direct database/message write.

## What it will verify

```text
normal authenticated upload + upload-intake
  -> one durable outbox event
  -> dispatcher publisher confirmation
  -> PostgreSQL event = published
  -> exact persistent demucs.requested delivery
  -> restricted smoke reader acknowledges that one delivery
```

`dispatcher_smoke_orchestrator.py` is the only component that creates test
data. It uses a disposable Keycloak user, the normal Job API `POST /jobs`, and
the API-issued presigned multipart POST to put one tiny valid WAV into MinIO.
It then waits for the existing upload-intake Pod to create the durable
`demucs.requested` outbox event. It reads only its event ID and delivery state,
and passes that stable contract to `dispatcher_smoke_client.py`.

The orchestrator calls the client in this order:

```text
passive empty Demucs-queue preflight
  -> temporary Keycloak identity
  -> normal authenticated Job API direct-upload contract
  -> API-issued presigned POST through private MinIO DNS
  -> native MinIO notification + upload-intake transaction
  -> one durable controlled outbox event
  -> PostgreSQL published assertion + restricted AMQP read/ack
  -> delete only the generated object/job/user/client
```

The verifier checks PostgreSQL before consuming because RabbitMQ can expose a
confirmed message just before the dispatcher commits its post-confirmation
`published` update. A non-empty preflight queue prevents the test's restricted
reader from inspecting ordinary Demucs work.

It refuses a non-empty queue, malformed/foreign message, mismatched AMQP
properties, missing publication timestamp, or uncleared lease. On an unexpected
delivery it calls `basic_nack(requeue=True)` and fails; it never acknowledges
or loops over a message it does not own.

## Dependencies

`requirements.lock` contains Pika, Psycopg, and the Boto3/MinIO cleanup
dependencies, all hash-locked. Boto3 is used only to delete the exact object
created by the test; the browser-equivalent upload itself uses the Job API's
presigned POST over HTTP. The Linux/ARM64 image build installs it with:

```bash
python -m pip install --require-hashes --requirement requirements.lock
```

The source imports these clients lazily, so unit tests run with fakes and do
not need a broker, database, MinIO server, image build, or local cluster
connection.

## Built image boundary

`Dockerfile` has a temporary validation stage that installs only the
hash-locked dependencies and runs both unit-test modules. Its final stage
contains only `dispatcher_smoke_orchestrator.py`,
`dispatcher_smoke_client.py`, their verified runtime packages, and an explicit
non-root UID/GID `10001`. The future Job injects its private endpoints and
temporary test-only credentials at run time; they are never baked into the
image. The first ARM64 build passed all 18 tests and is pinned under
`images.dispatcher-smoke-client` in `images.lock.yaml`.

Build it explicitly when ready:

```bash
./k8Deployment/kubernetes/scripts/build-dispatcher-smoke-client-image.sh
```

The script targets local Linux/ARM64 nodes and pushes only to the k3d registry.
After a deliberate rebuild, replace the lock entry with that newly printed
digest before changing the separate Kubernetes Job manifest.

## Prepared Kubernetes Job

[`../dispatcher-normal-path-smoke-job.yaml`](../dispatcher-normal-path-smoke-job.yaml)
is the prepared, unapplied one-shot Job. It uses the locked image in
`clouddsp-data`, preflights an empty Demucs queue, and mounts existing local
administrator Secrets only for temporary setup and exact cleanup. Its only
new input is a data-namespace copy of the restricted RabbitMQ smoke-reader
Secret. Create that ignored local copy from
[`../dispatcher-smoke-rabbitmq-data-credentials.secret.example.yaml`](../dispatcher-smoke-rabbitmq-data-credentials.secret.example.yaml),
apply it immediately before the Job, and delete it after the Job completes.
