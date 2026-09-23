# CloudDSP three-job six-stem local load-test contract

This directory contains the bounded end-to-end load-test implementation for
the local k3d profile: a real authenticated API/upload client, a temporary
identity/observer lifecycle broker, read-only PostgreSQL/MinIO/RabbitMQ/KEDA
observers, and a fully wired but deliberately suspended Kubernetes Job. This
repository state does not apply the Job, create Secrets, create user accounts,
submit requests, or alter cluster state. The staged implementation notes below
are historical checkpoints; the current final wiring is summarized at the end.

## Goal

Prove that the already-integrated local pipeline handles a small concurrent
batch without bypassing its durable boundaries:

```text
authenticated API requests (three unique jobs)
  -> presigned private MinIO source uploads
  -> MinIO notification and upload-intake verification
  -> PostgreSQL source events and outbox publication
  -> RabbitMQ requests and KEDA worker activation
  -> Demucs stems, Basic Pitch MIDI, and ADTOF drum output
  -> durable completion, exact artifact verification, queue drain, and KEDA cooldown
```

The observer currently retains the three generated PostgreSQL Jobs and their
MinIO objects for diagnosis; it removes only the temporary Keycloak, observer
PostgreSQL, and observer MinIO credentials. Exact durable-data cleanup is not
implemented in this smoke Job, so a separate reviewed cleanup workflow is
required before repeated runs are treated as clean-room benchmarks.

This is an integration/load check, not a musical-quality benchmark and not a
claim of production throughput. The local Apple-Silicon k3d profile runs CPU
workers and intentionally keeps Demucs at one replica.

## Approved workload

| Item | Fixed test decision | Reason |
| --- | --- | --- |
| Concurrent authenticated-user-equivalent jobs | 3 | Exercises three independent durable Jobs without filling the local cluster. |
| Default `stem_mode` | `6-stems` | Covers every Demucs output routing branch. |
| Sources | Three generated, valid, bounded WAV files with one unique run marker per filename | Avoids copyright material and lets the guarded observer recognize only this run. |
| Demucs requests | 3 | KEDA must activate the `0 → 1` capped CPU worker; RabbitMQ retains the remaining requests. |
| Basic Pitch requests | 15 | Five non-drum stems per six-stem Job exercise the existing `0 → 3` scaler. |
| ADTOF requests | 3 | One drum stem per Job exercises the existing `0 → 2` scaler. |
| Demucs KEDA cap | 1 | One local Pod may use up to 2 CPU/4 GiB; backlog is safer than competing model Pods. |
| Basic Pitch KEDA cap | 3 | Matches the reviewed local stem-burst policy. |
| ADTOF KEDA cap | 2 | Matches the reviewed local drum-worker policy. |

A six-stem Demucs result routes `drums` to ADTOF and `bass`, `vocals`,
`other`, `guitar`, and `piano` to Basic Pitch. The load client must not send
worker messages directly: generic dispatchers remain the only outbox-to-AMQP
publishers, and workers retain their normal restricted identities.

### What “authenticated-user-equivalent” means

The executable test will use the regular Job API and the regular constrained
MinIO POST form, exactly as an authenticated frontend does. It will obtain its
short-lived access token through a disposable Keycloak test client using the
password grant, rather than driving a real browser's Authorization Code + PKCE
redirect loop. That distinction is deliberate:

- it proves the authenticated API/upload/processing path under concurrent
  load without embedding browser automation or a real account in a cluster
  Job; and
- it does **not** replace the separate frontend OIDC smoke tests, which remain
  the authority for browser redirect, PKCE, registration, and email-verification
  behaviour.

The test client receives no PostgreSQL, MinIO root, RabbitMQ, or Kubernetes
credential. In particular, it never receives a general S3 credential: the
ordinary Job API still returns the one constrained upload form for each source.

## Identity, observation, and cleanup design

The three requests share one disposable Keycloak user. Sharing an owner makes
the Job API path realistic—each request still receives a distinct
server-generated `job_id`—and gives cleanup one narrow owner boundary. It does
not test cross-account authorization; the existing owner-isolation smoke tests
cover that separately.

### 1. Disposable authenticated client

A finite identity-bootstrap step will create the following resources for one
load run:

