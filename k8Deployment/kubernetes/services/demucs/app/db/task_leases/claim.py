"""Classify one delivery and acquire its first durable Demucs lease.

The ordered task/Job/task reads and published-outbox identity checks belong
with this operation's SQL. The outer composition owns the short transaction
and may acknowledge RabbitMQ only after this decision commits. This adapter
performs no broker, object-storage, model, or Kubernetes operation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Callable
from uuid import UUID, uuid4

from app.messaging.demucs_requested_message import DemucsRequestedMessage

from .contracts import (
    DEFAULT_DEMUCS_LEASE_SECONDS,
    DatabaseCursor,
    DemucsStaleRequestReason,
    DemucsTaskClaimDisposition,
    DemucsTaskClaimInconsistency,
    DemucsTaskClaimResult,
    DemucsTaskLeaseProtocolError,
)
from .validation import (
    _canonical_uuid,
    _mapping_or_error,
    _new_canonical_uuid,
    _row_lease,
    _row_text,
    _validated_lease_seconds,
)


# Lock an existing task first. When no task exists, the caller locks the Job
# row, then repeats this query: the second read closes the race where another
# worker inserted the task while this worker waited for the Job lock.
LOCK_EXISTING_DEMUCS_TASK_SQL = """
    SELECT
      task_id::text AS task_id,
      job_id::text AS job_id,
      request_event_id::text AS request_event_id,
      status,
      attempt_count,
      input_bucket,
      input_object_key,
      stem_mode,
      lease_token::text AS lease_token,
      lease_expires_at
    FROM public.processing_tasks
    WHERE job_id = %s::uuid
      AND stage = 'demucs'
      AND stem_name = ''
    FOR UPDATE
"""


# A Job lock serializes a first task claim with an intake/delete/result update.
# ``is_retained`` is computed by PostgreSQL's clock, avoiding a Pod-clock
# decision about expiry. The later parser already rejected arbitrary keys; this
# query repeats every durable identity check before a task row is inserted.
LOCK_JOB_FOR_DEMUCS_CLAIM_SQL = """
    SELECT
      job_id::text AS job_id,
      input_bucket,
      input_object_key,
      source_uploaded,
      stem_mode,
      status,
      revision,
      (expires_at > CURRENT_TIMESTAMP) AS is_retained
    FROM public.jobs
    WHERE job_id = %s::uuid
    FOR UPDATE
"""


# Outbox publication is monotonic after the dispatcher confirms RabbitMQ, so a
# normal read is sufficient. The Demucs role has SELECT only on these identity
# columns—not payload—and cannot change dispatch history or publish work.
READ_PUBLISHED_DEMUCS_OUTBOX_SQL = """
    SELECT
      event_id::text AS event_id,
      job_id::text AS job_id,
      stage,
      stem_name,
      event_type,
      publication_status
    FROM public.outbox_events
    WHERE event_id = %s::uuid
"""


# The first row begins leased but not running: a later source-validation/model
# adapter will switch it to running after its own guarded transition. The SQL
# calculates time inside PostgreSQL and returns the exact generated lease view.
INSERT_FIRST_DEMUCS_TASK_LEASE_SQL = """
    INSERT INTO public.processing_tasks (
      task_id,
      job_id,
      stage,
      stem_name,
      request_event_id,
      input_bucket,
      input_object_key,
      stem_mode,
      status,
      attempt_count,
      available_at,
      lease_token,
      lease_expires_at,
      started_at,
      completed_at,
      last_error_code
    )
    VALUES (
      %s::uuid,
      %s::uuid,
      'demucs',
      '',
      %s::uuid,
      %s,
      %s,
      %s,
      'leased',
      1,
      CURRENT_TIMESTAMP,
      %s::uuid,
      CURRENT_TIMESTAMP + (%s::integer * INTERVAL '1 second'),
      NULL,
      NULL,
      NULL
    )
    RETURNING
      task_id::text AS task_id,
      job_id::text AS job_id,
      request_event_id::text AS request_event_id,
      input_bucket,
      input_object_key,
      stem_mode,
      status,
      attempt_count,
      lease_token::text AS lease_token,
      lease_expires_at
