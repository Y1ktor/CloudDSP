"""Narrow PostgreSQL operations for one verified direct-upload candidate.

This module is intentionally *not* a RabbitMQ consumer and does not open a
network connection by itself.  A later composition task will own the Psycopg
connection, transaction lifetime, AMQP acknowledgement, and MinIO HeadObject
verification.  Keeping this module to parameterized SQL and result validation
lets focused tests prove the durable state-and-outbox contract independently of
the long-running Pod and its external connections.

The restricted ``clouddsp-upload-intake`` PostgreSQL role has exactly the
column privileges used here.  That is a second protection layer: even if a
future consumer has a bug, its database login cannot create jobs, delete jobs,
read ``owner_sub``, or rewrite stem/MIDI artifacts. Its one outbox privilege is
limited to creating the initial Demucs promise; it cannot dispatch or mutate it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from app.minio_event import SourceUploadCandidate


# A caller supplies a cursor that is already inside its own reviewed database
# transaction.  Defining the small protocol here keeps the first helper free
# from a Psycopg dependency and makes tests use an ordinary mock cursor.  The
# later runtime adapter may pass a real psycopg cursor because it has these two
# methods with compatible behaviour.
class DatabaseCursor(Protocol):
    """The minimal cursor surface needed for restricted intake SQL."""

    def execute(self, query: str, params: tuple[object, ...]) -> object:
        """Execute parameterized SQL without interpolating untrusted values."""

    def fetchone(self) -> Mapping[str, object] | None:
        """Return the one selected/returned row, if PostgreSQL found one."""


# This read is deliberately narrower than the Job API's browser snapshot.  It
# selects only fields the intake consumer must compare with a MinIO HeadObject
# result and its own idempotent transition.  ``owner_sub``, filename, artifact
# maps, timestamps, and full error text remain unavailable to the role.
FIND_PENDING_DIRECT_UPLOAD_SQL = """
    SELECT
      job_id::text AS job_id,
      source_type,
      input_bucket,
      input_object_key,
      source_content_type,
      source_size_bytes,
      source_uploaded,
      stem_mode,
      status,
      revision,
      expires_at
    FROM public.jobs
    WHERE job_id = %s::uuid
      AND input_bucket = %s
      AND input_object_key = %s
      AND source_type = 'direct_upload'
      AND status = 'upload_pending'
      AND source_uploaded = FALSE
      AND expires_at > CURRENT_TIMESTAMP
    LIMIT 1
"""

# The success mutation repeats every durable predicate from the lookup and adds
# the revision returned by that lookup.  It is therefore atomic: two consumer
# Pods may both observe an event, but only one can change revision N to N + 1.
# The other receives no row and treats the message as an idempotent no-op.
MARK_VERIFIED_SOURCE_UPLOADED_SQL = """
    UPDATE public.jobs
    SET
      source_uploaded = TRUE,
      status = 'source_uploaded',
      revision = revision + 1,
      error_message = NULL
    WHERE job_id = %s::uuid
      AND input_bucket = %s
      AND input_object_key = %s
      AND source_type = 'direct_upload'
      AND status = 'upload_pending'
      AND source_uploaded = FALSE
      AND revision = %s
      AND expires_at > CURRENT_TIMESTAMP
    RETURNING
      job_id::text AS job_id,
      source_uploaded,
      status,
      revision
"""

# This insert deliberately follows the conditional jobs UPDATE on the same
# cursor inside one PostgreSQL transaction. The value-bearing fields are bound
# parameters; the remaining literals are the only v002 event shape currently
# permitted by database CHECK constraints and the role's column-level grant.
# `ON CONFLICT DO NOTHING` is the database backstop for the stage idempotency
# key. It never creates an additional Demucs request for the same job.
INSERT_PENDING_DEMUCS_OUTBOX_EVENT_SQL = """
    INSERT INTO public.outbox_events (
      event_id,
      job_id,
      stage,
      stem_name,
      event_type,
      payload
    )
    VALUES (
      %s::uuid,
      %s::uuid,
      'demucs',
      '',
      'demucs.requested',
      %s::jsonb
    )
    ON CONFLICT ON CONSTRAINT outbox_events_job_stage_stem_idempotency
    DO NOTHING