| Resource | Runtime capability | Deliberate restriction |
| --- | --- | --- |
| Keycloak test client | Password-grant token issuance for this load run only | It is not the React client, has no administrator role, and receives the `clouddsp-job-api` audience only. |
| Keycloak test user | Owns only the three test Jobs | A random password exists only in the Pod's memory/shared `emptyDir`; its username and email use an opaque run marker. |
| Load-client container | `POST /jobs`, its returned upload forms, and owner-bound job reads | No database, S3 access key, RabbitMQ, Keycloak-admin, or Kubernetes API authority. |

The Keycloak bootstrap administrator Secret is mounted only into the finite
identity/lifecycle broker, never the load client. The broker creates the
temporary client and user, writes just the temporary client/user values needed
for token issuance to the bounded shared volume, and then waits only to revoke
those temporary records during teardown. It must never copy an administrator
token or password into that volume. The load client uses the temporary user to
call `/auth/me` first and records the returned immutable `sub` only in its
private run-coordinates handoff.

Each `POST /jobs` uses a filename shaped like
`six-stem-load-<opaque-run-marker>-<ordinal>.wav`. The Job API, not the test,
chooses the UUID and the `uploads/<job_id>/...` key. The marker lets a guarded
observer distinguish the three generated rows without allowing the client to
choose an object prefix or owner.

### Implementation notes from earlier phases (historical)

[`client/keycloak_identity_lifecycle.py`](client/keycloak_identity_lifecycle.py)
now provides the tested, standard-library Keycloak adapter for this step. It
creates the temporary direct-grant client, Job API audience mapper, verified
test user, and a random non-temporary password; its `revoke()` method obtains a
fresh administrator token and removes the user before the client. Its unit
tests use a fake transport, so they never contact the live Keycloak service or
need a Secret.

The adapter is intentionally not a Kubernetes Job. The lifecycle-broker
entrypoint and bounded shared handoff supply only temporary-user values to a
later client container. The later container is where a mounted local bootstrap
Secret becomes necessary.

[`client/lifecycle_broker.py`](client/lifecycle_broker.py) is that entrypoint.
It generates an opaque marker, provisions the Keycloak identity, writes only
`client_id`, `username`, `password`, and the marker to a 0600 JSON file, then
waits for the client’s complete three-job coordinate claim. Before the existing
strict `succeeded`/`failed` terminal marker can matter, it exchanges a fresh
temporary-user token, rechecks `/auth/me`, and reads all three owner-bound Job
snapshots. Only then does it write a third minimal 0600 verified-ingress marker
containing the run marker, fixed mode, and job count—not the `sub`, Job IDs,
checksums, URLs, or a credential. That marker is informational state, never an
authority grant: a later broker phase must perform its own in-process proof
before minting any observer capability. The broker catches Kubernetes'
graceful `SIGTERM` so its `finally` block removes the temporary identity and
every known handoff file before the Pod's termination grace period expires.
`SIGKILL` cannot be caught; an interrupted Pod therefore remains a deliberate
marker-scoped recovery case.

[`client/lifecycle_handoff.py`](client/lifecycle_handoff.py) contains the
atomic private-file protocol shared by the broker and the future authenticated
client. The eventual Pod must run both containers as the same non-root UID,
mount this one bounded `emptyDir` into those containers only, and expose no
administrator Secret or ServiceAccount token to the client.

[`client/authenticated_load_client.py`](client/authenticated_load_client.py)
now supplies the isolated ingress routine. Given only the temporary client/user
handoff values, it exchanges one user token, verifies `/auth/me`, then starts
exactly three concurrent `POST /jobs` requests with `stem_mode: 6-stems`. For
each server-generated job ID it validates the returned bound form fields and
posts its generated two-second stereo WAV through the same form, replacing
only the browser-facing `minio.localhost:8080` origin with the reviewed
in-cluster MinIO Service origin. Only after all three uploads complete does it
atomically write the subject, fixed mode, and the three IDs/filenames/sizes/
checksums to the private coordinate handoff. It has no database, AMQP,
object-store credential, Keycloak administration, or Kubernetes API code. Its
executable entrypoint deliberately fails closed: the later broker verification
and observer orchestration must be designed before a Pod can run it.