"""


def _existing_task_result(row: Mapping[str, object], *, message: DemucsRequestedMessage) -> DemucsTaskClaimResult:
    """Classify a pre-existing canonical task without mutating duplicate state."""

    if _canonical_uuid(_row_text(row, "job_id")) != message.job_id:
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    if _canonical_uuid(_row_text(row, "request_event_id")) != message.event_id:
        # One job/stage may map only to its one immutable outbox event. Treat a
        # corrupt or spoofed mismatch as unsafe—not as an innocent duplicate.
        raise DemucsTaskClaimInconsistency("Demucs request conflicts with its durable task.")
    status = _row_text(row, "status")
    if status not in {"leased", "running", "retry_scheduled", "succeeded", "failed"}:
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return DemucsTaskClaimResult(
        disposition=DemucsTaskClaimDisposition.DUPLICATE,
        duplicate_status=status,
    )


def _validate_locked_job(row: Mapping[str, object], *, message: DemucsRequestedMessage) -> DemucsStaleRequestReason | None:
    """Return a safe stale reason or raise when an active Job disagrees with AMQP."""

    status = _row_text(row, "status")
    if row.get("is_retained") is not True:
        return DemucsStaleRequestReason.JOB_EXPIRED
    if status in {"completed", "failed"}:
        return DemucsStaleRequestReason.JOB_TERMINAL
    if (
        row.get("source_uploaded") is not True
        or status != "source_uploaded"
        or _canonical_uuid(_row_text(row, "job_id")) != message.job_id
        or _row_text(row, "input_bucket") != message.source_bucket
        or _row_text(row, "input_object_key") != message.source_object_key
        or _row_text(row, "stem_mode") != message.stem_mode
    ):
        raise DemucsTaskClaimInconsistency("Demucs request conflicts with its durable Job.")
    return None


def _validate_outbox_event(row: Mapping[str, object] | None, *, message: DemucsRequestedMessage) -> None:
    """Require the matching immutable outbox event to be broker-confirmed published."""

    event = _mapping_or_error(row)
    if (
        _canonical_uuid(_row_text(event, "event_id")) != message.event_id
        or _canonical_uuid(_row_text(event, "job_id")) != message.job_id
        or _row_text(event, "stage") != "demucs"
        or event.get("stem_name") != ""
        or _row_text(event, "event_type") != "demucs.requested"
        or _row_text(event, "publication_status") != "published"
    ):
        raise DemucsTaskClaimInconsistency("Demucs request conflicts with its durable outbox event.")


def claim_demucs_task_for_delivery(
    cursor: DatabaseCursor,
    *,
    message: DemucsRequestedMessage,
    lease_seconds: int = DEFAULT_DEMUCS_LEASE_SECONDS,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> DemucsTaskClaimResult:
    """Create one first task lease or classify duplicate/stale delivery safely.

    The caller must invoke this inside one short database transaction and commit
    a ``CLAIMED`` lease *before* acknowledging RabbitMQ. On duplicate/stale
    outcomes it also commits no mutation, then may acknowledge. An inconsistency
    deliberately raises because a later terminal-transition adapter must record
    a safe durable failure before acknowledgement.
    """

    if not isinstance(message, DemucsRequestedMessage):
        raise TypeError("message must be DemucsRequestedMessage.")
    bounded_lease_seconds = _validated_lease_seconds(lease_seconds)

    cursor.execute(LOCK_EXISTING_DEMUCS_TASK_SQL, (message.job_id,))
    existing_task = cursor.fetchone()
    if existing_task is not None:
        return _existing_task_result(_mapping_or_error(existing_task), message=message)

    # A missing task does not lock a gap. Locking the Job serializes first-task
    # creation, then repeating the task read observes work inserted while this
    # transaction waited on that Job lock.
    cursor.execute(LOCK_JOB_FOR_DEMUCS_CLAIM_SQL, (message.job_id,))
    locked_job = cursor.fetchone()
    if locked_job is None:
        return DemucsTaskClaimResult(
            disposition=DemucsTaskClaimDisposition.STALE,
            stale_reason=DemucsStaleRequestReason.JOB_MISSING,
        )

    cursor.execute(LOCK_EXISTING_DEMUCS_TASK_SQL, (message.job_id,))
    existing_after_job_lock = cursor.fetchone()
    if existing_after_job_lock is not None:
        return _existing_task_result(_mapping_or_error(existing_after_job_lock), message=message)

    stale_reason = _validate_locked_job(_mapping_or_error(locked_job), message=message)
    if stale_reason is not None:
        return DemucsTaskClaimResult(
            disposition=DemucsTaskClaimDisposition.STALE,
            stale_reason=stale_reason,
        )

    cursor.execute(READ_PUBLISHED_DEMUCS_OUTBOX_SQL, (message.event_id,))
    _validate_outbox_event(cursor.fetchone(), message=message)

    task_id = _new_canonical_uuid(uuid_factory)
    lease_token = _new_canonical_uuid(uuid_factory)
    cursor.execute(
        INSERT_FIRST_DEMUCS_TASK_LEASE_SQL,
        (
            task_id,
            message.job_id,
            message.event_id,
            message.source_bucket,
            message.source_object_key,
            message.stem_mode,
            lease_token,
            bounded_lease_seconds,
        ),
    )
    claimed_lease = _row_lease(_mapping_or_error(cursor.fetchone()))
    if (
        claimed_lease.task_id != task_id
        or claimed_lease.job_id != message.job_id
        or claimed_lease.request_event_id != message.event_id
        or claimed_lease.lease_token != lease_token
        or claimed_lease.attempt_count != 1
    ):
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return DemucsTaskClaimResult(
        disposition=DemucsTaskClaimDisposition.CLAIMED,
        lease=claimed_lease,
    )