"""

# A later dispatcher and Demucs worker will consume this versioned internal
# payload contract. It stores stable private-object identifiers rather than a
# browser presigned URL or credentials, neither of which belongs in durable
# PostgreSQL/RabbitMQ work state.
DEMUCS_OUTBOX_PAYLOAD_VERSION = 1

# A permanently invalid object must not leave a known-bad job pending forever.
# The category is bound as a parameter—not copied into SQL—and can only come
# from the small enum below.  Raw SDK/driver exceptions and object metadata are
# never written into the durable error field or emitted by this helper.
MARK_PERMANENTLY_INVALID_SOURCE_SQL = """
    UPDATE public.jobs
    SET
      source_uploaded = FALSE,
      status = 'failed',
      revision = revision + 1,
      error_message = %s
    WHERE job_id = %s::uuid
      AND input_bucket = %s
      AND input_object_key = %s
      AND source_type = 'direct_upload'
      AND status = 'upload_pending'
      AND source_uploaded = FALSE
      AND revision = %s
      AND expires_at > CURRENT_TIMESTAMP
    RETURNING
      job_id::text AS job_id,
      source_uploaded,
      status,
      revision
"""


class DatabaseTransitionProtocolError(RuntimeError):
    """Raise a safe category when PostgreSQL returns an unexpected row shape.

    A later consumer will treat this as an infrastructure failure and retry;
    it must not acknowledge a RabbitMQ message after an unverified database
    result.  The public message contains no query parameters, object key,
    database hostname, role, or driver diagnostic.
    """


class SourceUploadTransitionOutcome(StrEnum):
    """The only durable results of one first-stage source-intake mutation."""

    # The one expected state change after a valid HeadObject comparison.
    SOURCE_UPLOADED = "source_uploaded"
    # A verified permanent object problem records a fixed safe category.
    FAILED = "failed"
    # A duplicate, race, expiry, deletion, or later-stage advance changed the
    # row first.  The consumer must not roll the state backward or retry it.
    NO_LONGER_PENDING = "no_longer_pending"


class PermanentSourceFailureCategory(StrEnum):
    """Bounded error categories allowed in ``jobs.error_message`` initially.

    These are durable operational categories, not raw exception messages.  A
    later MinIO verifier will select exactly one after it has determined that
    an object problem is permanent rather than a transient S3 outage.
    """

    OBJECT_MISSING = "source_object_missing"
    METADATA_MISMATCH = "source_object_metadata_mismatch"
    CONTENT_TYPE_MISMATCH = "source_object_content_type_mismatch"
    SIZE_LIMIT_EXCEEDED = "source_object_size_limit_exceeded"


@dataclass(frozen=True)
class PendingDirectUpload:
    """The restricted, durable information needed for a later HeadObject check.

    This is an internal persistence object, never a browser response.  It has
    no owner identity, filename, artifact URL, password, or full prior error.
    The retained ``revision`` becomes the optimistic-concurrency guard for the
    later conditional UPDATE.
    """

    job_id: str
    input_bucket: str
    input_object_key: str
    source_content_type: str | None
    source_size_bytes: int | None
    stem_mode: str
    revision: int
    expires_at: datetime


@dataclass(frozen=True)
class SourceUploadStateChange:
    """One result that tells a future consumer whether its mutation won.

    ``resulting_revision`` is absent for a no-op because PostgreSQL returned no
    row.  The caller must acknowledge that no-op without trying a broader
    diagnostic query: a later delivery or state transition must never be
    exposed or overwritten merely to classify a duplicate notification.
    """

    outcome: SourceUploadTransitionOutcome
    job_id: str
    resulting_revision: int | None


def _canonical_job_id(job_id: str) -> str:
    """Require the Job API's lowercase UUID spelling before binding SQL."""

    if not isinstance(job_id, str):
        raise ValueError("job_id must be canonical lowercase UUID text.")
    try:
        canonical_job_id = str(UUID(job_id))
    except (ValueError, AttributeError) as error:
        raise ValueError("job_id must be canonical lowercase UUID text.") from error
    if canonical_job_id != job_id:
        raise ValueError("job_id must be canonical lowercase UUID text.")
    return canonical_job_id


