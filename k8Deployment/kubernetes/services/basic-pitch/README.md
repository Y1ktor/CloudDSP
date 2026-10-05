# Basic Pitch worker

The implemented Kubernetes worker transcribes one approved non-drum Demucs WAV
stem into private MIDI. It uses a CPU Basic Pitch runtime and independent local
RabbitMQ/PostgreSQL/MinIO adapters; it does not import a cloud Lambda handler.

## Runtime interfaces

| Interface | Current contract |
| --- | --- |
| Runtime | `python -m app.worker_main`; outbound worker, no HTTP Service or Ingress. |
| RabbitMQ | Vhost `/clouddsp`, exchange `clouddsp.processing-events`, route `basic-pitch.requested`, queue `clouddsp.basic-pitch.requests`. |
| Request | Version-1 JSON: canonical `job_id`, `stem_name`, and `stem` containing bucket, key, WAV content type, byte length, and lowercase SHA-256. |
| Input | Private `clouddsp-uploads`, `stems/{job_id}/{stem_name}.wav`. |
| Allowed stems | `vocals`, `no_vocals`, `bass`, `other`, `guitar`, `piano`; `drums` belongs to ADTOF. |
| Task identity | `(job_id, 'basic-pitch', stem_name)`. |
| Output | Private `midi/{job_id}/{stem_name}.mid`, content type `audio/midi`, with verified byte length, SHA-256, and provenance. |

Requests are persistent JSON/UTF-8 deliveries. The AMQP type matches the routing
key, `message_id` is the durable outbox event ID, and `correlation_id` matches the
Job. The [parser](app/messaging/basic_pitch_requested_message.py) rejects unknown or
duplicate fields, non-canonical IDs, foreign paths/buckets, unsupported stems,
and invalid checksum/size evidence. The worker compares the published outbox,
registered stem, retained Job, and claimed task before reading private storage.
The [dispatcher contract](../dispatcher/README.md) defines the common envelope.

```text
Demucs stored stem + durable outbox
  -> generic dispatcher -> RabbitMQ -> committed task lease -> ACK
  -> verified WAV download -> CPU Basic Pitch -> verified MIDI upload
  -> guarded PostgreSQL task completion + tempo candidate
```

The [Dockerfile](Dockerfile) packages the CPU dependency lock and bundled TFLite
model. Pods do not download models. The portable Numba configuration is part of
the reviewed local profile. Use [images.lock.yaml](../../images.lock.yaml) and
[chart values](../../helm/basic-pitch/values.yaml) for the current immutable image;
digests in older build/trial notes are not upgrade instructions.

## Source layout

The worker modules are grouped by responsibility:

| Package | Responsibility |
| --- | --- |
| `app/db/` | Database access, task claims, leases, and guarded result transactions. |
| `app/artifacts/` | Object coordinates, downloads/uploads, and stored-object evidence. |
| `app/messaging/` | Request parsing, AMQP sessions, and delivery acknowledgement. |
| `app/processing/` | Model commands, inference, and media/artifact validation. |
| `app/runtime/` | Execution orchestration, recovery cadence, supervision, and shutdown. |

`app/worker_main.py` remains the public `python -m app.worker_main` launcher;
it delegates to [the runtime entry point](app/runtime/worker_main.py). Internal
imports and the image's source-compilation checks include the nested packages.
The launch command, task identities, and processing contracts are unchanged.

## Task ownership and result rules

- PostgreSQL claims one unique task in a short transaction. **The worker ACKs
  only after the claim commits; model work starts after ACK succeeds.**
- Matching duplicate/stale deliveries are ACKed without another model run.
  Malformed deliveries are NACKed without requeue into the configured DLQ.
  Unexpected claim/database errors leave the delivery unacknowledged.
- Default leases last 15 minutes. Every guarded state transition checks task
  identity, owner token, and expiry. Model/storage work runs outside database
  transactions. Loss of ownership cannot register a success for a newer owner.
- Due durable retries and expired active leases are recovered from PostgreSQL,
  independent of a new broker delivery. There are at most three real task
  attempts; classified transient failures normally schedule a 30-second retry.
  Exhaustion and permanent failures record bounded terminal categories.
- Input verification compares stored length, content type, provenance, and the
  downloaded SHA-256 with the registered stem before inference. Output is hashed,
  uploaded to its deterministic private key, and checked with `HeadObject`
  before the short completion transaction.
- The worker receives `EXECUTE` on the guarded completion function, not direct
  parent-Job mutation rights. Completion registers MIDI evidence, succeeds that
  task, and stores its tempo candidate atomically.

