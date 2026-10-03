> Historical snapshot, preserved on 2026-10-03 before the documentation reorganization.
> This page contains the complete earlier scripts README, including staged implementation notes,
> adoption results, obsolete trial counts, and commands whose prerequisites changed over time.
> Use the [current operator guide](../../scripts/README.md) and its focused references for deployment.
> Relative Markdown links have been rebased; repository-root command examples are preserved.

# Local cluster scripts

These non-interactive scripts manage only the versioned local k3d profile
declared in [`../cluster/k3d.yaml`](../../cluster/k3d.yaml). Run the commands from
the repository root, or change into this directory first; each script resolves
its own location and does not depend on the current working directory. The
platform bootstrap also requires the AWS CLI v2 because its MinIO bucket and
sample stages use the AWS S3 API client.

Docker must allow HTTP for the exact local image registry
`clouddsp-registry.localhost:5001`. Add
`"insecure-registries": ["clouddsp-registry.localhost:5001"]` to the Docker
daemon configuration and restart Docker before running a fresh bootstrap.
`cluster.sh create` checks this setting before creating resources. The registry
is a standalone k3d container; normal `cleanup` retains its images, while
`purge-registry` removes its container and image data volume.

## Root deployment plan

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh secrets-init
./k8Deployment/kubernetes/scripts/deploy-local.sh stages
./k8Deployment/kubernetes/scripts/deploy-local.sh plan
./k8Deployment/kubernetes/scripts/deploy-local.sh prepare
./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-mailpit
./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-postgresql
./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-rabbitmq
./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-minio
./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-platform
./k8Deployment/kubernetes/scripts/deploy-local.sh verify
./k8Deployment/kubernetes/scripts/deploy-local.sh reconcile
./k8Deployment/kubernetes/scripts/deploy-local.sh cleanup
./k8Deployment/kubernetes/scripts/deploy-local.sh purge-registry
```

`stages` prints the exact ordered steps currently wired into
`bootstrap-platform` without contacting the cluster. The `plan` mode is the first stage of the
[deployment orchestrator](../../deployment-orchestration-plan.md). It reads the
versioned workload manifests and lock files, then queries only the explicit
`k3d-clouddsp-local` context. It checks namespace and Helm ownership,
StatefulSet selectors/claim templates and bound PVCs, immutable image
references, and the *names* of runtime Secrets. It checks for matching ignored
local Secret filenames and committed example contracts without printing Secret
values. The command makes no cluster changes and returns nonzero when a
blocking inconsistency is found; warnings identify work needed before a fresh
bootstrap. Ruby's standard YAML/JSON libraries are required on the host.

## Local credential initialization

```bash
ruby ./k8Deployment/kubernetes/scripts/credential-catalog.rb
./k8Deployment/kubernetes/scripts/deploy-local.sh secrets-init
```

The value-free [`catalog.yaml`](../../credentials/catalog.yaml) describes the 24
runtime credential groups and nine additional local bootstrap Secret sources
needed by `bootstrap-platform`. Each group names its required fields and the
committed example contracts for the Secret manifests. The validator checks
those contracts and shared runtime/bootstrap mappings without opening
`k8Deployment/.local/` or contacting the cluster. It rejects password literals
in the catalog.

`secrets-init` uses the reviewed catalog to create all 33 ignored Secret
sources at once. It fills fixed service identities, generates independent
random passwords and MinIO keys, and uses the same values in matching runtime
and temporary bootstrap manifests. It derives MinIO's AMQP URL from the
restricted RabbitMQ identity. The command creates `k8Deployment/.local/` with
owner-only access and writes owner-only files. If every source already exists,
it checks their contracts and shared values without replacing them. If only
some sources exist, it stops without generating the rest. No Kubernetes API or
Helm release is contacted and no credential value is printed. Existing files
with broader permissions are reported but not changed by this command.

To choose a value, copy the
[`overrides.example.yaml`](../../credentials/overrides.example.yaml) contract to
an ignored or other private location, set owner-only permissions, and edit
that one file. For an ignored local copy:

```bash
mkdir -p k8Deployment/.local
chmod 700 k8Deployment/.local
cp k8Deployment/kubernetes/credentials/overrides.example.yaml k8Deployment/.local/credentials-input.yaml
chmod 600 k8Deployment/.local/credentials-input.yaml
${EDITOR:-vi} k8Deployment/.local/credentials-input.yaml
```

Under `credentials`, use a group name from the catalog, then the field name
to override. Only fields marked `default` or `generate` can be supplied;
fixed service identities remain fixed. For example, an input can set
`keycloak-admin.KC_BOOTSTRAP_ADMIN_USERNAME` and
`keycloak-admin.KC_BOOTSTRAP_ADMIN_PASSWORD`. Then run:

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh secrets-init --input k8Deployment/.local/credentials-input.yaml
```

Custom passwords and MinIO secret keys must contain at least 16 characters.
Keep the ignored sources for verification and later fresh installs. This
initializer does not rotate a live database, broker, identity provider, or
object-store credential. On a clean machine, `bootstrap-platform` first checks
that the target cluster is absent, then runs `secrets-init` before foundation
creation. Existing complete `.local` sources are validated and reused;
partial sources stop the run before any cluster resource is created. Run
`secrets-init --input` first if you want to choose values.

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
install Helm releases, or bootstrap external state. `bootstrap-platform`
composes the reviewed stages after `prepare`.

