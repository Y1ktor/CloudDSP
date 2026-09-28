# Local cluster scripts

These non-interactive scripts manage only the versioned local k3d profile
declared in [`../cluster/k3d.yaml`](../cluster/k3d.yaml). Run the commands from
the repository root, or change into this directory first; each script resolves
its own location and does not depend on the current working directory.

## Root deployment plan

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh plan
./k8Deployment/kubernetes/scripts/deploy-local.sh prepare
./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-mailpit
./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-postgresql
./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-rabbitmq
./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-minio
./k8Deployment/kubernetes/scripts/deploy-local.sh verify
./k8Deployment/kubernetes/scripts/deploy-local.sh reconcile
./k8Deployment/kubernetes/scripts/deploy-local.sh cleanup
./k8Deployment/kubernetes/scripts/deploy-local.sh purge-registry
```

The `plan` mode is the first stage of the
[deployment orchestrator](../deployment-orchestration-plan.md). It reads the
versioned workload manifests and lock files, then queries only the explicit
`k3d-clouddsp-local` context. It checks namespace and Helm ownership,
StatefulSet selectors/claim templates and bound PVCs, immutable image
references, and the *names* of runtime Secrets. It checks for matching ignored
local Secret filenames and committed example contracts without printing Secret
values. The command makes no cluster changes and returns nonzero when a
blocking inconsistency is found; warnings identify work needed before a fresh
bootstrap. Ruby's standard YAML/JSON libraries are required on the host.

## Fresh-cluster foundation stage

```bash
ruby ./k8Deployment/kubernetes/scripts/deploy-local-foundation.rb plan
ruby ./k8Deployment/kubernetes/scripts/deploy-local-foundation.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/deploy-local-foundation.rb verify
```

This standalone first stage validates the pinned k3d topology and three
versioned Namespace definitions. `plan` succeeds when the target cluster is
absent and either a healthy retained registry exists or a new one can be
created. `bootstrap` uses the existing
`cluster.sh create` command, waits for all nodes to become Ready, then creates
the namespaces after a Kubernetes server dry run. It verifies the exact node
roles, registry endpoint, namespace labels, and packaged CoreDNS, Traefik,
and local-path provisioner Deployments. An existing cluster stops before
mutation; a failure after cluster creation leaves that cluster
for explicit inspection rather than deleting it. On the current deployed
cluster, `verify` passes and `plan` correctly refuses fresh creation.

The root `prepare` command now composes this foundation with the image mirror.
It first refuses an existing cluster, validates every published Docker Hub
digest, creates the foundation, mirrors any missing locked images, then checks
both registries. It stops on the first failure and leaves a created cluster
for inspection. `prepare` does not install KEDA, create runtime Secrets,
install Helm releases, or bootstrap external state. The full root
`deploy-local.sh bootstrap` mode will be added after those stages have
reviewed fresh-cluster paths.

`bootstrap-mailpit` is the first root composition beyond `prepare`. On an
absent cluster it runs foundation and image preparation, the guarded Mailpit
`install`, and Mailpit `verify` in that order. A failure stops later stages
and leaves created resources for inspection. It does not install the remaining
CloudDSP releases or bootstrap application data; the full root `bootstrap`
mode remains pending. Use the separate Mailpit smoke command below when SMTP
capture needs a functional check.

`bootstrap-postgresql` is the parallel root slice for the first data service.
It runs `prepare`, creates and verifies the ignored PostgreSQL credential
Secret, installs the PostgreSQL Helm release, then verifies its Ready Pod and
bound PVC. A failure stops later stages and leaves the partial cluster or
release for inspection. The Mailpit and PostgreSQL partial commands each
start from an absent cluster; they are alternative trials, not sequential
commands to run against the same cluster. The eventual full `bootstrap` will
compose their component stages in one dependency order.

`bootstrap-rabbitmq` is the corresponding partial root command for the
broker. It runs `prepare`, creates and verifies the ignored administrator
Secret, installs the RabbitMQ Helm release, then verifies its Ready Pod and
bound PVC. Each child must succeed before the next starts. This command also
requires an absent cluster, so it is an alternative clean-cluster trial to
the Mailpit and PostgreSQL partial commands. The eventual full `bootstrap`
will run these component stages together in dependency order.

`bootstrap-minio` composes the broker and object-storage slice from an absent
cluster. It reuses `bootstrap-rabbitmq` for foundation, images, administrator
Secret, and broker Helm install. It then creates the MinIO root/AMQP and
upload-intake RabbitMQ runtime Secrets, reconciles and verifies the restricted
source-intake broker users and topology, and only then installs and verifies
MinIO. It creates the Job API runtime MinIO Secret and the two initially
private MinIO buckets, mirrors 461 hash-locked shared MIDI samples, and grants
their bucket a narrow anonymous browser-read policy. It then provisions the
restricted Job API user and both reviewed IAM policies.
The broker runner manages its temporary bootstrap Secret and removes it after
successful user verification. A failed stage leaves partial state
for inspection. This is an alternative partial trial to the other
`bootstrap-*` commands, not a command to run after them on the same cluster.
It configures the Job API MinIO identity, while other IAM users and application
releases remain for later stages.
The full root `bootstrap` will compose those remaining stages later.

## Docker Hub image source and local mirror

```bash
./k8Deployment/kubernetes/scripts/image-registry-stage.rb plan
./k8Deployment/kubernetes/scripts/image-registry-stage.rb verify-source
./k8Deployment/kubernetes/scripts/image-registry-stage.rb publish
./k8Deployment/kubernetes/scripts/image-registry-stage.rb mirror
./k8Deployment/kubernetes/scripts/image-registry-stage.rb verify
```

The public [`y1ktor/clouddsp`](https://hub.docker.com/r/y1ktor/clouddsp)
repository holds one tag per locally built image in `images.lock.yaml`.
`publish` copies the reviewed local image into that Docker Hub tag using the
saved Docker CLI login; it stops if either side has a different digest.
`verify-source` checks anonymous Docker Hub access and all locked public
digests before a fresh cluster is created; it does not contact the local
registry. `mirror` works in the other direction after the local registry
exists. It pulls a public Docker Hub image by its immutable digest, pushes it
into the matching local repository, and checks that the digest was preserved.
Existing matching local images are skipped. `verify` checks both registries
without writing. The lock remains the authority; none of these modes rewrites it.

All 18 current local images target Linux ARM64. The public repository and
every tag were verified by anonymous manifest requests. A Job API image was
pulled from Docker Hub and pushed to a disposable empty `.localhost` registry
with its digest unchanged. A complete fresh-registry `mirror` run and full root
`bootstrap` wiring are still pending; normal `cleanup` retains the populated
registry in the meantime.

## Root verification and existing-cluster reconcile

`verify` first runs that same preflight, checks the PostgreSQL, RabbitMQ,
MinIO, Job API MinIO, and upload-intake credential Secrets against their
ignored local sources without printing values, then checks the fourteen adopted CloudDSP
Helm releases, the implemented Job API PostgreSQL and RabbitMQ bootstrap
stages, the
[Keycloak realm/client state](keycloak-config-verify.rb),
the [MinIO bucket stage](minio-buckets-stage.rb), the
[Job API IAM stage](minio-job-api-iam-stage.rb), and
[IAM/notification state](minio-notification-stage.rb),
and the KEDA controller Deployments and CRDs in dependency order.
It stops at the first failed gate and names the component command to run for
focused diagnosis. It invokes no adopt, reconcile, bootstrap, or smoke mode;
therefore it does not create test Jobs or alter the live cluster. The Keycloak
gate reads the committed bootstrap Job payloads and checks realm
registration, SMTP, the public React PKCE client, Job API resource client,
access-token audience mapper, password registration form, and public issuer
through the live Admin API. The MinIO gate compares source and live immutable
policy ConfigMaps, both bucket boundaries, the prefix-limited AMQP notification,
IAM policy documents and attachments, and the five restricted users. It uses
the pinned local `mc` image in a transient container with credentials sent over
stdin and a temporary in-memory config directory. Both gates are read-only
and suppress credential-bearing client output on failure.

`reconcile` is currently for an already deployed cluster. It runs the same
preflight and ordered gates, but calls `reconcile` for six audited
external-state runners: Job API PostgreSQL database/migrations, RabbitMQ
processing topology, RabbitMQ source-intake topology/users, the MinIO shared
sample policy, Job API MinIO temporary Secret cleanup, and the MinIO
source-upload notification. The IAM stage deletes only a matching temporary
Secret after the exact user and both policies verify. Other runners
create only wholly missing versioned bootstrap state and verify its durable
result; partial state and drift stop the command. All fourteen Helm releases
remain in read-only `verify` mode, so a chart change, missing release, or
unexpected owner fails rather than triggering an unsafe takeover or revision.
This mode does not create a fresh cluster, install/upgrade Helm releases,
recreate missing MinIO buckets, create or change MinIO IAM policies/users, or
reconcile Keycloak realm state,
or run product smoke tests; it
fails if either read-only gate finds drift. The initial
existing-cluster trial passed 23 gates twice; the repeated run left
all fourteen Helm release revisions and retained bootstrap ConfigMaps unchanged
and created no bootstrap Job.
With the versioned Keycloak state check added, both root `verify` and
existing-cluster `reconcile` passed all 24 gates on the current cluster.
With the versioned MinIO state check added, both passed all 25 gates.
The MinIO notification stage now allows a wholly absent source-upload rule to
be restored through its fixed-name versioned Job. The existing-cluster trial
passed all 25 gates with the rule already present, so it made no MinIO write.
The shared-sample bucket stage adds a 26th gate. Its existing-cluster trial
found both bucket boundaries and all 461 locked sample keys and sizes already
correct, so it made no MinIO write.

For focused MinIO diagnosis, run:

```bash
ruby ./k8Deployment/kubernetes/scripts/minio-state-verify.rb verify
```

This checks metadata and IAM configuration without uploading, reading, or
deleting objects. It requires the existing local k3d server node, its cached
pinned `mc` image, `kubectl`, Docker, and the AWS CLI. It does not validate
delivery of a new notification message to a consumer or the secret half of an
application user's access key.

For the focused bucket boundary stage, run one of:

```bash
ruby ./k8Deployment/kubernetes/scripts/minio-buckets-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/minio-buckets-stage.rb verify
ruby ./k8Deployment/kubernetes/scripts/minio-buckets-stage.rb reconcile
```

This stage requires both expected buckets to exist. It refuses an extra or
missing bucket, any bucket policy on private uploads, notifications on the
shared-sample bucket, and any sample key or size outside the reviewed lock.
Only a wholly absent shared-sample policy can be restored. The write uses the
committed anonymous `GetObject` policy and then verifies its S3 metadata.
It never creates an empty replacement for a potentially lost bucket; sample
content hashes and the fresh-cluster bucket stage remain separate work.

For a focused notification stage, run one of:

```bash
ruby ./k8Deployment/kubernetes/scripts/minio-notification-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/minio-notification-stage.rb verify
ruby ./k8Deployment/kubernetes/scripts/minio-notification-stage.rb reconcile
```

An absent rule and absent fixed-name Job permit exactly one
versioned bootstrap Job after MinIO release and RabbitMQ source-intake checks.
An existing Job, partial rule, or changed target/event/prefix stops the stage
for inspection. The completed Job is retained until Kubernetes TTL removes
it; the S3 notification metadata is the durable success check.

The general `plan` reports resource ownership and source/live identity but
does not render a chart diff. The separate
[Mailpit](mailpit-release.rb), [Keycloak](keycloak-release.rb),
[PostgreSQL](postgresql-release.rb),
[MinIO](minio-release.rb),
[RabbitMQ](rabbitmq-release.rb),
[shared KEDA scaling authentication](scaling-auth-release.rb),
[Basic Pitch worker](basic-pitch-release.rb),
[ADTOF worker](adtof-release.rb),
[Demucs worker](demucs-release.rb),
[frontend](frontend-release.rb),
[legacy dispatcher](dispatcher-release.rb),
[generic dispatcher](generic-dispatcher-release.rb),
[Job API](job-api-release.rb), and
[upload-intake](upload-intake-release.rb) release
scripts perform component-specific render and live-spec comparisons. The
`plan` and `verify` do not install, adopt, migrate, bootstrap, or clean up
anything. The [resource ownership map](../resource-ownership-map.md) records the full
versioned/live snapshot and proposed boundaries.

## Mailpit Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/mailpit-release.rb plan
./k8Deployment/kubernetes/scripts/mailpit-release.rb adopt
./k8Deployment/kubernetes/scripts/mailpit-release.rb install
./k8Deployment/kubernetes/scripts/mailpit-release.rb verify
./k8Deployment/kubernetes/scripts/mailpit-release.rb smoke
```

