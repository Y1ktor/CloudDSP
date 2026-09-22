"""Reconstruct strict Demucs input evidence for one recovered task lease.

RabbitMQ was already acknowledged when the original ``demucs.requested``
delivery first acquired a durable task.  A due retry or expired lease therefore
has no new AMQP frame to parse.  This read-only PostgreSQL boundary rebuilds a
normal :class:`DemucsRequestedMessage` from the exact immutable, *published*
outbox event that originally authorized the task.

The caller must run this query immediately after the due/expired claim in the
same short PostgreSQL transaction.  This module does not claim or start a
task, open/commit a transaction, acknowledge/publish RabbitMQ, read MinIO, run
Demucs, sleep, or make a Kubernetes action.  A later composition will commit
only a matched lease/evidence pair, then send that pair through the ordinary
pre-model execution path.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final
from uuid import UUID

from app.demucs_requested_message import (
    DEMUCS_REQUESTED_EVENT_TYPE,
    DemucsRequestContractError,
    DemucsRequestedMessage,
    validate_demucs_requested_message,
)
from app.task_lease import MAX_DEMUCS_TASK_ATTEMPTS, DatabaseCursor, DemucsTaskLease


class DemucsRecoveryRequestProtocolError(RuntimeError):
    """The recovered lease or its durable authorization evidence is unsafe.

    This category deliberately omits private source keys, raw JSONB, database
    diagnostics, credentials, and tenant identity.  Its later transaction
    composition must roll back rather than commit a lease it cannot execute
    from proven inputs.
    """


# The recovery claim already owns the candidate row with `FOR UPDATE SKIP
# LOCKED`.  This SELECT rebinds every lease coordinate before joining the
# immutable event, so an expired/stale Pod cannot execute after another owner
# changed the task.  `published` is mandatory: an outbox record that the
# dispatcher never confirmed to RabbitMQ is not valid recovery authorization.
#
# This remains a SELECT—not another lock or lifecycle mutation.  PostgreSQL's
# clock, not the Pod clock, decides whether the freshly issued lease remains
# current.  The later `leased -> running` transition is still the final guard
# before source storage or a model process can be used.
READ_CURRENT_DEMUCS_RECOVERY_REQUEST_SQL: Final[str] = """
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
      AND task.stage = 'demucs'
      AND task.stem_name = ''
      AND task.request_event_id = %s::uuid
      AND task.input_bucket = %s
      AND task.input_object_key = %s
      AND task.stem_mode = %s
      AND task.status = 'leased'
      AND task.attempt_count = %s::integer
      AND task.lease_token = %s::uuid
      AND task.lease_expires_at > CURRENT_TIMESTAMP
      AND event.event_type = 'demucs.requested'
      AND event.publication_status = 'published'
"""


def _error() -> DemucsRecoveryRequestProtocolError:
    """Return one redacted category for all unusable recovery evidence."""

    return DemucsRecoveryRequestProtocolError("Demucs recovery evidence is invalid.")


def _canonical_uuid(value: object) -> str:
    """Require canonical lower-case UUID text before using it in a query."""

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
    """Read required non-empty, NUL-free row text without reporting its value."""

    value = row.get(name)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise _error()
    return value


def _validated_recovery_lease(value: object) -> DemucsTaskLease:
    """Accept only a fresh recovery lease, never a first AMQP attempt.

    Attempt one belongs to the delivery parser/first-claim path.  A recovery
    lease starts at attempt two; enforcing that boundary prevents a caller from
    bypassing the AMQP contract by handing this reader a first-attempt lease.
    SQL checks current time, so this function intentionally does not create a
    competing local-clock definition of lease expiry.
    """

    if not isinstance(value, DemucsTaskLease):
        raise _error()
    job_id = _canonical_uuid(value.job_id)
    _canonical_uuid(value.task_id)
    _canonical_uuid(value.request_event_id)
    _canonical_uuid(value.lease_token)
    if (
        type(value.attempt_count) is not int
        or not 2 <= value.attempt_count <= MAX_DEMUCS_TASK_ATTEMPTS
    ):
        raise _error()
    try:
        # Validate all source fields before this function binds them as SQL
        # parameters.  A recovery lease is database evidence, but its values
        # still must obey the exact same local object-coordinate contract as a
        # first AMQP request; annotations alone provide no runtime type guard.
        validate_demucs_requested_message(
            DemucsRequestedMessage(
                event_id=value.request_event_id,
                job_id=job_id,
                source_bucket=value.input_bucket,
                source_object_key=value.input_object_key,
                stem_mode=value.stem_mode,
            )
        )
    except DemucsRequestContractError as error:
        raise _error() from error
    return value


def _message_from_row(row: Mapping[str, object], *, lease: DemucsTaskLease) -> DemucsRequestedMessage:
    """Rebuild parser-equivalent request evidence from one outbox JSONB record."""

    event_id = _canonical_uuid(_text(row, "event_id"))
    job_id = _canonical_uuid(_text(row, "job_id"))
    if (
        event_id != lease.request_event_id
        or job_id != lease.job_id
        or _text(row, "stage") != "demucs"
        or row.get("stem_name") != ""
        or _text(row, "event_type") != DEMUCS_REQUESTED_EVENT_TYPE
        or _text(row, "publication_status") != "published"
    ):
        raise _error()

    payload = row.get("payload")
    if not isinstance(payload, Mapping) or set(payload) != {
        "schema_version",
        "job_id",
        "source",
        "stem_mode",
    }:
        raise _error()
    source = payload.get("source")
    if not isinstance(source, Mapping) or set(source) != {"bucket", "object_key"}:
        raise _error()
    if (
        type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
        or payload.get("job_id") != job_id
        or source.get("bucket") != lease.input_bucket
        or source.get("object_key") != lease.input_object_key
        or payload.get("stem_mode") != lease.stem_mode
    ):
        raise _error()

    try:
        # This uses the same UUID, bucket/key, and stem-mode contract as a
        # first AMQP delivery.  The row above proves where these values came
        # from; the shared validator proves their values remain safe to use.
        return validate_demucs_requested_message(
            DemucsRequestedMessage(
                event_id=event_id,
                job_id=job_id,
                source_bucket=lease.input_bucket,
                source_object_key=lease.input_object_key,
                stem_mode=lease.stem_mode,
            )
        )
    except DemucsRequestContractError as error:
        raise _error() from error


def read_current_demucs_recovery_request(
    cursor: DatabaseCursor,
    *,
    lease: DemucsTaskLease,
) -> DemucsRequestedMessage | None:
    """Return strict request evidence for one current fresh recovery lease.

    ``None`` is normal ownership loss: PostgreSQL found no matching unexpired
    `leased` task/event pair for this exact token.  The caller must stop without
    contacting MinIO or starting Demucs.  A malformed lease or row raises so
    its surrounding short recovery transaction rolls back, leaving no new
    lease stranded without recoverable execution evidence.
    """

    if not callable(getattr(cursor, "execute", None)) or not callable(getattr(cursor, "fetchone", None)):
        raise TypeError("cursor must provide execute and fetchone.")
    validated_lease = _validated_recovery_lease(lease)
    task_id = _canonical_uuid(validated_lease.task_id)
    job_id = _canonical_uuid(validated_lease.job_id)
    request_event_id = _canonical_uuid(validated_lease.request_event_id)
    lease_token = _canonical_uuid(validated_lease.lease_token)
    cursor.execute(
        READ_CURRENT_DEMUCS_RECOVERY_REQUEST_SQL,
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
