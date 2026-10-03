# ADTOF drums worker

The implemented Kubernetes worker transcribes a Demucs drums WAV into private
drum MIDI and a tempo-candidate JSON artifact. The standard local image uses
CPU PyTorch with bundled model weights. Its adapters are independent of cloud
handlers, and PostgreSQL owns durable task state.

## Runtime interfaces

| Interface | Current contract |
| --- | --- |
| Runtime | `python -m app.worker_main`; internal outbound consumer, no HTTP Service or Ingress. |
| RabbitMQ | Vhost `/clouddsp`, exchange `clouddsp.processing-events`, route `adtof.requested`, queue `clouddsp.adtof.requests`. |
| Request | Version-1 JSON for `stem_name=drums`, with canonical Job ID and registered stem bucket/key, WAV type, byte length, and SHA-256. |
| Input | Private `clouddsp-uploads`, exactly `stems/{job_id}/drums.wav`. |
| Modes | `4-stems` and `6-stems`; `2-stems` has no drums task. |
| Task identity | `(job_id, 'adtof', 'drums')`. |
| Outputs | Private `midi/{job_id}/drums.mid` and `midi/{job_id}/drums_bpm.json`. Both require verified stored byte length, SHA-256, and provenance. |

AMQP messages use persistent JSON/UTF-8 properties, `type=adtof.requested`, the
outbox event ID as `message_id`, and Job ID as `correlation_id`. The
[strict parser](app/adtof_requested_message.py) rejects foreign paths, modes,
stems, malformed IDs, duplicate/extra JSON fields, and inconsistent evidence.
The [dispatcher contract](../dispatcher/README.md) defines the shared envelope.

```text
Demucs drums + outbox -> generic dispatcher -> RabbitMQ
  -> committed drums task lease -> ACK -> verified private WAV
  -> CPU inference -> MIDI + tempo upload/storage proof
  -> guarded PostgreSQL result -> parent aggregate/tempo resolution
```

[model_configuration.py](app/model_configuration.py), the
[dependency lock](requirements.lock), and [Dockerfile](Dockerfile) pin the model
package/configuration and bundle its weights. The local CPU child process has
a default ten-minute timeout. No running Pod fetches models.
[images.lock.yaml](../../images.lock.yaml) and
[chart values](../../helm/adtof/values.yaml) define the current digest.

## Ownership, acknowledgement, and recovery

- The first claim compares the retained Job, registered drums stem, and published
  outbox event, then commits a unique task lease. **ACK happens after that durable
  claim and before inference.** Model work starts only after successful ACK.
- A matching duplicate/stale delivery is ACKed without another model run.
  Malformed deliveries are NACKed without requeue into the DLQ. Claim/database
  failures propagate without acknowledgement so the broker can redeliver.
- Default ownership lasts 15 minutes. Guarded state changes and completion
  require the current unexpired token. A stale owner cannot report success for
  a task claimed by a recovery worker. Model and storage I/O hold no long
  database transaction.
- A running Pod alternates normal receipt with bounded PostgreSQL recovery scans,
  beginning with recovery on startup. Expired `leased`/`running` tasks can be
  reclaimed with a fresh token, up to three real attempts. An expired third
  attempt becomes a terminal `lease_expired_attempts_exhausted` task fact.
- Bounded service-availability failures receive supervisor backoff. The current
  runtime does not implement the broader explicit `retry_scheduled` task policy
  found in Demucs/Basic Pitch; an acknowledged failed attempt may depend on
  lease expiry and recovery. Unexpected/model-contract failures remain visible
  rather than being called successful.
- Completion occurs only after both local artifacts have been validated, uploaded,
  and checked in MinIO. The guarded PostgreSQL function atomically registers MIDI
  and tempo evidence and succeeds the task. The database aggregate checks the
  expected child task/output set before making the parent terminal; tempo
  resolution prefers a credible drums candidate.

See [task claims/recovery](app/task_claim.py), [ACK handling](app/amqp_manual_ack.py),
[recovery cadence](app/recovery_cadence.py),
[failure classification](app/supervisor_failure_classification.py), and
[finalization](app/task_finalization.py). The worker does not publish requests,
create Kubernetes Jobs, or use an API token to launch another worker.