`bootstrap-mailpit` is the first root composition beyond `prepare`. On an
absent cluster it runs foundation and image preparation, the guarded Mailpit
`install`, and Mailpit `verify` in that order. A failure stops later stages
and leaves created resources for inspection. It does not install the remaining
CloudDSP releases or bootstrap application data; use `bootstrap-platform` for
the complete fresh-cluster sequence. Use the separate Mailpit smoke command
below when SMTP capture needs a functional check.

`bootstrap-postgresql` is the parallel root slice for the first data service.
It runs `prepare`, creates and verifies the ignored PostgreSQL credential
Secret, installs the PostgreSQL Helm release, then verifies its Ready Pod and
bound PVC. A failure stops later stages and leaves the partial cluster or
release for inspection. The Mailpit and PostgreSQL partial commands each
start from an absent cluster; they are alternative trials, not sequential
commands to run against the same cluster. `bootstrap-platform` composes these
component stages in one dependency order.

`bootstrap-rabbitmq` is the corresponding partial root command for the
broker. It runs `prepare`, creates and verifies the ignored administrator
Secret, installs the RabbitMQ Helm release, then verifies its Ready Pod and
bound PVC. Each child must succeed before the next starts. This command also
requires an absent cluster, so it is an alternative clean-cluster trial to
the Mailpit and PostgreSQL partial commands. `bootstrap-platform` runs these
component stages together in dependency order.

`bootstrap-minio` composes the broker and object-storage slice from an absent
cluster. It reuses `bootstrap-rabbitmq` for foundation, images, administrator
Secret, and broker Helm install. It then creates the MinIO root/AMQP and
upload-intake RabbitMQ runtime Secrets, reconciles and verifies the restricted
source-intake broker users and topology, and only then installs and verifies
MinIO. It creates the Job API, upload-intake, Demucs, Basic Pitch, and ADTOF
runtime MinIO Secrets and the two initially private MinIO buckets, mirrors 461
hash-locked shared MIDI samples, and grants their bucket a narrow anonymous
browser-read policy. It then provisions the restricted Job API user with two
policies, the upload-intake user with its source-read policy, the Demucs worker
with its private source-read and stem-write policy, the Basic Pitch worker
with its stem-read and MIDI-write policy, and the ADTOF worker with its
drums-read and fixed MIDI/tempo-write policy. Once these IAM stages verify,
the fixed notification Job configures the private uploads bucket to send
`uploads/` object PUT events to the restricted RabbitMQ source-intake target.
The next read-only gate verifies the exact durable notification rule.
The broker runner manages its temporary bootstrap Secret and removes it after
successful user verification. A failed stage leaves partial state
for inspection. This is an alternative partial trial to the other
`bootstrap-*` commands, not a command to run after them on the same cluster.
It configures the Job API, upload-intake, Demucs, Basic Pitch, and ADTOF MinIO
identities. `bootstrap-platform` continues through application identity and
fresh release stages after this shared service foundation.

`bootstrap-platform` first checks that the target cluster is absent and
initializes ignored credential sources. It composes the guarded fresh
PostgreSQL, RabbitMQ, and MinIO child steps once, then creates Keycloak's isolated PostgreSQL
credential Secret and database using the versioned one-shot Job. It installs
and verifies Mailpit, stages the ignored Keycloak bootstrap-admin Secret, then
installs and verifies the Keycloak Helm release. Six versioned Admin API Jobs
then configure and verify the CloudDSP realm, SMTP, registration policies,
React PKCE client, and Job API audience. It creates the Job API
PostgreSQL runtime Secret, bootstraps the Job API schema owner, applies schema
migrations v001–v006, provisions the Basic Pitch and ADTOF restricted database
roles, then applies v007–v009. It imports/verifies RabbitMQ processing topology,
installs and verifies the Job API Helm release, then installs and verifies the
pinned KEDA chart, controllers, CRDs, and Helm values. It then creates the
remaining database and RabbitMQ identities, installs KEDA's shared
TriggerAuthentication release, and installs upload-intake, both dispatchers,
Demucs, Basic Pitch, ADTOF, and frontend. Every step stops at the first error
and leaves a partial cluster for inspection. This is an absent-cluster
command, not one to run after a partial bootstrap. The earlier 53-stage path
completed in an isolated Ubuntu 24.04 VM
on September 29, 2026 after installing the AWS CLI v2. It found and fixed a
direct-`ctr` pull that bypassed k3s registry rewriting.

The prior 57-stage platform subset passed in a new disposable Ubuntu 24.04
ARM64 VM. Both the CloudDSP cluster and local registry were absent initially. The
run anonymously verified and digest-mirrored all 19 locked images, created
the MinIO source-upload notification from an absent rule and verified it,
installed KEDA as Helm revision 1 at chart 2.20.2, and verified all three
controller Deployments at 1/1 plus the committed values and six CRDs. The
command exited 0 after stage 57/57. Independent read-only KEDA and MinIO
notification verifiers passed before the VM was purged. The later, complete
91-stage clean-VM trial is recorded in
[`2026-09-30-clean-vm-bootstrap-d903e8c.md`](../trials/2026-09-30-clean-vm-bootstrap-d903e8c.md).

