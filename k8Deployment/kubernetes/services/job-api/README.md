# Local Job API

The local Job API is a FastAPI service behind the `clouddsp-job-api` Helm
release in `clouddsp-app`. It authenticates Keycloak access tokens, owns
browser-facing direct-upload intents, and reads durable job state from
PostgreSQL. The browser transfers audio and artifacts directly to MinIO.

The [root deployment](../../scripts/README.md) installs its credentials,
database, migrations, IAM policy, Keycloak clients, and chart in dependency
order. Earlier incremental contract and migration notes are preserved in
[implementation history](../../docs/history/service-api-implementation-notes.md).

## Implemented HTTP contract

The frontend and API share `http://clouddsp.localhost:8080`. Traefik sends
`/auth`, `/jobs`, and `/score-jobs` to the API Service; `/` belongs to the frontend.
Every user route requires `Authorization: Bearer <Keycloak access token>`.

| Method and path | Current behavior |
| --- | --- |
| `GET /healthz` | Dependency-free process liveness. |
| `GET /readyz` | Bounded PostgreSQL connection and `SELECT 1`; does not prove schema, storage, broker, or identity-provider health. |
| `GET /auth/me` | Return the validated immutable token subject without a database lookup. |
| `GET /jobs` | Return the caller's non-expired compact job summaries, newest first. Does not return or enforce a daily quota. |
| `POST /jobs` | Validate a direct-upload intent, commit an owner-bound `upload_pending` row, then return a constrained MinIO presigned POST contract with HTTP 201. |
| `GET /jobs/{job_id}` | Return the caller's non-expired durable snapshot and freshly signed URLs for verified source/stem/MIDI/tempo artifacts. Missing, expired, and foreign jobs share HTTP 404. |
| `GET /score-jobs` | Return only the caller's retained sheet-to-MIDI summaries, newest first, without storage coordinates or signed URLs. |
| `POST /score-jobs` | Create a separate owner-bound sheet upload and return a constrained MinIO POST contract. |
| `GET /score-jobs/{job_id}` | Return owner-bound score state, a signed source preview after upload verification, and signed MIDI/MusicXML after completion. |

`POST /jobs` accepts `filename`, optional `content_type`, `size_bytes`, and
`stem_mode`. It rejects extra fields, path/traversal filenames, unsupported
formats, and sizes outside 1 byte–256 MiB. Stem modes are `2-stems`, `4-stems`,
and `6-stems`; supported formats are WAV, MP3, FLAC, M4A, AAC, OGG, Opus,
AIFF, and WebM. The server chooses the job UUID, owner, object coordinates,
revision, status, and fourteen-day expiry.

The response contains `job_id`, `status`, `revision`, `expires_at`, `upload_url`,
`upload_fields`, and `max_source_bytes`. The browser copies the form fields
unchanged, appends the file, and POSTs to MinIO. The signature restricts the
object to `uploads/{job_id}/{filename}`, content type, job/stem metadata,
size range, and a maximum fifteen-minute validity. Presigning performs local
cryptography; the API does not proxy bytes or synchronously invoke a worker.

After upload, [upload-intake](../upload-intake/README.md) validates the durable
object and records the first outbox event. The
[dispatchers](../dispatcher/README.md) publish work. The browser polls job
detail to recover current progress and artifact URLs. Selecting a file alone
makes no job-creation request.

### Sheet upload contract

`POST /score-jobs` requires the same Keycloak bearer token and JSON
`{"direction":"score_to_midi","filename":"scan.pdf","content_type":"application/pdf","size_bytes":12345}`.
`content_type` may be omitted when the browser cannot report it. Only PDF,
PNG, JPG, and JPEG are accepted, with a declared size of 1 byte–25 MiB.
Mismatched MIME types, paths, extra fields, and the future `midi_to_score`
direction return HTTP 400. The filename is retained for display only: the API
chooses `score-inputs/{job_id}/source.<ext>` in the existing private
`clouddsp-uploads` bucket. The response contains `job_id`, `direction`,
`status=upload_pending`, `revision`, `expires_at`, `upload_url`,
`upload_fields`, and `max_source_bytes`. Copy every form field unchanged,
append the file as `file`, and POST it to `upload_url` before the five-minute
signature expires. The signature binds the exact key, canonical MIME type,
`job-id` and `score-direction` metadata, and 1 byte–25 MiB length range.

