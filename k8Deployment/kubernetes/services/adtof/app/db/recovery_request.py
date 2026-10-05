"""Reconstruct strict ADTOF request evidence for one recovered active lease.

After a RabbitMQ delivery has been acknowledged, an expired active lease has no
raw AMQP frame to parse again. This read-only boundary instead rebuilds the
same ``ADTOFRequestedMessage`` from the matching immutable, published outbox
event. Its caller must invoke it immediately after the expired-lease claim in
the same short PostgreSQL transaction; otherwise a fresh recovery lease could
be committed without the evidence needed to run it safely.

The module does not claim work or open/commit a transaction. It does not
acknowledge/publish RabbitMQ, contact MinIO, run ADTOF, sleep, create a loop,
or make a Kubernetes action. A later composition will atomically join the
recovery claim and this reader before handing their committed pair to the
ordinary post-claim success coordinator.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final
from uuid import UUID

from app.messaging.adtof_requested_message import (
    ADTOF_REQUESTED_EVENT_TYPE,
    ADTOF_STAGE,
    ADTOF_STEM_NAME,
    LOCAL_UPLOADS_BUCKET,
    STEM_CONTENT_TYPE,
    ADTOFRequestedMessage,
)
from app.db.task_claim import (
    MAX_ADTOF_TASK_ATTEMPTS,
    ADTOFTaskClaimProtocolError,
    ADTOFTaskLease,
    DatabaseCursor,
    validate_adtof_requested_message,
)


class ADTOFRecoveryRequestProtocolError(RuntimeError):
    """A recovery lease or its durable outbox evidence is unsafe to execute.

    The stable public category deliberately omits raw JSONB, bucket/object
    keys, credentials, and driver diagnostics. A caller must roll back the
    shared recovery transaction rather than commit a lease without strict
    execution evidence.
    """


# This query requires the exact fresh recovery lease to remain current in
# PostgreSQL while it joins its immutable published event. It is a SELECT, not
# an extra lock or lifecycle mutation: the preceding candidate claim already
# holds the task row lock in the same short transaction. PostgreSQL's clock is
# authoritative for lease currency; local time is never used here.
READ_CURRENT_ADTOF_RECOVERY_REQUEST_SQL: Final[str] = """
    SELECT
      event.event_id::text AS event_id,
      event.job_id::text AS job_id,
      event.stage,
      event.stem_name,
      event.event_type,
      event.payload,
      event.publication_status
    FROM public.processing_tasks AS task
    JOIN public.outbox_events AS event
      ON event.event_id = task.request_event_id
      AND event.job_id = task.job_id
      AND event.stage = task.stage
      AND event.stem_name = task.stem_name
    WHERE task.task_id = %s::uuid
      AND task.job_id = %s::uuid
      AND task.stage = 'adtof'
      AND task.stem_name = 'drums'
      AND task.request_event_id = %s::uuid
      AND task.input_bucket = %s
      AND task.input_object_key = %s
      AND task.stem_mode = %s
      AND task.status = 'leased'
      AND task.attempt_count = %s::integer
      AND task.lease_token = %s::uuid
      AND task.lease_expires_at > CURRENT_TIMESTAMP
      AND event.event_type = 'adtof.requested'
      AND event.publication_status = 'published'
