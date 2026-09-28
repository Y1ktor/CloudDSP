# MinIO Helm release

This chart owns the existing `StatefulSet/clouddsp-minio`, normal
`Service/clouddsp-minio`, governing headless
`Service/clouddsp-minio-headless`, and browser S3
`Ingress/clouddsp-minio-s3` in `clouddsp-data`. The generated
`PersistentVolumeClaim/minio-data-clouddsp-minio-0`, its bound PV, all buckets
and objects, IAM state, runtime Secrets, and policy/notification bootstrap
Jobs remain outside the Helm manifest. The server image is pinned by
[`images.minio.immutableReference`](../../images.lock.yaml).

The templates retain the source manifests' names, selectors, headless Service
linkage, claim template and retention policy, S3 origin, AMQP notification
configuration, Secret references, and probes. The
[`source manifests`](../../services/minio/) remain comparison baselines; do
not apply them over Helm-owned objects.

## Fresh-cluster root credential prerequisite

The chart references but does not own the root administrator Secret. After
the fresh foundation creates `clouddsp-data`, populate the ignored `.local/`
manifest from the committed example, then run:

```bash
ruby ./k8Deployment/kubernetes/scripts/minio-root-secret-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/minio-root-secret-stage.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/minio-root-secret-stage.rb verify
```

The stage creates only an absent Secret and checks live values against the
ignored source without displaying them. A separate RabbitMQ notification
Secret and broker identity are also required by the StatefulSet. Those and a
guarded fresh MinIO Helm install remain later deployment steps.

## Protected adoption and checks

From the repository root:

```bash
./k8Deployment/kubernetes/scripts/minio-release.rb plan
./k8Deployment/kubernetes/scripts/minio-release.rb adopt
./k8Deployment/kubernetes/scripts/minio-release.rb verify
./k8Deployment/kubernetes/scripts/minio-release.rb smoke
```

`plan` performs strict chart lint, image-lock and source/render/live spec
comparisons, API-server dry run, Ready-Pod and bound-PVC checks. `adopt`
repeats those checks and runs the versioned
[`minio-backup-and-restore-test.py`](../../scripts/minio-backup-and-restore-test.py)
before Helm takeover. The backup briefly scales **only MinIO** to zero so
the node-local PVC directory can be archived consistently, then restores its
single replica and checks the original StatefulSet, PVC, and PV identities.
The script compares bucket/object-version and notification inventories
before and after the pause; it also starts a disposable server from the
archive, compares its inventory, and checks one object's downloaded bytes.
The isolated server receives the same AMQP target configuration so a saved
bucket notification remains visible through the S3 API.

After a successful restore rehearsal, Helm takes ownership of exactly four
long-lived resources. The script checks their UIDs, the post-backup Pod UID,
both Service IPs, bound PVC/PV identity, running image digest, and S3 health
route. A failed takeover remains available for inspection; do not uninstall
the release or delete the claim as a recovery shortcut. `verify` checks Helm's
stored manifest and live state. `smoke` creates the versioned
[`minio-s3-api-smoke`](../../tests/minio-smoke/minio-s3-api-smoke-job.yaml)
Job, verifies an in-cluster S3 create/read/delete round trip, then removes
that Job.

On 2026-09-27, the tested snapshot contained two buckets and 489 current
objects. The isolated restore matched their object-version and notification
inventories and an object download. Helm release `clouddsp-minio` reached
revision 1 without replacing any adopted resource, the post-backup Pod, or
the bound PVC/PV. The S3 API smoke passed. The separate restricted Job API
smoke also passed, including denials for writes outside `uploads/*` and
deletion of its fixed verification object.

## Backup and recovery boundary

The archive and a small verification manifest are retained under ignored
`k8Deployment/.local/backups/minio-<timestamp>-<pid>/`, with owner-only
directory and file permissions. The archive includes private objects, user
data, IAM state, and MinIO metadata. Keep it private and make an encrypted
off-machine copy if recovery from loss of this laptop is required. The script
does not overwrite the live PVC during its restore test: it extracts into a
temporary directory and removes that test copy afterward.

For a real data-loss recovery, stop application writers and preserve the
original PVC for inspection. Create a **new empty** volume and restore the
archive into it while the replacement MinIO server is stopped. Start that
server with the reviewed image, root/AMQP Secrets, and same configuration;
compare bucket, version, notification, and IAM behavior, then run the S3 and
restricted-access smoke tests before switching clients. There is deliberately
no automated in-place restore command, because it would overwrite the
authoritative current PVC.