The [Mailpit chart](../helm/mailpit/README.md) owns only the existing
Deployment, two Services, and browser Ingress in `clouddsp-data`. Run `plan`
before its one-time `adopt` operation. Once it is adopted, use `verify` for
read-only ownership, readiness, and route checks; `smoke` creates only the
versioned disposable SMTP capture Job and removes it after success. The
script stops on unknown ownership or any rendered/source/live spec drift.
The raw manifests under `services/mailpit/` are now an adoption baseline and
must not be reapplied to the Helm-owned objects.

For a new cluster after `deploy-local.sh prepare`, use `install` instead of
`adopt`. It lints/renders the same chart, checks its image lock and Kubernetes
schema, requires the Mailpit release and all four named objects to be absent,
then installs without Helm takeover flags. It waits for readiness and checks
the installed manifest, live objects, Pod, and ingress route. A failed install
is left for inspection; rerunning `install` against an existing release or a
partial set of objects stops before another Helm write. This standalone
Mailpit path does not yet make `prepare` a complete application bootstrap.

## PostgreSQL credential Secret stage

```bash
ruby ./k8Deployment/kubernetes/scripts/postgresql-secret-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/postgresql-secret-stage.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/postgresql-secret-stage.rb verify
```

This fresh-cluster prerequisite uses the ignored
`k8Deployment/.local/postgresql-credentials.secret.yaml` file. It checks the
committed example's Secret identity, labels, type, and required keys, rejects
its placeholder password, and keeps all populated values out of output.
`plan` requires the Secret to be absent. `bootstrap` performs a Kubernetes
server dry run, creates that one Secret only when absent, then compares the
live encoded data with the ignored source in memory. It refuses an existing
Secret, even if matching; `verify` checks an existing Secret read-only and is
now also part of root `verify` and `reconcile`. A fresh PostgreSQL StatefulSet
Helm install and root bootstrap wiring remain separate work.