def _nonempty_text(*, name: str, value: str) -> str:
    """Reject non-text, empty, or NUL-bearing values before binding SQL."""

    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"{name} must be non-empty text without NUL.")
    return value


def _candidate_database_identity(candidate: SourceUploadCandidate) -> tuple[str, str, str]:
    """Extract the exact notification identity that must match the DB row.

    The MinIO parser already narrowed this candidate.  Rechecking its basic
    shape here keeps the database boundary safe when a later reconciler or
    unit test calls it directly rather than going through AMQP parsing.
    """

    if not isinstance(candidate, SourceUploadCandidate):
        raise TypeError("candidate must be a SourceUploadCandidate.")
    return (
        _canonical_job_id(candidate.job_id),
        _nonempty_text(name="candidate.bucket_name", value=candidate.bucket_name),
        _nonempty_text(name="candidate.object_key", value=candidate.object_key),
    )


def _positive_revision(revision: int) -> int:
    """Require a real optimistic-concurrency revision, never a Boolean."""

    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("expected_revision must be a positive integer.")
    return revision


def _required_text_row_value(
    row: Mapping[str, object],
    *,
    name: str,
) -> str:
    """Read one required text column while failing closed on driver drift."""

    value = row.get(name)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise DatabaseTransitionProtocolError("PostgreSQL returned an invalid upload-intake row.")
    return value


def _required_positive_integer_row_value(
    row: Mapping[str, object],
    *,
    name: str,
) -> int:
    """Read one positive integer column without accepting ``True`` as ``1``."""

    value = row.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DatabaseTransitionProtocolError("PostgreSQL returned an invalid upload-intake row.")
    return value


def _pending_direct_upload_from_row(
    row: Mapping[str, object],
    *,
    expected_identity: tuple[str, str, str],
) -> PendingDirectUpload:
    """Validate that a selected row still matches its candidate exactly."""

    expected_job_id, expected_bucket, expected_object_key = expected_identity
    job_id = _required_text_row_value(row, name="job_id")
    input_bucket = _required_text_row_value(row, name="input_bucket")
    input_object_key = _required_text_row_value(row, name="input_object_key")
    source_type = _required_text_row_value(row, name="source_type")
    status = _required_text_row_value(row, name="status")
    source_uploaded = row.get("source_uploaded")
    if (
        job_id != expected_job_id
        or input_bucket != expected_bucket
        or input_object_key != expected_object_key
        or source_type != "direct_upload"
        or status != "upload_pending"
        or source_uploaded is not False
    ):
        raise DatabaseTransitionProtocolError("PostgreSQL returned an invalid upload-intake row.")

    source_content_type = row.get("source_content_type")
    if source_content_type is not None and not isinstance(source_content_type, str):
        raise DatabaseTransitionProtocolError("PostgreSQL returned an invalid upload-intake row.")

    source_size_bytes = row.get("source_size_bytes")
    if (
        source_size_bytes is not None
        and (isinstance(source_size_bytes, bool) or not isinstance(source_size_bytes, int) or source_size_bytes < 1)
    ):
        raise DatabaseTransitionProtocolError("PostgreSQL returned an invalid upload-intake row.")

    expires_at = row.get("expires_at")
    if (
        not isinstance(expires_at, datetime)
        or expires_at.tzinfo is None
        or expires_at.utcoffset() is None
    ):
        raise DatabaseTransitionProtocolError("PostgreSQL returned an invalid upload-intake row.")

    return PendingDirectUpload(
        job_id=job_id,
        input_bucket=input_bucket,
        input_object_key=input_object_key,
        source_content_type=source_content_type,
        source_size_bytes=source_size_bytes,
        stem_mode=_required_text_row_value(row, name="stem_mode"),
        revision=_required_positive_integer_row_value(row, name="revision"),
        expires_at=expires_at,
    )