## Fresh application identity and Helm stages

The new `application-identity-stage.rb` handles the remaining restricted
PostgreSQL accounts for upload-intake, dispatcher, Demucs, and the Demucs KEDA
scaler; its RabbitMQ accounts cover dispatcher publishing, Demucs, Basic
Pitch, ADTOF, and KEDA queue observation. For example:

```bash
ruby ./k8Deployment/kubernetes/scripts/application-identity-stage.rb database upload-intake plan
ruby ./k8Deployment/kubernetes/scripts/application-identity-stage.rb database upload-intake bootstrap
ruby ./k8Deployment/kubernetes/scripts/application-identity-stage.rb database upload-intake verify
ruby ./k8Deployment/kubernetes/scripts/application-identity-stage.rb rabbitmq demucs plan
ruby ./k8Deployment/kubernetes/scripts/application-identity-stage.rb rabbitmq demucs bootstrap
ruby ./k8Deployment/kubernetes/scripts/application-identity-stage.rb rabbitmq demucs verify
```

The stage reads the ignored runtime Secret and committed example, generates
the matching data-namespace bootstrap Secret in memory, then validates the
Secret and fixed Job manifests with API-server dry runs. It runs the Jobs,
checks durable PostgreSQL grants or RabbitMQ tags/permissions and login, and
removes the temporary Secret. Passwords are never passed in command-line
arguments or printed. `verify` audits the durable identity directly rather
than relying on short-lived completed Jobs. Existing MinIO credentials,
Basic Pitch/ADTOF database roles, and upload-intake's source-intake RabbitMQ
identity keep their dedicated stages.

After KEDA installs, `scaling-auth-release.rb install` creates the two
TriggerAuthentication resources. Fresh `install` modes now cover upload-
intake, both dispatchers, Demucs, Basic Pitch, ADTOF, and frontend. Each checks
its credentials and service prerequisites, refuses an existing release or
chart-owned object, and verifies the installed workload. KEDA workers install
at zero idle replicas with shared authentication already present. Frontend
installation checks the Keycloak realm and Job API release, then verifies the
browser route and static assets.

For focused Keycloak database diagnosis, run:

```bash
ruby ./k8Deployment/kubernetes/scripts/keycloak-database-stage.rb verify
```

The read-only `plan` mode reports whether the dedicated role and database are
wholly absent or already complete. The `bootstrap` mode creates the ignored
Secret and versioned one-shot Job only for wholly absent state. It does not
repair partial state, rotate a password, install Keycloak, or configure its
realm. The finished Job can expire; PostgreSQL role, database, ownership, and
grants are checked directly. Verification also authenticates with the ignored
local password through the existing PostgreSQL Pod; the password travels on
stdin and is never printed.

All application identities and guarded fresh release paths are now wired into
`bootstrap-platform`. `stages` lists 93 ordered stages without contacting
Kubernetes. On 2026-10-01, a fresh Ubuntu 24.04 ARM64 VM with no `.local`
directory passed all 93 bootstrap stages. The command generated all 33
owner-only credential sources and completed in 16m29s; read-only `verify`
passed all 56 gates in 80s. A separate clean-VM trial passed
`secrets-init --input` with five custom fields across four credential groups,
including matching Job API runtime and bootstrap sources. Neither trial
printed credential values. The new
application-identity Jobs generate their temporary administrator-namespace
credential Secrets in memory from runtime values, so those identities need no
separate temporary bootstrap Secret files.

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
`verify-source` checks that Docker Buildx is installed and verifies anonymous
Docker Hub access and all locked public digests before a fresh cluster is
created; it does not contact the local registry. After the local registry
exists, `mirror` uses Docker Buildx to copy each pinned public manifest or OCI
index to the registry's `127.0.0.1:5001` endpoint, then verifies the matching local
repository and immutable digest. The loopback address reaches the same registry
as `clouddsp-registry.localhost:5001` and avoids Docker Desktop's proxy and
Buildx's HTTPS probe for the custom registry hostname. A prior Docker push may
have left a tag pointing to one child of the locked index; `mirror` repairs
only that specific mismatch. Existing matching local images are skipped, while
an unrelated mismatched tag remains an error. `verify` checks both registries
without writing. The lock remains the authority; none of these modes rewrites it.

All 19 current local images target Linux ARM64. The public repository and
every tag were verified by anonymous manifest requests. A Job API image was
pulled from Docker Hub and pushed to a disposable empty `.localhost` registry
with its digest unchanged. A complete fresh-registry `mirror` run and full root
`bootstrap-platform` run passed in the September 29 VM trials with matching
digests in both registries. The full 91-stage clean-VM trial then passed from
an empty registry on September 30, 2026; the bootstrap mirrored all 19 images,
and root `verify` passed all 56 gates. See the
[trial record](../trials/2026-09-30-clean-vm-bootstrap-d903e8c.md).
Normal `cleanup` retains the populated registry.

## Root verification and existing-cluster reconcile