## PostgreSQL protected Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/postgresql-release.rb plan
./k8Deployment/kubernetes/scripts/postgresql-release.rb adopt
./k8Deployment/kubernetes/scripts/postgresql-release.rb install
./k8Deployment/kubernetes/scripts/postgresql-release.rb verify
./k8Deployment/kubernetes/scripts/postgresql-release.rb smoke
```

The [PostgreSQL chart](../helm/postgresql/README.md) owns the existing
StatefulSet, normal ClusterIP Service, and governing headless Service. Its
script verifies source/render/live equality and the bound generated PVC.
Before `adopt` can call Helm, the versioned
[`backup and restore rehearsal`](postgresql-backup-and-restore-test.sh)
captures all databases and roles into an ignored owner-only file, restores
them in an isolated Docker container with no network, and compares counts.
Adoption preserved resource and Pod UIDs, Service IPs, and PVC/PV identity.
`smoke` ran the versioned read/write Job through the ordinary Service and
removed its disposable table and Job. The generated PVC, database contents,
Secret, migrations, and bootstrap Jobs remain outside Helm ownership.

On a fresh prepared cluster, first run the credential Secret stage above,
then use `postgresql-release.rb install`. This mode requires the release,
StatefulSet, both Services, generated PVC, and matching Pod to be absent. It
verifies the credential Secret before ordinary Helm install, waits up to five
minutes for the single StatefulSet Pod, then checks Helm ownership, the
reviewed manifest, Ready Pod, and bound PVC contract. It never calls the
adoption backup gate or passes takeover flags. Any partial prior release or
retained claim stops before installation; this path has isolated guard tests
but still needs a clean-cluster trial.

## Job API PostgreSQL bootstrap and migration order

```bash
./k8Deployment/kubernetes/scripts/job-api-postgresql-stage.rb plan
./k8Deployment/kubernetes/scripts/job-api-postgresql-stage.rb verify
./k8Deployment/kubernetes/scripts/job-api-postgresql-stage.rb reconcile
```

This narrow combined command runs the database/role stage first and the schema
migration stage second. On a fresh cluster, `plan` reports the pending
database bootstrap and defers migration inspection until the database exists.
`verify` fails if either stage is incomplete. `reconcile` waits for the
database bootstrap and its metadata checks to finish before starting missing
schema migrations; a failure in either stage stops the command. The same two
stages remain independently runnable for diagnosis. This command assumes the
PostgreSQL StatefulSet and the ignored runtime credentials are available; it
does not install other services or Helm releases.

## Job API database and schema-owner bootstrap stage

```bash
./k8Deployment/kubernetes/scripts/job-api-database-bootstrap.rb plan
./k8Deployment/kubernetes/scripts/job-api-database-bootstrap.rb verify
./k8Deployment/kubernetes/scripts/job-api-database-bootstrap.rb reconcile
```

This stage runs after PostgreSQL is Ready and before the Job API schema
migrations below. It reads only PostgreSQL catalog metadata to verify the
`clouddsp_job_api` database, restricted `clouddsp-job-api` login, database
owner, `public` schema owner, and reviewed grants. A complete match makes
`reconcile` a no-op. One-sided or drifted state, an unexplained bootstrap Job,
or a lingering temporary Secret stops the stage for inspection.

When both database and role are absent, `reconcile` checks the ignored local
bootstrap and runtime Secret files against each other and the live app Secret
without printing their values. It creates the versioned bootstrap Job and its
temporary data-namespace Secret, waits for Job completion, verifies the
database catalog state, then removes that temporary Secret. A Job failure
retains the Job and temporary Secret for diagnosis. Password rotation is a
separate reviewed operation; catalog metadata alone cannot prove an existing
role's password matches a subsequently changed runtime Secret.

## Job API PostgreSQL migration stage

```bash
./k8Deployment/kubernetes/scripts/job-api-migrations.rb plan
./k8Deployment/kubernetes/scripts/job-api-migrations.rb verify
./k8Deployment/kubernetes/scripts/job-api-migrations.rb reconcile
```

This standalone bootstrap stage runner requires PostgreSQL, the
`clouddsp_job_api` database, and the app-namespace Job API schema-owner Secret
must exist before a pending migration can run. `plan` reads only the database's
`schema_migrations` ledger and immutable SQL ConfigMaps; `verify` additionally
requires every versioned migration to be applied. The script reads the ledger
through the existing PostgreSQL Pod and its Secret-backed environment. It does
not fetch Secret values to the host or query application data.

`reconcile` accepts only an exact prefix of the v001–v009 migration IDs and
descriptions extracted from versioned SQL. It checks each applied SQL
ConfigMap against the live immutable copy and refuses a pending fixed-name
Job that still exists without a ledger row. For a missing migration, it
validates and creates its versioned ConfigMap/Job, waits for completion, and
checks the next ledger row before advancing. A failed or ambiguous Job is
left in place for inspection. Repeated reconcile on a complete ledger is a
read-only no-op. Use the [combined PostgreSQL stage](job-api-postgresql-stage.rb)
to run this after database and schema-owner bootstrap in one command.

## MinIO root credential Secret stage

```bash
ruby ./k8Deployment/kubernetes/scripts/minio-root-secret-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/minio-root-secret-stage.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/minio-root-secret-stage.rb verify
```

For a fresh namespace, populate the ignored
`k8Deployment/.local/minio-root-credentials.secret.yaml` from the committed
example, replacing both placeholders. `plan` requires the Secret to be
absent; `bootstrap` validates it with a server dry run, creates only an
absent Secret, and compares the live result with the local values in memory.
`verify` is read-only and gates the MinIO release in root `verify` and
existing-cluster `reconcile`. This stage does not create MinIO's separate
RabbitMQ notification Secret, broker identity, buckets, or IAM state.

## MinIO RabbitMQ notification credential Secret stage

```bash
ruby ./k8Deployment/kubernetes/scripts/minio-amqp-secret-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/minio-amqp-secret-stage.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/minio-amqp-secret-stage.rb verify
```

Populate the ignored
`k8Deployment/.local/minio-source-intake-rabbitmq-credentials.secret.yaml`
from its committed example. The stage checks that its AMQP URL uses the
reviewed broker, `/clouddsp` vhost, and restricted username, and that its
percent-decoded password matches the separate password field. `plan` requires
absence; `bootstrap` performs a server dry run, creates only the absent
Secret, then checks its live encoded values without displaying them. Root
`verify` and existing-cluster `reconcile` use its read-only `verify` mode
before the MinIO release. The restricted broker user and topology are checked
by the separate source-intake RabbitMQ runner. The fresh MinIO Helm install
is described below; bucket and IAM creation use separate stages.

## Job API MinIO runtime credential Secret stage

```bash
ruby ./k8Deployment/kubernetes/scripts/job-api-minio-secret-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/job-api-minio-secret-stage.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/job-api-minio-secret-stage.rb verify
```

Populate the ignored `k8Deployment/.local/job-api-minio-credentials.secret.yaml`
and `k8Deployment/.local/job-api-minio-bootstrap-credentials.secret.yaml` from
their committed examples with the same restricted secret key. The stage checks
both source contracts and matching values. Fresh `bootstrap` creates only the
`clouddsp-app` runtime Secret after a server dry run; the temporary
`clouddsp-data` bootstrap Secret remains for the later IAM Job stage to create
and remove. `verify` compares live runtime values with the ignored source in
memory without printing the key. Root `bootstrap-minio` runs the create step
after MinIO install; root `verify` checks it before the bucket and IAM gates.

## Job API MinIO IAM bootstrap and temporary Secret cleanup

```bash
ruby ./k8Deployment/kubernetes/scripts/minio-job-api-iam-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/minio-job-api-iam-stage.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/minio-job-api-iam-stage.rb verify
ruby ./k8Deployment/kubernetes/scripts/minio-job-api-iam-stage.rb reconcile
```

Fresh `bootstrap` requires the MinIO release, locked shared samples, and Job
API runtime Secret to verify. It refuses a pre-existing Job API MinIO user,
either policy, temporary Secret, policy ConfigMap, or bootstrap Job. After
server dry runs it creates the ignored temporary Secret in `clouddsp-data`,
then runs the uploads policy/user Job followed by the artifact-read policy
Job. It checks each durable MinIO policy attachment before continuing and
removes the temporary Secret only after both policies and the user match the
reviewed source. A failure leaves partial resources for inspection.

`verify` is read-only and requires the temporary Secret to be absent. On an
existing cluster, `reconcile` verifies the complete IAM state and compares a
leftover temporary Secret with the ignored source before deleting only that
Secret. It never recreates a missing user or policy. Root `bootstrap-minio`
includes the fresh stage; root `verify` and `reconcile` include its respective
read-only and narrow cleanup modes.

## MinIO fresh Helm install, protected adoption, and verification

```bash
./k8Deployment/kubernetes/scripts/minio-release.rb install
./k8Deployment/kubernetes/scripts/minio-release.rb plan
./k8Deployment/kubernetes/scripts/minio-release.rb adopt
./k8Deployment/kubernetes/scripts/minio-release.rb verify
./k8Deployment/kubernetes/scripts/minio-release.rb smoke
```

For a fresh namespace, create both MinIO Secrets with their guarded stages
before `install`. The release runner requires the release, four chart objects,
generated PVC, and matching Pod to be absent. It verifies both Secrets before
an ordinary Helm install, then checks the Ready Pod, bound PVC/PV, locked
image digest, and S3 health route. A partial install stops future `install`
attempts for inspection. The protected adoption backup runs only for `adopt`.
This path has not been trialed on an empty cluster; the existing live cluster
is retained. The root `bootstrap-minio` composes source-intake broker state
and the fresh bucket stage around this release. IAM remains separate.

## Fresh MinIO bucket boundaries

```bash
ruby ./k8Deployment/kubernetes/scripts/minio-fresh-buckets-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/minio-fresh-buckets-stage.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/minio-fresh-buckets-stage.rb verify
```

Use `bootstrap` only as part of a new `bootstrap-minio` run that started with
an absent cluster. It requires the MinIO Helm release to verify and the server
to have **zero** buckets; one existing bucket or an unexpected bucket stops
before a write. It creates `clouddsp-uploads` and `clouddsp-midi-samples`
private, then checks both S3 endpoints and policy boundaries. A failed second
creation leaves the first bucket in place for inspection, and a repeat
bootstrap is refused. `verify` checks this initial boundary without requiring
sample content or IAM. The existing-cluster reconciler above remains the
read-only/missing-policy path and never creates buckets. The sample mirror
uploads all 461 locked assets before granting anonymous `GetObject` in the
next stage.

## Fresh shared MIDI sample mirror

```bash
ruby ./k8Deployment/kubernetes/scripts/minio-fresh-samples-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/minio-fresh-samples-stage.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/minio-fresh-samples-stage.rb verify
```

The root `bootstrap-minio` runs this stage after creating both buckets. The
fresh mode requires exactly those buckets, no current sample objects, no
sample policy, and no sample notifications. Its Python mirror reads 461 fixed
keys and reviewed upstream URLs from the committed lock without local
frontend dependencies, downloads each file, checks every SHA-256 and size,
and repeats the empty/private bucket check just before uploading. It grants
anonymous `GetObject` on shared samples only after upload and inventory verification.
The final stage verifies the exact locked key/size catalog and public policy.
A failed mirror can leave some private sample objects in place; inspect them
before a fresh retry. The mirror needs network access to the reviewed sample
origins and AWS CLI access to MinIO. The separate catalog inspection mode
still derives sample names from the pinned frontend package during updates.

The [MinIO chart](../helm/minio/README.md) owns its existing StatefulSet,
normal and headless Services, and S3 Ingress. Its script checks exact
source/render/live spec and bound-PVC parity before adoption. The automatic
[`backup and restore rehearsal`](minio-backup-and-restore-test.py) briefly
stops the MinIO Pod, archives its node-local PVC, restarts the original Pod,
and compares an isolated restored server's S3 inventory and object bytes.
The owner-only archive remains under ignored `k8Deployment/.local/backups/`.
The successful Helm takeover preserved the four resource UIDs, post-backup
Pod UID, Service IPs, and bound PVC/PV. `smoke` passed the S3 API
create/read/delete test through the normal Service and removed its Job. The
separate restricted Job API identity smoke passed and its Job was removed.
Bucket data, IAM, Secrets, and bootstrap Jobs remain outside this release.

## RabbitMQ credential Secret stage

```bash
ruby ./k8Deployment/kubernetes/scripts/rabbitmq-secret-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/rabbitmq-secret-stage.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/rabbitmq-secret-stage.rb verify
```

This fresh-cluster prerequisite validates the ignored
`k8Deployment/.local/rabbitmq-credentials.secret.yaml` against the committed
Secret identity, labels, type, and exact key contract. It rejects both
placeholder credentials and the reserved `guest` account. `plan` requires
absence; `bootstrap` performs a server dry run, creates only the absent
Secret, then verifies live data without displaying credentials. An existing
Secret stops creation, even if matching. `verify` is read-only and now runs
before the RabbitMQ release in root `verify` and `reconcile`. The fresh
RabbitMQ StatefulSet Helm install uses the stage's read-only `verify` mode
again immediately before the Helm write.

## RabbitMQ fresh Helm install, protected adoption, and verification

```bash
./k8Deployment/kubernetes/scripts/rabbitmq-release.rb install
./k8Deployment/kubernetes/scripts/rabbitmq-release.rb plan
./k8Deployment/kubernetes/scripts/rabbitmq-release.rb adopt
./k8Deployment/kubernetes/scripts/rabbitmq-release.rb verify
./k8Deployment/kubernetes/scripts/rabbitmq-release.rb smoke
```

For a fresh namespace, run the Secret stage's `bootstrap` first, then
`install`. It requires the Helm release, all five named resources, generated
PVC, and matching broker Pod to be absent. It checks the Secret without
printing its values, runs ordinary Helm install with a five-minute readiness
wait, and verifies the bound claim and running image. A partial install stops
future `install` attempts for inspection. This path does not run the adoption
backup or take ownership of existing objects. It has not been trialed on an
empty cluster; the existing live cluster is retained.

The [RabbitMQ chart](../helm/rabbitmq/README.md) owns the existing broker
StatefulSet, AMQP, headless and management Services, and its ingress
NetworkPolicy. Its automatic
[`backup and restore rehearsal`](rabbitmq-backup-and-restore-test.py) checks
that no messages are unacknowledged, briefly stops the broker, archives its
bound PVC, restarts the original Pod, then compares full definitions and queue
depths on a disposable no-network restore. The owner-only archive remains
under ignored `k8Deployment/.local/backups/`. The successful takeover
preserved five resource UIDs, post-backup Pod UID, Service IPs, and bound
PVC/PV identity. `smoke` passed a real AMQP publish/consume/acknowledge round
trip through the normal Service and removed its Job. Broker state, runtime
Secrets, bootstrap Jobs, and KEDA resources remain outside this release.

## RabbitMQ processing topology bootstrap

```bash
./k8Deployment/kubernetes/scripts/rabbitmq-processing-topology.rb plan
./k8Deployment/kubernetes/scripts/rabbitmq-processing-topology.rb verify
./k8Deployment/kubernetes/scripts/rabbitmq-processing-topology.rb reconcile
```

This stage runs after RabbitMQ is Ready and before processing publishers or
workers depend on its routes. It compares the broker's live `/clouddsp`
exchanges, quorum queue arguments, and bindings with the immutable v001
Demucs and v002 Basic Pitch/ADTOF definitions. Broker state is the durable
completion evidence even after a bootstrap Job's TTL removes that Job.
`plan` is read-only, `verify` requires both versions, and `reconcile` imports
only an entirely absent version, in order, through its existing versioned
ConfigMap and one-shot Job. Partial state, drift, a changed ConfigMap, or a
fixed-name Job without complete broker state stops the run for inspection.
This runner reads no broker credentials or message contents and does not
manage source-intake topology or RabbitMQ users.

## Upload-intake RabbitMQ runtime credential Secret stage

```bash
ruby ./k8Deployment/kubernetes/scripts/upload-intake-rabbitmq-secret-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/upload-intake-rabbitmq-secret-stage.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/upload-intake-rabbitmq-secret-stage.rb verify
```

Populate the ignored runtime Secret in `clouddsp-app` and its temporary
bootstrap companion source in `clouddsp-data` from their committed examples.
This stage requires both local files to contain the same reviewed username
and password. `plan` requires the runtime Secret to be absent; `bootstrap`
server-validates and creates only that absent runtime Secret, then compares
its live values with the ignored source without printing them. The temporary
data-namespace Secret is created and removed by the source-intake broker
bootstrap runner below. Root `verify` and existing-cluster `reconcile` use
this stage's read-only `verify` before the broker user/topology gate.

## RabbitMQ source-intake topology and users

```bash
./k8Deployment/kubernetes/scripts/rabbitmq-source-intake-bootstrap.rb plan
./k8Deployment/kubernetes/scripts/rabbitmq-source-intake-bootstrap.rb verify
./k8Deployment/kubernetes/scripts/rabbitmq-source-intake-bootstrap.rb reconcile
```

This independent stage checks the immutable source-intake v001 exchanges,
quorum queues, and bindings plus the MinIO publisher and upload-intake
consumer accounts. It requires their exact restricted permission regexes and
checks both passwords against the ignored local and live runtime Secrets
without printing values. `plan` reads the broker and Secrets; `verify` requires
complete state; `reconcile` creates the versioned ConfigMap and fixed-name Job
only when the topology and both users are entirely absent. A fresh run creates
the temporary data-namespace consumer Secret, verifies broker state and both
logins after Job completion, then removes that Secret. Partial state, drift,
or an unexplained Job stops for inspection. This stage does not configure
MinIO notifications or run upload-intake.

## Shared KEDA scaling authentication adoption

```bash
./k8Deployment/kubernetes/scripts/scaling-auth-release.rb plan
./k8Deployment/kubernetes/scripts/scaling-auth-release.rb adopt
./k8Deployment/kubernetes/scripts/scaling-auth-release.rb verify
```

The [scaling-auth chart](../helm/scaling-auth/README.md) owns only the two
existing app-namespace `TriggerAuthentication` resources. Its read-only plan
requires exact source/render/live spec parity, the pinned KEDA release, both
referenced Secret names, three Ready worker `ScaledObject`s, and their
correctly owned HPAs. Adoption preserved both authentication UIDs and spec
generations plus all dependent scaler, HPA, and worker Deployment UIDs.
Secret values, worker scale decisions, and the KEDA controller remain outside
this release. Each worker adoption and processing smoke is a later task.

## Basic Pitch worker Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb plan
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb adopt
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb verify
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb upgrade-scaling
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb upgrade-numba
```

