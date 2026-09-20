# Basic Pitch KEDA burst-smoke contract

This directory defines a controlled **three-request Basic Pitch burst
test**. It is intentionally separate from the one-request
[`basic-pitch-worker-smoke`](../basic-pitch-worker-smoke/README.md): that test
proves one durable worker path, while this contract must create enough valid
simultaneous backlog to observe the Basic Pitch KEDA policy scale from zero
toward its configured maximum of three Pods.

This document creates no Kubernetes workload, Secret, PostgreSQL role or row,
MinIO policy or object, RabbitMQ message, HPA, or KEDA resource. Later small
tasks will implement the restricted identities and client only for the exact
coordinates fixed here.

## What the live run proves

The future client must exercise this ordinary durable path three times in one
short transaction scope; it must not publish directly to RabbitMQ or simulate a
worker:

```text
three controlled private WAV objects
             |
             v
one restricted PostgreSQL prepare function creates three Jobs + outbox events
             |
             v
generic dispatcher publishes three basic-pitch.requested messages
             |
             v
KEDA observes clouddsp.basic-pitch.requests and scales the Deployment
             |
             v
three deployed Basic Pitch workers claim/process durable task leases
             |
             v
three verified private MIDI objects + succeeded PostgreSQL task records
```

The smoke client will have no RabbitMQ credential and no Kubernetes API token.
It therefore cannot read queue depth, force a replica count, inspect an HPA, or
compete with a worker for a delivery. The operator observes those controller
facts separately with `kubectl` and RabbitMQ commands while the restricted
client proves durable database/object results.

## Fixed request coordinates

Each row represents one independent normal Basic Pitch input. `4-stems` makes
`vocals`, `bass`, and `other` valid non-drum stage inputs, so the burst covers
three legitimate per-stem task keys rather than reusing a single request
identity. All IDs are fixed source constants in the later client and database
functions. No environment variable may substitute another user Job, object,
or event.

| Request | Job ID | Outbox event ID | Synthetic upstream Demucs task ID | Stem | Input object | Expected MIDI object |
| --- | --- | --- | --- | --- | --- | --- |
| vocals | `2a1e8097-7da6-4d06-8b27-c006b02e0d91` | `32e10a72-b5e7-4f11-8dfe-b922ec7ebbd9` | `b1f9a310-9b6c-4707-84f7-ec1510c12201` | `vocals` | `stems/2a1e8097-7da6-4d06-8b27-c006b02e0d91/vocals.wav` | `midi/2a1e8097-7da6-4d06-8b27-c006b02e0d91/vocals.mid` |
| bass | `8e27f4ad-6c19-4e3f-9b56-fa287429c0a2` | `c4b23de8-4a63-47e3-a96e-155abfd38c36` | `e9875430-1df0-4389-a60c-48ad0fb21334` | `bass` | `stems/8e27f4ad-6c19-4e3f-9b56-fa287429c0a2/bass.wav` | `midi/8e27f4ad-6c19-4e3f-9b56-fa287429c0a2/bass.mid` |
| other | `96ba2e39-e035-4fe8-a50a-b1e6db208ac3` | `47ae0c63-e1b1-4663-b47a-8efaa8f37bc7` | `0b4970e2-6735-48be-8741-64a720be79ce` | `other` | `stems/96ba2e39-e035-4fe8-a50a-b1e6db208ac3/other.wav` | `midi/96ba2e39-e035-4fe8-a50a-b1e6db208ac3/other.mid` |

All three Jobs will use the fixed test-only owner
`clouddsp-basic-pitch-keda-burst-smoke`, the private
`clouddsp-uploads` bucket, `stem_mode=4-stems`, and durable status
`midi_processing`. The test must reject any existing fixed Job, event,
processing task, input WAV, or output MIDI before it writes anything. Residue
from a failed run is evidence for diagnosis, never permission to overwrite it.

## Controlled input and output evidence

The later client will generate three deterministic 12-second mono PCM WAVs at
44.1 kHz, 16-bit samples. Each is 1,058,444 bytes including the WAV header,
well below a deliberately restrictive future 2 MiB-per-object test policy and
far below the normal worker's 256 MiB input ceiling. The three frequencies are
440 Hz (`vocals`), 493.883 Hz (`bass`), and 523.251 Hz (`other`), so each object
has independent byte/SHA-256 evidence while remaining small enough for a local
smoke test.

Each input must have exactly the same validated Demucs-style metadata contract
used by normal Basic Pitch work:

- `schema-version=1` and `producer=demucs`;
- its own fixed `job-id`, synthetic `task-id`, `stem-name`, and
  `stem-mode=4-stems`;