`verify` first runs that same preflight, checks Keycloak's database, role,
grants, and runtime Secret after the PostgreSQL release, then checks the RabbitMQ,
MinIO, Job API MinIO, upload-intake RabbitMQ/MinIO, Demucs MinIO, Basic Pitch
MinIO, and ADTOF MinIO Secrets against their ignored local sources without
printing values. It directly audits the new upload-intake, dispatcher,
Demucs, and KEDA PostgreSQL/RabbitMQ identities against restricted grants and
permissions; those checks do not depend on completed Jobs that may expire.
It checks Keycloak's bootstrap-admin Secret before the Keycloak release, then
checks all fifteen CloudDSP Helm releases and the Job API and application
database/broker stages, the
[Keycloak realm/client state](../../scripts/keycloak-config-verify.rb),
the [MinIO bucket stage](../../scripts/minio-buckets-stage.rb), the
[Job API IAM stage](../../scripts/minio-job-api-iam-stage.rb), the
[upload-intake IAM stage](../../scripts/minio-upload-intake-iam-stage.rb), the
[Demucs IAM stage](../../scripts/minio-demucs-iam-stage.rb), the
[Basic Pitch IAM stage](../../scripts/minio-basic-pitch-iam-stage.rb), the
[ADTOF IAM stage](../../scripts/minio-adtof-iam-stage.rb), and
[IAM/notification state](../../scripts/minio-notification-stage.rb), the pinned
[KEDA release](../../scripts/keda-release-stage.rb), and its controller Deployments and CRDs
in dependency order.
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
and suppress credential-bearing client output on failure. Since direct
containerd `ctr` pulls do not use k3s's configured registry rewrite, the MinIO
state verifier pulls the same digest from the registry's node-internal HTTP
endpoint before running the transient client.

`reconcile` is currently for an already deployed cluster. It runs the same
preflight and ordered gates, but calls `reconcile` for ten audited
external-state runners: Job API PostgreSQL database/migrations, RabbitMQ
processing topology, RabbitMQ source-intake topology/users, the MinIO shared
sample policy, Job API, upload-intake, Demucs, Basic Pitch, and ADTOF MinIO
temporary Secret cleanup, and the MinIO source-upload notification. Each IAM
stage deletes only a matching temporary Secret after its exact user and policy
state verifies. Other runners
create only wholly missing versioned bootstrap state and verify its durable
result; partial state and drift stop the command. All fifteen Helm releases
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
[Mailpit](../../scripts/mailpit-release.rb), [Keycloak](../../scripts/keycloak-release.rb),
[PostgreSQL](../../scripts/postgresql-release.rb),
[MinIO](../../scripts/minio-release.rb),
[RabbitMQ](../../scripts/rabbitmq-release.rb),
[shared KEDA scaling authentication](../../scripts/scaling-auth-release.rb),
[Basic Pitch worker](../../scripts/basic-pitch-release.rb),
[ADTOF worker](../../scripts/adtof-release.rb),
[Demucs worker](../../scripts/demucs-release.rb),
[frontend](../../scripts/frontend-release.rb),
[legacy dispatcher](../../scripts/dispatcher-release.rb),
[generic dispatcher](../../scripts/generic-dispatcher-release.rb),
[Job API](../../scripts/job-api-release.rb), and
[upload-intake](../../scripts/upload-intake-release.rb) release
scripts perform component-specific render and live-spec comparisons. The
`plan` and `verify` do not install, adopt, migrate, bootstrap, or clean up
anything. The [resource ownership map](../../resource-ownership-map.md) records the full
versioned/live snapshot and proposed boundaries.

## Mailpit Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/mailpit-release.rb plan
./k8Deployment/kubernetes/scripts/mailpit-release.rb adopt
./k8Deployment/kubernetes/scripts/mailpit-release.rb install
./k8Deployment/kubernetes/scripts/mailpit-release.rb verify
./k8Deployment/kubernetes/scripts/mailpit-release.rb smoke
```

The [Mailpit chart](../../helm/mailpit/README.md) owns only the existing
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
also part of root `verify` and `reconcile`. The guarded fresh PostgreSQL Helm
install and `bootstrap-platform` run this stage in dependency order.

## PostgreSQL protected Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/postgresql-release.rb plan
./k8Deployment/kubernetes/scripts/postgresql-release.rb adopt
./k8Deployment/kubernetes/scripts/postgresql-release.rb install
./k8Deployment/kubernetes/scripts/postgresql-release.rb verify
./k8Deployment/kubernetes/scripts/postgresql-release.rb smoke
```

The [PostgreSQL chart](../../helm/postgresql/README.md) owns the existing
StatefulSet, normal ClusterIP Service, and governing headless Service. Its
script verifies source/render/live equality and the bound generated PVC.
Before `adopt` can call Helm, the versioned
[`backup and restore rehearsal`](../../scripts/postgresql-backup-and-restore-test.sh)
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

## Job API PostgreSQL runtime credential Secret stage

```bash
ruby ./k8Deployment/kubernetes/scripts/job-api-database-secret-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/job-api-database-secret-stage.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/job-api-database-secret-stage.rb verify
```

