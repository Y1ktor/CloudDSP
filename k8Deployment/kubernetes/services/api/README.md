# Local Job API: contract inventory

## Scope of this task

This document records the browser contract that the local Kubernetes Job API
must eventually satisfy. It is based on the preserved cloud Lambda handler and
the already-deployed local React variant. This work creates the small API source
and image *recipe* described below plus the reviewed, not-yet-applied v001
schema migration. It does not build an image or create a long-running
Kubernetes workload, queue, MinIO event, or Ingress.

The purpose is to avoid an accidental architectural shift: the local service
will preserve the browser-visible behavior while replacing AWS-specific
implementation details deliberately in later, small tasks.

## Current source and image boundary

The local boundary now exists at [`app/`](app/) with a minimal FastAPI ASGI
application. Its source intentionally exposes two infrastructure probes, one
narrow authenticated identity proof, saved-job history/detail reads, and
direct-upload job creation:

| Path | Meaning today | Why it is not a Job API route |
| --- | --- | --- |
| `GET /healthz` | The API process is running and can serve HTTP. | It does not read PostgreSQL, MinIO, RabbitMQ, or Keycloak. |
| `GET /readyz` | The process can connect as `clouddsp-job-api` and execute `SELECT 1` in PostgreSQL. | It does not yet prove the jobs schema, Keycloak, MinIO, RabbitMQ, or processing pipeline is ready. |
| `GET /auth/me` | A Bearer token is signature/issuer/audience/expiry-validated and its immutable `sub` is returned. | It does not return a user profile, create a job, query PostgreSQL, or expose the token/other claims. |
| `GET /jobs` | The verified `sub` is bound to one read-only PostgreSQL query for that user's non-expired compact job summaries. | It does not create a job, calculate quota, sign MinIO URLs, publish RabbitMQ work, or run processing. |
| `POST /jobs` | Validates one direct-upload intent, commits an owner-bound `upload_pending` row, then returns a short-lived constrained MinIO form. | It does not proxy audio bytes, confirm that MinIO received them, publish RabbitMQ work, calculate quota, or run processing. |
| `GET /jobs/{job_id}` | Returns one non-expired current snapshot only when its UUID and the verified `sub` both match. A new upload exposes its durable `upload_pending` state and empty artifact maps. | It does not reveal whether a 404 is missing, expired, or owned by another user; it does not inspect MinIO, issue artifact URLs, alter status, or publish work. |

The digest-pinned [`Dockerfile`](Dockerfile) starts the ASGI application as an
unprivileged user on container port 8080. Its
[`requirements.lock`](requirements.lock) pins FastAPI, Uvicorn, and every
resolved transitive dependency with hashes. The `pydantic-core` wheel hash is
for `linux/arm64` and Python 3.12, matching the current Apple-Silicon k3d
nodes. A future `linux/amd64` image task must add the reviewed x86_64 wheel
hash rather than silently weakening `--require-hashes`.

The active Job API Deployment runs the immutable image containing `GET /jobs`,
`POST /jobs`, and `GET /jobs/{job_id}`. The applied same-origin Ingress routes
`/auth/*` and `/jobs/*` on `clouddsp.localhost:8080` to that Service; no CORS
policy is needed because React and the API share a browser origin. It uses the
API's own PostgreSQL environment values, a bounded `SELECT 1` for `/readyz`,
separate bounded read-only connections for list/detail reads, and one
transaction plus local-only MinIO form signing for `POST /jobs`. The remaining
omissions are intentional task boundaries, not missing runtime configuration.

## PostgreSQL identity

The API will not connect as PostgreSQL's `clouddsp-admin` administrator. The
one-shot [`job-api-database-bootstrap-job.yaml`](job-api-database-bootstrap-job.yaml)
creates the isolated `clouddsp_job_api` database and its only application login,
`clouddsp-job-api`. The future API Pod receives that login through the
namespaced [`job-api-database-credentials.secret.example.yaml`](job-api-database-credentials.secret.example.yaml)
Secret in `clouddsp-app`.

