# Demucs worker

The implemented local worker separates an uploaded source into private WAV stems,
then commits the per-stem MIDI requests to PostgreSQL. It runs as a CPU worker in
the standard k3d profile. This validates the processing contract and local
correctness; it does not validate CUDA performance.

## Runtime interfaces

```text
upload-intake -> PostgreSQL outbox -> dispatcher -> RabbitMQ
  -> durable Demucs task claim -> ACK -> source verification + CPU separation
  -> verified MinIO stems -> guarded result + downstream outbox transaction
  -> generic dispatcher -> Basic Pitch / ADTOF
```

| Interface | Current contract |
| --- | --- |
| Runtime | `python -m app.worker_main`; internal outbound consumer, no HTTP Service or Ingress. |
| RabbitMQ | Vhost `/clouddsp`, exchange `clouddsp.processing-events`, route `demucs.requested`, queue `clouddsp.demucs.requests`. |
| Request | Version-1 JSON with `schema_version`, canonical `job_id`, `source.bucket`, `source.object_key`, and `stem_mode`. |
| Source | Private `clouddsp-uploads`, exactly `uploads/{job_id}/<one filename>`. |
| Task identity | `(job_id, 'demucs', '')` in `processing_tasks`. |
| Outputs | Private `stems/{job_id}/{stem_name}.wav`; stored byte length, SHA-256, and provenance are verified before database registration. |
| Downstream | One outbox event per approved stem: `basic-pitch.requested` for non-drums; `adtof.requested` for drums. |

Every request requires persistent delivery (`delivery_mode=2`), JSON/UTF-8
properties, `type=demucs.requested`, `message_id=outbox_events.event_id`, and
`correlation_id=job_id`. The [strict parser](app/messaging/demucs_requested_message.py)
rejects unknown fields, duplicate JSON members, non-canonical IDs, other buckets,
foreign paths, and mismatched envelope properties. The
[dispatcher contract](../dispatcher/README.md) describes publication and routing.

Before a first claim, the worker compares the request with a retained,
source-uploaded Job and its matching published outbox event. MinIO `HeadObject`
then verifies the fixed coordinate, size up to 256 MiB, approved audio content
type, and `job-id` / `stem-mode` metadata. Downloaded audio passes FFprobe's codec
and positive-duration checks, including the 500-second limit, before model work.
A broker notification alone cannot authorize access to an arbitrary object.

| Stem mode | Required output set |
| --- | --- |
| `2-stems` | `vocals`, `no_vocals` |
| `4-stems` | `drums`, `bass`, `vocals`, `other` |
| `6-stems` | `drums`, `bass`, `vocals`, `other`, `guitar`, `piano` |

The image contains checksum-locked `htdemucs` / `htdemucs_6s` weights from
[model-artifacts.lock.yaml](model-artifacts.lock.yaml). Running Pods do not
download model weights. The [Dockerfile](Dockerfile) and
[requirements.lock](requirements.lock) define the local CPU runtime;
[images.lock.yaml](../../images.lock.yaml) and
[chart values](../../helm/demucs/values.yaml) pin the deployable image digest.

The 2026-10-03 `0.1.10-lease-refactor` build passed 321 image tests and real
two-stem CPU inference, then a normal Helm upgrade and the live worker smoke.
The [dated rollout record](../../docs/trials/2026-10-03-frontend-demucs-refactor-rollout.md)
records the exact image, runtime identity, downstream completion, and fixture
cleanup. The [image recipe](image-recipe.md) distinguishes that current build
from the preserved first-image milestones.

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

The public [task-lease interface](app/db/task_lease.py) re-exports the shared
types and operations from `app/db/task_leases/`. Existing worker imports use
that interface; the internal modules import their shared contracts directly
to avoid circular dependencies. Each SQL statement stays beside the operation
that executes it:

| Task-lease module | Responsibility |
| --- | --- |
| [contracts.py](app/db/task_leases/contracts.py) | Immutable lease/result evidence, safe errors, and bounded task vocabulary. |
| [validation.py](app/db/task_leases/validation.py) | Canonical UUIDs, lease duration bounds, and dictionary-row evidence. |
| [claim.py](app/db/task_leases/claim.py) | First-delivery claims and duplicate/stale request classification. |
| [ownership.py](app/db/task_leases/ownership.py) | Token-guarded task start and lease renewal. |
| [recovery.py](app/db/task_leases/recovery.py) | Due-task recovery, exhausted-lease failure, and durable-deadline failure. |
| [completion.py](app/db/task_leases/completion.py) | Bounded downstream evidence and atomic task/Job/outbox completion. |

These adapters issue parameterized SQL through a supplied cursor. The existing
outer database compositions own transaction commit and rollback; moving the
adapters does not move network calls or media processing inside a transaction.

## Durable processing and acknowledgement

- A short PostgreSQL transaction claims the unique task with a random lease
  token. **ACK occurs after that claim commits, before audio processing.**
- Matching duplicate or stale requests produce an authoritative no-work result
  and are ACKed. Malformed requests are NACKed without requeue to the configured
  DLQ. Database/claim failures remain unacknowledged for broker redelivery.
