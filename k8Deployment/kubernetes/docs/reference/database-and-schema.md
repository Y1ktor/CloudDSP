# PostgreSQL database and schema stages

The [operator guide](../../scripts/README.md) is the entry point for complete
fresh deployment and existing-cluster verification. These focused helpers
assume the PostgreSQL Helm release and required credential sources exist.
They do not install PostgreSQL or other services.

## Job API database ownership

[job-api-database-bootstrap.rb](../../scripts/stages/database/job-api-database-bootstrap.rb)
accepts `plan`, `verify`, and `reconcile`:

```bash
ruby ./k8Deployment/kubernetes/scripts/stages/database/job-api-database-bootstrap.rb plan
ruby ./k8Deployment/kubernetes/scripts/stages/database/job-api-database-bootstrap.rb verify
ruby ./k8Deployment/kubernetes/scripts/stages/database/job-api-database-bootstrap.rb reconcile
```

It verifies the `clouddsp_job_api` database, restricted `clouddsp-job-api`
login, database/schema ownership, and reviewed grants through PostgreSQL
catalog metadata. Matching durable state makes reconciliation a no-op.
One-sided state, changed ownership/grants, an unexplained fixed-name Job,
or a leftover temporary Secret stops the stage.

Only when both database and role are absent can reconciliation create the
versioned Job and temporary `clouddsp-data` Secret. It first checks matching
ignored runtime/bootstrap sources and the live runtime Secret. After Job
completion it checks the catalog result and deletes the temporary Secret.
Failure leaves evidence for inspection. This is initialization, not password
rotation or database/data restoration.

## Migration ledger and fresh ordering

[job-api-migrations.rb](../../scripts/stages/database/job-api-migrations.rb) supports:

```bash
ruby ./k8Deployment/kubernetes/scripts/stages/database/job-api-migrations.rb plan
ruby ./k8Deployment/kubernetes/scripts/stages/database/job-api-migrations.rb verify
ruby ./k8Deployment/kubernetes/scripts/stages/database/job-api-migrations.rb reconcile-prerequisites
ruby ./k8Deployment/kubernetes/scripts/stages/database/job-api-migrations.rb reconcile
```

`plan` reads the `schema_migrations` ledger and immutable SQL ConfigMaps;
`verify` also requires every current migration to be present. The accepted
ledger is an exact prefix of reviewed v001–v009 identifiers/descriptions.
Every already-applied SQL ConfigMap must still match its versioned source.
The ledger is read through the existing PostgreSQL Pod's Secret-backed
connection; the helper does not query user job data.

The full fresh bootstrap uses this order:

1. Create the Job API database/schema owner and runtime Secret.
2. Apply v001–v006 with `reconcile-prerequisites`.
3. Bootstrap the Basic Pitch and ADTOF database roles.
4. Apply v007–v009 with `reconcile`.
5. Verify the complete database/schema state before installing the Job API.

The worker roles must exist before v007. A pending migration is server
validated, created as its reviewed ConfigMap/Job, awaited, and checked against
the next ledger row before another migration runs. A failed Job or a Job
without a matching ledger row remains for diagnosis; Job absence does not
prove migration success. Repeated reconciliation of a complete ledger performs
no migration write. Historic migration SQL is immutable.

[job-api-postgresql-stage.rb](../../scripts/stages/database/job-api-postgresql-stage.rb)
combines database ownership and the complete migration runner:

```bash
ruby ./k8Deployment/kubernetes/scripts/stages/database/job-api-postgresql-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/stages/database/job-api-postgresql-stage.rb verify
ruby ./k8Deployment/kubernetes/scripts/stages/database/job-api-postgresql-stage.rb reconcile
```

Its full reconcile is appropriate after worker roles exist. Fresh bootstrap
uses the staged order above. If the database is absent, its plan defers ledger
inspection until database creation is possible.

## Worker roles and other service databases

[worker-database-stage.rb](../../scripts/stages/database/worker-database-stage.rb) accepts
`basic-pitch|adtof plan|bootstrap|verify`:

```bash
ruby ./k8Deployment/kubernetes/scripts/stages/database/worker-database-stage.rb basic-pitch verify
ruby ./k8Deployment/kubernetes/scripts/stages/database/worker-database-stage.rb adtof verify
```

Fresh bootstrap checks its prerequisite migration and matching runtime/
bootstrap sources, creates the runtime Secret, provisions grants with a
versioned Job, verifies durable privileges, then removes the temporary
provisioning Secret. Verification checks the role and live runtime Secret
without changing either. Other application roles and the read-only KEDA
observer use [application identity stages](credentials-and-identities.md).

[keycloak-database-stage.rb](../../scripts/stages/keycloak/keycloak-database-stage.rb)
accepts `plan`, `bootstrap`, and `verify`:

```bash
ruby ./k8Deployment/kubernetes/scripts/stages/keycloak/keycloak-database-stage.rb verify
```

It owns the separate Keycloak role/database initialization. Plan distinguishes
wholly absent and complete state; bootstrap creates only wholly absent state
through the versioned Job. It does not install Keycloak, configure its realm,
rotate passwords, or repair a partial database. Verification checks database
ownership/grants and authenticates using the ignored local credential via
stdin without printing it.

## Stateful release and maintenance boundary

The [PostgreSQL chart](../../helm/postgresql/README.md) owns the StatefulSet
and its normal/headless Services. The generated PVC stores local service
data and is checked by the release verifier. Schema migrations, roles,
runtime Secrets, and one-shot Jobs have separate ownership.

```bash
ruby ./k8Deployment/kubernetes/scripts/releases/postgresql-release.rb verify
```

Fresh `install` refuses an existing release, workload, Service, matching Pod,
or retained claim. Optional one-time `adopt` runs the versioned
[backup/restore rehearsal](../../scripts/maintenance/postgresql-backup-and-restore-test.sh)
before taking ownership; that maintenance test writes an owner-only archive
and restores it in an isolated container. It is not part of fresh bootstrap
and is not a general restore path. See [Helm release reference](helm-releases-and-scaling.md).