PostgreSQL calls this a **role**: it is the database identity used after the
Pod presents a username and password over its internal ClusterIP connection.
It is not an AWS IAM-style role assumption, and it cannot create databases,
roles, or access Keycloak's separate database. The bootstrap Job temporarily
uses the administrator credential only to create this restricted identity, then
authenticates as the restricted identity to prove the future API can connect.

## MinIO source-upload foundation

The prepared (not yet applied) MinIO manifests establish a separate storage
identity before the API gains any write route:

- [`../minio/minio-job-api-uploads-policy-v002-configmap.yaml`](../minio/minio-job-api-uploads-policy-v002-configmap.yaml)
  holds the reviewed IAM-compatible policy. It limits the Job API to the
  `uploads/*` prefix inside the one durable `clouddsp-uploads` bucket.
- [`../minio/minio-job-api-uploads-bootstrap-job.yaml`](../minio/minio-job-api-uploads-bootstrap-job.yaml)
  is a short-lived administrator-only Job. It creates the bucket if needed,
  explicitly removes anonymous access, creates or rotates the application S3
  user, attaches the narrow policy, and then removes its temporary root alias.
- [`job-api-minio-credentials.secret.example.yaml`](job-api-minio-credentials.secret.example.yaml)
  is the namespaced runtime credential template for the future API Deployment.
  It contains only the application access/secret key, never MinIO's root
  administrator credentials.

The bootstrap Job must use a temporary duplicate of the restricted credentials
in `clouddsp-data`, because it needs to read the MinIO root Secret there.
Kubernetes does not permit cross-namespace Secret reads. After a successful
bootstrap, that temporary Secret is deleted and only the `clouddsp-app`
runtime Secret remains.

The v002 policy permits `GetObject`, `PutObject`, and the multipart-upload
operations only below `clouddsp-uploads/uploads/*`; it does not permit
anonymous access, MinIO administration, object deletion, or access to any
other bucket/prefix. The source `POST /jobs` route creates a server-owned key
in the exact form `uploads/{job-id}/{one-filename}` and issues a presigned
POST only after its owner-bound PostgreSQL row commits. Workers will receive
their own distinct identity in a later task rather than reusing the API's
credentials.

The live API Deployment now receives its restricted MinIO access/secret key
alongside these explicit non-secret settings:

| Setting | Local value | Used by a future task for |
| --- | --- | --- |
| `JOB_API_S3_INTERNAL_ENDPOINT` | `http://clouddsp-minio.clouddsp-data.svc:9000` | Server-originated S3 API calls over private Service DNS. |
| `JOB_API_S3_PUBLIC_ENDPOINT` | `http://minio.localhost:8080` | The browser-reachable host embedded into presigned upload/download URLs. Generating a presigned URL is local signing and does not make a Pod call to this address. |
| `JOB_API_S3_UPLOADS_BUCKET` | `clouddsp-uploads` | The one durable private source-upload bucket. |
| `JOB_API_S3_REGION` | `us-east-1` | S3 Signature V4's required region value for local MinIO. |
| `JOB_API_S3_ADDRESSING_STYLE` | `path` | Ensures URLs use the one Traefik host plus `/clouddsp-uploads/...`, rather than an unconfigured bucket subdomain. |

