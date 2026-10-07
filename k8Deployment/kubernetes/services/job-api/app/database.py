"""Small, dependency-focused PostgreSQL support for the local Job API.

The module keeps database settings, readiness, the first user-owned read-only
query, and the first durable direct-upload insert together. Route code supplies
only a Keycloak-verified ``owner_sub``; this module binds it as a PostgreSQL
query parameter and never accepts a browser-supplied user identifier. Each
operation uses a short-lived connection for clarity in this first local
milestone. A later performance task may introduce a reviewed connection pool
without changing the route contract.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from app.direct_upload_contract import DirectUploadJobRequest
from app.score_upload_contract import ScoreUploadRequest


# These non-secret defaults describe the current local Kubernetes network. A
# future Deployment may set the same values explicitly; keeping them here makes
# the code's expected in-cluster route clear without placing a password in any
# source file or ConfigMap.
DEFAULT_DATABASE_HOST = "clouddsp-postgresql.clouddsp-data.svc"
DEFAULT_DATABASE_PORT = 5432
DEFAULT_CONNECT_TIMEOUT_SECONDS = 3
DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS = 2_000
# The durable PostgreSQL row, not a browser clock or an S3 form expiry, owns
# retention. This matches the preserved cloud product setting of fourteen days.
DIRECT_UPLOAD_RETENTION_DAYS = 14

# Select exactly the compact fields the React saved-jobs library needs.  In
# particular, this list deliberately excludes input-object keys, source URLs,
# error text, artifact locations, database credentials, and future presigned
# URLs.  `job_id::text` keeps the eventual JSON response independent of the
# driver's UUID adaptation details.
LIST_RETAINED_JOBS_FOR_OWNER_SQL = """
    SELECT
      job_id::text AS job_id,
      source_filename,
      status,
      stem_mode,
      tempo,
      created_at,
      updated_at,
      expires_at
    FROM jobs
    WHERE owner_sub = %s
      AND expires_at > CURRENT_TIMESTAMP
    ORDER BY updated_at DESC
"""

# Read a single browser workspace snapshot using the same three ownership
# boundaries as the list query: a canonical job UUID, the Keycloak-verified
# owner subject, and unexpired retention. The two underscored aliases are
# private signer inputs, not browser fields. The HTTP serializer removes them
# after generating fresh owner-authorized URLs for the exact durable keys.
# Stable keys remain in PostgreSQL; their expiring signatures never do.
GET_RETAINED_JOB_SNAPSHOT_FOR_OWNER_SQL = """
    SELECT
      job_id::text AS job_id,
      input_bucket AS _storage_input_bucket,
      input_object_key AS _storage_input_object_key,
      source_type,
      source_filename,
      source_content_type,
      source_size_bytes,
      source_uploaded,
      stem_mode,
      status,
      revision,
      stems,
      midi,
      tempo,
      error_message AS error,
      created_at,
      updated_at,
      expires_at
    FROM jobs
    WHERE job_id = %s::uuid
      AND owner_sub = %s
      AND expires_at > CURRENT_TIMESTAMP
    LIMIT 1
"""

# This write lists every column deliberately instead of relying on a table
# column order. It sets only the immutable submission intent; source_uploaded,
# stems, MIDI, timestamps, and error fields keep their reviewed database
# defaults until future intake/worker tasks update them. The two RETURNING
# values needed by the eventual browser response are read from the same atomic
# insert, not from a second query that could observe an unrelated state change.
CREATE_DIRECT_UPLOAD_PENDING_JOB_SQL = """
    INSERT INTO jobs (
      job_id,
      owner_sub,
      source_type,
      input_bucket,
      input_object_key,
      source_filename,
      source_content_type,
      source_size_bytes,
      stem_mode,
      status,
      expires_at
    )
    VALUES (
      %s,
      %s,
      'direct_upload',
      %s,
      %s,
      %s,
      %s,
      %s,
      %s,
      'upload_pending',
      %s
    )
    RETURNING
      job_id::text AS job_id,
      status,
      revision,
      expires_at
