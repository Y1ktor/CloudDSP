"""Owner-bound MIDI-to-sheet persistence; separate from audio and OMR state."""
from datetime import datetime, timedelta
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from app.database import (DatabaseSettings, DatabaseUnavailable, CreatedScoreUploadJob,
    _trusted_owner_sub, _trusted_upload_bucket, _created_at_utc, _canonical_job_id,
    DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS, DIRECT_UPLOAD_RETENTION_DAYS)
from app.sheet_upload_contract import SheetUploadRequest

CREATE_SHEET_UPLOAD_PENDING_JOB_SQL = """
    INSERT INTO midi_sheet_jobs (
      job_id, owner_sub, direction, input_bucket, input_object_key,
      source_filename, source_content_type, source_size_bytes, expires_at
    ) VALUES (%s, %s, 'midi_to_sheet', %s, %s, %s, %s, %s, %s)
    RETURNING job_id::text AS job_id, direction, status, revision, expires_at
"""

# MIDI engraving history remains separate from stem and score-to-MIDI jobs.
# The existing (owner_sub, updated_at DESC) index supports this owner-bound read.
# Never return storage keys or sign URLs in a compact library response.
LIST_RETAINED_SHEET_JOBS_FOR_OWNER_SQL = """
    SELECT job_id::text AS job_id, direction, source_filename, status,
           created_at, updated_at, expires_at
    FROM public.midi_sheet_jobs
    WHERE owner_sub = %s AND direction = 'midi_to_sheet'
      AND expires_at > CURRENT_TIMESTAMP
    ORDER BY updated_at DESC, job_id DESC
"""

# Score polling is a separate owner-bound read. The underscored coordinates
# never reach JSON; the HTTP layer signs only completed deterministic outputs.
GET_RETAINED_SHEET_JOB_FOR_OWNER_SQL = """
    SELECT job_id::text AS job_id, direction, source_filename, source_uploaded,
           source_content_type, source_size_bytes,
           input_bucket AS _source_bucket, input_object_key AS _source_key,
           status, revision, attempt_count, error_message AS error,
           result_bucket AS _result_bucket,
           result_pdf_key AS _result_pdf_key,
           result_musicxml_key AS _result_musicxml_key,
           created_at, updated_at, expires_at
    FROM public.midi_sheet_jobs
    WHERE job_id = %s::uuid AND owner_sub = %s
      AND expires_at > CURRENT_TIMESTAMP
    LIMIT 1
"""


def list_retained_sheet_jobs_for_owner(owner_sub: str) -> list[dict[str, object]]:
    """Read compact retained sheet jobs using only the verified token subject.

    The browser cannot choose a different owner or make a write through this
    read-only connection. Source/result permissions are issued only by the
    separate owner-checked detail route when a saved item is opened.
    """

    trusted_owner = _trusted_owner_sub(owner_sub)
    settings = DatabaseSettings.from_environment()
    try:
        with psycopg.connect(
            host=settings.host, port=settings.port, dbname=settings.database,
            user=settings.username, password=settings.password,
            connect_timeout=settings.connect_timeout_seconds,
            options=(f"-c statement_timeout={DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS} "
                     "-c default_transaction_read_only=on"),
            application_name="clouddsp-job-api-list-score-jobs",
            autocommit=True, row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(LIST_RETAINED_SHEET_JOBS_FOR_OWNER_SQL, (trusted_owner,))
                return [dict(row) for row in cursor.fetchall()]
    except (psycopg.Error, OSError) as error:
        raise DatabaseUnavailable("PostgreSQL score history is unavailable.") from error


def get_retained_sheet_job_for_owner(*, job_id: str, owner_sub: str) -> dict[str, object] | None:
    """Read one score state with the same Keycloak owner/expiry boundary."""

    canonical_id = _canonical_job_id(job_id)
    trusted_owner = _trusted_owner_sub(owner_sub)
    settings = DatabaseSettings.from_environment()
    try:
        with psycopg.connect(
            host=settings.host, port=settings.port, dbname=settings.database,
            user=settings.username, password=settings.password,
            connect_timeout=settings.connect_timeout_seconds,
            options=(f"-c statement_timeout={DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS} "
                     "-c default_transaction_read_only=on"),
            application_name="clouddsp-job-api-get-score-detail",
            autocommit=True, row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(GET_RETAINED_SHEET_JOB_FOR_OWNER_SQL,
                               (canonical_id, trusted_owner))
                row = cursor.fetchone()
    except (psycopg.Error, OSError) as error:
        raise DatabaseUnavailable("PostgreSQL score detail is unavailable.") from error
    if row is None:
        return None
    if not isinstance(row, dict):
        raise DatabaseUnavailable("PostgreSQL score detail returned an invalid row.")
    return dict(row)


def create_sheet_upload_pending_job(
    *,
    owner_sub: str,
    request: SheetUploadRequest,
    input_bucket: str,
    now: datetime | None = None,
) -> CreatedScoreUploadJob:
    """Commit one score upload intent before the caller signs any MinIO form."""

    trusted_owner = _trusted_owner_sub(owner_sub)
    trusted_bucket = _trusted_upload_bucket(input_bucket)
    created_at = _created_at_utc(now)
    job_id = str(uuid4())
    input_object_key = f"midi-sheet-inputs/{job_id}/source{request.extension}"
    expires_at = created_at + timedelta(days=DIRECT_UPLOAD_RETENTION_DAYS)
    settings = DatabaseSettings.from_environment()
    try:
        with psycopg.connect(
            host=settings.host,
            port=settings.port,
            dbname=settings.database,
            user=settings.username,
            password=settings.password,
            connect_timeout=settings.connect_timeout_seconds,
            options=f"-c statement_timeout={DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS}",
            application_name="clouddsp-job-api-create-score-upload",
            autocommit=False,
            row_factory=dict_row,
        ) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        CREATE_SHEET_UPLOAD_PENDING_JOB_SQL,
                        (
                            job_id, trusted_owner, trusted_bucket, input_object_key,
                            request.filename, request.canonical_content_type,
                            request.size_bytes, expires_at,
                        ),
                    )
                    row = cursor.fetchone()
    except (psycopg.Error, OSError) as error:
        raise DatabaseUnavailable("PostgreSQL score job creation is unavailable.") from error

    if (
        not isinstance(row, dict)
        or row.get("job_id") != job_id
        or row.get("direction") != "midi_to_sheet"
        or row.get("status") != "upload_pending"
        or isinstance(row.get("revision"), bool)
        or not isinstance(row.get("revision"), int)
        or row["revision"] < 1
        or not isinstance(row.get("expires_at"), datetime)
        or row["expires_at"].tzinfo is None
        or row["expires_at"].utcoffset() is None
    ):
        raise DatabaseUnavailable("PostgreSQL score job creation returned an invalid row.")
    return CreatedScoreUploadJob(
        job_id=job_id,
        input_object_key=input_object_key,
        direction=row["direction"],
        status=row["status"],
        revision=row["revision"],
        expires_at=row["expires_at"],
    )
