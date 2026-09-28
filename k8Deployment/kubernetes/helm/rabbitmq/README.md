# RabbitMQ Helm release

This chart owns the existing `StatefulSet/clouddsp-rabbitmq`, normal AMQP
`Service/clouddsp-rabbitmq`, governing headless
`Service/clouddsp-rabbitmq-headless`, private metrics
`Service/clouddsp-rabbitmq-management`, and
`NetworkPolicy/clouddsp-rabbitmq-ingress` in `clouddsp-data`. The generated
`PersistentVolumeClaim/rabbitmq-data-clouddsp-rabbitmq-0`, its bound PV,
durable messages, broker users and topology, runtime Secret, KEDA resources,
and one-shot bootstrap Jobs remain outside the Helm manifest. The image is
pinned by [`images.rabbitmq.immutableReference`](../../images.lock.yaml).

The templates retain the source manifests' names, selector, headless Service
linkage, claim template and retention policy, three existing Service
contracts, Secret references, probes, and management ingress rules. The
[`source manifests`](../../services/rabbitmq/) remain comparison baselines;
do not apply them over Helm-owned objects.

## Fresh-cluster credential prerequisite

The Helm chart references but does not own the administrator Secret. After
the fresh foundation creates `clouddsp-data`, run:

```bash
ruby ./k8Deployment/kubernetes/scripts/rabbitmq-secret-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/rabbitmq-secret-stage.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/rabbitmq-secret-stage.rb verify
```

The populated manifest stays under ignored `.local/` configuration. The
stage refuses to replace an existing Secret and suppresses credential values.
A guarded fresh StatefulSet Helm install is the next release step.

## Protected adoption and checks

From the repository root:

```bash
./k8Deployment/kubernetes/scripts/rabbitmq-release.rb plan
./k8Deployment/kubernetes/scripts/rabbitmq-release.rb adopt
./k8Deployment/kubernetes/scripts/rabbitmq-release.rb verify
./k8Deployment/kubernetes/scripts/rabbitmq-release.rb smoke
```

`plan` performs strict chart lint, image-lock and source/render/live spec
comparisons, API-server dry run, Ready-Pod and bound-PVC checks. `adopt`
repeats those checks and runs the versioned
[`rabbitmq-backup-and-restore-test.py`](../../scripts/rabbitmq-backup-and-restore-test.py)
before Helm takeover. The backup requires no unacknowledged messages, briefly
scales **only RabbitMQ** to zero, archives the bound node-local PVC directory,
and restores the original replica. It confirms the StatefulSet and PVC/PV
identities and checks that full broker definitions and queue depths still
match. The script then restores the archive into a disposable Linux Docker
volume and starts the pinned image with the same short node name and no
network. It compares the restored broker's definitions and queue depths
without printing user password hashes or message data.

Only after that restore rehearsal does Helm take ownership of five existing
objects. The release script checks their UIDs, the post-backup Pod UID, three
Service IPs, bound PVC/PV identity, and running image digest. A failed
takeover remains available for inspection; do not uninstall the release or
delete the claim as a recovery shortcut. `verify` rechecks Helm's stored
manifest and live state. `smoke` creates the versioned
[`rabbitmq-amqp-smoke`](../../tests/rabbitmq-smoke/rabbitmq-amqp-smoke-job.yaml)
Job through the ordinary ClusterIP Service, verifies AMQP
publish/consume/acknowledge, and removes that disposable Job.

On 2026-09-27, the protected snapshot restored two vhosts and 12 queues.
All queues had zero ready and zero unacknowledged messages at backup time.
Helm release `clouddsp-rabbitmq` reached revision 1 without replacing any
adopted resource, the post-backup Pod, or the bound PVC/PV. The in-cluster
AMQP smoke passed. Since the queues were empty at snapshot time, this restore
rehearsal checked topology and queue state but did not replay a saved message.

## Backup and recovery boundary

The archive and small verification manifest remain under ignored
`k8Deployment/.local/backups/rabbitmq-<timestamp>-<pid>/`, with owner-only
directory and file permissions. The archive includes the Erlang cookie,
password hashes, topology, and any messages present at backup time. Keep it
private and arrange an encrypted off-machine copy if loss of this laptop
must be recoverable. The isolated test uses a disposable Docker volume and
deletes that volume after verification; it never mounts or overwrites the
live PVC.

For a real data-loss recovery, stop publishers and consumers, preserve the
original PVC, and restore the archive into a **new empty** volume while the
replacement broker is stopped. Start the reviewed image with the same node
name, then verify broker definitions, queues, application credentials,
management access, and AMQP smoke before switching clients. There is no
automated in-place restore command because that could overwrite authoritative
current message state.