Populate the ignored `k8Deployment/.local/job-api-database-credentials.secret.yaml`
and `k8Deployment/.local/job-api-database-bootstrap-credentials.secret.yaml`
from their committed API templates with the same restricted database, role,
and password. The stage checks both Secret contracts and rejects a placeholder
password. Fresh `bootstrap` creates only the absent `clouddsp-app` runtime
Secret after a server dry run; the data-namespace duplicate is created and
removed by the separate database-role Job runner. Read-only `verify` compares
the live encoded values with the ignored source without printing the password.
`bootstrap-platform` runs this stage before Job API PostgreSQL reconciliation,
and root `verify` checks it before the database/migration gate.

## Job API PostgreSQL bootstrap and migration order

```bash
./k8Deployment/kubernetes/scripts/job-api-postgresql-stage.rb plan
./k8Deployment/kubernetes/scripts/job-api-postgresql-stage.rb verify
./k8Deployment/kubernetes/scripts/job-api-postgresql-stage.rb reconcile
```

This narrow combined command runs the database/role stage first and the schema
migration stage second. It is useful for verification and for reconciliation
after both worker roles have been provisioned. On a fresh cluster, `plan` reports the pending
database bootstrap and defers migration inspection until the database exists.
`verify` fails if either stage is incomplete. Full `reconcile` requires both
worker roles before v007. For an empty cluster use `bootstrap-platform`, whose
staged migration path creates the roles after v006. The same two
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
./k8Deployment/kubernetes/scripts/job-api-migrations.rb reconcile-prerequisites
./k8Deployment/kubernetes/scripts/job-api-migrations.rb reconcile
```

This standalone bootstrap stage runner requires PostgreSQL, the
`clouddsp_job_api` database, and the app-namespace Job API schema-owner Secret
must exist before a pending migration can run. `plan` reads only the database's
`schema_migrations` ledger and immutable SQL ConfigMaps; `verify` additionally
requires every versioned migration to be applied. The script reads the ledger
through the existing PostgreSQL Pod and its Secret-backed environment. It does
not fetch Secret values to the host or query application data.

`reconcile-prerequisites` applies only v001–v006, the schema required by the
Basic Pitch and ADTOF database role Jobs. After both roles exist, `reconcile`
applies v007–v009. Both modes accept only an exact prefix of the v001–v009 migration IDs and
descriptions extracted from versioned SQL. It checks each applied SQL
ConfigMap against the live immutable copy and refuses a pending fixed-name
Job that still exists without a ledger row. For a missing migration, it
validates and creates its versioned ConfigMap/Job, waits for completion, and
checks the next ledger row before advancing. A failed or ambiguous Job is
left in place for inspection. Repeated reconcile on a complete ledger is a
read-only no-op.

## Worker PostgreSQL role bootstrap

```bash
ruby ./k8Deployment/kubernetes/scripts/worker-database-stage.rb basic-pitch plan
ruby ./k8Deployment/kubernetes/scripts/worker-database-stage.rb basic-pitch bootstrap
ruby ./k8Deployment/kubernetes/scripts/worker-database-stage.rb basic-pitch verify
ruby ./k8Deployment/kubernetes/scripts/worker-database-stage.rb adtof plan
ruby ./k8Deployment/kubernetes/scripts/worker-database-stage.rb adtof bootstrap
ruby ./k8Deployment/kubernetes/scripts/worker-database-stage.rb adtof verify
```

Each worker needs matching ignored runtime and bootstrap PostgreSQL Secret
sources in `k8Deployment/.local/`. The stage requires the worker's prerequisite
Job API migration, creates the app-namespace runtime Secret, then creates a
temporary data-namespace copy for its versioned role/grant Job. It waits for
completion, verifies the restricted role's catalog privileges, and removes
the temporary Secret. A failed Job and temporary Secret remain for inspection.
`verify` checks the live runtime Secret and role without changing them.

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

## upload-intake MinIO runtime credential Secret stage

```bash
ruby ./k8Deployment/kubernetes/scripts/upload-intake-minio-secret-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/upload-intake-minio-secret-stage.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/upload-intake-minio-secret-stage.rb verify
```

Populate the ignored `k8Deployment/.local/upload-intake-minio-credentials.secret.yaml`
and `k8Deployment/.local/upload-intake-minio-bootstrap-credentials.secret.yaml`
from their committed templates with the same restricted key. The stage checks
both source contracts, fixed access-key identity, and matching non-placeholder
values. Fresh `bootstrap` creates only the absent runtime Secret in
`clouddsp-app` after a server dry run. The temporary `clouddsp-data` Secret
belongs to the later upload-intake IAM Job stage. Read-only `verify` compares
the live Secret with the ignored source in memory and suppresses credentials
from output. Root `bootstrap-minio` stages it after the MinIO release; root
`verify` checks it before the bucket and IAM gates.

## Demucs MinIO runtime credential Secret stage

```bash
ruby ./k8Deployment/kubernetes/scripts/demucs-minio-secret-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/demucs-minio-secret-stage.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/demucs-minio-secret-stage.rb verify
```

Populate the ignored `k8Deployment/.local/demucs-minio-credentials.secret.yaml`
and `k8Deployment/.local/demucs-minio-bootstrap-credentials.secret.yaml` from
their committed templates with the same restricted key. The stage checks
both Kubernetes Secret contracts and the matching non-placeholder MinIO key.
Fresh `bootstrap` creates only the absent `clouddsp-app` runtime Secret after
a server dry run. The temporary `clouddsp-data` Secret and artifacts policy
belong to a later IAM Job stage. Read-only `verify` compares the live encoded
values with the ignored runtime source in memory without printing credentials.
Root `bootstrap-minio` stages this Secret before the bucket work; root `verify`
checks it before the MinIO IAM gates.

## Basic Pitch MinIO runtime credential Secret stage

```bash
ruby ./k8Deployment/kubernetes/scripts/basic-pitch-minio-secret-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/basic-pitch-minio-secret-stage.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/basic-pitch-minio-secret-stage.rb verify
```

Populate the ignored
`k8Deployment/.local/basic-pitch-minio-credentials.secret.yaml` and
`k8Deployment/.local/basic-pitch-minio-bootstrap-credentials.secret.yaml`
from their committed templates with one restricted key pair. The stage checks
both Secret contracts, fixed MinIO access-key identity, and matching
non-placeholder secret keys. Fresh `bootstrap` creates only the absent
`clouddsp-app` runtime Secret after a server dry run. Its temporary
`clouddsp-data` counterpart and artifacts policy belong to the later Basic
Pitch IAM Job stage. Read-only `verify` checks the live encoded values against
the ignored source in memory without printing credentials. Root
`bootstrap-minio` stages the Secret before bucket creation; root `verify`
checks it before the MinIO IAM gates.

## ADTOF MinIO runtime credential Secret stage

```bash
ruby ./k8Deployment/kubernetes/scripts/adtof-minio-secret-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/adtof-minio-secret-stage.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/adtof-minio-secret-stage.rb verify
```

Populate the ignored `k8Deployment/.local/adtof-minio-credentials.secret.yaml`
and `k8Deployment/.local/adtof-minio-bootstrap-credentials.secret.yaml` from
their committed ADTOF templates with the same restricted key pair. The stage
checks both Secret contracts, the fixed `clouddsp-adtof` access-key identity,
and matching non-placeholder secret keys. Fresh `bootstrap` creates only the
absent `clouddsp-app` runtime Secret after a server dry run. The temporary
`clouddsp-data` provisioning Secret and artifacts policy belong to the later
ADTOF IAM Job stage. Read-only `verify` compares live encoded values with the
ignored runtime source without printing credentials. Root `bootstrap-minio`
stages the runtime Secret before bucket creation; root `verify` checks it
before MinIO IAM gates.

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

## upload-intake MinIO IAM bootstrap and temporary Secret cleanup

```bash
ruby ./k8Deployment/kubernetes/scripts/minio-upload-intake-iam-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/minio-upload-intake-iam-stage.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/minio-upload-intake-iam-stage.rb verify
ruby ./k8Deployment/kubernetes/scripts/minio-upload-intake-iam-stage.rb reconcile
```

Fresh `bootstrap` requires the MinIO release, bucket boundaries, and
upload-intake runtime Secret to verify. It refuses an existing upload-intake
user, policy, temporary Secret, policy ConfigMap, or Job. After server dry
runs, it creates the ignored temporary restricted Secret in `clouddsp-data`,
the immutable source-read policy ConfigMap, and the fixed provisioning Job.
It waits for Job completion and checks the exact MinIO policy document, user,
and attachment before deleting the temporary Secret. A partial run stops for
inspection. `verify` is read-only and requires that Secret to be absent.
Existing-cluster `reconcile` deletes only a leftover temporary Secret whose
values match the ignored source, after all durable IAM state verifies. It
never creates or changes the existing MinIO user or policy.

## Demucs MinIO IAM bootstrap and temporary Secret cleanup

```bash
ruby ./k8Deployment/kubernetes/scripts/minio-demucs-iam-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/minio-demucs-iam-stage.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/minio-demucs-iam-stage.rb verify
ruby ./k8Deployment/kubernetes/scripts/minio-demucs-iam-stage.rb reconcile
ruby ./k8Deployment/kubernetes/tests/minio-smoke/demucs-iam-smoke.rb
```

Fresh `bootstrap` requires the MinIO release, private bucket boundaries, and
Demucs runtime Secret to verify. It refuses an existing Demucs user, policy,
temporary Secret, policy ConfigMap, or Job. After server dry runs, it creates
the ignored temporary restricted Secret in `clouddsp-data`, the immutable
artifacts policy ConfigMap, and the fixed provisioning Job. It waits for the
Job and checks the exact source-read/stem-write policy document, user, and
attachment before deleting the temporary Secret. A partial run remains for
inspection. Read-only `verify` requires that Secret to be absent. Existing
cluster `reconcile` removes only a matching leftover temporary Secret after
complete IAM state verifies; it never recreates or rotates the user or policy.
The focused S3 smoke uses the Demucs runtime key to put/get one unique private
stem probe and requires MIDI writes, deletion, and bucket listing to be denied.
It uses the ignored local MinIO root key only to remove that probe object;
it never creates an `uploads/` object or a processing message.

## Basic Pitch MinIO IAM bootstrap and temporary Secret cleanup

```bash
ruby ./k8Deployment/kubernetes/scripts/minio-basic-pitch-iam-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/minio-basic-pitch-iam-stage.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/minio-basic-pitch-iam-stage.rb verify
ruby ./k8Deployment/kubernetes/scripts/minio-basic-pitch-iam-stage.rb reconcile
ruby ./k8Deployment/kubernetes/tests/minio-smoke/basic-pitch-iam-smoke.rb
```

Fresh `bootstrap` requires the MinIO release, private bucket boundaries, and
Basic Pitch runtime Secret to verify. It refuses an existing Basic Pitch user,
policy, temporary Secret, policy ConfigMap, or Job. After server dry runs it
creates the ignored temporary restricted Secret in `clouddsp-data`, the
immutable artifacts policy ConfigMap, and the fixed provisioning Job. It waits
for completion, verifies the exact stem-read/MIDI-write policy attachment,
then removes the temporary Secret. A partial run remains for inspection.
Read-only `verify` requires the temporary Secret to be absent. Existing-cluster
`reconcile` removes only a matching leftover Secret after durable IAM verifies;
it never recreates or rotates the user or policy. The focused S3 smoke plants
one random stem with the root key, verifies that Basic Pitch can read it and
put/get a random MIDI object, and requires stem writes, deletion, and bucket
listing to be denied. It removes those exact probes with the root key and
never creates an `uploads/` notification or processing message.

## ADTOF MinIO IAM bootstrap and temporary Secret cleanup

```bash
ruby ./k8Deployment/kubernetes/scripts/minio-adtof-iam-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/minio-adtof-iam-stage.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/minio-adtof-iam-stage.rb verify
ruby ./k8Deployment/kubernetes/scripts/minio-adtof-iam-stage.rb reconcile
ruby ./k8Deployment/kubernetes/tests/minio-smoke/adtof-iam-smoke.rb
```

Fresh `bootstrap` requires the MinIO release, private buckets, and ADTOF
runtime Secret to verify. It refuses an existing ADTOF user, policy, temporary
Secret, policy ConfigMap, or Job. After server dry runs it creates the ignored
temporary restricted Secret in `clouddsp-data`, the immutable artifacts policy
ConfigMap, and the fixed provisioning Job. It waits for completion and checks
the exact drums-read/fixed MIDI-and-tempo-write policy and user attachment
before removing the temporary Secret. The Job's 256 MiB policy association
limit is checked because a smaller limit previously caused an OOM. A partial
run remains for inspection. Read-only `verify` requires the temporary Secret
to be absent. Existing-cluster `reconcile` deletes only a matching leftover
Secret after durable IAM state verifies; it never recreates or rotates the
user or policy. The focused S3 smoke checks allowed `drums.wav` reads and the
two fixed output names, plus denied other-stem reads, writes outside those
names, deletion, and bucket listing. It uses random `stems/` and `midi/` keys,
removes those exact probes, and never sends an upload notification.

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
This path passed in the disposable fresh-cluster platform trial; the existing
Mac cluster was retained. The root `bootstrap-minio` composes source-intake
broker state and the fresh bucket stage around this release. IAM remains
separate.

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

The [MinIO chart](../../helm/minio/README.md) owns its existing StatefulSet,
normal and headless Services, and S3 Ingress. Its script checks exact
source/render/live spec and bound-PVC parity before adoption. The automatic
[`backup and restore rehearsal`](../../scripts/minio-backup-and-restore-test.py) briefly
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
backup or take ownership of existing objects. This path passed in the
disposable fresh-cluster platform trial; the existing Mac cluster was retained.

The [RabbitMQ chart](../../helm/rabbitmq/README.md) owns the existing broker
StatefulSet, AMQP, headless and management Services, and its ingress
NetworkPolicy. Its automatic
[`backup and restore rehearsal`](../../scripts/rabbitmq-backup-and-restore-test.py) checks
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
./k8Deployment/kubernetes/scripts/scaling-auth-release.rb install
./k8Deployment/kubernetes/scripts/scaling-auth-release.rb adopt
./k8Deployment/kubernetes/scripts/scaling-auth-release.rb verify
./k8Deployment/kubernetes/scripts/scaling-auth-release.rb verify-prerequisites
```