The `GET /jobs`, `GET /jobs/{job_id}`, and `/readyz` implementations still make no MinIO call, so
this configuration does not turn MinIO into a readiness dependency. The source
`POST /jobs` route reads and validates it only when it needs to locally sign an
upload form; presigning itself makes no MinIO request. The source-level
[`app/object_storage.py`](app/object_storage.py) parser and its focused unit
tests validate the Pod's endpoint/credential contract. The separately focused
[`app/presigned_upload.py`](app/presigned_upload.py) helper now uses pinned
`boto3`/`botocore` to calculate a constrained Signature V4 POST **locally**;
it does not call MinIO or open a network connection. For a canonical job UUID,
it permits only `uploads/{job-id}/{one-filename}`, one exact content type and
stem-mode metadata, a one-byte-to-256-MiB range (or a lower caller-supplied
limit), and a maximum fifteen-minute expiry. Its tests decode the local policy
to prove those MinIO-enforced constraints and patch the HTTP transport to prove
that presigning performs no I/O. The separately pure
[`app/direct_upload_contract.py`](app/direct_upload_contract.py) now defines
and tests the direct-upload request/201-response shapes: it accepts only a
safe supported-audio filename/type/size/stem-mode pair and makes server-owned
fields such as owner, status, bucket, object key, and MinIO form fields
unrepresentable as browser input. It has no route or I/O. The separately
unit-tested `create_direct_upload_pending_job` database helper creates the
owner-bound `upload_pending` row, a server-generated UUID/object key, and
fourteen-day expiry in one PostgreSQL transaction. It returns an internal
persistence record rather than a browser response and makes no MinIO request.
The source `POST /jobs` route composes the validator, helper, and presigned
POST helper in that order: validate configuration, persist the row, then
locally sign and return only the reviewed `201` browser contract.
The prepared
[`../minio/minio-s3-ingress.yaml`](../minio/minio-s3-ingress.yaml) publishes
only the S3 API at `http://minio.localhost:8080`; it does not expose the MinIO
console and does not bypass S3 authentication. The MinIO Community server does
not implement per-bucket S3 CORS configuration, so
[`../minio/minio-statefulset.yaml`](../minio/minio-statefulset.yaml) sets its
supported server-level `MINIO_API_CORS_ALLOW_ORIGIN` setting to the single
React origin. That origin rule controls only browser access; private-bucket IAM
and presigned-request signatures still authorize every S3 operation. The disposable
[`../../tests/minio-smoke/minio-job-api-restricted-access-smoke-job.yaml`](../../tests/minio-smoke/minio-job-api-restricted-access-smoke-job.yaml)
uses the runtime Secret from `clouddsp-app` to verify the intended allowed and
denied S3 operations. The next task is a source-image build and a deliberately
separate authenticated integration smoke test; it will verify the composed
route through the actual Kubernetes Service without widening this request into
an upload-intake or worker task.

## Initial jobs schema (migration v001)

[`job-api-schema-migration-v001-configmap.yaml`](job-api-schema-migration-v001-configmap.yaml)
holds non-secret SQL as an immutable ConfigMap. The companion
[`job-api-schema-migration-v001-job.yaml`](job-api-schema-migration-v001-job.yaml)
mounts that file and executes it through the PostgreSQL ClusterIP Service using
only the API credential in `clouddsp-app`.

The v001 migration has been applied to the local PostgreSQL instance. It
created a `schema_migrations` ledger plus the `jobs` table. That table retains
the cloud-compatible owner, source object key, stem mode, status,
optimistic-concurrency revision, private artifact metadata, safe error text,
timestamps, and retention expiry. It deliberately omits source URLs and
presigned URLs because their query strings expire and can be sensitive.

Daily quotas, task leases, and normalized worker-stage records remain separate
migrations. This keeps the first schema change small and ensures the first API
route can create/retrieve a durable job before worker coordination is
introduced. The applied v002 migration is the deliberately narrow exception:
it adds only the durable `outbox_events` table, its idempotency constraint, and
dispatcher lease fields. The applied separate
[`upload-intake outbox permission bootstrap Job`](../upload-intake/upload-intake-outbox-permissions-bootstrap-job.yaml)
grants the intake role only the columns needed to insert its initial
`pending` Demucs event. It still cannot publish RabbitMQ work, inspect or
update outbox rows, or run a dispatcher; those remain separate small tasks.