[`client/owner_bound_job_verifier.py`](client/owner_bound_job_verifier.py)
now supplies that verification boundary as a separate broker-only adapter. It
uses a fresh token for the in-memory temporary user, rechecks `/auth/me`, then
performs exactly three owner-bound `GET /jobs/{job_id}` calls. Each snapshot
must reproduce the claimed canonical ID, direct-upload source, filename, byte
count, audio type, and `6-stems` mode. It never lists Jobs, reads a database,
contacts MinIO/RabbitMQ, or grants an observer capability. A subject or
snapshot mismatch stops before later broker code can create one.

[`client/observer_capability_contract.py`](client/observer_capability_contract.py)
defines a pure boundary. It accepts only the broker's in-memory
owner-bound proof—not the writable handoff marker—and revalidates the fixed
three-job shape before deriving the future database owner/Job coordinates and
MinIO scope: three exact source keys plus three Job-specific `stems/` and
`midi/` prefixes in `clouddsp-uploads`. It has no policy document, password,
role/user name, network client, file writer, subprocess, or Kubernetes object.
It is packaged in the runtime image so the broker can use this reviewed
contract after its owner-bound proof; no live cluster resource has yet been
allowed to mint or consume an observer capability.

The lifecycle, ingress, owner-bound verification, PostgreSQL snapshot, and
terminal-outcome handoff modules are packaged by
[`client/Dockerfile`](client/Dockerfile) into the ARM64 image tagged
`0.1.10-terminal-report-handoff` and pinned at
`clouddsp-registry.localhost:5001/six-stem-load-client@sha256:edb5631a4a86d34fec0f974bdaeeea99df893f898d322dc1733f3c00c50d8993`.
Its local uncompressed size is 50,604,261 bytes (48.26 MiB). The offline
source suite passes 51 tests, and the image validation stage passes 49 tests
without a Secret or live service. The final stage includes the hash-locked
Psycopg 3.2.13 binary driver for broker provisioning and observer reads, plus
its pinned `typing-extensions` transitive dependency, alongside the
standard-library runtime modules and a dedicated non-root UID. The local
registry digest pull was verified after
push and is recorded in
[`../../images.lock.yaml`](../../images.lock.yaml).

[`client/postgresql_admin_boundary.py`](client/postgresql_admin_boundary.py)
is the broker-only credential reader for the bootstrap adapter. It accepts
only dedicated broker environment names, pins the target to the in-cluster
PostgreSQL Service/database/port, bounds the values, and hides username and
password from its normal object representation. The connection driver is
loaded lazily by the bootstrap call. [`client/requirements.lock`](client/requirements.lock)
pins Psycopg 3.2.13, its ARM64 binary package, and the required
`typing-extensions` dependency by hash; the image dependency stage installs
only those reviewed packages without a compiler or system package manager.

The committed
[`six-stem-load-keycloak-bootstrap-credentials.secret.example.yaml`](six-stem-load-keycloak-bootstrap-credentials.secret.example.yaml)
documents the one temporary app-namespace Secret copy the broker will need.
Its ignored populated copy must contain the existing local master-realm admin
credentials; it is mounted only as two environment values in the broker, never
into the future authenticated client or observer containers.

The committed
[`six-stem-load-postgresql-bootstrap-credentials.secret.example.yaml`](six-stem-load-postgresql-bootstrap-credentials.secret.example.yaml)
documents a second, temporary app-namespace copy of the existing PostgreSQL
administrator username/password. It must copy the actual values from the
ignored `clouddsp-postgresql-credentials` Secret in `clouddsp-data`; the source
manifest's example username can differ from a local installation, so do not
guess or generate a new administrator password. Kubernetes Secrets are
namespace-scoped, which is why the future broker needs its own short-lived
copy. The paired connection reader fixes the destination to
`clouddsp-postgresql.clouddsp-data.svc:5432` and database
`clouddsp_job_api`, which is where the Job API schema and migrations reside.

[`six-stem-load-job.skeleton.yaml`](six-stem-load-job.skeleton.yaml) is valid
but deliberately `suspend: true`. It shows the broker's immutable image,
memory-backed 1 MiB handoff volume, non-root UID, 30-second graceful shutdown,
and absence of a ServiceAccount token. It has no authenticated client yet, so
it must not be unsuspended or applied as a load test. No Kubernetes Job,
Secret, or live Keycloak record has been created by this task.

