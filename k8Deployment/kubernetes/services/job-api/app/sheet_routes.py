"""Authenticated MIDI engraving API; file selection itself never creates a job."""
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from app.authentication import AuthenticatedPrincipal, require_authenticated_principal
from app.database import DatabaseConfigurationError, DatabaseUnavailable
from app.sheet_database import (create_sheet_upload_pending_job, get_retained_sheet_job_for_owner, list_retained_sheet_jobs_for_owner)
from app.sheet_upload_contract import SheetUploadRequest, SheetUploadCreatedResponse
from app.object_storage import ObjectStorageConfigurationError, ObjectStorageSettings
from app.presigned_download import (PresignedDownloadContractError, PresignedDownloadSigningError, create_presigned_download_url)
from app.presigned_upload import PresignedUploadContractError, PresignedUploadSigningError
from app.sheet_presigned_upload import create_constrained_sheet_upload_post

router = APIRouter()

def response(status, content):
    return JSONResponse(status_code=status, content=jsonable_encoder(content), headers={"cache-control": "no-store"})
def saved_jobs_response(rows): return response(200, {"jobs": rows})
def job_history_unavailable_response(): return response(503, {"error": "Job history is temporarily unavailable."})
def job_snapshot_unavailable_response(): return response(503, {"error": "Job details are temporarily unavailable."})
def sheet_upload_unavailable_response(): return response(503, {"error": "MIDI upload is temporarily unavailable."})
def job_not_found_response(): return response(404, {"error": "Job not found."})

def sheet_job_snapshot_response(row: dict[str, object]) -> JSONResponse:
    """Return safe MIDI engraving state and fresh result URLs after owner verification."""

    public = dict(row)
    source_bucket = public.pop("_source_bucket", None)
    source_key = public.pop("_source_key", None)
    bucket = public.pop("_result_bucket", None)
    pdf_key = public.pop("_result_pdf_key", None)
    xml_key = public.pop("_result_musicxml_key", None)
    needs_source = public.get("source_uploaded") is True
    if needs_source or public.get("status") == "completed":
        settings = ObjectStorageSettings.from_environment()
    if needs_source:
        if source_bucket != settings.uploads_bucket or not source_key:
            raise PresignedDownloadContractError("MIDI source coordinates are incomplete.")
        public["source_url"] = create_presigned_download_url(
            settings, job_id=public["job_id"], object_key=source_key, kind="sheet-source")
    if public.get("status") == "completed":
        if bucket != settings.uploads_bucket or not pdf_key or not xml_key:
            raise PresignedDownloadContractError("sheet result coordinates are incomplete.")
        public["pdf_url"] = create_presigned_download_url(
            settings, job_id=public["job_id"], object_key=pdf_key, kind="sheet-pdf")
        public["musicxml_url"] = create_presigned_download_url(
            settings, job_id=public["job_id"], object_key=xml_key, kind="sheet-musicxml")
    if public.get("status") != "failed":
        public["error"] = None
    return JSONResponse(status_code=200, content=jsonable_encoder(public),
                        headers={"cache-control": "no-store"})


@router.get("/sheet-jobs", include_in_schema=False)
def list_sheet_jobs(
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
) -> JSONResponse:
    """List retained sheet jobs for the signed owner without artifact signing.

    This read never merges the audio jobs table, accepts an owner parameter,
    publishes work, or changes processing state. Opening a row uses the same
    owner/expiry checks as ordinary score polling.
    """

    try:
        rows = list_retained_sheet_jobs_for_owner(principal.subject)
    except (DatabaseConfigurationError, DatabaseUnavailable):
        return job_history_unavailable_response()
    return saved_jobs_response(rows)


@router.post("/sheet-jobs", include_in_schema=False)
def create_sheet_upload_job(
    submission: SheetUploadRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
) -> JSONResponse:
    """Create a durable owner-bound sheet upload and return its exact S3 form.

    Only the editor Queue action calls this route. MinIO notification and
    the independent MuseScore consumer handle the verified source afterward. The browser cannot select a bucket, key, owner, or status.
    """

    try:
        storage = ObjectStorageSettings.from_environment()
    except ObjectStorageConfigurationError:
        return sheet_upload_unavailable_response()
    try:
        created = create_sheet_upload_pending_job(
            owner_sub=principal.subject,
            request=submission,
            input_bucket=storage.uploads_bucket,
        )
    except (DatabaseConfigurationError, DatabaseUnavailable):
        return sheet_upload_unavailable_response()
    try:
        signed = create_constrained_sheet_upload_post(
            storage,
            job_id=created.job_id,
            input_object_key=created.input_object_key,
            content_type=submission.canonical_content_type,
        )
        response = SheetUploadCreatedResponse(
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
        return sheet_upload_unavailable_response()
    return JSONResponse(
        status_code=201,
        content=jsonable_encoder(response.model_dump(mode="json")),
        headers={"cache-control": "no-store"},
    )


@router.get("/sheet-jobs/{job_id}", include_in_schema=False)
def get_sheet_job(
    job_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
) -> JSONResponse:
    """Poll an owner-bound conversion; completed results receive fresh URLs."""

    try:
        row = get_retained_sheet_job_for_owner(job_id=str(job_id), owner_sub=principal.subject)
    except (DatabaseConfigurationError, DatabaseUnavailable):
        return job_snapshot_unavailable_response()
    if row is None:
        return job_not_found_response()
    try:
        return sheet_job_snapshot_response(row)
    except (ObjectStorageConfigurationError, PresignedDownloadContractError, PresignedDownloadSigningError):
        return job_snapshot_unavailable_response()
