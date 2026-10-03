# PostgreSQL Helm release

This chart owns the existing `StatefulSet/clouddsp-postgresql`, ordinary
`Service/clouddsp-postgresql`, and governing headless
`Service/clouddsp-postgresql-headless` in `clouddsp-data`. The generated
`PersistentVolumeClaim/postgres-data-clouddsp-postgresql-0`, its bound PV,
database contents, runtime Secret, migrations, roles, and bootstrap Jobs stay
outside the Helm manifest. The chart's immutable image reference comes from
[`images.postgresql.immutableReference`](../../images.lock.yaml).

The templates retain the names, Pod selector, headless Service linkage,
`volumeClaimTemplates` name/class/access/size, `Retain` claim policy,
one-replica rollout behavior, Secret references, and probes from the
[`source manifests`](../../services/postgresql/). The source manifests remain
comparison baselines and must not be reapplied to Helm-owned resources.

## Fresh-cluster installation

On an absent target cluster, this partial root command runs foundation and
image preparation, credential creation, PostgreSQL install, and verification:

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-postgresql
```

### Credential prerequisite

The PostgreSQL chart does not own its administrator Secret. If the fresh
foundation has already created `clouddsp-data`, run the versioned credential
stage before installing the StatefulSet:

```bash
ruby ./k8Deployment/kubernetes/scripts/stages/credentials/postgresql-secret-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/stages/credentials/postgresql-secret-stage.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/stages/credentials/postgresql-secret-stage.rb verify
```

The populated manifest remains under ignored `.local/` configuration. The
stage refuses an existing Secret and never displays its values. After this
credential stage, the fresh release path is:

```bash
./k8Deployment/kubernetes/scripts/releases/postgresql-release.rb install
./k8Deployment/kubernetes/scripts/releases/postgresql-release.rb verify
```

`install` requires an absent Helm release, StatefulSet, both Services, the
generated PVC, and matching Pod. It rechecks the credential Secret, uses
ordinary Helm install without takeover flags, waits for the StatefulSet,
and verifies its Pod and bound claim. It does not run the adoption backup
gate because this path starts without a data claim. A failed or partial
install is retained for inspection; a clean-cluster trial remains pending.

## Protected adoption and checks

From the repository root:

```bash
./k8Deployment/kubernetes/scripts/releases/postgresql-release.rb plan
./k8Deployment/kubernetes/scripts/releases/postgresql-release.rb adopt
./k8Deployment/kubernetes/scripts/releases/postgresql-release.rb verify
./k8Deployment/kubernetes/scripts/releases/postgresql-release.rb smoke
```

`plan` runs strict Helm lint, image-lock and source/render/live comparisons,
API-server dry run, Ready-Pod check, and bound-PVC contract check. An existing
`kubectl.kubernetes.io/restartedAt` Pod-template annotation is treated as an
operational rollout marker; no other spec drift is allowed. A separate
server-side apply dry run confirmed that marker would remain on the live Pod.

`adopt` repeats the preflight, then automatically runs
[`postgresql-backup-and-restore-test.sh`](../../scripts/maintenance/postgresql-backup-and-restore-test.sh).
Only after that fresh backup restores successfully into a disposable Docker
PostgreSQL instance with `--network none` does Helm take ownership. The
script checks the StatefulSet and both Service UIDs, Pod UID, ordinary Service
IP, generated PVC UID/name, and bound PV name before and after takeover. It
does not uninstall or automatically roll back a failed takeover; doing so
could delete Helm-owned objects. `verify` rechecks release, source/live spec,
readiness, and PVC identity. `smoke` runs the
[`postgresql-read-write-smoke`](../../tests/postgresql-smoke/postgresql-read-write-smoke-job.yaml)
Job through the ClusterIP Service, then deletes that exact completed Job.

On 2026-09-27, the backup restored successfully with four databases and
fourteen login roles. Adoption produced release `clouddsp-postgresql`
revision 1 without replacing any of its three resources, the running Pod, or
the bound claim/PV. The read/write smoke created, read, and dropped its
isolated test table successfully.

## Backup and recovery boundary

The backup script writes an owner-only SQL file under ignored
`k8Deployment/.local/backups/`. It includes all database data, roles, and
password hashes. Keep the file private and arrange an encrypted off-machine
copy if local-machine recovery is needed; a file beside the local cluster is
not protection against losing that machine. The script verifies the dump by
restoring it with `ON_ERROR_STOP` into a disposable container, then comparing
database and login-role counts. Its scratch container publishes no port,
has no Docker network, and never mounts the live PVC.

If the live database fails, preserve the PVC and first inspect the Pod,
StatefulSet, and Helm release; do not uninstall the release or delete the
claim. For data loss that requires restoration, stop application writers,
provision a **new empty** PostgreSQL 17 instance and separate restore-control
database, replay the retained SQL backup with `psql --set=ON_ERROR_STOP=1`,
then verify database/role counts, migration ledger, Keycloak discovery/login,
and Job API authenticated reads before routing traffic to the replacement.
The tested script provides the exact isolated restore rehearsal. Restoration
into the current live PVC is intentionally not automated because it would
overwrite authoritative identity and job data.