[`client/postgresql_observer_bootstrap.py`](client/postgresql_observer_bootstrap.py)
now runs the database provisioning boundary inside the broker. It generates a
random observer password in memory, derives a SCRAM verifier locally, and
creates the role plus run-bound aggregate function in one transaction. Before
commit it checks the role flags and memberships, direct database/schema/
function ACLs, function owner/security-definer/search-path, absence of PUBLIC
execute on that function, and absence of application-table, sequence,
schema-create, or extra direct-function privileges. Any failed check rolls
back both the role and function. The raw password is not placed in SQL or
progress logs, and it is not handed off until all grant checks pass.

After commit, the broker atomically writes one bounded `0600` credential file
to a separate 1 MiB memory-backed `emptyDir`. It is distinct from the
authenticated-client handoff volume; the client must never mount it. The
future `durable-state-observer` will mount this volume read-only. At the
terminal outcome the broker drops only the marker-derived function and role,
then removes only the known credential file. Cleanup does not use `CASCADE`,
so unexpected dependencies stop it rather than deleting other database
objects.

At that implementation checkpoint, the reviewed
`0.1.9-postgresql-state-observer` ARM64 image had been built, pushed, and
digest-verified; it has since been superseded by `0.1.10-terminal-report-handoff`
after adding broker consumption of the non-terminal snapshot and final-result
contracts. No Secret or Kubernetes resource has been applied for this task.
The skeleton remains suspended until the durable-state observer and other
required components are implemented. Verified ingress is not pipeline
completion.

### 2. Separate observers, not elevated load-client access

One Kubernetes Job will eventually compose cooperating containers with a
bounded `emptyDir` handoff. Environment variables and volume mounts are
container-specific, so administrative values never become visible to the
authenticated load client.

| Container | Credentials / access | Responsibility |
| --- | --- | --- |
| `authenticated-load-client` | Temporary Keycloak user only | Creates three Jobs, uploads their three forms concurrently, and writes their IDs plus the authenticated `sub` to the handoff after validating every API response. |
| `capability-lifecycle-broker` | Keycloak bootstrap administration plus PostgreSQL/MinIO administration, each mounted only here | Verifies the three handed-off Jobs through the same temporary user's owner-bound API reads; then mints and later revokes exact-coordinate observer capabilities. It never processes audio or submits a Job. |
| `durable-state-observer` | A broker-minted PostgreSQL role limited to reviewed `SECURITY DEFINER` functions; a broker-minted MinIO identity for the three verified prefixes; a RabbitMQ monitoring-only identity | Reads aggregate state for exactly the three handed-off Job IDs, verifies private object metadata/content hashes, and checks the known main/retry/DLQ queue counts without reading message bodies. |
| `keda-scale-observer` | A projected, read-only ServiceAccount token mounted only in this container | Polls the three named Deployments, HPAs, and KEDA `ScaledObject` resources; records observed replica maxima but cannot change them. |

The later `Role`/`RoleBinding` must grant the KEDA observer only `get`,
`list`, and `watch` for `deployments.apps`, `horizontalpodautoscalers.autoscaling`,
and `scaledobjects.keda.sh` in `clouddsp-app`. It must not grant `create`,
`patch`, `delete`, `exec`, `pods/log`, `secrets`, or any cross-namespace access.
The other two containers retain `automountServiceAccountToken: false`.

The broker cannot trust a caller-supplied UUID list: it first obtains a fresh
token for the temporary user and checks all three `GET /jobs/{job_id}`
responses through the normal owner-bound API. It requires three distinct IDs,
the expected filename marker, `6-stems`, and the returned temporary `sub`
before it creates any observer policy. It then creates database functions with
that owner, marker, and exact three IDs embedded as reviewed literals, plus a
MinIO policy containing only those three `uploads/`, `stems/`, and `midi/`
prefixes. This dynamic minting is needed because the Job API—not the test—
generates each UUID.

The observer database functions return aggregate states and counts only. They
grant no direct table, schema, sequence, DDL, or role access. This is necessary
because the normal Job API deliberately exposes owner-safe snapshots rather
than internal task/outbox/delivery evidence.

