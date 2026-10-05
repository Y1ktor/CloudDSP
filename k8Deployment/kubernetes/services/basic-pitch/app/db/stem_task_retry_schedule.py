"""Durably defer a transient pre-model Basic Pitch stem failure.

This module owns one narrow PostgreSQL transition for a current Basic Pitch
task whose *pre-model* dependency is temporarily unavailable: for example,
MinIO cannot be reached while verifying or downloading the claimed stem.  In
that situation the immutable input has not been disproved, so the task should
not be terminally failed.  Instead, a later recovery/dispatch component may
claim it again only after PostgreSQL's durable ``available_at`` time.

It deliberately has no PostgreSQL connection factory, exception classifier,
RabbitMQ operation, MinIO request, model invocation, sleep, worker loop,
image entrypoint, or Kubernetes action.  A later small composition task will
decide which reviewed transient exceptions may call this adapter, commit its
result, and arrange a new delivery after the durable delay.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.messaging.basic_pitch_requested_message import LOCAL_UPLOADS_BUCKET
from app.db.task_lease import (
    BASIC_PITCH_STEMS_BY_MODE,
    MAX_BASIC_PITCH_TASK_ATTEMPTS,
    BasicPitchTaskLease,
    DatabaseCursor,
)


# This durable delay is deliberately independent of the supervisor's short
# in-memory reconnect backoff.  A Pod restart must not erase it.  The bounds
# keep a future environment setting from creating a busy retry loop or silently
# parking a task for an unhelpfully long local-development interval.
DEFAULT_BASIC_PITCH_RETRY_AFTER_SECONDS = 30
MIN_BASIC_PITCH_RETRY_AFTER_SECONDS = 1
MAX_BASIC_PITCH_RETRY_AFTER_SECONDS = 3_600


class BasicPitchStemRetryScheduleCode(StrEnum):
    """The finite safe retry code allowed in ``processing_tasks.last_error_code``.

    This is an operational category, not a raw MinIO/SDK exception, object
    key, checksum, credential, stack trace, or user-facing error message.  It
    represents a temporary storage reachability failure only; permanent
    metadata/checksum inconsistencies belong to the terminal-failure adapter.
    """

    STORAGE_UNAVAILABLE = "basic_pitch_stem_storage_unavailable"


class BasicPitchStemRetryScheduleProtocolError(RuntimeError):
    """The lease, retry delay/code, or returned PostgreSQL evidence is invalid."""


@dataclass(frozen=True)
class BasicPitchStemRetrySchedule:
    """Committed evidence that a pre-model task is eligible for a later retry.

    The result exists only after a caller's short transaction commits.  It
    retains no bucket/key, checksum, raw backend response, lease token, or
    browser identity.  ``attempt_count`` is intentionally unchanged here: it
    records the attempt that just encountered the transient dependency failure;
    a future re-claim transaction owns incrementing it.
    """

    task_id: str
    job_id: str
    attempt_count: int
    failure_code: BasicPitchStemRetryScheduleCode
    available_at: datetime


# This statement changes only a still-current *pre-model* lease.  It clears
# the active ownership and sets a database-clock retry time, but leaves
# ``started_at`` and ``completed_at`` untouched: Basic Pitch never started and
# the task is neither complete nor terminal.  The attempt predicate is a
# second durable guard in addition to caller validation.  A task on its final
# allowed attempt must not be silently rescheduled forever; the later explicit
# exhaustion policy will choose its terminal outcome instead.
#
# Every immutable coordinate, the lease token, and PostgreSQL's clock are in
# the predicate.  A stale Pod therefore cannot overwrite recovery, another
# attempt, model progress, or a terminal result.  The statement never updates
# ``jobs``; a future aggregate remains the only owner of overall Job state.
SCHEDULE_LEASED_BASIC_PITCH_STEM_RETRY_SQL = """
    UPDATE public.processing_tasks AS task
    SET
      status = 'retry_scheduled',
      available_at = CURRENT_TIMESTAMP + (%s::integer * INTERVAL '1 second'),
      lease_token = NULL,
      lease_expires_at = NULL,
      last_error_code = %s
    WHERE task.task_id = %s::uuid
      AND task.job_id = %s::uuid
      AND task.stage = 'basic-pitch'
      AND task.stem_name = %s
      AND task.input_bucket = %s
      AND task.input_object_key = %s
      AND task.stem_mode = %s
      AND task.status = 'leased'
      AND task.attempt_count < %s::integer
      AND task.lease_token = %s::uuid
      AND task.lease_expires_at > CURRENT_TIMESTAMP
    RETURNING
      task.task_id::text AS task_id,
      task.job_id::text AS job_id,
      task.attempt_count,
      task.available_at,
      task.last_error_code