The [Basic Pitch chart](../helm/basic-pitch/README.md) owns the existing
Deployment and RabbitMQ ScaledObject. The read-only preflight compared both
source manifests with the chart and live specs. The one-time takeover retained
both resource UIDs, the ScaledObject spec generation, generated HPA UID, and
zero idle replicas. `verify` checks Helm's stored manifest, KEDA Ready state,
shared authentication references, HPA target/ownership, and zero Pods. The first fixed one-request smoke exposed an obsolete parent-status
assertion. After guarded cleanup, a versioned restricted-function update and
a digest-pinned client rebuild, the rerun passed: durable publication,
first-attempt task success, verified MIDI bytes/provenance, and scoped cleanup.
The test fixture's parent Job is intentionally incomplete; only its exact
expected finalization error is accepted.

The Demucs two-stem smoke later exposed Basic Pitch's early AMQP ACK: the
queue became empty while its model task still held a PostgreSQL lease, and
KEDA scaled the worker away. Chart 0.1.2 added the existing restricted
PostgreSQL task-count trigger and a six-minute scale-in stabilization window.
`upgrade-scaling` verifies the queue-only v0.1.0 baseline and upgrades it to
the current policy. A cluster already at intermediate v0.1.1 uses
`upgrade-stabilization`. A later Demucs trial exposed a Numba illegal
instruction in the Basic Pitch tempo step on the ARM64 k3d node. Chart 0.1.3
sets `NUMBA_CPU_NAME=generic`, validated against that exact WAV in the running
worker image. The guarded `upgrade-numba` path upgrades an installed v0.1.2
release. All paths keep the Deployment, ScaledObject, and generated HPA UIDs
stable.