def find_pending_direct_upload(
    cursor: DatabaseCursor,
    *,
    candidate: SourceUploadCandidate,
) -> PendingDirectUpload | None:
    """Find one currently eligible row for an already-parsed notification.

    ``None`` intentionally combines absent, expired, non-direct, already
    uploaded, failed, and later-stage rows.  The eventual consumer will safely
    acknowledge them as non-actionable; it must not issue a second broader
    query that reveals or revives a job outside this state boundary.
    """

    expected_identity = _candidate_database_identity(candidate)
    cursor.execute(FIND_PENDING_DIRECT_UPLOAD_SQL, expected_identity)
    row = cursor.fetchone()
    if row is None:
        return None
    if not isinstance(row, Mapping):
        raise DatabaseTransitionProtocolError("PostgreSQL returned an invalid upload-intake row.")
    return _pending_direct_upload_from_row(row, expected_identity=expected_identity)


def _state_change_from_returning_row(
    row: Mapping[str, object],
    *,
    expected_job_id: str,
    expected_previous_revision: int,
    expected_status: str,
    expected_source_uploaded: bool,
    outcome: SourceUploadTransitionOutcome,
) -> SourceUploadStateChange:
    """Validate an UPDATE RETURNING row before a future AMQP acknowledgement."""

    returned_job_id = _required_text_row_value(row, name="job_id")
    returned_status = _required_text_row_value(row, name="status")
    returned_source_uploaded = row.get("source_uploaded")
    returned_revision = _required_positive_integer_row_value(row, name="revision")
    if (
        returned_job_id != expected_job_id
        or returned_status != expected_status
        or returned_source_uploaded is not expected_source_uploaded
        or returned_revision != expected_previous_revision + 1
    ):
        raise DatabaseTransitionProtocolError("PostgreSQL returned an invalid upload-intake state change.")
    return SourceUploadStateChange(
        outcome=outcome,
        job_id=returned_job_id,
        resulting_revision=returned_revision,
    )


def _no_longer_pending(candidate: SourceUploadCandidate) -> SourceUploadStateChange:
    """Return the idempotent no-op result without a second diagnostic query."""

    job_id, _, _ = _candidate_database_identity(candidate)
    return SourceUploadStateChange(
        outcome=SourceUploadTransitionOutcome.NO_LONGER_PENDING,
        job_id=job_id,
        resulting_revision=None,
    )