- A crash after ACK leaves a durable task. The runtime scans due retries and
  expired active leases with row locking and `SKIP LOCKED`; a new owner receives
  a new token. Three real task attempts are allowed, independent of broker
  delivery count. Classified transient task failures use a 30-second durable
  retry schedule; exhausted attempts become terminal facts.
- The default lease is 15 minutes. Renewal and completion require the current,
  unexpired token. The runtime renews ownership during long work; a stale worker
  cannot commit a recovered task's result.
- Download, model execution, and MinIO writes do not hold a database transaction
  open. After the complete stem set has stored-object proof, one guarded commit
  records `jobs.stems`, succeeds the task, advances the Job to `midi_processing`,
  and inserts the downstream outbox events. Incomplete output is not success.
- The worker neither publishes downstream requests directly nor creates
  Kubernetes Jobs. PostgreSQL owns the result; RabbitMQ is at-least-once transport.

The implementation boundaries are [task ownership](app/db/task_lease.py),
[manual ACK](app/messaging/amqp_manual_ack.py), [audio validation](app/processing/audio_probe.py),
and [stem publication](app/processing/demucs_stem_set_publish.py). Fixed failure categories
keep raw media paths, credentials, URLs, and model diagnostics out of normal logs.

## Security and scaling

The release mounts separate restricted PostgreSQL, RabbitMQ, and MinIO runtime
Secrets. Administrator bootstrap credentials do not belong in this Pod. MinIO
permissions cover source reads and private stem writes; the application still
checks the exact durable Job/task coordinate because prefix IAM cannot identify
a lease owner. Browser authorization and presigning belong to the Job API.

The Pod is non-root, uses a read-only root filesystem with bounded scratch
storage, drops capabilities, and has no ServiceAccount API token. RabbitMQ's
ingress NetworkPolicy admits the selected worker to its AMQP port. This is not
complete worker egress isolation or a default-deny policy for every dependency.
Credentials are initialized into ignored local files by the
[operator workflow](../../scripts/README.md), not Helm values.

The [Helm release](../../helm/demucs/README.md) owns the Deployment and
ScaledObject. KEDA owns the generated HPA and Deployment scale; the chart omits
`spec.replicas`. Both RabbitMQ backlog and PostgreSQL active/due task counts keep
work visible after ACK. The current CPU profile scales from zero to one worker.
Zero Pods is healthy while idle; termination can remain visible during the
reviewed shutdown grace period.

## Install and verify

Run from the repository root. Fresh full deployment initializes dependencies,
roles, policies, schema, and runtime Secrets in their ordered stages:

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-platform
./k8Deployment/kubernetes/scripts/deploy-local.sh verify
```

For an already prepared cluster where this release and its resources are absent:

```bash
./k8Deployment/kubernetes/scripts/releases/demucs-release.rb install
./k8Deployment/kubernetes/scripts/releases/demucs-release.rb verify
```

`install` checks the declared dependencies and rejects conflicting existing
resources. `verify` checks the lock, chart, source baseline, stored Helm manifest,
live ownership, and KEDA/HPA relationships. It checks the idle scale state, so
run it after work and cooldown finish. The raw `demucs-*.yaml` workload files are
comparison baselines; do not apply them over Helm-owned objects. `adopt` is the
historical one-time ownership migration, not the fresh-install command.

## Smoke and troubleshooting

The [worker smoke runbook](../../tests/demucs-worker-smoke/README.md) documents
its fixed test coordinates, restricted database functions/MinIO identities, and
cleanup rules. Those smoke prerequisites must exist separately; the release
helper does not bootstrap them or substitute administrator worker credentials.
Once the release is idle and the fixture is clean:

```bash
./k8Deployment/kubernetes/scripts/releases/demucs-release.rb smoke
```

This submits the fixed `demucs-worker-smoke` Job, waits up to 900 seconds, verifies
the actual dispatcher/worker path and hash-matched private stems, and observes
its downstream Basic Pitch completion. A passing run cleans only its fixture;
the helper removes its disposable Kubernetes Job. Failure leaves the Job for
inspection and can retain fixture evidence. Do not rerun over a dirty fixture.

```bash
kubectl --context k3d-clouddsp-local -n clouddsp-app logs job/demucs-worker-smoke
kubectl --context k3d-clouddsp-local -n clouddsp-app get deployment/clouddsp-demucs scaledobject/clouddsp-demucs-rabbitmq-scaler
kubectl --context k3d-clouddsp-local -n clouddsp-app logs deployment/clouddsp-demucs --tail=100
```

An idle Deployment may have no Pod to log. Diagnose durable task state and scaler
conditions before changing replicas or purging queues. The test-specific cleanup
procedures require inspection and never authorize deletion of ordinary jobs.

## Limitations and history

The shipped profile is CPU and local storage. A native Linux/NVIDIA profile and
its capacity/throughput checks remain separate work. The declared broker retry
queues are not the worker's retry engine; current retries use PostgreSQL task
state. Cleanup removes cluster data and fresh bootstrap does not restore it.

The complete staged design, implementation steps, and earlier trial evidence are
preserved as [historical implementation notes](../../docs/history/service-demucs-implementation-notes.md).
