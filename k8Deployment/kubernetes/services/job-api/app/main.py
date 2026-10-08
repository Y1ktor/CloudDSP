"""Minimal HTTP boundary for CloudDSP's local Job API.

The cloud implementation is a Python 3.12 Lambda handler. This ASGI process
does not copy that handler yet because its DynamoDB, S3, Lambda, and API Gateway
assumptions must be replaced deliberately by PostgreSQL, MinIO, RabbitMQ, and
Keycloak-aware components. `GET /auth/me` proves that a Keycloak access token
reaches this process and yields a verified owner identity. `GET /jobs` lists
only that verified owner's non-expired database rows, while
`GET /jobs/{job_id}` returns one current owner-bound snapshot. `POST /jobs`
creates an upload-pending PostgreSQL row and returns a short-lived, constrained
MinIO form; the browser uploads audio directly to MinIO, not through this API
Pod. `POST /score-jobs` creates a separate owner-bound sheet upload intent.
That score route does not publish RabbitMQ work or start an OMR worker.
`GET /score-jobs/{job_id}` returns an owner-bound score snapshot and fresh
private result URLs after the homr worker completes. `readyz` proves only that the API can reach its restricted
PostgreSQL database.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.authentication import AuthenticatedPrincipal, require_authenticated_principal
from app.database import (
    DatabaseConfigurationError,
    DatabaseUnavailable,
    create_direct_upload_pending_job,
    create_score_upload_pending_job,
    get_retained_score_job_for_owner,
    get_retained_job_snapshot_for_owner,
    list_retained_jobs_for_owner,
    verify_database_connection,
)
from app.direct_upload_contract import DirectUploadJobCreatedResponse, DirectUploadJobRequest
from app.job_error_messages import owner_visible_job_error
from app.object_storage import ObjectStorageConfigurationError, ObjectStorageSettings
from app.presigned_download import (
    PresignedDownloadContractError,
    PresignedDownloadSigningError,
    create_presigned_download_url,
)
from app.presigned_upload import (
    PresignedUploadContractError,
    PresignedUploadSigningError,
    create_constrained_source_upload_post,
    create_constrained_score_upload_post,
)
from app.score_upload_contract import ScoreUploadCreatedResponse, ScoreUploadRequest

SERVICE_NAME = "clouddsp-job-api"
# This value appears only in the non-sensitive health/readiness responses. It
# must track the immutable local image milestone so `kubectl exec`/port-forward
# diagnostics can confirm which API code Kubernetes actually rolled out.
SERVICE_VERSION = "0.0.11-score-omr"


# Disable FastAPI's generated schema and interactive documentation until the
# browser contract in ../README.md is implemented and protected. Publishing
# half-finished routes or an unstable schema would make this preliminary
# boundary appear like a supported public API.
app = FastAPI(
    title="CloudDSP local Job API",
    version=SERVICE_VERSION,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.exception_handler(RequestValidationError)
async def direct_upload_request_validation_error(
    request: Request,
    error: RequestValidationError,
):
    """Give both upload routes a small, stable malformed-input response.

    FastAPI normally returns a detailed 422 response for request-model errors.
    The preserved cloud endpoint instead exposes one small 400 contract, so a
    browser cannot depend on framework-specific validation details. Other
    routes retain FastAPI's standard validation behaviour for future focused
    work. The original error is passed to FastAPI only on those other routes.
    """

    if request.method == "POST" and request.url.path in {"/jobs", "/score-jobs"}:
        return JSONResponse(
            status_code=400,
            content={"error": (
                "Invalid score-upload request." if request.url.path == "/score-jobs"
                else "Invalid direct-upload request."
            )},
            headers={"cache-control": "no-store"},
        )
    return await request_validation_exception_handler(request, error)


def health_response(*, readiness: bool) -> JSONResponse:
    """Return a successful probe response without serializing private details."""

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "service": SERVICE_NAME,
            "version": SERVICE_VERSION,
            "readiness": readiness,
        },
        headers={"cache-control": "no-store"},
    )


def readiness_failure_response(*, reason: str) -> JSONResponse:
    """Return a non-sensitive readiness failure while keeping liveness healthy.

    `reason` is deliberately a short, fixed category. Driver errors could
    contain hostnames, usernames, or implementation details, so they are never
    exposed to the browser or a Kubernetes probe response.
    """

    return JSONResponse(
        status_code=503,
        content={
            "status": "not_ready",
            "service": SERVICE_NAME,
            "version": SERVICE_VERSION,
            "readiness": False,
            "reason": reason,
        },
        headers={"cache-control": "no-store"},
    )


def authenticated_identity_response(principal: AuthenticatedPrincipal) -> JSONResponse:
    """Serialize the one trusted identity value safe for this diagnostic route.

    This response intentionally omits the original JWT, its email/name/profile
    claims, roles, Keycloak URLs, and database data. The caller already owns
    its own immutable `sub`; returning it here proves the *API* independently
    validated the signature, issuer, audience, and expiry before a future
    `/jobs` route uses the same value for `jobs.owner_sub`.
    """

    return JSONResponse(
        status_code=200,
        content={"subject": principal.subject},
        # Identity responses must not be stored by a shared browser/proxy cache.
        headers={"cache-control": "no-store"},
    )


def saved_jobs_response(rows: list[dict[str, object]]) -> JSONResponse:
    """Return compact retained job history without exposing database internals.

    ``list_retained_jobs_for_owner`` already selects only the reviewed public
    fields. ``jsonable_encoder`` then serializes PostgreSQL timestamps and
    JSONB tempo values into browser JSON without returning a driver row object,
    an input bucket/key, private artifact metadata, or an error message.
    """

    return JSONResponse(
        status_code=200,
        content=jsonable_encoder({"jobs": rows}),
        # Job history is user-specific and should never be stored by a shared
        # browser/proxy cache between two signed-in users.
        headers={"cache-control": "no-store"},
    )


def job_history_unavailable_response() -> JSONResponse:
    """Return a safe retryable database failure for the browser job library."""

    return JSONResponse(
        status_code=503,
        # The existing React client already reads `error` from non-2xx JSON.
        # Keep this wording generic: database host/role/SQL errors stay only in
        # private server-side exception chains, never in browser responses.
        content={"error": "Job history is temporarily unavailable."},
        headers={"cache-control": "no-store"},
    )


def job_snapshot_response(row: dict[str, object]) -> JSONResponse:
    """Render a browser-safe snapshot and fresh URLs from owner-checked keys.

    PostgreSQL returned this row only after matching the verified Keycloak
    subject and retention window. Private bucket/key aliases are removed from
    the public JSON. For each present artifact, the signer independently checks
    its deterministic Job path and emits a fresh expiring URL; no URL is
    persisted and the API never proxies file bytes.
    """

    public_row = dict(row)
    # Store stable worker codes in PostgreSQL, but render only reviewed text
    # after the immutable Keycloak owner has been checked by the detail query.
    public_row["error"] = owner_visible_job_error(
        status=public_row.get("status"), error=public_row.get("error"),
    )
    input_bucket = public_row.pop("_storage_input_bucket", None)
    input_object_key = public_row.pop("_storage_input_object_key", None)
    source_uploaded = public_row.get("source_uploaded") is True
    job_id = public_row.get("job_id")
    needs_signer = source_uploaded or any(
        isinstance(public_row.get(collection), dict)
        and any(
            isinstance(artifact, dict)
            and (artifact.get("s3_key") is not None or artifact.get("bpm_key") is not None)
            for artifact in public_row[collection].values()
        )
        for collection in ("stems", "midi")
    )

    if needs_signer:
        if not isinstance(job_id, str):
            raise PresignedDownloadContractError("snapshot job identifier was invalid.")
        settings = ObjectStorageSettings.from_environment()

        # Do not sign a predicted source key before upload-intake has verified
        # MinIO. A yt-dlp failure before upload likewise has no original file.
        if source_uploaded:
            if not isinstance(input_bucket, str) or not isinstance(input_object_key, str):
                raise PresignedDownloadContractError("verified source coordinates were incomplete.")
            if input_bucket != settings.uploads_bucket:
                raise PresignedDownloadContractError("source bucket was not the reviewed private bucket.")
            public_row["original_url"] = create_presigned_download_url(
                settings,
                job_id=job_id,
                object_key=input_object_key,
                kind="source",
            )

        for collection_name in ("stems", "midi"):
            artifact_map = public_row.get(collection_name) or {}
            if not isinstance(artifact_map, dict):
                raise PresignedDownloadContractError("artifact map was not an object.")
            rendered_artifacts: dict[str, dict[str, object]] = {}
            for stem_name, artifact_value in artifact_map.items():
                if not isinstance(artifact_value, dict):
                    raise PresignedDownloadContractError("artifact record was not an object.")
                rendered = dict(artifact_value)
                object_key = rendered.pop("s3_key", None)
                tempo_key = rendered.pop("bpm_key", None)
                artifact_bucket = rendered.pop("bucket", None)
                if artifact_bucket is not None and artifact_bucket != settings.uploads_bucket:
                    raise PresignedDownloadContractError("artifact bucket was not the reviewed private bucket.")

                if object_key is not None:
                    if not isinstance(object_key, str):
                        raise PresignedDownloadContractError("artifact key was invalid.")
                    kind = "stem" if collection_name == "stems" else "midi"
                    rendered["url"] = create_presigned_download_url(
                        settings,
                        job_id=job_id,
                        object_key=object_key,
                        kind=kind,
                        stem_name=stem_name,
                    )
                if tempo_key is not None:
                    if (
                        not isinstance(tempo_key, str)
                        or stem_name != "drums"
                        or collection_name != "midi"
                    ):
                        raise PresignedDownloadContractError("tempo artifact key was invalid.")
                    rendered["bpm_url"] = create_presigned_download_url(
                        settings,
                        job_id=job_id,
                        object_key=tempo_key,
                        kind="tempo",
                    )
                rendered_artifacts[stem_name] = rendered
            public_row[collection_name] = rendered_artifacts

    return JSONResponse(
        status_code=200,
        content=jsonable_encoder(public_row),
        headers={"cache-control": "no-store"},
    )


def score_job_snapshot_response(row: dict[str, object]) -> JSONResponse:
    """Return safe score state and fresh result URLs after owner verification."""

    public = dict(row)
    bucket = public.pop("_result_bucket", None)
    midi_key = public.pop("_result_midi_key", None)
    xml_key = public.pop("_result_musicxml_key", None)
    if public.get("status") == "completed":
        settings = ObjectStorageSettings.from_environment()
        if bucket != settings.uploads_bucket or not midi_key or not xml_key:
            raise PresignedDownloadContractError("score result coordinates are incomplete.")
        public["midi_url"] = create_presigned_download_url(
            settings, job_id=public["job_id"], object_key=midi_key, kind="score-midi")
        public["musicxml_url"] = create_presigned_download_url(
            settings, job_id=public["job_id"], object_key=xml_key, kind="score-musicxml")
    if public.get("status") != "failed":
        public["error"] = None
    return JSONResponse(status_code=200, content=jsonable_encoder(public),
                        headers={"cache-control": "no-store"})


def job_not_found_response() -> JSONResponse:
    """Hide missing, expired, and foreign jobs behind one non-enumerating 404."""

    return JSONResponse(
        status_code=404,
        content={"error": "Job not found."},
        headers={"cache-control": "no-store"},
    )


def job_snapshot_unavailable_response() -> JSONResponse:
    """Give browser polling one safe retryable failure category for detail reads."""

    return JSONResponse(
        status_code=503,
        content={"error": "Job details are temporarily unavailable."},
        headers={"cache-control": "no-store"},
    )


def direct_upload_unavailable_response() -> JSONResponse:
    """Return one retryable error without leaking a storage or database cause.

    A client cannot safely distinguish a temporary PostgreSQL failure, a
    missing MinIO environment value, or a local signing-library failure. All
    are server-side conditions, so this shared response avoids exposing
    credentials, object names, endpoint details, or implementation state.
    """

    return JSONResponse(
        status_code=503,
        content={"error": "Direct upload is temporarily unavailable."},
        headers={"cache-control": "no-store"},
    )


def created_direct_upload_response(
    payload: DirectUploadJobCreatedResponse,
) -> JSONResponse:
    """Serialize only the reviewed browser upload contract with HTTP 201."""

    return JSONResponse(
        status_code=201,
        content=jsonable_encoder(payload.model_dump(mode="json")),
        # Presigned form values authorize one short-lived upload. A browser or
        # intermediary must not cache and later replay this response.
        headers={"cache-control": "no-store"},
    )


def score_upload_unavailable_response() -> JSONResponse:
    """Hide internal database, storage, and signing errors from the browser."""

    return JSONResponse(
        status_code=503,
        content={"error": "Score upload is temporarily unavailable."},
        headers={"cache-control": "no-store"},
    )


@app.get("/healthz", include_in_schema=False)
def healthz() -> JSONResponse:
    """Liveness endpoint: only prove this Python HTTP process is running.

    Kubernetes should restart a Pod that cannot serve HTTP. It should *not*
    restart every API Pod merely because PostgreSQL has a short outage; that is
    why database validation belongs to the separate readiness endpoint below.
    """

    return health_response(readiness=False)


@app.get("/readyz", include_in_schema=False)
def readyz() -> JSONResponse:
    """Readiness endpoint: verify a bounded PostgreSQL `SELECT 1` succeeds.

    A `503` makes a future Kubernetes Service remove this Pod from its ready
    endpoints while retaining the process for diagnosis. This is not yet a
    schema, Keycloak, MinIO, or RabbitMQ health check; those dependencies are
    deliberately introduced in later focused tasks.
    """

    try:
        verify_database_connection()
    except DatabaseConfigurationError:
        return readiness_failure_response(reason="database_configuration")
    except DatabaseUnavailable:
        return readiness_failure_response(reason="database_unavailable")

    return health_response(readiness=True)


@app.get("/auth/me", include_in_schema=False)
def authenticated_identity(
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
) -> JSONResponse:
    """Prove one Bearer token is valid without creating any durable state.

    FastAPI resolves `require_authenticated_principal` *before* this handler
    runs. Missing/invalid tokens return its generic 401 response; a Keycloak
    signing-key outage returns its generic retryable 503 response. Therefore no
    route body can accidentally read a browser-supplied user ID or run work for
    an unauthenticated caller.
    """

    return authenticated_identity_response(principal)


@app.get("/jobs", include_in_schema=False)
def list_jobs(
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
) -> JSONResponse:
    """List only the authenticated user's non-expired job summaries.

    FastAPI resolves the Keycloak validator before entering this function, so
    `principal.subject` is a signed immutable owner identity—not an HTTP
    parameter supplied by the browser. The database query is intentionally a
    narrow read-only operation: it does not create records, calculate quotas,
    sign MinIO URLs, publish RabbitMQ messages, or start processing work.
    """

    try:
        rows = list_retained_jobs_for_owner(principal.subject)
    except (DatabaseConfigurationError, DatabaseUnavailable):
        return job_history_unavailable_response()

    return saved_jobs_response(rows)


@app.get("/jobs/{job_id}", include_in_schema=False)
def get_job_detail(
    job_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
) -> JSONResponse:
    """Return one retained snapshot only when its immutable owner matches.

    ``job_id`` is parsed by FastAPI as a UUID before this handler runs, while
    ``principal.subject`` comes only from Keycloak's validated access token.
    The query binds both values and retention time in PostgreSQL, so neither a
    guessed UUID nor a browser-supplied owner can retrieve another account's
    row. A missing, expired, or foreign row shares one 404 response. The route
    signs URLs locally only for verified source/artifact keys; it never reads
    or proxies MinIO bytes. A new upload-pending row therefore needs no signer
    call until upload-intake confirms the source object.
    """

    try:
        row = get_retained_job_snapshot_for_owner(
            job_id=str(job_id),
            owner_sub=principal.subject,
        )
    except (DatabaseConfigurationError, DatabaseUnavailable):
        return job_snapshot_unavailable_response()

    if row is None:
        return job_not_found_response()
    try:
        return job_snapshot_response(row)
    except (ObjectStorageConfigurationError, PresignedDownloadContractError, PresignedDownloadSigningError):
        return job_snapshot_unavailable_response()
    except Exception:
        # SDK signing failures may contain endpoint details. Expose only one
        # retryable response; never serialize a private error or URL signature.
        return job_snapshot_unavailable_response()


@app.post("/jobs", include_in_schema=False)
def create_direct_upload_job(
    submission: DirectUploadJobRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
) -> JSONResponse:
    """Create one durable upload intent and its browser-to-Minio form.

    The verified Keycloak subject supplies ownership; the browser cannot choose
    an owner, bucket, object key, job ID, status, or expiry. Configuration is
    validated before the insert so a malformed Pod environment never creates a
    row that cannot be signed. PostgreSQL commits the row before local signing,
    ensuring every returned form refers to a durable job. Signature generation
    is pure local cryptography and makes no MinIO network request.

    This route intentionally ends after issuing the form. A later small task
    will confirm upload completion, change upload_pending state, and publish a
    RabbitMQ message for processing; no audio bytes or worker trigger pass
    through this HTTP request today.
    """

    try:
        storage = ObjectStorageSettings.from_environment()
    except ObjectStorageConfigurationError:
        return direct_upload_unavailable_response()

    try:
        created_job = create_direct_upload_pending_job(
            owner_sub=principal.subject,
            request=submission,
            input_bucket=storage.uploads_bucket,
        )
    except (DatabaseConfigurationError, DatabaseUnavailable):
        return direct_upload_unavailable_response()

    try:
        upload_post = create_constrained_source_upload_post(
            storage,
            job_id=created_job.job_id,
            input_object_key=created_job.input_object_key,
            content_type=submission.canonical_source_content_type,
            stem_mode=submission.stem_mode,
        )
        response_payload = DirectUploadJobCreatedResponse(
            job_id=created_job.job_id,
            status=created_job.status,
            revision=created_job.revision,
            expires_at=created_job.expires_at,
            upload_url=upload_post.url,
            upload_fields=dict(upload_post.fields),
            max_source_bytes=upload_post.maximum_source_bytes,
        )
    except (PresignedUploadContractError, PresignedUploadSigningError, ValidationError):
        # The durable row remains upload_pending if a local implementation
        # problem prevents form creation. That is safer than issuing a form
        # without state; expiration/reconciliation is a later intake task.
        return direct_upload_unavailable_response()

    return created_direct_upload_response(response_payload)


@app.post("/score-jobs", include_in_schema=False)
def create_score_upload_job(
    submission: ScoreUploadRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
) -> JSONResponse:
    """Create a durable owner-bound sheet upload and return its exact S3 form.

    This route only stages the source. Score object intake, notification, and
    OMR processing are separate milestones; a signed form never starts work by
    itself. The browser cannot select a bucket, key, owner, or status.
    """

    try:
        storage = ObjectStorageSettings.from_environment()
    except ObjectStorageConfigurationError:
        return score_upload_unavailable_response()
    try:
        created = create_score_upload_pending_job(
            owner_sub=principal.subject,
            request=submission,
            input_bucket=storage.uploads_bucket,
        )
    except (DatabaseConfigurationError, DatabaseUnavailable):
        return score_upload_unavailable_response()
    try:
        signed = create_constrained_score_upload_post(
            storage,
            job_id=created.job_id,
            input_object_key=created.input_object_key,
            content_type=submission.canonical_content_type,
        )
        response = ScoreUploadCreatedResponse(
            job_id=created.job_id,
            direction=created.direction,
            status=created.status,
            revision=created.revision,
            expires_at=created.expires_at,
            upload_url=signed.url,
            upload_fields=dict(signed.fields),
            max_source_bytes=signed.maximum_source_bytes,
        )
    except (PresignedUploadContractError, PresignedUploadSigningError, ValidationError):
        # A signing failure leaves the durable intent pending for retention
        # cleanup, never a browser permission without a corresponding row.
        return score_upload_unavailable_response()
    return JSONResponse(
        status_code=201,
        content=jsonable_encoder(response.model_dump(mode="json")),
        headers={"cache-control": "no-store"},
    )


@app.get("/score-jobs/{job_id}", include_in_schema=False)
def get_score_job(
    job_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
) -> JSONResponse:
    """Poll an owner-bound conversion; completed results receive fresh URLs."""

    try:
        row = get_retained_score_job_for_owner(job_id=str(job_id), owner_sub=principal.subject)
    except (DatabaseConfigurationError, DatabaseUnavailable):
        return job_snapshot_unavailable_response()
    if row is None:
        return job_not_found_response()
    try:
        return score_job_snapshot_response(row)
    except (ObjectStorageConfigurationError, PresignedDownloadContractError, PresignedDownloadSigningError):
        return job_snapshot_unavailable_response()
