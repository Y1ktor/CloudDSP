# MinIO and RabbitMQ stage reference

Use the [operator guide](../../scripts/README.md) for complete deployment.
These helpers separate fresh initialization from narrow existing-cluster
reconciliation. Kubernetes Job completion is a progress check; durable
service configuration is the final success evidence.

## Fresh buckets and shared samples

[minio-fresh-buckets-stage.rb](../../scripts/minio-fresh-buckets-stage.rb)
and [minio-fresh-samples-stage.rb](../../scripts/minio-fresh-samples-stage.rb)
accept `plan`, `bootstrap`, and `verify`.

```bash
ruby ./k8Deployment/kubernetes/scripts/minio-fresh-buckets-stage.rb verify
ruby ./k8Deployment/kubernetes/scripts/minio-fresh-samples-stage.rb verify
```

The bucket bootstrap requires a verified MinIO release and **zero** buckets.
It creates `clouddsp-uploads` and `clouddsp-midi-samples` as private buckets.
An existing or unexpected bucket stops it. Failure after the first creation
leaves that bucket present for inspection; repeating fresh creation is refused.

The sample bootstrap requires exactly those buckets, no sample objects,
no sample policy, and no sample notifications. Its
[Python mirror](../../services/minio/midi_sample_mirror.py) downloads all 461
fixed keys from reviewed sources, checks SHA-256 and size against the
[asset catalog](../../services/minio/midi-sample-assets.lock.json), then
rechecks the empty/private boundary before uploading. It publishes anonymous
`GetObject` only after catalog verification. A partial failed mirror can leave
private sample objects and must be inspected before retrying.

Fresh deployment reads the committed catalog without requiring frontend
packages on the machine. Catalog maintenance derives updated sample names
from the pinned shared frontend dependency; `--record-lock` changes reviewed
inputs and belongs to a deliberate update. The local frontend uses these
shared samples; user uploads, stems, and generated results stay private.

## Existing bucket and notification checks

[minio-state-verify.rb](../../scripts/minio-state-verify.rb) accepts `verify`:

```bash
ruby ./k8Deployment/kubernetes/scripts/minio-state-verify.rb verify
```

It audits immutable policy ConfigMaps, private/public bucket boundaries,
notification metadata, policy attachments, and restricted user identities.
It uses the pinned `mc` client transiently in the existing k3d server node,
with credentials on stdin and an in-memory configuration directory. Client
credential-bearing output is suppressed. This check does not read user
objects, test new notification delivery, or prove an application user's
secret access-key half works.

[minio-buckets-stage.rb](../../scripts/minio-buckets-stage.rb) accepts
`plan`, `verify`, and `reconcile`:

```bash
ruby ./k8Deployment/kubernetes/scripts/minio-buckets-stage.rb verify
ruby ./k8Deployment/kubernetes/scripts/minio-buckets-stage.rb reconcile
```

It requires both expected buckets, private uploads, no sample notifications,
and the exact sample key/size inventory. An extra or missing bucket, wrong
policy, or unexpected object stops the command. Reconciliation can restore
only a wholly absent shared-sample anonymous-read policy. It does not create
buckets or replace missing objects; its inventory verification checks keys
and sizes rather than recomputing every stored content hash.

[minio-notification-stage.rb](../../scripts/minio-notification-stage.rb)
also accepts `plan`, `verify`, and `reconcile`:

```bash
ruby ./k8Deployment/kubernetes/scripts/minio-notification-stage.rb verify
ruby ./k8Deployment/kubernetes/scripts/minio-notification-stage.rb reconcile
```

Only a wholly absent source-upload rule and absent fixed-name Job permit the
versioned writer, after MinIO and RabbitMQ source-intake checks. An existing
Job, partial rule, or changed target/event/prefix stops the stage. The rule
sends `uploads/` ObjectCreated events to the restricted source-intake AMQP
target. MinIO calls this the `put` notification class, which includes the
browser's presigned POST upload.
Durable notification metadata is checked after completion; the Job can later
expire through TTL without losing that evidence.

## Restricted MinIO IAM users

Each helper accepts `plan`, `bootstrap`, `verify`, and `reconcile`:

| Helper | Reviewed runtime capability |
| --- | --- |
| [minio-job-api-iam-stage.rb](../../scripts/minio-job-api-iam-stage.rb) | Upload policy plus artifact-read policy. |
| [minio-upload-intake-iam-stage.rb](../../scripts/minio-upload-intake-iam-stage.rb) | Read only the source-upload scope. |
| [minio-demucs-iam-stage.rb](../../scripts/minio-demucs-iam-stage.rb) | Read private sources and write approved stems. |
| [minio-basic-pitch-iam-stage.rb](../../scripts/minio-basic-pitch-iam-stage.rb) | Read stems and write approved MIDI artifacts. |
| [minio-adtof-iam-stage.rb](../../scripts/minio-adtof-iam-stage.rb) | Read drums and write fixed MIDI/tempo output names. |

Fresh bootstrap requires verified bucket/sample prerequisites and runtime
Secrets. It refuses existing user/policy/ConfigMap/Job/provisioning Secret
state, server-validates inputs, runs the fixed provisioning Jobs, checks exact
policy documents and attachments, then removes temporary Secrets. Job API
uses two policy Jobs; ADTOF's reviewed policy-association container budget is
256 MiB. Failed partial work remains for diagnosis.

```bash
ruby ./k8Deployment/kubernetes/scripts/minio-demucs-iam-stage.rb verify
ruby ./k8Deployment/kubernetes/scripts/minio-demucs-iam-stage.rb reconcile
```

Verification requires complete IAM state and no temporary Secret. Existing
reconcile removes only a leftover provisioning Secret whose values match the
ignored source, after complete IAM verification. It does not create, repair,
or rotate a user or policy. Runtime Secret staging is described in the
[credential reference](credentials-and-identities.md); active authorization
smokes are in the [test-tool reference](image-builds-and-smoke-tools.md).

## RabbitMQ topology and source intake

[rabbitmq-processing-topology.rb](../../scripts/rabbitmq-processing-topology.rb)
and [rabbitmq-source-intake-bootstrap.rb](../../scripts/rabbitmq-source-intake-bootstrap.rb)
accept `plan`, `verify`, and `reconcile`:

```bash
ruby ./k8Deployment/kubernetes/scripts/rabbitmq-processing-topology.rb verify
ruby ./k8Deployment/kubernetes/scripts/rabbitmq-source-intake-bootstrap.rb verify
```

Processing topology owns the reviewed processing exchanges, quorum queues,
and bindings. Source-intake topology owns its separate source exchanges,
queues/bindings, MinIO publisher, and upload-intake consumer identities. The
latter checks exact restricted permissions and both logins against local/live
sources without printing passwords.

Reconciliation creates the versioned ConfigMap/Job only for wholly absent
state, then checks durable broker configuration. Existing correct state is a
no-op; partial/drifted state or an unexplained fixed-name Job stops the stage.
Source intake uses a temporary provisioning Secret and removes it after user
and topology verification. Other processing consumers/publishers use the
[application identity runner](credentials-and-identities.md). RabbitMQ
reconciliation does not configure MinIO notifications or start upload-intake.