[`client/postgresql_observer_contract.py`](client/postgresql_observer_contract.py)
now supplies the first concrete database definition without executing it. From
the in-memory verified-object contract it derives one marker-scoped temporary
role name and one zero-argument `SECURITY DEFINER` function name. The rendered
function embeds the verified owner and exact three UUIDs, joins only those Jobs
to their processing tasks/outbox events, and returns counts plus status maps:
no Job IDs, filenames, object keys, event payloads, errors, timestamps, or raw
rows. Its role contract is exactly `LOGIN`, `NOINHERIT`, nonsuperuser/no-DDL
attributes, one connection, `CONNECT`, `USAGE` on `public`, and `EXECUTE` on
that one function. The definition revokes PostgreSQL's default `PUBLIC`
function execution before granting the named role. It contains no password or
`CREATE ROLE` statement and has no database client, so it cannot create a role
or alter the running local database in this task.

[`client/postgresql_durable_state_observer.py`](client/postgresql_durable_state_observer.py)
is the first read-only observer adapter. It reads the broker's credential
document from the separate observer volume, revalidates the marker-derived
role/function names, connects only to the fixed PostgreSQL Service with
read-only transactions enabled, and selects the ten explicitly named
aggregate columns from the zero-argument function. It rejects extra/missing
rows, malformed counters, and unsafe status-map categories, while suppressing
database diagnostics that could reveal credentials or SQL details. Its result
contains only counts and status categories—never Job IDs, owner subjects,
object keys, or error text.

This adapter intentionally does not decide whether the end-to-end run passed:
it does not yet verify MinIO artifact hashes, RabbitMQ queue depth, or KEDA
cooldown, and it does not clean up data. Its five offline tests use a fake
connection and temporary local handoff only. The adapter is copied into the
published image, where its import was verified together with Psycopg 3.2.13.
The image still defaults to the lifecycle broker; no Kubernetes container
command or Job manifest invokes the observer yet, and the skeleton remains
suspended.

[`client/postgresql_observer_report.py`](client/postgresql_observer_report.py)
defines the observer-to-broker file contract. It writes one atomic `0600`
report with the opaque run marker and validated aggregate counts, or a fixed
failure code that contains no database diagnostics. The broker-side reader
requires the expected run marker and waits under a finite timeout. The report
must live on a dedicated memory-backed volume mounted only by the broker and
observer—not the authenticated client and not the observer's read-only
credential volume. An `observed` report is one database snapshot, not overall
pipeline completion or permission to clean up. Six offline tests cover the
schema, atomic/private write, failure category, run binding, and stale-file
rejection.

[`client/load_test_terminal_outcome.py`](client/load_test_terminal_outcome.py)
defines a second, separate report for the combined terminal outcome. The
lifecycle broker first waits for the PostgreSQL snapshot, logs that the run is
still incomplete, and then waits for this terminal report using the same
overall deadline. Only `succeeded` or `failed` from the exact run marker ends
that wait. Arrival of a PostgreSQL snapshot by itself does not revoke the
temporary PostgreSQL role or Keycloak identity. A failed combined report
returns a failing broker exit code after those temporary credentials are
revoked; it does not delete application Jobs, objects, or queue evidence.

The report directory is a dedicated memory-backed volume mounted by the
broker and reserved for the future trusted observer/orchestrator. The
authenticated client does not mount it. Offline tests prove that a PostgreSQL
snapshot cannot satisfy the terminal waiter and that broker teardown waits
until the separate final outcome arrives. The Job skeleton remains suspended:
the orchestrator that checks MinIO, RabbitMQ, and KEDA has not been implemented,
the report-capable ARM64 image has now been rebuilt, digest-pulled, and recorded,
and no smoke Job was applied.

### 3. Exact successful cleanup and failed-run preservation

The local Job API currently has no owner-authorized `DELETE /jobs/{job_id}`
route. The load test therefore must not claim to test product deletion or use
an over-broad object-store policy just to tidy up. Instead:

1. The observer cleans up **only after** all success evidence and worker
   cooldown observations are durable.
2. Its guarded PostgreSQL cleanup function was minted for exactly the three
   verified UUIDs and refuses unless their temporary owner, marker, `6-stems`
   mode, terminal-success task inventory, and absence of active leases remain
   true. It then removes only rows belonging to those three Jobs.
3. Its MinIO cleanup identity derives the three exact `uploads/`, `stems/`,
   and `midi/` prefixes from those verified database rows. It may `HeadObject`,
   stream-verify, and delete those exact keys, but must not list a bucket,
   presign objects, or operate on a caller-supplied arbitrary prefix.