"""


def _error() -> BasicPitchStemRetryScheduleProtocolError:
    """Return one stable error without embedding private task evidence."""

    return BasicPitchStemRetryScheduleProtocolError("Basic Pitch retry schedule evidence is invalid.")


def _canonical_uuid(value: object) -> str:
    """Require canonical UUID text before using it as a guarded SQL parameter."""

    if not isinstance(value, str):
        raise _error()
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise _error() from error
    if canonical != value:
        raise _error()
    return canonical


def _validated_lease(value: object) -> BasicPitchTaskLease:
    """Require the complete immutable pre-model identity, not only a token."""

    if not isinstance(value, BasicPitchTaskLease):
        raise _error()
    job_id = _canonical_uuid(value.job_id)
    _canonical_uuid(value.task_id)
    _canonical_uuid(value.request_event_id)
    _canonical_uuid(value.lease_token)
    if (
        value.input_bucket != LOCAL_UPLOADS_BUCKET
        or value.stem_mode not in BASIC_PITCH_STEMS_BY_MODE
        or value.stem_name not in BASIC_PITCH_STEMS_BY_MODE[value.stem_mode]
        or value.input_object_key != f"stems/{job_id}/{value.stem_name}.wav"
        or type(value.attempt_count) is not int
        or not 1 <= value.attempt_count <= MAX_BASIC_PITCH_TASK_ATTEMPTS
    ):
        raise _error()
    return value


def _validated_retry_after_seconds(value: object) -> int:
    """Accept only a bounded, explicit whole-second durable retry delay."""

    if (
        type(value) is not int
        or not MIN_BASIC_PITCH_RETRY_AFTER_SECONDS <= value <= MAX_BASIC_PITCH_RETRY_AFTER_SECONDS
    ):
        raise _error()
    return value


def _validated_failure_code(value: object) -> BasicPitchStemRetryScheduleCode:
    """Forbid raw dependency diagnostics from becoming durable task state."""

    if not isinstance(value, BasicPitchStemRetryScheduleCode):
        raise _error()
    return value


def _returned_retry_schedule(
    row: Mapping[str, object],
    *,
    task_id: str,
    job_id: str,
    attempt_count: int,
    failure_code: BasicPitchStemRetryScheduleCode,
) -> BasicPitchStemRetrySchedule:
    """Validate only proof of the exact guarded retry scheduling transition."""

    returned_task_id = _canonical_uuid(row.get("task_id"))
    returned_job_id = _canonical_uuid(row.get("job_id"))
    returned_attempt_count = row.get("attempt_count")
    available_at = row.get("available_at")
    returned_code = row.get("last_error_code")
    if (
        returned_task_id != task_id
        or returned_job_id != job_id
        or returned_attempt_count != attempt_count
        or type(returned_attempt_count) is not int
        or not 1 <= returned_attempt_count < MAX_BASIC_PITCH_TASK_ATTEMPTS
        or returned_code != failure_code.value
        or not isinstance(available_at, datetime)
        or available_at.tzinfo is None
    ):
        raise _error()
    return BasicPitchStemRetrySchedule(
        task_id=task_id,
        job_id=job_id,
        attempt_count=attempt_count,
        failure_code=failure_code,
        available_at=available_at,
    )


def schedule_leased_basic_pitch_stem_retry(
    cursor: DatabaseCursor,
    *,
    lease: BasicPitchTaskLease,
    retry_after_seconds: int = DEFAULT_BASIC_PITCH_RETRY_AFTER_SECONDS,
    failure_code: BasicPitchStemRetryScheduleCode,
) -> BasicPitchStemRetrySchedule | None:
    """Durably defer one current transient pre-model failure, or return ``None``.

    ``None`` means the guarded retry was not scheduled.  It is deliberately
    non-specific: another owner may have recovered the task, the lease may
    have expired, the task may have reached a different state, or its final
    permitted attempt may have been reached.  The caller must stop rather than
    manufacture a second outcome.  A later bounded-attempt policy will handle
    that final-attempt case explicitly.

    A returned result is only SQL evidence.  The short outer transaction must
    commit before a later runtime reports this retry schedule or arranges a
    new delivery.
    """

    if not callable(getattr(cursor, "execute", None)) or not callable(getattr(cursor, "fetchone", None)):
        raise TypeError("cursor must provide execute and fetchone.")
    validated_lease = _validated_lease(lease)
    bounded_retry_seconds = _validated_retry_after_seconds(retry_after_seconds)
    validated_code = _validated_failure_code(failure_code)
    task_id = _canonical_uuid(validated_lease.task_id)
    job_id = _canonical_uuid(validated_lease.job_id)
    lease_token = _canonical_uuid(validated_lease.lease_token)

    cursor.execute(
        SCHEDULE_LEASED_BASIC_PITCH_STEM_RETRY_SQL,
        (
            bounded_retry_seconds,
            validated_code.value,
            task_id,
            job_id,
            validated_lease.stem_name,
            validated_lease.input_bucket,
            validated_lease.input_object_key,
            validated_lease.stem_mode,
            MAX_BASIC_PITCH_TASK_ATTEMPTS,
            lease_token,
        ),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    if not isinstance(row, Mapping):
        raise _error()
    return _returned_retry_schedule(
        row,
        task_id=task_id,
        job_id=job_id,
        attempt_count=validated_lease.attempt_count,
        failure_code=validated_code,
    )