- exact `size-bytes`; and
- lower-case SHA-256 matching the streamed WAV bytes.

Each resulting MIDI object must be non-empty, have `audio/midi` content type,
contain a valid Standard MIDI File header, and have object metadata whose
job/task/event/stem/input-SHA-256 values exactly match the corresponding
request. The smoke client streams each MIDI object and verifies its final
SHA-256 rather than trusting only a metadata claim.

## KEDA observation contract

The test client creates all three durable events before it starts polling for
completion. This gives KEDA a three-message backlog rather than three serial
requests. The Basic Pitch policy currently has a five-second scale-from-zero
polling interval, a one-message target, a zero-to-three replica range, and an
HPA policy that permits up to three Pods per 15-second interval.

During the future live run, the operator must observe all of the following:

1. `clouddsp-basic-pitch-rabbitmq-scaler` becomes active after the dispatcher
   has published the outbox rows.
2. The generated `keda-hpa-clouddsp-basic-pitch-rabbitmq-scaler` reaches a
   desired/actual count of three while work is queued or unacknowledged.
3. Three Basic Pitch Pods are created. Scheduler capacity remains authoritative:
   a Pending Pod is a valid capacity diagnosis, not a reason to bypass the
   three-Pod ceiling.
4. All three durable tasks later succeed, their MIDI objects verify, and the
   main request queue drains without the test client reading it.
5. After the 60-second idle cooldown, the Deployment returns to zero.

Because this is a short CPU workload, the later client must use a 300-second
durable completion wait while the operator starts observation before or
immediately after applying the test Job. The future test must report the three
safe fixed request labels and durable status transitions, never passwords,
raw AMQP bodies, access keys, object bytes, or presigned URLs.

The reviewed local run completed successfully on 2026-09-20. The normal
database/outbox/dispatcher/worker path completed for all three requests, KEDA
scaled the Basic Pitch Deployment from zero to its three-Pod ceiling, and it
returned to zero after the configured idle cooldown. This result validates the
specific local policy and current node capacity; it is not a claim about a
future GPU or cloud profile.

## MinIO identity boundary

The prepared immutable
[`six-key MinIO policy`](basic-pitch-keda-burst-smoke-minio-policy-v001-configmap.yaml)
permits only `GetObject`, `PutObject`, and `DeleteObject` for the three exact
WAV/MIDI pairs. Its paired
[`runtime Secret template`](basic-pitch-keda-burst-smoke-minio-credentials.secret.example.yaml)
belongs in `clouddsp-app`; the matching
[`temporary bootstrap template`](basic-pitch-keda-burst-smoke-minio-bootstrap-credentials.secret.example.yaml)
belongs in `clouddsp-data`. The prepared
[`MinIO bootstrap Job`](minio-basic-pitch-keda-burst-smoke-objects-bootstrap-job.yaml)
uses a root alias only in ordered init containers, then creates/rotates the one
restricted user, attaches the fixed policy, proves the association, and removes
the alias before completion. The local bootstrap completed successfully; its
temporary `clouddsp-data` Secret was removed, while the runtime app Secret and
the immutable policy ConfigMap remain. The reviewed source remains useful for a
deliberate repair on a recreated local cluster, but reading it alone makes no
cluster change.

## PostgreSQL identity boundary

The prepared
[`runtime Secret template`](basic-pitch-keda-burst-smoke-database-credentials.secret.example.yaml)
defines one `clouddsp-app`-only login. Its matching
[`temporary bootstrap template`](basic-pitch-keda-burst-smoke-database-bootstrap-credentials.secret.example.yaml)
is intentionally scoped to `clouddsp-data`, where only the administrator
bootstrap Pod can mount it alongside the PostgreSQL admin credential. The
prepared
[`PostgreSQL bootstrap Job`](basic-pitch-keda-burst-smoke-database-bootstrap-job.yaml)
creates/rotates the new role and removes all direct database/table/sequence
authority before granting only these three `SECURITY DEFINER` functions:

- `prepare(size, sha256, size, sha256, size, sha256)` accepts evidence only
  for the three fixed WAV coordinates and atomically creates their Jobs and
  outbox rows;
- `observe()` returns a deliberately compact status projection for those three
  coordinates only; and
- `cleanup()` deletes all three Jobs only after every event was published and
  each normal worker task succeeded on its first attempt with no active lease.

The restricted client cannot list a bucket, access normal user objects,
publish/consume RabbitMQ, read Kubernetes resources, or use direct SQL against
application tables. The local PostgreSQL bootstrap completed successfully. Its
temporary `clouddsp-data` Secret was removed after the safe capability checks
passed; the restricted runtime Secret remains in `clouddsp-app`.