## ADTOF worker Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/adtof-release.rb plan
./k8Deployment/kubernetes/scripts/adtof-release.rb adopt
./k8Deployment/kubernetes/scripts/adtof-release.rb verify
./k8Deployment/kubernetes/scripts/adtof-release.rb smoke
```

The [ADTOF chart](../helm/adtof/README.md) owns only the existing worker
Deployment and RabbitMQ ScaledObject. Its read-only preflight checked image
lock, source/render/live spec parity, KEDA readiness, HPA ownership, and the
zero-Pod idle state. Helm takeover preserved both resource UIDs, scaler spec
generation, generated HPA UID, and zero replicas. The fixed one-drum worker
smoke passed after its test-only finalizer fixture update: KEDA activated one
Pod, the real dispatcher and worker completed the request, the client verified
MIDI and tempo evidence, and scoped cleanup removed its Job and fixed data.

## Demucs worker Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/demucs-release.rb plan
./k8Deployment/kubernetes/scripts/demucs-release.rb adopt
./k8Deployment/kubernetes/scripts/demucs-release.rb verify
./k8Deployment/kubernetes/scripts/demucs-release.rb smoke
```

The [Demucs chart](../helm/demucs/README.md) owns its existing worker
Deployment and dual-trigger ScaledObject. The read-only preflight checked the
image lock, exact source/render/live specs, both RabbitMQ and PostgreSQL
authentication references, scaler readiness, HPA ownership, and zero idle
Pods. Helm takeover preserved both object UIDs, the scaler generation, the
KEDA-owned HPA UID, and zero replicas. The fixed two-stem smoke validates the
real dispatcher and Demucs worker, verifies stem bytes and provenance, waits
for both downstream Basic Pitch tasks and the parent Job to complete, and
uses guarded cleanup of only its test data. The first trial reached verified
Demucs completion, then stalled because Basic Pitch scaled away during its
second task; the full smoke remains failed with fixed evidence preserved.