The [scaling-auth chart](../../helm/scaling-auth/README.md) owns only the two
app-namespace `TriggerAuthentication` resources. Fresh `install` requires the
KEDA controller and both restricted scaler identities, refuses existing
authentication or worker scaler resources, and installs only those two
objects. `verify-prerequisites` checks that release before the first fresh
worker install. Full `verify` also requires all three Ready worker
`ScaledObject`s and their correctly owned HPAs. `adopt` remains the protected
one-time takeover for an existing cluster. Secret values, worker scale
decisions, and the KEDA controller remain outside this release.

## Basic Pitch worker Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb plan
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb adopt
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb verify
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb upgrade-scaling
./k8Deployment/kubernetes/scripts/basic-pitch-release.rb upgrade-numba
```

The [Basic Pitch chart](../../helm/basic-pitch/README.md) owns the existing
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

The [ADTOF chart](../../helm/adtof/README.md) owns only the existing worker
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

The [Demucs chart](../../helm/demucs/README.md) owns its existing worker
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

### Reassert the installed Demucs scaling profile

```bash
./k8Deployment/kubernetes/scripts/reconcile-demucs-scaling.sh
```

This scoped maintenance command verifies the PostgreSQL and RabbitMQ scaler
identities, then runs the scaling-auth and idle Demucs Helm preflight checks
before either write. It upgrades the existing `clouddsp-scaling-auth` release
and then `clouddsp-demucs` using their versioned charts, with explicit context
`k3d-clouddsp-local`, namespace `clouddsp-app`, and three-minute waits. Final
release checks verify Helm ownership, stored and live manifests, authentication
references, scaler readiness, generated HPA ownership, and idle worker state.

Both releases must already be deployed and match the reviewed configuration;
missing or failed releases, configuration drift, or active Demucs work stop
before any Helm upgrade. Fresh creation belongs to `bootstrap-platform`, and
intentional chart changes need a separately reviewed rollout. The helper
verifies existing credentials instead of applying Secrets or rerunning a
database bootstrap Job. It uses Helm for both shared authentications and the
worker resources and stops at the first failed stage for inspection.

## Keycloak Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/keycloak-release.rb plan
./k8Deployment/kubernetes/scripts/keycloak-release.rb adopt
./k8Deployment/kubernetes/scripts/keycloak-release.rb install
./k8Deployment/kubernetes/scripts/keycloak-release.rb verify
./k8Deployment/kubernetes/scripts/keycloak-release.rb smoke
```