## Client contract boundary

[`client/basic_pitch_keda_burst_smoke.py`](client/basic_pitch_keda_burst_smoke.py)
defines the pure, dependency-free portion of the future smoke client. It
contains the three literal coordinate records, deterministic 12-second WAV
construction, the exact metadata/evidence supplied to PostgreSQL `prepare`,
and strict validation of the private PostgreSQL/MinIO Service routes and test
identity. The paired [`MinIO adapter`](client/minio_adapter.py) accepts only an
injected S3-compatible client and performs `HeadObject`, `PutObject`,
`GetObject`, and `DeleteObject` only on the six fixed keys; it never receives a
prefix or a bucket-list operation. The paired
[`PostgreSQL adapter`](client/postgresql_adapter.py) accepts only an injected
dictionary-row cursor connection and calls only the three restricted
`prepare`, `observe`, and `cleanup` functions—never direct application-table
SQL. None of these modules imports an SDK, database driver, AMQP, Kubernetes,
or container library, so their unit tests cannot alter the cluster. Dependency
locking, image construction, and a Kubernetes Job remain separate tasks.

The reviewed [`requirements.lock`](client/requirements.lock) now pins Boto3,
`psycopg-binary`, and every required transitive package by SHA-256 for the
future Python 3.12 Linux/ARM64 image. It excludes AMQP, Kubernetes, web,
Keycloak, ML, GPU, and compiler dependencies because this one-shot verifier
must observe the existing dispatcher and workers rather than replace them.

[`client/burst_runner.py`](client/burst_runner.py) now composes those narrow
adapters in the only permitted order: preflight, upload three WAVs, call
PostgreSQL `prepare`, wait only on the durable observation projection, verify
three private MIDI objects, then delete objects before the success-only
database cleanup. Before `prepare`, a failed upload receives best-effort
six-key cleanup because no durable request exists. From `prepare` onward, a
timeout, database failure, incomplete observation, or MIDI validation failure
preserves all durable database/object evidence for diagnosis. It does not
inspect queues, query KEDA/HPA state, create Kubernetes resources, or invoke a
worker.

[`client/burst_entrypoint.py`](client/burst_entrypoint.py) now supplies the
actual process boundary for the later one-shot Job. It builds a Psycopg
dictionary-row autocommit connection using only the restricted database
Secret, then builds a path-style Boto3 client using only the six-key MinIO
Secret. Its imports are local to the factories, and expected errors yield a
safe nonzero exit code without printing a password, access key, DSN, endpoint,
SDK response, object data, or traceback. It closes its short-lived PostgreSQL
connection on either outcome.

The prepared [`client Dockerfile`](client/Dockerfile) uses the pinned Docker
Official Python 3.12 runtime in two stages. Its validation stage installs only
the complete hash-checked lock and executes all local client tests. Its final
stage copies only the verified CPython site-packages tree and the five runtime
modules, then runs `burst_entrypoint.py` as non-root UID/GID `10007` with no
home directory. The final stage has no test source, pip, lockfile, Secret,
endpoint value, queue client, Service, or Ingress. It was built for
`linux/arm64`, successfully ran all 26 client tests in its validation stage,
and was pushed to the local registry. Its Docker local layer size is `63.07
MiB`; the verified immutable image reference is
`clouddsp-registry.localhost:5001/basic-pitch-keda-burst-smoke-client@sha256:49d4bae35dcb36930c6a76e886819c4cf9cbea4eca0e3548658cc9d1e317a659`,
recorded in [`images.lock.yaml`](../../images.lock.yaml). A future workload
must copy that digest, never the readable build tag.

The prepared [`one-shot Job`](basic-pitch-keda-burst-smoke-job.yaml) mounts
only the two restricted runtime Secrets in `clouddsp-app`, calls the immutable
client image as non-root, has no ServiceAccount token or service-link
environment, and is bounded by a 420-second active deadline. It is intentionally
not applied by this source task: the next explicit run must begin KEDA/HPA
observation before applying it, so the temporary three-message backlog can be
seen scaling the worker from `0 → 3 → 0`.

On successful completion, the client deletes exactly its three MIDI objects
and three controlled WAVs, then invokes its narrow database cleanup function.
After a failure or timeout following durable preparation, it preserves all six
objects and the three durable records for investigation. A later separate
administrator-only failed-run cleanup task may remove only these same fixed
coordinates after review.

## Next small tasks

1. Review the KEDA/HPA observation commands and apply the separately reviewed
   one-shot Job to run the live `0 → 3 → 0` observation.