The [lease adapter](app/db/task_lease.py), [ACK adapter](app/messaging/amqp_manual_ack.py),
[task execution](app/runtime/basic_pitch_task_execution.py), and
[completion adapter](app/db/midi_task_completion.py) define these boundaries.

Tempo analysis is best-effort: a non-credible candidate does not discard valid
MIDI. [Tempo candidate validation](app/processing/tempo_candidate.py) records BPM and its
confidence evidence. The current PostgreSQL migrations resolve the Job's master
tempo from registered candidates and aggregate the exact expected child task
set. A parent completes only after the required results are present; a single
stem's success is not whole-Job completion. No separate completion-polling
Deployment is required.

## Security and scaling

The runtime mounts distinct restricted database, consume-only RabbitMQ, and
MinIO Secrets. Its MinIO policy allows known stem reads and MIDI writes/verification;
it grants no bucket listing, source-upload access, object deletion, or browser
presigning. Static prefix permissions do not replace durable coordinate checks.
Bootstrap administrator credentials are kept outside the worker release.

The Pod runs non-root, drops capabilities, uses a read-only root filesystem plus
bounded scratch, and mounts no Kubernetes API token. It connects to private
Service DNS endpoints; RabbitMQ's ingress NetworkPolicy admits the selected
worker to AMQP. There is no claim of complete worker egress or PostgreSQL/MinIO
network isolation. The [operator guide](../../scripts/README.md) initializes
ignored credential files; secrets are not committed or embedded in image/Helm
values.

The [Helm chart](../../helm/basic-pitch/README.md) owns the Deployment and
ScaledObject. KEDA owns the HPA and scale subresource; `spec.replicas` is omitted.
Both queue backlog and PostgreSQL active/due tasks are scaling inputs, keeping
acknowledged work visible. The current CPU profile scales from zero to three
workers. Zero Pods is healthy when idle; allow cooldown and termination to finish
before the idle release check.

## Install and verify

Run from the repository root. The fresh full bootstrap supplies the dependencies,
schema, policies, identities, and runtime Secrets in stage order:

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-platform
./k8Deployment/kubernetes/scripts/deploy-local.sh verify
```

If those prerequisites are already prepared and this release/resources are absent:

```bash
./k8Deployment/kubernetes/scripts/releases/basic-pitch-release.rb install
./k8Deployment/kubernetes/scripts/releases/basic-pitch-release.rb verify
```

`install` guards prerequisites and conflicting existing resources. `verify` checks
reviewed source/rendered/live specs, immutable image, Helm ownership/stored
manifest, and scaler/HPA relationships. It expects the idle scale state. Raw
workload manifests are comparison baselines and must not be applied over
Helm-owned objects. Historical adoption and targeted upgrade modes in earlier
notes are not fresh-install steps.

## Smoke and troubleshooting

Prepare the restricted functions and MinIO smoke identity described in the
[worker smoke runbook](../../tests/basic-pitch-worker-smoke/README.md). The helper
does not create those prerequisites. With idle workers and clean fixed fixture:

```bash
./k8Deployment/kubernetes/scripts/releases/basic-pitch-release.rb smoke
```

The helper runs `basic-pitch-worker-smoke`, waits up to 360 seconds, and verifies
actual generic dispatch, durable task completion, and hash-matched private MIDI.
The scoped fixture cleanup must succeed before the disposable Job is removed.
A failure retains evidence for inspection; follow the test's guarded cleanup
procedure before another attempt.

```bash
kubectl --context k3d-clouddsp-local -n clouddsp-app logs job/basic-pitch-worker-smoke
kubectl --context k3d-clouddsp-local -n clouddsp-app get deployment/clouddsp-basic-pitch scaledobject/clouddsp-basic-pitch-rabbitmq-scaler
kubectl --context k3d-clouddsp-local -n clouddsp-app logs deployment/clouddsp-basic-pitch --tail=100
```

No Pod log is available at scale zero. Inspect task/scaler state before changing
replicas. The separate [KEDA burst test](../../tests/basic-pitch-keda-burst-smoke/README.md)
checks concurrency; it is not required for an ordinary component verification.

## Limitations and history

The standard local profile validates CPU correctness, not GPU capacity or
transcription quality for every input. Broker retry queues exist in topology,
but durable PostgreSQL scheduling is the worker retry path. Fresh bootstrap
creates new state and does not recover earlier cluster data.

The complete development sequence and historical rollout evidence are preserved
in [historical implementation notes](../../docs/history/service-basic-pitch-implementation-notes.md).