The [Keycloak chart](../../helm/keycloak/README.md) owns the existing identity
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

For a fresh cluster, `bootstrap-platform` creates and verifies the Keycloak
database, installs Mailpit, then creates the ignored bootstrap-admin Secret
before `keycloak-release.rb install`. Direct `install` refuses an existing
Helm release or any of the three workload objects and verifies the database
and admin Secret before Helm writes. `keycloak-realm-stage.rb bootstrap` then
runs the six versioned Admin API Jobs in dependency order only if the CloudDSP
realm is absent. Its `plan` and `verify` modes are read-only; an existing but
incomplete realm stops without applying Jobs. Final state is checked through
the same full realm/client verifier used by root `verify`.

```bash
ruby ./k8Deployment/kubernetes/scripts/keycloak-realm-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/keycloak-realm-stage.rb bootstrap
ruby ./k8Deployment/kubernetes/scripts/keycloak-realm-stage.rb verify
```

## Frontend Helm adoption and verification

```bash
./k8Deployment/kubernetes/scripts/frontend-release.rb plan
./k8Deployment/kubernetes/scripts/frontend-release.rb adopt
./k8Deployment/kubernetes/scripts/frontend-release.rb verify
```

The [frontend chart](../../helm/frontend/README.md) owns its existing
Deployment, ClusterIP Service, and Traefik Ingress in `clouddsp-app`.
The shared [`stateless-release.rb`](../../scripts/stateless-release.rb) helper supplies the
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