## Keycloak Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/keycloak-release.rb plan
./k8Deployment/kubernetes/scripts/keycloak-release.rb adopt
./k8Deployment/kubernetes/scripts/keycloak-release.rb verify
./k8Deployment/kubernetes/scripts/keycloak-release.rb smoke
```

The [Keycloak chart](../helm/keycloak/README.md) owns the existing identity
Deployment, ClusterIP Service, and browser Ingress in `clouddsp-data`. The
release script checks the image lock and exact source/render/live spec before
its one-time takeover. `verify` checks Helm's stored manifest, the Ready Pod,
and the public OIDC discovery route. `smoke` runs the versioned internal
discovery/issuer Job and deletes only that completed Job. Adoption preserved
the three object UIDs, Service IP, and Pod UID. The separate React PKCE
authorization, Keycloak-to-Mailpit verification-email, and temporary-user
authenticated-read smoke Jobs also passed and were deleted after completion.
PostgreSQL, Secrets, and realm, client, and SMTP bootstrap remain outside this
release.

## Frontend Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/frontend-release.rb plan
./k8Deployment/kubernetes/scripts/frontend-release.rb adopt
./k8Deployment/kubernetes/scripts/frontend-release.rb verify
```

The [frontend chart](../helm/frontend/README.md) owns its existing
Deployment, ClusterIP Service, and Traefik Ingress in `clouddsp-app`.
The shared [`stateless-release.rb`](stateless-release.rb) helper supplies the
same source/render/live comparison and one-time ownership gate as Mailpit.
`verify` also checks the local browser app shell, CSP header, and linked
JavaScript and CSS assets. After adoption, the raw
`services/frontend/` manifests are a retained comparison baseline; use
the Helm chart for subsequent delivery changes.

## Legacy dispatcher Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/dispatcher-release.rb plan
./k8Deployment/kubernetes/scripts/dispatcher-release.rb adopt
./k8Deployment/kubernetes/scripts/dispatcher-release.rb verify
```

The [dispatcher chart](../helm/dispatcher/README.md) owns only the existing
`Deployment/clouddsp-dispatcher` in `clouddsp-app`. Its image is checked
against `images.dispatcher-demucs-only`, distinct from the generic controller's
image lock. The shared release helper compares source, render, and live spec
before the one-time ownership handoff. `verify` checks Helm ownership, stored
manifest, unchanged spec, Ready Pod, and running image digest. There is no
Service or HTTP route for this internal outbox publisher. The separate
normal-path smoke test is required to prove actual message publication; this
adoption does not run that credential-bearing integration test.

## Generic dispatcher Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/generic-dispatcher-release.rb plan
./k8Deployment/kubernetes/scripts/generic-dispatcher-release.rb adopt
./k8Deployment/kubernetes/scripts/generic-dispatcher-release.rb verify
```

The [generic dispatcher chart](../helm/generic-dispatcher/README.md) owns only
`Deployment/clouddsp-generic-dispatcher` in `clouddsp-app`. Its pinned image
and explicit `app.dispatcher_generic_runtime` command are compared with the
source and live Deployment before the one-time Helm ownership transfer.
`verify` checks the stored release manifest, unchanged live spec, Ready Pod,
and running image digest. It has no Service or HTTP route. The separate
Basic Pitch routing smoke test is needed to demonstrate message behavior;
it passed on 2026-09-26 with one controlled event published, acknowledged,
and cleaned up. The disposable Job was removed. This adoption keeps the legacy
dispatcher running at one replica.

## Job API Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/job-api-release.rb plan
./k8Deployment/kubernetes/scripts/job-api-release.rb adopt
./k8Deployment/kubernetes/scripts/job-api-release.rb verify
```

The [Job API chart](../helm/job-api/README.md) owns its existing Deployment,
ClusterIP Service, and same-origin `/auth` and `/jobs` Ingress in
`clouddsp-app`. The release script checks the image lock, source/render/live
spec parity, and API-server schema before one-time adoption. `verify` checks
Helm ownership, stored manifest, ready Pod and running digest, and that both
protected browser paths still reject unauthenticated requests with HTTP 401.
The Service IP, three resource UIDs, and Pod UID were preserved. Database
migrations and runtime Secret contents remain outside Helm.
The versioned authenticated-read smoke Job passed with a temporary Keycloak
user and client, then cleaned up both identities and its disposable Job.

## Upload-intake Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/upload-intake-release.rb plan
./k8Deployment/kubernetes/scripts/upload-intake-release.rb adopt
./k8Deployment/kubernetes/scripts/upload-intake-release.rb verify
```

The [upload-intake chart](../helm/upload-intake/README.md) owns only its
outbound Deployment in `clouddsp-app`. It preserves the Pod's three restricted
PostgreSQL, MinIO, and RabbitMQ Secret references and the reviewed image
digest. The release checker requires source/render/live equality before the
one-time adoption and then verifies the original Deployment and Pod UIDs.
`verify` checks Helm ownership, stored manifest, Ready Pod, and running
digest. The separate [source-to-outbox integration smoke](../tests/source-intake-smoke/README.md)
subsequently passed after its test image was aligned with the Job API lock and
the fixed test Pod received narrowly scoped RabbitMQ management access. Its
runbook pauses and restores both dispatcher releases around the pending-row
assertion.

## Prerequisites

Start Docker Desktop, then confirm that the local command-line tools are
available:

```bash
docker info
k3d version
kubectl version --client
```

`k3d` uses Docker to create the K3s server, agent, load-balancer, and local
registry containers. `kubectl` is the Kubernetes client used by the status
portion of `cluster.sh`; it connects through the `k3d-clouddsp-local` context
created by k3d.

## Create the cluster

```bash
./k8Deployment/kubernetes/scripts/cluster.sh create
```

`create` is also the default action, so this equivalent command is available:

```bash
./k8Deployment/kubernetes/scripts/cluster.sh
```

The script validates Docker, k3d, kubectl, and the cluster configuration first.
It creates the `clouddsp-local` cluster only when it is absent, then lists its
nodes and K3s system Pods. If the cluster already exists, it leaves it intact;
this protects persistent development data added in later phases.

k3d adds the `k3d-clouddsp-local` context to the standard kubeconfig and, by
default, switches the current context to it. Use the context explicitly when a
command must be unambiguous:

```bash
kubectl --context k3d-clouddsp-local get nodes
kubectl --context k3d-clouddsp-local get pods --all-namespaces
```

## Inspect cluster status

```bash
./k8Deployment/kubernetes/scripts/cluster.sh status
```

This is read-only. It shows the three Kubernetes nodes and the Pods in every
namespace. The `default` namespace remains empty until a CloudDSP workload is
deployed; K3s platform Pods such as CoreDNS and Traefik run in `kube-system`.