## Security and current scaling limitation

Runtime PostgreSQL, RabbitMQ, and MinIO Secrets are distinct restricted service
identities. MinIO access is limited to known private stems and MIDI artifacts;
exact coordinate/provenance checks still bind each operation to a durable task.
Administrator credentials belong only to bootstrap/test setup. Worker output
contains stable keys rather than public or presigned user-data URLs.

The Pod is non-root with capabilities dropped, a read-only root filesystem,
bounded scratch, private dependency endpoints, and no ServiceAccount token.
RabbitMQ's ingress NetworkPolicy admits the selected worker to AMQP; it does not
establish complete worker egress or PostgreSQL/MinIO network isolation. Follow the
[operator guide](../../scripts/README.md) for generated or custom ignored secrets,
never values embedded in Helm.

The [chart](../../helm/adtof/README.md) owns the Deployment and ScaledObject;
KEDA owns the generated HPA and scale subresource. The chart omits replicas and
scales the CPU profile from zero to two workers using **RabbitMQ queue depth only**.

This is a current recovery limitation: ACK removes the queue signal before long
work finishes. PostgreSQL recovery is implemented inside a running Pod, but
there is no PostgreSQL task-count trigger to keep a Pod running or wake the
Deployment from zero for an acknowledged task. Stabilization/cooldown reduce
scale-down risk but do not guarantee recovery after the Deployment reaches zero.
A reviewed durable-task scaling trigger remains necessary for that guarantee.

## Install and verify

Run from the repository root. Fresh full bootstrap initializes dependencies,
identities, schema, policies, and runtime Secrets in order:

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-platform
./k8Deployment/kubernetes/scripts/deploy-local.sh verify
```

On a prepared cluster with this release and its resources absent:

```bash
./k8Deployment/kubernetes/scripts/adtof-release.rb install
./k8Deployment/kubernetes/scripts/adtof-release.rb verify
```

`install` guards prerequisites and conflicting resources. `verify` checks chart,
image lock, source/live/stored Helm manifest, ownership, scaler readiness, and HPA
relationships. It checks idle scale state; run it after cooldown and termination.
Passing `verify` does not prove the post-ACK recovery guarantee above. Raw workload
manifests are comparison baselines, not commands to apply to Helm-owned objects.

## Smoke and troubleshooting

The [worker smoke runbook](../../tests/adtof-worker-smoke/README.md) defines fixed
fixture coordinates, restricted PostgreSQL functions/MinIO policy, and cleanup.
Provision those test prerequisites first; the helper does not bootstrap them.
With an idle release and clean fixture:

```bash
./k8Deployment/kubernetes/scripts/adtof-release.rb smoke
```

The helper runs `adtof-worker-smoke`, waits up to 840 seconds, checks actual generic
dispatch and worker execution, and verifies both private output artifacts.
Successful scoped fixture cleanup precedes deletion of the disposable Job.
Failure leaves evidence for inspection; do not rerun over retained fixture data.

```bash
kubectl --context k3d-clouddsp-local -n clouddsp-app logs job/adtof-worker-smoke
kubectl --context k3d-clouddsp-local -n clouddsp-app get deployment/clouddsp-adtof scaledobject/clouddsp-adtof-rabbitmq-scaler
kubectl --context k3d-clouddsp-local -n clouddsp-app logs deployment/clouddsp-adtof --tail=100
```

An idle Deployment has no Pod to log. The
[exhausted-lease recovery smoke](../../tests/adtof-exhausted-lease-recovery-smoke/README.md)
checks a separate durable terminalization boundary; its controlled conditions do
not remove the scale-from-zero limitation.

## Limitations and history

The local CPU profile is not a CUDA throughput test or a universal transcription
quality benchmark. Explicit task retry classification/scheduling and durable
scaling coverage remain narrower than the other workers. Fresh bootstrap creates
new state and does not restore previous cluster data.

The full staged design and earlier trial evidence are preserved in
[historical implementation notes](../../docs/history/service-adtof-implementation-notes.md).