4. The lifecycle broker removes the temporary PostgreSQL role/functions and
   MinIO user/policy, then revokes the disposable Keycloak user/client at the
   end of either a successful or failed run. Revocation prevents a failed test
   credential from remaining usable; it does not erase retained Job/object
   evidence.

If an assertion fails or the global deadline expires, the observer must return
a failed Job result and preserve PostgreSQL/MinIO/RabbitMQ evidence. It logs
only the opaque run marker—not tokens, paths, object keys, emails, passwords,
or full UUIDs. A later explicit recovery Job will accept that marker, re-check
the same three-job owner/mode/terminal boundary, and perform the exact cleanup.
It will never use a prefix-wide or namespace-wide purge.

## Required evidence

The future client must use generated test-only identifiers and record only
safe aggregate facts in its log—counts, state names, and elapsed durations.
It must never print an access token, password, OIDC code, presigned URL,
object key, user email, full UUID, source bytes, or service response body.

A passing run must establish all of these facts:

1. The normal authenticated API accepts exactly three unique six-stem jobs;
   no test client writes directly to PostgreSQL.
2. Each source uploads through its returned constrained form and upload intake
   marks the matching durable Job source-ready exactly once.
3. Demucs records exactly three successful canonical tasks and each Job gets
   its complete six-stem private object inventory once. Duplicate delivery
   must not create a second task for the same `(job_id, demucs, '')` key.
4. Exactly fifteen Basic Pitch and three ADTOF downstream tasks are created,
   complete once, and produce their expected private artifacts.
5. KEDA observes queued work and reaches the configured caps when backlog
   permits: one Demucs Pod, up to three Basic Pitch Pods, and up to two ADTOF
   Pods. A test must record observed replica maxima without assuming that a
   scheduler may exceed the configured caps.
6. No Job/task reaches a failed terminal state, no retry is left due after the
   bounded observation period, and relevant RabbitMQ main/retry/DLQ queues
   contain no stranded test delivery.
7. After work is idle, all three ScaledObjects return to zero according to
   their own cooldowns: Basic Pitch (one minute), ADTOF (three minutes), and
   Demucs (five minutes).
8. The observer retains successful and failed run data for diagnosis. It does
   not delete test Jobs or objects; a separate reviewed exact-scope cleanup
   workflow is required before repeated runs are treated as clean-room tests.

## Bounds and abort conditions

The executable load client and observer use explicit global deadlines that are
longer than the slowest allowed worker/cooldown path but finite. The client
must stop
creating new work as soon as any authenticated API, upload, durable-state,
integrity, queue, or terminal failure is observed. It must not delete evidence
when the result is uncertain. Do not improvise a database, object-store, or
queue purge.

## Current complete implementation

The current published ARM64 image is
`clouddsp-registry.localhost:5001/six-stem-load-client@sha256:96b002198c81cf3f9c436a0849e05d2df0336fc647196a7a4d7a83f6a60b9edf`, built
as tag `0.1.19-minio-owned-volume-fix`. Its local Docker size is
78,161,300 bytes (74.54 MiB). The image validation stage passed 78 offline
tests; the full local client suite passes 80 tests and the parent
workload-manifest suite passes six. The digest was pulled back from the local
registry and confirmed as `linux/arm64`.

[`six-stem-load-job.yaml`](six-stem-load-job.yaml) contains a restricted root
init container that changes five otherwise world-writable Kubernetes
`emptyDir` mount roots to group-only `0770` before application startup, plus
three non-root containers:
the lifecycle broker (temporary Keycloak/PostgreSQL/MinIO provisioning and
cleanup), the ordinary authenticated user-equivalent API/upload client, and
the evidence observer (PostgreSQL, exact-object MinIO, RabbitMQ metrics, and
KEDA observations). All use the same digest, but each has a distinct command,
environment, mounts, and authority. The Job remains `suspend: true`; no Job,
RBAC object, or Secret is created merely by keeping the manifest in Git.
The init container has no bootstrap Secret or projected Kubernetes token.