The applied [`v004 downstream-outbox migration`](job-api-schema-migration-v004-downstream-outbox-configmap.yaml)
retains v002's idempotency/publication-state fields while allowing only
per-stem `basic-pitch.requested` and `adtof.requested` records after Demucs
completes. The applied
[`v005 Basic Pitch processing-task migration`](job-api-schema-migration-v005-basic-pitch-processing-tasks-configmap.yaml)
is the separate task-table evolution. It replaces only v003's Demucs-only stage,
stem, and input-key checks with one compound constraint: a Demucs task still
uses its job-wide `uploads/{job-id}/...` source, while Basic Pitch can use only
one allowed non-drum `stems/{job-id}/{stem}.wav` task coordinate. It retains
the existing task primary key, request-event uniqueness, `(job_id, stage,
stem_name)` idempotency key, retry/lease indexes, status/lease checks, and
timestamps. Its completed companion Job recorded
`v005_basic_pitch_processing_tasks` in the authoritative migration ledger. A
future Basic Pitch database role or worker image remains separate work; neither
is implied by this documentation.

## PostgreSQL readiness boundary

[`app/database.py`](app/database.py) centralizes the connection settings read
from the API's namespaced Secret. `GET /healthz` remains dependency-free, while
`GET /readyz` opens a bounded Psycopg connection through
`clouddsp-postgresql.clouddsp-data.svc` and runs `SELECT 1`. Source-level
`GET /jobs` and `GET /jobs/{job_id}` open separate short-lived connections
configured with `default_transaction_read_only=on`. The list binds only the
verified owner `sub` and orders retained rows by `updated_at DESC`; the detail
lookup additionally binds one canonical UUID and returns 404 for missing,
expired, or foreign records alike. A failed or missing database connection
returns only a fixed `503` category, never a password, database error, hostname,
username, or connection string.

The driver is the `psycopg[binary]` installation form. Its binary package
bundles the required PostgreSQL client library, avoiding a compiler and system
`libpq` dependency in the slim runtime image. Psycopg 3.3.5 is newer, but it
does not yet have a matching binary package; the locked 3.2.13 pair is the
newest available binary-compatible choice for the current Linux/ARM64 image.

Keycloak RS256 validation uses `PyJWT[crypto]`. Its Cryptography backend is
pinned to 46.0.7 rather than the newer 50.0.1 because the latter's Linux/ARM64
wheel raised `Illegal instruction` during a real RSA operation in this Docker
Desktop environment. The selected wheel was verified with the same operation;
revisiting the pin belongs to a deliberate Docker Desktop compatibility task.

The local [`../../scripts/verify-job-api-local-image.sh`](../../scripts/verify-job-api-local-image.sh)
smoke test runs the immutable image without a network or database Secret. It
therefore proves `healthz` is dependency-free and `readyz` fails closed with a
safe configuration error. A future Kubernetes Deployment task will instead
inject the Secret and prove `readyz` reaches PostgreSQL through Service DNS.

## First API Deployment

[`job-api-deployment.yaml`](job-api-deployment.yaml) runs one non-root,
read-only API Pod from `images.job-api.immutableReference`. It injects only the
three API database Secret values, routes to PostgreSQL through its fully
qualified ClusterIP Service DNS name, and separates a dependency-free liveness
probe (`/healthz`) from database-aware readiness (`/readyz`). Its currently
deployed image contains the protected `/auth/me` and `GET /jobs` routes. The
new image lock contains the built `POST /jobs` image, but that route will not
run in the Pod until a later explicit Deployment image update and rollout.

## Internal API Service

[`job-api-service.yaml`](job-api-service.yaml) selects ready Job API Pods and
offers them as
`clouddsp-job-api.clouddsp-app.svc:80`. The Service maps port 80 to the
container port named `http` (8080). It creates no Mac port itself. The applied
[`job-api-ingress.yaml`](job-api-ingress.yaml) routes only `/auth/*` and
`/jobs/*` from the browser to this stable Service rather than to a changing Pod
IP.

## Answer: when does React call the Job API?