## Install or reconcile KEDA

```bash
./k8Deployment/kubernetes/scripts/install-keda.sh
```

This is the versioned, non-interactive Helm entry point for the KEDA event
autoscaler. It reads the pinned official chart release and local values from
[`../helm/keda/`](../helm/keda/), targets only the
`k3d-clouddsp-local` context, and waits for KEDA's operator, metrics API
server, admission webhook, and custom resource definitions to become ready.
The three KEDA controller Pods run inside the `keda` namespace; Helm itself is
only the short-lived Mac command that submits their manifests.

The script creates or reconciles the KEDA platform dependency only. It does
not create a `ScaledObject`, resize a processing worker, read a RabbitMQ
credential, or enqueue audio work. Those three independent worker policies
remain later, separately reviewed tasks.

## Verify the local registry

Apply the namespace manifest first, then run the end-to-end registry smoke
test:

```bash
kubectl --context k3d-clouddsp-local apply \
  --filename k8Deployment/kubernetes/cluster/namespaces.yaml

./k8Deployment/kubernetes/scripts/verify-registry.sh
```

The script pulls the exact `busybox:1.37.0` release for the Mac's active
container architecture, tags and pushes it as
`clouddsp-registry.localhost:5001/registry-smoke:1.37.0`, then applies the
versioned `registry-smoke` Job in `clouddsp-app`. The Job uses
`imagePullPolicy: Always`, so K3s must query the local registry on each run.
The script prints the Job's Pod pull events and logs, then leaves the completed
Job and Pod available for inspection. Kubernetes's TTL controller removes them
about five minutes after completion. A later test run deletes only that prior
smoke Job before it creates a fresh one. The harmless test image remains in the
dedicated registry for future checks and is removed when the registry itself is
cleaned up.

## Build the local frontend image

```bash
./k8Deployment/kubernetes/scripts/build-frontend-image.sh
```

This uses the local frontend's two-stage Dockerfile: the pinned official Node
image builds Vite assets, then the pinned official NGINX image serves only those
assets as a non-root process on container port 8080. The script reads the
public Keycloak, Job API, and MinIO browser settings from the ignored
`services/frontend/app/.env.production`, pushes the arm64 image to
`clouddsp-registry.localhost:5001/frontend`, and prints its immutable registry
digest plus Docker's local uncompressed size. Vite generates a CSP meta tag and
matching NGINX response-header include, allowing only the explicit configured
MinIO origin for direct presigned POSTs. It does not deploy a Pod; that is the
following Kubernetes delivery task.

## Build the local Job API image

```bash
./k8Deployment/kubernetes/scripts/build-job-api-image.sh
```

This builds the digest-pinned Python 3.12 Job API recipe for `linux/arm64`,
installs its fully hashed FastAPI/Uvicorn/Psycopg/PyJWT/Boto3 dependencies, and
runs the isolated JWT, job-history, direct-upload contract, PostgreSQL helper,
route-composition, and presigned-POST unit tests before it pushes the result to
`clouddsp-registry.localhost:5001/job-api`. It prints the immutable registry
digest and Docker's local uncompressed size. The runtime image contains source
for `/healthz`, database-aware `/readyz`, token validation, `GET /jobs`, and
`POST /jobs`, but no test files, credentials, Pod, Service, or Ingress.

## Build the MinIO-to-RabbitMQ smoke-client image

```bash
./k8Deployment/kubernetes/scripts/build-minio-source-intake-smoke-client-image.sh
```

This builds the small, purpose-built AMQP client used only by the later native
MinIO-notification smoke Job. It has a pinned Python base and a hash-locked
Pika dependency, targets the local ARM64 k3d nodes, pushes to the dedicated
registry, and prints the immutable digest that must be copied into
`images.lock.yaml`. It does not create a test Job, upload an object, read a
RabbitMQ queue, or change MinIO configuration.

## Build the upload-intake worker image

When a previously acknowledged source event left one retained upload pending,
use the versioned one-job recovery command *after* rolling out the corrected
upload-intake image. It accepts only a canonical job UUID, uses the running
Pod's restricted identities, and reuses the normal HeadObject plus atomic
PostgreSQL source/outbox transition:

```bash
./k8Deployment/kubernetes/scripts/reconcile-one-upload-intake-job.sh JOB_UUID
```

```bash
./k8Deployment/kubernetes/scripts/build-upload-intake-image.sh
```

This builds the local long-running upload-intake worker for `linux/arm64` from
the pinned official Python 3.12 slim base. The Dockerfile installs the fully
hash-locked Boto3, Psycopg, and Pika dependencies, then runs the parser,
transaction, MinIO-HeadObject, manual-ack, graceful-shutdown, and reconnect
unit suite while building. Its final non-root image contains only application
source and validated runtime packages; it exposes no HTTP port and contains no
cluster credentials. The script pushes the image to the dedicated local
registry and prints the immutable digest and local Docker size. It does not
create a Pod, Deployment, Service, Ingress, database change, MinIO object, or
RabbitMQ message.

## Build the outbox dispatcher image

```bash
./k8Deployment/kubernetes/scripts/build-dispatcher-image.sh
```

This builds the internal PostgreSQL-outbox dispatcher for `linux/arm64` from
the pinned official Python 3.12 slim base. Its two-stage Dockerfile installs
the fully hashed Pika/Psycopg dependencies and runs the Demucs-compatible and
generic outbox lease, route-selection, publisher-confirmation,
database-transaction, one-attempt composition, and graceful-shutdown tests
before it copies only validated source and runtime packages into a non-root
final image. The image deliberately keeps the Demucs-only runtime as its
default entrypoint; a later generic Deployment must select
`app.dispatcher_generic_runtime` explicitly. The dispatcher accepts no inbound
HTTP traffic and therefore exposes no port. The script pushes the image to the
dedicated local registry and prints the immutable digest and local Docker size;
it does not create or update a Deployment, Pod, Service, Ingress, database row,
or RabbitMQ message.

## Build the dispatcher smoke-client image

```bash
./k8Deployment/kubernetes/scripts/build-dispatcher-smoke-client-image.sh
```

This builds the disposable normal-upload smoke client for `linux/arm64` from
the pinned Python 3.12 runtime. Its validation stage installs hash-locked
Boto3, Psycopg, and Pika packages and runs the isolated orchestrator/verifier
unit tests. The non-root final image contains only the two smoke modules and
their runtime dependencies, then the script pushes it to the dedicated local
registry and prints an immutable digest and local image size. It does not
create a Kubernetes Job, Keycloak identity, MinIO object, RabbitMQ delivery, or
PostgreSQL record.

## Build the generic dispatcher Basic Pitch smoke-client image

```bash
./k8Deployment/kubernetes/scripts/build-generic-dispatcher-basic-pitch-smoke-client-image.sh
```

This builds the separate, restricted v004 Basic Pitch routing verifier for
`linux/arm64`. Its validation stage installs only hash-locked Psycopg/Pika
dependencies and runs its 16 isolated tests. The non-root runtime image can
call only its three PostgreSQL smoke functions and read/ack its exact Basic
Pitch queue once a later Job supplies the already-applied Secrets. The script
pushes it to the local registry and prints the immutable digest that is
recorded in `images.lock.yaml`; it does not create a Job, synthetic event,
RabbitMQ delivery, or other cluster workload.