Migration v010 adds an independent `score_jobs` table and leaves audio `jobs`
unchanged. The table stores durable owner, source coordinates, revision,
retention, and future private result coordinates; it never stores signed URLs.
The API route creates an upload intent. The browser then sends the signed form
directly to MinIO. A separate `score-inputs/*` PutObject policy extends the
Job API's existing MinIO identity without granting bucket-wide access. A
score-prefix ObjectCreated rule publishes to `clouddsp.score-intake` through
the `score.upload.created` RabbitMQ route. The score OMR consumer validates the
event against the durable row and live object, then advances the leased job
through processing to private MIDI and MusicXML results. Authenticated
`GET /score-jobs/{job_id}` returns only the verified owner's current state and
signs fresh result URLs after completion. Verified sources receive a `source_url`
plus `source_content_type` and `source_size_bytes` so History can restore the
PDF/image without uploading it again. Pending uploads have no source URL.
The upload response and MinIO acknowledgement
do not represent a completed transcription. Score artifact retention cleanup
remains separate implementation work.

### Score history and rollout

`GET /score-jobs` returns `{"jobs":[...]}` with `job_id`,
`direction=score_to_midi`, `source_filename`, `status`, `created_at`,
`updated_at`, and `expires_at`. The existing owner/update-time index supports
the read. Both history routes require the validated Keycloak subject, filter
expired records using PostgreSQL time, and return `Cache-Control: no-store`.
The score list never merges audio jobs or future reverse-conversion jobs.

The account History action follows the active studio: `/score-to-midi` reads
`/score-jobs`; Studio reads `/jobs`. Opening a score only GETs its snapshot
and private artifacts, resumes polling for unfinished work, and restores the
MIDI editor for completed work. It never creates a job or publishes work.
The selected score remains in React memory across tabs and is cleared when
the account changes. No history, job IDs, or signed URLs are persisted in
browser storage. Score deletion is not part of this endpoint/UI.

Before rolling out the updated API/frontend images, apply the versioned
read-only source-preview IAM stage for the existing Job API identity:

```sh
python3 k8Deployment/kubernetes/scripts/stages/credentials/score-omr-bootstrap.py --history-only
```