The API is **not** called when a user merely clicks **Browse** and selects a
file. That action only places a `File` object in React memory. It is called in
these situations after the user has a valid Keycloak session:

| User or browser event | React request | Why the API is needed |
| --- | --- | --- |
| Successful sign-in / restored session | `GET /jobs` | Populate the account's durable job history and quota display. This also runs when the history panel is refreshed. |
| Click **Upload & Split** with a selected file | `POST /jobs` | Create one durable job and obtain a short-lived presigned upload contract. |
| Browser submits the returned HTML form to object storage | **No Job API request** | The file goes directly to S3 today, and will go directly to MinIO later. The browser must not relay up to 256 MiB through the API Pod. |
| Active direct-upload or linked-source job | `GET /jobs/{job_id}` | Fetch durable status and fresh artifact URLs immediately and then during five-second polling; WebSocket notifications are only an optional hint. |
| Click **Extract & Split** after pasting a media URL | `POST /jobs/link` | Create a durable linked-source job and request yt-dlp ingestion. |
| Click delete for a terminal history item | `DELETE /jobs/{job_id}` | Delete only the caller's completed/failed job and its stored input/output objects. |

Therefore, the Job API is a small authenticated **control plane** for durable
job state and short-lived object-storage URLs. It is not the data path for
audio bytes and must not invoke Demucs synchronously.

For a direct upload, the future processing pipeline begins only after the
browser has successfully uploaded the file to MinIO. A later upload-intake
component will observe or reconcile that durable object and publish the
Demucs-stage message to RabbitMQ. For a linked source, `POST /jobs/link` will
instead publish/request the yt-dlp ingestion stage. Neither worker is a child
process of the API Pod.

## Existing browser contract

Every request uses `Authorization: Bearer <Keycloak access token>`. The local
API must derive the owner from the verified token's immutable `sub` claim; it
must never accept a browser-supplied user ID.

| Method and path | Request body | Successful response contract | Local implementation responsibility |
| --- | --- | --- | --- |
| `GET /jobs` | None | `jobs` array with `job_id`, source filename, status, stem mode, tempo, timestamps, expiry; may include `quota` | Query PostgreSQL by verified owner, newest first. |
| `POST /jobs` | `filename`, `content_type`, `size_bytes`, `stem_mode` | `201` with `job_id`, `status: "upload_pending"`, `revision`, `expires_at`, `upload_url`, `upload_fields`, and maximum size; may include `quota` | Validate request, transactionally create the PostgreSQL job/quota record, then issue a constrained MinIO presigned POST for `uploads/{job_id}/...`. |
| `POST /jobs/link` | `source_url`, `stem_mode` | `202` with `job_id`, `status: "source_ingestion"`, `revision`, `expires_at`; may include `quota` | Validate the reviewed source URL policy, create the job/quota record, then publish/request the yt-dlp stage asynchronously. |
| `GET /jobs/{job_id}` | None | Owned current snapshot. The initial `upload_pending` version includes durable state, empty `stems`/`midi` maps, and no storage coordinates or URLs. | Bind canonical UUID + verified owner + retention in a read-only PostgreSQL lookup. A later artifact task will add fresh MinIO URLs only for ready, owned objects. |
| `DELETE /jobs/{job_id}` | None | `200` with `job_id` and `deleted_objects` | Allow only the owner to delete terminal jobs; remove MinIO input/stem/MIDI objects and the PostgreSQL record. |

The valid initial stem modes are `2-stems`, `4-stems`, and `6-stems`. The
cloud contract currently accepts WAV, MP3, FLAC, M4A, AAC, OGG, Opus, AIFF,
and WebM, with a 256 MiB browser/API validation limit. Retaining these values
at first avoids a silent UI/API mismatch; changing them is a separate product
decision.

## Local replacements, not behavior changes