"""


# This separate table preserves audio jobs' stem-specific constraints. Only a
# server-generated ID/key and a Keycloak-verified owner enter this insert.
CREATE_SCORE_UPLOAD_PENDING_JOB_SQL = """
    INSERT INTO score_jobs (
      job_id, owner_sub, direction, input_bucket, input_object_key,
      source_filename, source_content_type, source_size_bytes, expires_at
    ) VALUES (%s, %s, 'score_to_midi', %s, %s, %s, %s, %s, %s)
    RETURNING job_id::text AS job_id, direction, status, revision, expires_at
"""


class DatabaseConfigurationError(RuntimeError):
    """Raised when the API Pod is missing its required database environment."""


class DatabaseUnavailable(RuntimeError):
    """Raised when PostgreSQL cannot complete one bounded API operation."""


@dataclass(frozen=True)
class CreatedDirectUploadJob:
    """Server-owned values created by one successful upload-pending insert.

    The future HTTP route needs the input key only to ask MinIO for one
    presigned form. It must return the public browser contract instead of this
    internal persistence object, so the object key has no JSON route serializer
    and cannot accidentally be added to a generic response.
    """

    job_id: str
    input_object_key: str
    status: str
    revision: int
    expires_at: datetime


@dataclass(frozen=True)
class CreatedScoreUploadJob:
    """Private durable score row coordinates needed only for form signing."""

    job_id: str
    input_object_key: str
    direction: str
    status: str
    revision: int
    expires_at: datetime


def required_environment_value(name: str) -> str:
    """Return a required non-empty environment value without ever logging it.

    The username and database name are identifiers, while the password is a
    Secret value. They are all required before attempting a connection. Keeping
    this validation here makes a missing `secretKeyRef` show up as `/readyz`
    being unavailable instead of as an opaque driver exception later.
    """

    value = os.environ.get(name)
    if value is None or value == "":
        raise DatabaseConfigurationError(f"Required environment variable {name} is absent.")
    return value


def bounded_positive_integer(*, name: str, value: str, maximum: int) -> int:
    """Parse a small timeout value and reject invalid probe configuration."""

    try:
        parsed = int(value)
    except ValueError as error:
        raise DatabaseConfigurationError(f"{name} must be an integer.") from error
    if not 1 <= parsed <= maximum:
        raise DatabaseConfigurationError(f"{name} must be between 1 and {maximum}.")
    return parsed


@dataclass(frozen=True)
class DatabaseSettings:
    """Connection details supplied by the API's Kubernetes environment.

    `password` is excluded from the automatic dataclass representation. This
    prevents accidental credential disclosure if a settings object is ever
    included in a diagnostic exception or development log.
    """

    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)
    connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS

    @classmethod
    def from_environment(cls) -> "DatabaseSettings":
        """Build settings from the future Deployment's Secret/config values."""

        host = os.environ.get("JOB_API_DB_HOST", DEFAULT_DATABASE_HOST).strip()
        if not host:
            raise DatabaseConfigurationError("JOB_API_DB_HOST must not be empty.")

        port = bounded_positive_integer(
            name="JOB_API_DB_PORT",
            value=os.environ.get("JOB_API_DB_PORT", str(DEFAULT_DATABASE_PORT)),
            maximum=65_535,
        )
        connect_timeout_seconds = bounded_positive_integer(
            name="JOB_API_DB_CONNECT_TIMEOUT_SECONDS",
            value=os.environ.get(
                "JOB_API_DB_CONNECT_TIMEOUT_SECONDS",
                str(DEFAULT_CONNECT_TIMEOUT_SECONDS),
            ),
            # Readiness must fail promptly so Kubernetes removes an unhealthy
            # Pod from Service endpoints instead of accumulating stuck probes.
            maximum=10,
        )
        return cls(
            host=host,
            port=port,
            database=required_environment_value("JOB_API_DB_NAME"),
            username=required_environment_value("JOB_API_DB_USERNAME"),
            password=required_environment_value("JOB_API_DB_PASSWORD"),
            connect_timeout_seconds=connect_timeout_seconds,
        )