The [dispatcher chart](../../helm/dispatcher/README.md) owns only the existing
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

The [generic dispatcher chart](../../helm/generic-dispatcher/README.md) owns only
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

The [Job API chart](../../helm/job-api/README.md) owns its existing Deployment,
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

The [upload-intake chart](../../helm/upload-intake/README.md) owns only its
outbound Deployment in `clouddsp-app`. It preserves the Pod's three restricted
PostgreSQL, MinIO, and RabbitMQ Secret references and the reviewed image
digest. The release checker requires source/render/live equality before the
one-time adoption and then verifies the original Deployment and Pod UIDs.
`verify` checks Helm ownership, stored manifest, Ready Pod, and running
digest. The separate [source-to-outbox integration smoke](../../tests/source-intake-smoke/README.md)
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
ruby ./k8Deployment/kubernetes/scripts/keda-release-stage.rb plan
ruby ./k8Deployment/kubernetes/scripts/keda-release-stage.rb install
ruby ./k8Deployment/kubernetes/scripts/keda-release-stage.rb verify
```

The guarded `install` path is part of `bootstrap-platform`. It requires no
KEDA Helm release, namespace, CRD, or external metrics API registration,
then calls the pinned installer and verifies the exact Helm values, three
controller rollouts, six KEDA CRDs, and metrics API registration. `plan` and
`verify` are read-only. The fresh install passed as stage 56/57 in an empty
Ubuntu VM, followed by independent read-only verification.

The underlying shell command supports a separately requested install or
upgrade of the pinned KEDA release:

```bash
./k8Deployment/kubernetes/scripts/install-keda.sh
```

It reads the pinned official chart release and local values from
[`../helm/keda/`](../../helm/keda), targets only the
`k3d-clouddsp-local` context, and waits for KEDA's operator, metrics API
server, admission webhook, and custom resource definitions to become ready.
The three KEDA controller Pods run inside the `keda` namespace; Helm itself is
only the short-lived host command that submits their manifests.

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

This uses the local frontend's two-stage Dockerfile and the root `frontend/`
named build context: the pinned official Node image builds the shared UI's
local profile, then the pinned official NGINX image serves only those
assets as a non-root process on container port 8080. The script reads the
public Keycloak, Job API, and MinIO browser settings from the ignored
`k8Deployment/.local/frontend.env.production`, pushes the arm64 image to
`clouddsp-registry.localhost:5001/frontend`, and prints its immutable registry
digest plus Docker's local uncompressed size. Vite generates a CSP meta tag and
matching NGINX response-header include, allowing only the explicit configured
MinIO origin for direct presigned POSTs. It does not deploy a Pod; update the
reviewed image lock and Helm chart with the resulting digest before an upgrade.

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