"""


def _error() -> ADTOFRecoveryRequestProtocolError:
    """Return one redacted protocol category for all unsafe recovery evidence."""

    return ADTOFRecoveryRequestProtocolError("ADTOF recovery evidence is invalid.")


def _canonical_uuid(value: object) -> str:
    """Require canonical lower-case UUID text before it enters an SQL predicate."""

    if not isinstance(value, str):
        raise _error()
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise _error() from error
    if canonical != value:
        raise _error()
    return canonical


def _text(row: Mapping[str, object], name: str) -> str:
    """Read safe required text without including the source value in an error."""

    value = row.get(name)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise _error()
    return value


def _validated_recovery_lease(value: object) -> ADTOFTaskLease:
    """Accept only attempt-two/three recovery ownership from the claim query."""

    if not isinstance(value, ADTOFTaskLease):
        raise _error()
    job_id = _canonical_uuid(value.job_id)
    _canonical_uuid(value.task_id)
    _canonical_uuid(value.request_event_id)
    _canonical_uuid(value.lease_token)
    if (
        value.stem_name != ADTOF_STEM_NAME
        or value.stem_mode not in {"4-stems", "6-stems"}
        or value.input_bucket != LOCAL_UPLOADS_BUCKET
        or value.input_object_key != f"stems/{job_id}/{ADTOF_STEM_NAME}.wav"
        or type(value.attempt_count) is not int
        or not 2 <= value.attempt_count <= MAX_ADTOF_TASK_ATTEMPTS
    ):
        raise _error()
    return value


def _message_from_row(row: Mapping[str, object], *, lease: ADTOFTaskLease) -> ADTOFRequestedMessage:
    """Rebuild parser-equivalent message evidence from one immutable JSONB row."""

    event_id = _canonical_uuid(_text(row, "event_id"))
    job_id = _canonical_uuid(_text(row, "job_id"))
    stem_name = _text(row, "stem_name")
    if (
        event_id != lease.request_event_id
        or job_id != lease.job_id
        or stem_name != lease.stem_name
        or _text(row, "stage") != ADTOF_STAGE
        or _text(row, "event_type") != ADTOF_REQUESTED_EVENT_TYPE
        or _text(row, "publication_status") != "published"
    ):
        raise _error()

    payload = row.get("payload")
    if not isinstance(payload, Mapping) or set(payload) != {
        "schema_version",
        "job_id",
        "stem_name",
        "stem",
    }:
        raise _error()
    stem = payload.get("stem")
    if not isinstance(stem, Mapping) or set(stem) != {
        "bucket",
        "object_key",
        "content_type",
        "size_bytes",
        "sha256",
    }:
        raise _error()
    if (
        type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
        or payload.get("job_id") != job_id
        or payload.get("stem_name") != ADTOF_STEM_NAME
        or stem.get("bucket") != lease.input_bucket
        or stem.get("object_key") != lease.input_object_key
        or stem.get("content_type") != STEM_CONTENT_TYPE
        or type(stem.get("size_bytes")) is not int
    ):
        raise _error()

    try:
        return validate_adtof_requested_message(
            ADTOFRequestedMessage(
                event_id=event_id,
                job_id=job_id,
                stem_name=ADTOF_STEM_NAME,
                stem_bucket=lease.input_bucket,
                stem_object_key=lease.input_object_key,
                stem_content_length=stem["size_bytes"],
                stem_sha256=stem.get("sha256"),
            )
        )
    except ADTOFTaskClaimProtocolError as error:
        raise _error() from error


def read_current_adtof_recovery_request(
    cursor: DatabaseCursor,
    *,
    lease: ADTOFTaskLease,
) -> ADTOFRequestedMessage | None:
    """Return strict request evidence for a current fresh recovery lease.

    `None` is normal ownership loss: another recovery, terminal transition, or
    lease expiry changed the task before this query ran. Callers must stop
    without contacting MinIO or starting ADTOF. Any malformed lease/event row
    raises so the surrounding recovery transaction rolls back rather than
    committing an unusable fresh lease.
    """

    if not callable(getattr(cursor, "execute", None)) or not callable(getattr(cursor, "fetchone", None)):
        raise TypeError("cursor must provide execute and fetchone.")
    validated_lease = _validated_recovery_lease(lease)
    task_id = _canonical_uuid(validated_lease.task_id)
    job_id = _canonical_uuid(validated_lease.job_id)
    request_event_id = _canonical_uuid(validated_lease.request_event_id)
    lease_token = _canonical_uuid(validated_lease.lease_token)
    cursor.execute(
        READ_CURRENT_ADTOF_RECOVERY_REQUEST_SQL,
        (
            task_id,
            job_id,
            request_event_id,
            validated_lease.input_bucket,
            validated_lease.input_object_key,
            validated_lease.stem_mode,
            validated_lease.attempt_count,
            lease_token,
        ),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    if not isinstance(row, Mapping):
        raise _error()
    return _message_from_row(row, lease=validated_lease)