def verify_database_connection() -> None:
    """Open a short PostgreSQL connection and run a dependency-free `SELECT 1`.

    This function opens no connection during module import and retains no
    process-wide pool yet. A future API request task will add a reviewed pool;
    connecting per readiness probe keeps this first boundary easy to inspect.
    The function deliberately returns no database version, hostname, role, or
    exception details to callers because readiness responses are operational,
    not a database-discovery API.
    """

    settings = DatabaseSettings.from_environment()
    try:
        # `connect_timeout` bounds DNS/TCP/authentication. PostgreSQL's
        # `statement_timeout` separately bounds the SQL query after connection.
        # Both values are static local configuration, never browser input.
        with psycopg.connect(
            host=settings.host,
            port=settings.port,
            dbname=settings.database,
            user=settings.username,
            password=settings.password,
            connect_timeout=settings.connect_timeout_seconds,
            options=f"-c statement_timeout={DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS}",
            application_name="clouddsp-job-api-readiness",
            autocommit=True,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                if cursor.fetchone() != (1,):
                    raise DatabaseUnavailable("PostgreSQL readiness query returned an unexpected value.")
    except (psycopg.Error, OSError) as error:
        # Preserve the driver exception as a private exception cause for future
        # server-side diagnostics, but the HTTP endpoint never serializes it.
        raise DatabaseUnavailable("PostgreSQL is unavailable.") from error


def list_retained_jobs_for_owner(owner_sub: str) -> list[dict[str, object]]:
    """Return retained saved-job summaries belonging only to one trusted owner.

    The ``owner_sub`` value comes exclusively from the authenticated FastAPI
    dependency.  Psycopg binds it separately from the SQL text, so it is never
    interpreted as executable SQL.  PostgreSQL additionally receives
    ``default_transaction_read_only=on`` for this route's fresh connection:
    any accidental write added to this list operation fails at the database
    boundary rather than silently changing user job state.

    Rows use ``dict_row`` so the HTTP layer can explicitly choose its public
    JSON fields.  No connection pool is introduced in this focused task; a
    short-lived connection makes lifecycle and failure behaviour easy to see.
    """

    settings = DatabaseSettings.from_environment()
    try:
        with psycopg.connect(
            host=settings.host,
            port=settings.port,
            dbname=settings.database,
            user=settings.username,
            password=settings.password,
            connect_timeout=settings.connect_timeout_seconds,
            # `statement_timeout` bounds one potentially growing history query.
            # `default_transaction_read_only` enforces this route's no-write
            # contract independently from the PostgreSQL role's broader schema
            # privileges that are still needed by the migration Job.
            options=(
                f"-c statement_timeout={DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS} "
                "-c default_transaction_read_only=on"
            ),
            application_name="clouddsp-job-api-list-jobs",
            autocommit=True,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                # The first positional parameter is the authenticated subject,
                # not a value copied from an HTTP query string or request body.
                cursor.execute(LIST_RETAINED_JOBS_FOR_OWNER_SQL, (owner_sub,))
                # Make an ordinary dictionary copy before the cursor/connection
                # close. It contains only the explicitly selected row values.
                return [dict(row) for row in cursor.fetchall()]
    except (psycopg.Error, OSError) as error:
        # Do not expose driver messages; they can include database internals.
        # The HTTP route maps this safe category to a generic retryable 503.
        raise DatabaseUnavailable("PostgreSQL job history is unavailable.") from error


def _canonical_job_id(job_id: str) -> str:
    """Normalize one internal UUID before it becomes a bound SQL parameter.

    FastAPI performs this validation for the public route. Keeping it at this
    reusable persistence boundary means a later worker, CLI, or test cannot
    accidentally query with a malformed identifier either. The comparison with
    the original input preserves the API's lowercase canonical UUID convention.
    """

    if not isinstance(job_id, str):
        raise ValueError("job_id must be a UUID string.")
    try:
        canonical_job_id = str(UUID(job_id))
    except (ValueError, AttributeError) as error:
        raise ValueError("job_id must be a UUID string.") from error
    if canonical_job_id != job_id:
        raise ValueError("job_id must use canonical lowercase UUID form.")
    return canonical_job_id


def get_retained_job_snapshot_for_owner(
    *,
    job_id: str,
    owner_sub: str,
) -> dict[str, object] | None:
    """Return one current durable snapshot only for its verified owner.

    PostgreSQL executes this on a fresh read-only connection. A result of
    ``None`` intentionally covers a missing ID, another user's ID, and an
    expired record; the HTTP layer maps all three to the same 404 response so
    callers cannot use this endpoint to enumerate CloudDSP jobs. This helper
    does not generate MinIO URLs, inspect object storage, write a status, or
    publish RabbitMQ work—those are later isolated pipeline responsibilities.
    """

    canonical_job_id = _canonical_job_id(job_id)
    trusted_owner = _trusted_owner_sub(owner_sub)
    settings = DatabaseSettings.from_environment()
    try:
        with psycopg.connect(
            host=settings.host,
            port=settings.port,
            dbname=settings.database,
            user=settings.username,
            password=settings.password,
            connect_timeout=settings.connect_timeout_seconds,
            # Detail polling happens frequently while processing. Keep each
            # lookup bounded and make an accidental future write fail closed.
            options=(
                f"-c statement_timeout={DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS} "
                "-c default_transaction_read_only=on"
            ),
            application_name="clouddsp-job-api-get-job-detail",
            autocommit=True,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    GET_RETAINED_JOB_SNAPSHOT_FOR_OWNER_SQL,
                    (canonical_job_id, trusted_owner),
                )
                row = cursor.fetchone()
    except (psycopg.Error, OSError) as error:
        # Database diagnostics can expose internal addresses and role names;
        # retain them only as an exception cause, never a browser response.
        raise DatabaseUnavailable("PostgreSQL job detail is unavailable.") from error

    # Psycopg's dict_row factory yields a mapping for a matched row. A missing
    # row is expected and must not be converted into an exception or a fake
    # placeholder job, because the 404 response is part of the ownership model.
    if row is None:
        return None
    if not isinstance(row, dict):
        # A configured dict_row connection should never reach this branch. Do
        # not mislabel a driver/configuration regression as an ordinary 404.
        raise DatabaseUnavailable("PostgreSQL job detail returned an invalid row.")
    return dict(row)


def _trusted_owner_sub(owner_sub: str) -> str:
    """Reject a malformed internal owner value without rewriting its identity.

    Keycloak validation supplies this string in the future route. This helper
    still checks the minimal database invariant because it is a reusable
    persistence boundary: an empty or NUL-containing owner must never become a
    durable job row even if a future caller bypasses the HTTP dependency.
    """

    if not isinstance(owner_sub, str) or not owner_sub.strip() or "\x00" in owner_sub:
        raise ValueError("owner_sub must be one non-empty trusted subject string.")
    return owner_sub


def _trusted_upload_bucket(input_bucket: str) -> str:
    """Reject an empty configuration value before it reaches the jobs table."""

    if not isinstance(input_bucket, str) or not input_bucket.strip() or "\x00" in input_bucket:
        raise ValueError("input_bucket must be one non-empty configured bucket name.")
    return input_bucket


def _created_at_utc(now: datetime | None) -> datetime:
    """Return one timezone-aware clock value, with an injectable test clock."""

    created_at = datetime.now(UTC) if now is None else now
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("now must include a timezone offset.")
    return created_at.astimezone(UTC)


def create_direct_upload_pending_job(
    *,
    owner_sub: str,
    request: DirectUploadJobRequest,
    input_bucket: str,
    now: datetime | None = None,
) -> CreatedDirectUploadJob:
    """Persist one authorized direct-upload intent in one PostgreSQL transaction.

    The caller must pass a previously validated DirectUploadJobRequest and a
    Keycloak-verified owner. This function creates all remaining state itself:
    a UUID, a job-owned uploads/UUID/filename key, upload_pending status, first
    revision, and retention expiry. It makes no MinIO, RabbitMQ, Keycloak, or
    HTTP request, and it deliberately does not sign or upload a browser form.

    Psycopg starts a transaction for the INSERT because autocommit is false.
    The explicit transaction context commits only after the INSERT and RETURNING
    row both succeed; a driver error rolls it back. This ordering is essential:
    a later route must never issue a MinIO upload permission until this durable
    authorization record exists.
    """

    trusted_owner = _trusted_owner_sub(owner_sub)
    trusted_bucket = _trusted_upload_bucket(input_bucket)
    created_at = _created_at_utc(now)
    job_id = str(uuid4())
    input_object_key = f"uploads/{job_id}/{request.filename}"
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
            application_name="clouddsp-job-api-create-direct-upload",
            autocommit=False,
            row_factory=dict_row,
        ) as connection:
            # There is exactly one write inside this transaction. The explicit
            # scope makes the all-or-nothing persistence boundary visible to a
            # learner and prevents a future second statement from committing
            # independently by accident.
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        CREATE_DIRECT_UPLOAD_PENDING_JOB_SQL,
                        (
                            job_id,
                            trusted_owner,
                            trusted_bucket,
                            input_object_key,
                            request.filename,
                            request.canonical_source_content_type,
                            request.size_bytes,
                            request.stem_mode,
                            expires_at,
                        ),
                    )
                    row = cursor.fetchone()
    except (psycopg.Error, OSError) as error:
        # Driver diagnostics can disclose hosts, roles, or SQL details. The
        # later HTTP route maps this safe category to a generic retryable 503.
        raise DatabaseUnavailable("PostgreSQL job creation is unavailable.") from error

    if not isinstance(row, dict):
        raise DatabaseUnavailable("PostgreSQL job creation returned no durable row.")

    returned_job_id = row.get("job_id")
    returned_status = row.get("status")
    returned_revision = row.get("revision")
    returned_expiry = row.get("expires_at")
    if (
        returned_job_id != job_id
        or returned_status != "upload_pending"
        or isinstance(returned_revision, bool)
        or not isinstance(returned_revision, int)
        or returned_revision < 1
        or not isinstance(returned_expiry, datetime)
        or returned_expiry.tzinfo is None
        or returned_expiry.utcoffset() is None
    ):
        # The database schema is the durable source of truth. A surprising
        # RETURNING row means the API must fail closed instead of creating a
        # potentially mismatched MinIO permission for a browser.
        raise DatabaseUnavailable("PostgreSQL job creation returned an invalid durable row.")

    return CreatedDirectUploadJob(
        job_id=returned_job_id,
        input_object_key=input_object_key,
        status=returned_status,
        revision=returned_revision,
        expires_at=returned_expiry,
    )


def create_score_upload_pending_job(
    *,
    owner_sub: str,
    request: ScoreUploadRequest,
    input_bucket: str,
    now: datetime | None = None,
) -> CreatedScoreUploadJob:
    """Commit one score upload intent before the caller signs any MinIO form."""

    trusted_owner = _trusted_owner_sub(owner_sub)
    trusted_bucket = _trusted_upload_bucket(input_bucket)
    created_at = _created_at_utc(now)
    job_id = str(uuid4())
    input_object_key = f"score-inputs/{job_id}/source{request.extension}"
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
                        CREATE_SCORE_UPLOAD_PENDING_JOB_SQL,
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
        or row.get("direction") != "score_to_midi"
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