| Cloud implementation | Local Kubernetes successor | Contract that remains unchanged |
| --- | --- | --- |
| API Gateway + Lambda | HTTP API container behind ClusterIP and Traefik | The five browser routes and JSON responses. |
| Cognito/API Gateway JWT authorizer | In-process Keycloak JWT validation in the API | Verified access-token `sub` is the job owner. |
| DynamoDB jobs and daily quota table | PostgreSQL job and quota tables | Durable ownership, status, revision, retention, and quota behavior. |
| S3 presigned POST/GET | MinIO S3-compatible presigned POST/GET | Browser uploads/downloads directly to private object storage. |
| Lambda async invocation / S3 event | RabbitMQ stage messages plus upload-intake reconciliation | The API returns quickly; workers process asynchronously. |

## Keycloak token-validation boundary

The API source contains a reusable FastAPI dependency that validates a
browser's Keycloak access token before a user-owned route executes. Its first
deployed consumer is `GET /auth/me`; it returns just the validated immutable
subject, with no database lookup or side effect. The disposable Keycloak smoke
Job proved that route internally, and the applied local-only Ingress now makes
it reachable on the same browser origin while still requiring a Bearer token.
The same dependency protects `GET /jobs`; its companion authenticated-read
smoke assertion verifies that a new temporary user's history is exactly empty.

| Deployment setting | Local value | Why it is separate |
| --- | --- | --- |
| `JOB_API_OIDC_ISSUER` | `http://keycloak.localhost:8080/realms/clouddsp` | This exact public address must equal a signed token's `iss` claim. |
| `JOB_API_OIDC_AUDIENCE` | `clouddsp-job-api` | This is the resource server that may accept the token; it is intentionally distinct from the React public client `clouddsp-react`. |
| `JOB_API_OIDC_JWKS_URL` | `http://clouddsp-keycloak.clouddsp-data.svc:8080/realms/clouddsp/protocol/openid-connect/certs` | The Pod obtains public signing keys on private cluster DNS, without routing its verification traffic through Traefik. |

The validator accepts only RS256 signatures, requires `exp`, `iat`, and `sub`,
checks the exact issuer and resource audience, and caches the public JWKS for
five minutes. An unknown signing-key ID triggers one immediate refresh to
handle ordinary Keycloak key rotation. Invalid/missing tokens produce a generic
`401`; unavailable Keycloak signing metadata produces a generic retryable
`503`. No token or claims are logged.

Keycloak now has the `clouddsp-job-api` resource client and an audience mapper
on the existing `clouddsp-react` client. A newly issued browser access token
therefore names this API in `aud`, rather than merely naming another realm
client. Sign out/in after that Keycloak configuration to obtain such a token.

## Deliberately deferred decisions

- PostgreSQL daily-quota, task-lease, transactional-outbox, and worker-stage
  schemas, plus the exact quota-retention implementation.
- PostgreSQL connection pooling and every job-table operation beyond the
  deliberate `GET /jobs` history query, `GET /jobs/{job_id}` detail query, and
  `POST /jobs` upload-intent insert.
- Browser upload-completion confirmation, object validation, retention
  reconciliation, and the durable transition out of `upload_pending`.
- Claims/role policy beyond immutable owner `sub`, and a future Keycloak
  availability signal for operational dashboards.
- RabbitMQ message schema, retry policy, and the upload-intake reconciler.
- Browser-side submission of the returned presigned form, frontend job-state
  integration, WebSocket replacement, and autoscaling.

Each is a separate task because it changes a different Kubernetes or security
boundary. The immediate next task is to build the changed source image and run
a disposable authenticated integration smoke Job. It must verify the route
without implementing upload intake or worker processing.

## Evidence reviewed

- Local React request sites: `../frontend/app/src/App.jsx` and its
  `ControlBar` component.
- Cloud route implementation: `../../../../cloudDeployment/src/DSP/src/Cloud/job_api.py`.
- Cloud API Gateway/Lambda wiring: `../../../../cloudDeployment/IaC/api.yaml`.