## Build the Basic Pitch worker smoke-client image

```bash
./k8Deployment/kubernetes/scripts/build-basic-pitch-worker-smoke-client-image.sh
```

This builds the separate end-to-end Basic Pitch worker verifier for
`linux/arm64`. Its validation stage installs only the hash-locked Boto3 and
Psycopg dependency closure and runs eight isolated tests. The non-root runtime
can call only its fixed PostgreSQL functions and access only its two reserved
MinIO objects once a later Job supplies the already-applied restricted Secrets.
The script pushes the image to the local registry and prints its immutable
digest; it does not create a Job, synthetic event, RabbitMQ message, object, or
other cluster workload.

## Build the ADTOF worker smoke-client image

```bash
./k8Deployment/kubernetes/scripts/build-adtof-worker-smoke-client-image.sh
```

This rebuilds the separate end-to-end ADTOF worker verifier for `linux/arm64`
from its digest-pinned Python 3.12 base. The Docker validation stage installs
only the full hash-pinned Boto3/Psycopg closure and runs the 63 isolated client
tests. The final non-root image can call only its three fixed PostgreSQL smoke
functions and access only its three reserved MinIO object keys after a future
Job provides its restricted Secrets. The script checks Docker, the exact k3d
registry, and all narrow build inputs before it builds and pushes. It then
prints the immutable registry digest and Docker's uncompressed local size; copy
that digest into `images.lock.yaml` before any future Job manifest refers to a
new rebuild. It does not create a Job, synthetic event, RabbitMQ message,
object, or other cluster workload.

## Build the Demucs worker stage smoke-client image

```bash
./k8Deployment/kubernetes/scripts/build-demucs-worker-smoke-client-image.sh
```

This validates the narrow Python client, builds the ARM64 image, pushes it to
the local registry, prints the immutable digest, and verifies that the pushed
digest resolves from the registry.  It deliberately does **not** apply any
Kubernetes resource or create test data.  After a reviewed rebuild, copy the
printed digest and measured image size into `images.lock.yaml`, then update
the smoke Job image reference before applying the test manifests.

## Build the Demucs worker image

```bash
./k8Deployment/kubernetes/scripts/build-demucs-image.sh
```

This validates the entire Linux/ARM64 Demucs worker source and its locked model
artifacts inside the Docker validation stage, then runs a real fixed two-stem
inference through the local ARM64 CPU launcher before pushing the resulting
image to the dedicated local registry. The launcher disables Torch MKLDNN
before Demucs imports because the default local model path reproduced a SIGILL;
the check requires both generated stem files, so a successful import alone is
not treated as proof. The script prints the immutable digest and local layer
size. It creates or updates an OCI image only: it does not apply a Deployment,
Secret, Job, Service, database migration, MinIO object, or RabbitMQ message.
After a reviewed build, record the reported digest in `images.lock.yaml` and
copy the same immutable reference into `services/demucs/demucs-deployment.yaml`.

## Build the Basic Pitch worker image

```bash
./k8Deployment/kubernetes/scripts/build-basic-pitch-image.sh
```

This builds the CPU-only Basic Pitch worker for `linux/arm64` from its separate
pinned Python 3.11 base image. The two-stage Dockerfile installs the complete
hash-locked inference/client dependency closure, runs the worker unit suite,
and proves that the fixed `basic-pitch` command and bundled TensorFlow Lite
model are present before the final non-root image is published to
`clouddsp-registry.localhost:5001/basic-pitch`. The script prints the immutable
registry digest and Docker's local uncompressed size. It does not create or
update a Deployment, Pod, Service, Ingress, Secret, database row, MinIO object,
or RabbitMQ message. Its published digest is recorded separately in
`images.lock.yaml` before a future workload manifest may refer to it.

## Build the ADTOF worker image

```bash
./k8Deployment/kubernetes/scripts/build-adtof-image.sh
```

This builds the CPU-only ADTOF drum-to-MIDI worker for `linux/arm64` from its
dedicated digest-pinned Python 3.11 base. Its throwaway validation stage runs
the complete worker test suite, including Kubernetes `fsGroup` scratch-path and
fixed `/app` CPU-child-import working-directory checks, and confirms the pinned
ADTOF model package and bundled weights are present. The script pushes only the image to
`clouddsp-registry.localhost:5001/adtof` and prints the immutable digest and
Docker's uncompressed local image size. Copy that digest into
`images.lock.yaml`, then explicitly update and apply the ADTOF Deployment; the
script never changes cluster workloads or durable processing data itself.

## Smoke-test the local Job API image

```bash
./k8Deployment/kubernetes/scripts/verify-job-api-local-image.sh
```

This starts the immutable Job API image as a short-lived Docker container with
no network and no database credential. It verifies that `/healthz` returns 200
with the expected image version and that `/readyz` correctly returns a 503
`database_configuration` response. It uses `docker exec` inside the
network-isolated container, so it opens no Mac port, then removes the container
at the end. This is an image/process test; it does not deploy Kubernetes
resources or prove in-cluster PostgreSQL connectivity.

## Verify HTTP routing on port 8080

```bash
./k8Deployment/kubernetes/scripts/verify-http-routing.sh
```

This test pushes a separate BusyBox HTTP-server image, deploys a `Deployment`,
`ClusterIP` Service, and Traefik `Ingress`, then checks the exact response at:

```text
http://routing-smoke.localhost:8080/
```

The resources remain in `clouddsp-app` for inspection. They are deliberately
isolated behind `routing-smoke.localhost`, so they do not match a future
CloudDSP hostname. Remove only this routing test when finished:

```bash
kubectl --context k3d-clouddsp-local delete \
  --filename k8Deployment/kubernetes/tests/routing-smoke/http-routing-smoke.yaml
```

## Delete the local cluster

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh cleanup
```

This removes exactly the `clouddsp-local` k3d cluster, its workloads, node
containers, and PVC-backed local data. It retains the separate
`clouddsp-registry.localhost` container and its images for the next cluster.
`cluster.sh create` attaches that registry to the replacement cluster; on a
clean machine it creates the registry from the checked-in k3d configuration.
Before deleting the current k3d cluster, cleanup connects its registry to the
fixed `clouddsp-registry-hold` Docker network. k3d then disconnects its own
cluster network and keeps the registry; the separate purge removes that hold
network after deleting the registry.
The command delegates to `cleanup-cluster.sh --confirm` and succeeds if the
cluster is already absent. Ignored host configuration files also remain.

To remove the dedicated registry and every image stored only there, first
clean up the cluster, then run the separate command:

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh purge-registry
```

`purge-registry` refuses to run while the CloudDSP cluster exists and succeeds
if the registry is already absent. No command removes unrelated Docker
resources. Until the Docker Hub image source and digest checks are implemented,
do not purge the existing registry: some locked image revisions have no complete
rebuild path in the current scripts.

Use `--help` with any script to print its supported command form without
changing local resources:

```bash
./k8Deployment/kubernetes/scripts/cluster.sh --help
./k8Deployment/kubernetes/scripts/cleanup-cluster.sh --help
./k8Deployment/kubernetes/scripts/purge-registry.sh --help
./k8Deployment/kubernetes/scripts/verify-registry.sh --help
./k8Deployment/kubernetes/scripts/verify-http-routing.sh --help
```