Run the command from the repository root. It adds only `s3:GetObject` on
`clouddsp-uploads/score-inputs/*`, with no listing, write, delete, or admin
grant to the API. The detail signer checks the exact job UUID and one of the
four server-chosen `source.<ext>` keys before signing. Fresh score bootstrap
includes the same stage. No schema migration or queue change is needed.
The rollout locks API `0.0.12-score-history` and frontend
`0.6.6-score-history` to immutable ARM64 digests. Flux applies these references;
the v004 score smoke verifies authenticated history and signed restoration.
The 2026-10-08 local rollout passed both release checks and live v004 smoke;
see the [verification record](../../tests/score-omr-smoke/README.md#score-history-rollout-verified--2026-10-08).

## Ownership and authentication

The API derives ownership solely from the verified token's `sub`; no request
may supply an owner ID. List/detail queries bind that subject, retention time,
and the job UUID where appropriate. Artifact coordinates must match the exact
job's known output layout before signing; raw bucket/key fields are removed
from browser JSON. A newly created, unconfirmed upload has no original URL.

| Setting | Reviewed local value |
| --- | --- |
| `JOB_API_OIDC_ISSUER` | `http://keycloak.localhost:8080/realms/clouddsp` |
| `JOB_API_OIDC_AUDIENCE` | `clouddsp-job-api` |
| `JOB_API_OIDC_JWKS_URL` | `http://clouddsp-keycloak.clouddsp-data.svc:8080/realms/clouddsp/protocol/openid-connect/certs` |

The validator accepts RS256, verifies issuer/audience/expiry and required
claims, caches public keys, and refreshes once for an unknown key ID. Invalid
or missing tokens return generic HTTP 401; unavailable signing metadata returns
HTTP 503. The public React client uses PKCE and has no client secret. Its
Keycloak audience mapper supplies the API audience. Tokens, credentials,
private object keys, and signed URLs must not appear in diagnostic logs.

## Database and object-storage boundary

`clouddsp_job_api` is separate from Keycloak's database. The API connects as
`clouddsp-job-api`; workers, intake, dispatchers, and scalers have their own
restricted identities. Schema migrations are immutable versioned ConfigMaps
and one-shot Jobs, checked by a durable migration ledger. Fresh bootstrap runs
v001–v006 before provisioning Basic Pitch/ADTOF roles, then the remaining
migrations through v010. See the
[database reference](../../docs/reference/database-and-schema.md).

PostgreSQL owns job state, revisions, source/artifact keys, processing leases,
and outbox events. Completion aggregation waits for all mode-required tasks
and artifacts; MIDI updates resolve the top-level tempo. Workers cannot mark
a parent completed independently of those invariants. Persisted expiry filters
API reads; expiry does not physically remove MinIO objects.

The API MinIO policy permits constrained source uploads and read access to
job artifacts in the private `clouddsp-uploads` bucket. It grants no object
delete or MinIO administration. Runtime keys are referenced from a namespaced
Secret, outside Helm values/release history. The internal endpoint is
`http://clouddsp-minio.clouddsp-data.svc:9000`; presigned browser URLs use
`http://minio.localhost:8080` with path-style S3 addressing. IAM and signed
requests authorize access; CORS alone does not make objects public.

## Deployment and verification

For a complete fresh deployment, use `deploy-local.sh bootstrap-platform`.
For a current release's read-only checks, run from the repository root:

```bash
./k8Deployment/kubernetes/scripts/releases/job-api-release.rb verify
./k8Deployment/kubernetes/scripts/deploy-local.sh verify
```

The [chart guide](../../helm/job-api/README.md) documents the separate guarded
`install` and historical `plan`/`adopt` paths. Component `plan` expects the
pre-adoption kubectl ownership state; use `verify` for a Helm-owned release. Use reviewed Helm delivery for workload
changes; do not reapply the retained Deployment, Service, or Ingress baselines.
Use the [database stage](../../scripts/stages/database/job-api-postgresql-stage.rb) for migration
or bootstrap checks rather than applying migration Jobs out of order.

The API image and base images are digest-pinned. The hash-locked Python recipe
currently targets `linux/arm64`; use the [image lock](../../images.lock.yaml)
for the reviewed reference rather than copying a historical tag from notes.

Release verification checks readiness, stored/chart/live specs, image identity,
and unauthenticated browser routes returning HTTP 401. That does not prove an
authenticated upload. The
[authenticated-read Job](../../tests/keycloak-smoke/keycloak-job-api-auth-me-smoke-job.yaml)
uses a disposable Keycloak user/client and removes them. The
[source-intake smoke](../../tests/source-intake-smoke/README.md) exercises the
API → signed upload → intake/outbox path and duplicate notification. Follow
its queue/pause/cleanup prerequisites. Worker and six-stem load smokes cover
later processing boundaries separately.

## Current local limitations

The local service does not implement `POST /jobs/link`, `DELETE /jobs/{job_id}`,
a UTC quota table/enforcement/response, scheduled retention cleanup, or a
realtime notification endpoint. The shared frontend has cloud features whose
backend routes are not yet available locally. A fourteen-day record expiry
and owner-filtered reads are implemented; they are not a deletion mechanism.
The local pipeline currently supports direct uploads and polling.

## Source navigation

- [Routes and response mapping](app/main.py)
- [Token validation](app/authentication.py)
- [Owner-bound PostgreSQL operations](app/database.py)
- [Direct-upload request contract](app/direct_upload_contract.py)
- [Upload signing](app/presigned_upload.py) and [download signing](app/presigned_download.py)
- [Container recipe](Dockerfile) and [hash-locked dependencies](requirements.lock)

## MIDI-to-sheet jobs

The independent `/sheet-jobs` POST/GET and `/sheet-jobs/{uuid}` GET routes use
Keycloak owner checks and the v012 `midi_sheet_jobs` table. Creation returns an
exact-key, size-constrained MIDI POST form. History excludes expired/foreign
rows. Detail returns fresh private source/PDF/MusicXML signatures, with no
stored signed URLs or browser-selected storage paths. See the
[MIDI renderer contract](../midi-sheet/README.md) for limits and delivery.