[`six-stem-load-observer-rbac.yaml`](six-stem-load-observer-rbac.yaml) grants
only `get` on three named Deployments, three generated HPAs, and three KEDA
ScaledObjects in `clouddsp-app`. The ServiceAccount's automatic token mount is
disabled. The Job explicitly projects a short-lived token and namespace CA into
the observer container only. RabbitMQ's ingress NetworkPolicy also has one
port-15672 exception restricted to Pods labelled `six-stem-load` plus
`integration-test`; those Pods use the existing monitoring-only RabbitMQ
identity and do not receive AMQP credentials.

Before a run, local app-namespace copies of the existing Keycloak administrator,
PostgreSQL administrator, and MinIO root Secrets are needed by the broker.
Their committed examples contain placeholders only. The existing
`clouddsp-keda-rabbitmq-scaler-credentials` Secret is used only by the observer.
No secret value is part of the image or manifest literals.

The final evidence decision requires three completed Jobs, the expected 3/15/3
Demucs/Basic Pitch/ADTOF task inventory, successful durable outbox publication,
no terminal failures or active leases, 42 exact S3 objects verified against
database/object SHA-256 evidence, all nine RabbitMQ main/retry/DLQ queues
drained, each worker stage observed above zero without exceeding its configured
cap, and all three KEDA stages returned to zero. The report logs only safe
counts and peak replicas.

Review limitation: the observer does not delete successful test Jobs or their
MinIO artifacts. The broker revokes only temporary test identities and
observer credentials. A successful run therefore leaves three inaccessible
owner records and their objects for inspection; design a narrowly scoped,
explicit cleanup step before repeated runs or before describing this as a
clean-room benchmark.

On 2026-09-23, after the user authorized a clean baseline, exactly one message
was purged from each of `clouddsp.basic-pitch.requests.dlq` and
`clouddsp.source-intake.dlq`; subsequent queue counts were all zero. The
failed preflight Job was removed, and the temporary Secret copies were kept
for repeated test runs.

The next live run passed the queue/KEDA baseline. The client received a
temporary-user token, passed `GET /auth/me`, received all three `POST /jobs`
contracts, and completed all three presigned MinIO uploads. The Job still
failed because the lifecycle broker did not reach its authenticated-coordinate
handoff/owner-verification log point; it revoked the temporary Keycloak
identity, and the observer exited after seeing broker failure. Code review
found the exact cause: `wait_for_authenticated_load_coordinates` has a
keyword-only `timeout_seconds` parameter, but the broker called it
positionally, raising `TypeError` before owner verification. The broker now
passes that argument by name, and its callback Protocol plus offline tests lock
the keyword-only signature.

The independent application pipeline continued after the test Job failed. It
published all 3 Demucs, 15 Basic Pitch, and 3 ADTOF outbox events; all 21
processing tasks reached `succeeded`; queues drained and the three KEDA-managed
workers returned to zero replicas. The three top-level Jobs remained in
`midi_processing` because the database did not yet have a parent-level
aggregate or Basic Pitch artifact registration path. Migration v007 and the
worker/API updates added the durable completion path. The first live rerun
then exposed two separate issues: the broker targeted the wrong PostgreSQL
database, and v007 treated an asynchronously incomplete downstream task set
as a terminal failure. The load-test broker now targets `clouddsp_job_api`;
migration v008 keeps the parent in `midi_processing` until all expected
downstream tasks are registered, while retaining terminal failure behavior
for actual child failure or invalid task sets.

### Verified full load-test run

On 2026-09-23, the corrected three-job, six-stem run completed successfully
from 18:10:34 to 18:17:07 UTC (6m33s). All three parent Jobs reached
`completed`; the run registered and completed 3 Demucs, 15 Basic Pitch, and 3
ADTOF tasks, with the corresponding 3/15/3 outbox events published. The
observer verified 42 exact MinIO objects (3 sources, 18 stems, and 21
MIDI/tempo outputs) against PostgreSQL evidence and SHA-256 checks. All nine
RabbitMQ main/retry/DLQ queues drained. KEDA observed peak replicas of
Demucs=1, Basic Pitch=3, and ADTOF=1, within their configured caps, then
returned all three workers to zero. The Job log records `terminal result
succeeded`.

The broker also reported successful revocation of the temporary MinIO,
PostgreSQL, and Keycloak identities; a follow-up PostgreSQL role count was
zero. No application Job rows or MinIO objects were deleted. The completed
load-test Job and its logs remain available until the manifest's 900-second
TTL cleanup. The committed manifest remains suspended so another run still
requires an explicit apply/resume action.