def _pending_demucs_outbox_payload(
    *,
    pending: PendingDirectUpload,
    expected_identity: tuple[str, str, str],
    expected_revision: int,
) -> str:
    """Serialize the narrow, versioned request committed with one state change.

    ``pending`` came from the restricted pre-HeadObject lookup, while
    ``expected_identity`` came directly from the parsed MinIO record. Checking
    both again stops a future caller from pairing an otherwise valid job update
    with another job's object reference. The JSON string is parameter-bound and
    cast by PostgreSQL to JSONB; it is never built into SQL text.
    """

    if not isinstance(pending, PendingDirectUpload):
        raise TypeError("pending must be a PendingDirectUpload.")
    job_id, bucket_name, object_key = expected_identity
    if (
        pending.job_id != job_id
        or pending.input_bucket != bucket_name
        or pending.input_object_key != object_key
        or pending.revision != expected_revision
    ):
        raise DatabaseTransitionProtocolError("PostgreSQL returned an invalid upload-intake row.")

    stem_mode = _nonempty_text(name="pending.stem_mode", value=pending.stem_mode)
    return json.dumps(
        {
            "schema_version": DEMUCS_OUTBOX_PAYLOAD_VERSION,
            "job_id": job_id,
            "source": {
                "bucket": bucket_name,
                "object_key": object_key,
            },
            "stem_mode": stem_mode,
        },
        # Deterministic compact JSON makes focused tests and later message
        # signing/correlation review easier. The payload holds no floats, but
        # allow_nan=False keeps this durable format valid JSON if it evolves.
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def mark_verified_source_uploaded_and_enqueue_demucs(
    cursor: DatabaseCursor,
    *,
    candidate: SourceUploadCandidate,
    pending: PendingDirectUpload,
    expected_revision: int,
) -> SourceUploadStateChange:
    """Atomically advance one verified source and create its Demucs promise.

    The caller must complete its private MinIO metadata comparison first.  The
    conditional UPDATE is still self-protecting: it repeats the direct-upload,
    pending, unuploaded, matching key, revision, and unexpired predicates. If
    it returns no row, no INSERT is attempted. If it returns one row, this
    function inserts the one durable `demucs.requested` promise using the same
    cursor. The surrounding ``write_cursor`` transaction commits both changes
    together or rolls both back when this insert, cursor, or caller fails.

    This function intentionally does *not* publish RabbitMQ work. PostgreSQL
    cannot atomically commit with a broker, so a separate dispatcher later
    leases and publishes the committed outbox event with at-least-once safety.
    """

    job_id, bucket_name, object_key = _candidate_database_identity(candidate)
    revision = _positive_revision(expected_revision)
    # Validate/serialize before changing the jobs row. An invalid caller cannot
    # even begin the write transaction's state update, while a later SQL error
    # remains protected by the surrounding all-or-nothing transaction.
    serialized_outbox_payload = _pending_demucs_outbox_payload(
        pending=pending,
        expected_identity=(job_id, bucket_name, object_key),
        expected_revision=revision,
    )
    cursor.execute(
        MARK_VERIFIED_SOURCE_UPLOADED_SQL,
        (job_id, bucket_name, object_key, revision),
    )
    row = cursor.fetchone()
    if row is None:
        return _no_longer_pending(candidate)
    if not isinstance(row, Mapping):
        raise DatabaseTransitionProtocolError("PostgreSQL returned an invalid upload-intake state change.")
    state_change = _state_change_from_returning_row(
        row,
        expected_job_id=job_id,
        expected_previous_revision=revision,
        expected_status="source_uploaded",
        expected_source_uploaded=True,
        outcome=SourceUploadTransitionOutcome.SOURCE_UPLOADED,
    )

    # Generate an opaque correlation ID before the INSERT. The durable unique
    # `(job_id, stage, stem_name)` constraint—not UUID randomness—is the
    # idempotency guarantee; this UUID lets the future dispatcher correlate one
    # stored event with one AMQP publication without exposing user identity.
    cursor.execute(
        INSERT_PENDING_DEMUCS_OUTBOX_EVENT_SQL,
        (
            str(uuid4()),
            job_id,
            serialized_outbox_payload,
        ),
    )
    return state_change


def mark_permanently_invalid_source(
    cursor: DatabaseCursor,
    *,
    candidate: SourceUploadCandidate,
    expected_revision: int,
    category: PermanentSourceFailureCategory,
) -> SourceUploadStateChange:
    """Atomically fail one verified permanent source problem, or safely no-op.

    Only a ``PermanentSourceFailureCategory`` may become durable error text.
    A future S3 client must raise/retry transient connection and service faults
    instead of calling this function with an exception string or an arbitrary
    status supplied by a message producer.
    """

    if not isinstance(category, PermanentSourceFailureCategory):
        raise TypeError("category must be a PermanentSourceFailureCategory.")
    job_id, bucket_name, object_key = _candidate_database_identity(candidate)
    revision = _positive_revision(expected_revision)
    cursor.execute(
        MARK_PERMANENTLY_INVALID_SOURCE_SQL,
        (category.value, job_id, bucket_name, object_key, revision),
    )
    row = cursor.fetchone()
    if row is None:
        return _no_longer_pending(candidate)
    if not isinstance(row, Mapping):
        raise DatabaseTransitionProtocolError("PostgreSQL returned an invalid upload-intake state change.")
    return _state_change_from_returning_row(
        row,
        expected_job_id=job_id,
        expected_previous_revision=revision,
        expected_status="failed",
        expected_source_uploaded=False,
        outcome=SourceUploadTransitionOutcome.FAILED,
    )
