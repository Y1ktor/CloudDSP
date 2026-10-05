"""Make one lease-token-guarded durable decision for a Demucs source failure.

``source_failure_classification.py`` says whether a source failure is known to
be immutable or temporarily unavailable. This module owns the next, equally
narrow step: its parameterized PostgreSQL statement turns that classification
into one durable outcome while the task is still ``leased``.

* a first or second transient MinIO outage becomes ``retry_scheduled`` at a
  PostgreSQL-clock time 30 seconds later; and
* a permanent source rejection, or a third transient outage, atomically marks
  both the Demucs task and its authoritative Job ``failed``.

The task and Job state changes share one SQL statement so a browser can never
see a failed Job with an active Demucs task, nor a terminal task whose only
Job still advertises it as processable. Every branch requires the exact task,
Job, source coordinate, attempt number, current lease token, and an unexpired
PostgreSQL lease. A stale Pod therefore receives no row and must stop.

This is deliberately a pure cursor adapter. It does not create a connection or
commit a transaction; it does not catch execution exceptions; and it makes no
MinIO, RabbitMQ, FFprobe, Demucs, sleep, worker-loop, image, or Kubernetes
operation. A later small composition owns the short commit/rollback scope and
the one-task runtime's exception handoff.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.artifacts.demucs_artifacts import DEMUCS_STEMS_BY_MODE
from app.runtime.source_failure_classification import (
    DemucsPreModelFailureClassification,
    DemucsPreModelFailureDisposition,
    DemucsPreModelRetryCode,
    DemucsPreModelTerminalFailureCode,
)
from app.artifacts.source_object import LOCAL_UPLOADS_BUCKET
from app.db.task_lease import MAX_DEMUCS_TASK_ATTEMPTS, DatabaseCursor, DemucsTaskLease


# PostgreSQL—not a sleeping Pod—owns this delay. The lower bound avoids a busy
# recovery loop, while the upper bound prevents a future local configuration
# typo from parking recoverable work for an unexpectedly long period.
DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS = 30
MIN_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS = 1
MAX_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS = 3_600


class DemucsPreModelRetryExhaustionCode(StrEnum):
    """Safe terminal code when all allowed source-storage attempts were spent."""

    STORAGE_UNAVAILABLE = "demucs_source_storage_retry_exhausted"


class DemucsPreModelFailureTransitionDisposition(StrEnum):
    """The two durable outcomes a reviewed pre-model error may produce."""

    RETRY_SCHEDULED = "retry_scheduled"
    TERMINAL_FAILURE = "terminal_failure"


class DemucsPreModelFailureTransitionProtocolError(RuntimeError):
    """The supplied lease/classification or returned SQL evidence is invalid."""


@dataclass(frozen=True)
class DemucsPreModelRetrySchedule:
    """Committed-proof shape for one later recovery-eligible Demucs attempt.

    This carries no source key, lease token, SDK detail, or exception text. The
    task's unchanged ``attempt_count`` is the attempt that encountered the
    outage; recovery owns incrementing it when it later grants a new lease.
    """

    task_id: str
    job_id: str
    attempt_count: int
    failure_code: DemucsPreModelRetryCode
    available_at: datetime


@dataclass(frozen=True)
class DemucsPreModelTerminalFailure:
    """Committed-proof shape for a terminal task/Job result from source work."""

    task_id: str
    job_id: str
    attempt_count: int
    failure_code: DemucsPreModelTerminalFailureCode | DemucsPreModelRetryExhaustionCode
    completed_at: datetime
    job_revision: int


@dataclass(frozen=True)
class DemucsPreModelFailureTransition:
    """One durable result exposed only after an outer transaction commits."""

    disposition: DemucsPreModelFailureTransitionDisposition
    retry_schedule: DemucsPreModelRetrySchedule | None = None
    terminal_failure: DemucsPreModelTerminalFailure | None = None

    def __post_init__(self) -> None:
        """Require exactly one evidence type for the selected durable result."""

        if self.disposition is DemucsPreModelFailureTransitionDisposition.RETRY_SCHEDULED:
            if not isinstance(self.retry_schedule, DemucsPreModelRetrySchedule) or self.terminal_failure is not None:
                raise TypeError("A scheduled Demucs retry requires only retry evidence.")
            return
        if self.disposition is DemucsPreModelFailureTransitionDisposition.TERMINAL_FAILURE:
            if self.retry_schedule is not None or not isinstance(
                self.terminal_failure,
                DemucsPreModelTerminalFailure,
            ):
                raise TypeError("A terminal Demucs source failure requires only terminal evidence.")
            return
        raise TypeError("Demucs source-failure transition disposition is invalid.")


# A source check happens before the worker earns permission to start Demucs, so
# only a current ``leased`` task can use this retry path. Locking its Job for
# the same short statement also proves the request remains retained and in the
# exact source-uploaded state. This prevents an expired/deleted/advanced Job
# from gaining a fresh recoverable task state after its browser-visible history
# is no longer processable.
SCHEDULE_LEASED_DEMUCS_PRE_MODEL_RETRY_SQL = """
    WITH locked_job AS MATERIALIZED (
      SELECT job_id
      FROM public.jobs
      WHERE job_id = %s::uuid
        AND source_uploaded = TRUE
        AND status = 'source_uploaded'
        AND expires_at > CURRENT_TIMESTAMP
      FOR UPDATE
    )
    UPDATE public.processing_tasks AS task
    SET
      status = 'retry_scheduled',
      available_at = CURRENT_TIMESTAMP + (%s::integer * INTERVAL '1 second'),
      lease_token = NULL,
      lease_expires_at = NULL,
      last_error_code = %s
    FROM locked_job
    WHERE task.task_id = %s::uuid
      AND task.job_id = locked_job.job_id
      AND task.stage = 'demucs'
      AND task.stem_name = ''
      AND task.input_bucket = %s
      AND task.input_object_key = %s
      AND task.stem_mode = %s
      AND task.status = 'leased'
      AND task.attempt_count = %s::integer
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


# Permanent source evidence and exhausted transient storage retries both end
# the only Demucs stage in version 1. `locked_job` serializes this statement
# with completion/deletion/intake changes. `failed_task` has the full current
# lease predicate, then `failed_job` depends on that exact returned row. Thus a
# permission/constraint failure rolls back both updates, and a stale lease
# returns no rows without rewriting newer durable state.
FAIL_LEASED_DEMUCS_PRE_MODEL_TASK_AND_JOB_SQL = """
    WITH locked_job AS MATERIALIZED (
      SELECT job_id
      FROM public.jobs
      WHERE job_id = %s::uuid
        AND source_uploaded = TRUE
        AND status = 'source_uploaded'
        AND expires_at > CURRENT_TIMESTAMP
      FOR UPDATE
    ),
    failed_task AS (
      UPDATE public.processing_tasks AS task
      SET
        status = 'failed',
        available_at = CURRENT_TIMESTAMP,
        lease_token = NULL,
        lease_expires_at = NULL,
        completed_at = CURRENT_TIMESTAMP,
        last_error_code = %s
      FROM locked_job
      WHERE task.task_id = %s::uuid
        AND task.job_id = locked_job.job_id
        AND task.stage = 'demucs'
        AND task.stem_name = ''
        AND task.input_bucket = %s
        AND task.input_object_key = %s
        AND task.stem_mode = %s
        AND task.status = 'leased'
        AND task.attempt_count = %s::integer
        AND task.lease_token = %s::uuid
        AND task.lease_expires_at > CURRENT_TIMESTAMP
      RETURNING
        task.task_id::text AS task_id,
        task.job_id::text AS job_id,
        task.attempt_count,
        task.completed_at,
        task.last_error_code
    ),
    failed_job AS (
      UPDATE public.jobs AS job
      SET
        status = 'failed',
        revision = job.revision + 1,
        error_message = %s
      FROM locked_job, failed_task
      WHERE job.job_id = locked_job.job_id
        AND job.job_id = failed_task.job_id
      RETURNING
        job.job_id::text AS job_id,
        job.revision,
        job.status,
        job.error_message
    )
    SELECT
      failed_task.task_id,
      failed_task.job_id,
      failed_task.attempt_count,
      failed_task.completed_at,
      failed_task.last_error_code,
      failed_job.revision AS job_revision,
      failed_job.status AS job_status,
      failed_job.error_message
    FROM failed_task
    JOIN failed_job ON failed_job.job_id = failed_task.job_id
"""


def _error() -> DemucsPreModelFailureTransitionProtocolError:
    """Return one stable error that does not expose task or database details."""

    return DemucsPreModelFailureTransitionProtocolError("Demucs pre-model failure transition is invalid.")


def _canonical_uuid(value: object) -> str:
    """Require canonical UUID text before binding it to a guarded SQL predicate."""

    if not isinstance(value, str):
        raise _error()
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise _error() from error
    if canonical != value:
        raise _error()
    return canonical


def _validated_lease(value: object) -> DemucsTaskLease:
    """Require every immutable source/ownership coordinate before a state write."""

    if not isinstance(value, DemucsTaskLease):
        raise _error()
    job_id = _canonical_uuid(value.job_id)
    _canonical_uuid(value.task_id)
    _canonical_uuid(value.request_event_id)
    _canonical_uuid(value.lease_token)
    source_name = value.input_object_key.removeprefix(f"uploads/{job_id}/") if isinstance(value.input_object_key, str) else ""
    if (
        value.input_bucket != LOCAL_UPLOADS_BUCKET
        or not source_name
        or "/" in source_name
        or value.input_object_key != f"uploads/{job_id}/{source_name}"
        or not isinstance(value.stem_mode, str)
        or value.stem_mode not in DEMUCS_STEMS_BY_MODE
        or type(value.attempt_count) is not int
        or not 1 <= value.attempt_count <= MAX_DEMUCS_TASK_ATTEMPTS
        or not isinstance(value.lease_expires_at, datetime)
        or value.lease_expires_at.tzinfo is None
    ):
        raise _error()
    return value


def _validated_retry_after_seconds(value: object) -> int:
    """Allow only a bounded whole-second PostgreSQL retry delay."""

    if (
        type(value) is not int
        or not MIN_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS <= value <= MAX_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS
    ):
        raise _error()
    return value


def _validated_classification(value: object) -> DemucsPreModelFailureClassification:
    """Forbid unclassified/forged output from reaching durable task state."""

    if not isinstance(value, DemucsPreModelFailureClassification):
        raise _error()
    if value.disposition not in {
        DemucsPreModelFailureDisposition.TERMINAL_FAILURE,
        DemucsPreModelFailureDisposition.RETRY_SCHEDULED,
    }:
        raise _error()
    return value


def _returned_retry_schedule(
    row: Mapping[str, object],
    *,
    lease: DemucsTaskLease,
    failure_code: DemucsPreModelRetryCode,
) -> DemucsPreModelRetrySchedule:
    """Validate the exact row returned by a guarded retry schedule statement."""

    task_id = _canonical_uuid(row.get("task_id"))
    job_id = _canonical_uuid(row.get("job_id"))
    attempt_count = row.get("attempt_count")
    available_at = row.get("available_at")
    if (
        task_id != lease.task_id
        or job_id != lease.job_id
        or attempt_count != lease.attempt_count
        or type(attempt_count) is not int
        or not 1 <= attempt_count < MAX_DEMUCS_TASK_ATTEMPTS
        or row.get("last_error_code") != failure_code.value
        or not isinstance(available_at, datetime)
        or available_at.tzinfo is None
    ):
        raise _error()
    return DemucsPreModelRetrySchedule(
        task_id=task_id,
        job_id=job_id,
        attempt_count=attempt_count,
        failure_code=failure_code,
        available_at=available_at,
    )


def _returned_terminal_failure(
    row: Mapping[str, object],
    *,
    lease: DemucsTaskLease,
    failure_code: DemucsPreModelTerminalFailureCode | DemucsPreModelRetryExhaustionCode,
) -> DemucsPreModelTerminalFailure:
    """Validate atomic task/Job terminal evidence without retaining raw errors."""

    task_id = _canonical_uuid(row.get("task_id"))
    job_id = _canonical_uuid(row.get("job_id"))
    attempt_count = row.get("attempt_count")
    completed_at = row.get("completed_at")
    job_revision = row.get("job_revision")
    if (
        task_id != lease.task_id
        or job_id != lease.job_id
        or attempt_count != lease.attempt_count
        or type(attempt_count) is not int
        or row.get("last_error_code") != failure_code.value
        or not isinstance(completed_at, datetime)
        or completed_at.tzinfo is None
        or type(job_revision) is not int
        or job_revision < 1
        or row.get("job_status") != "failed"
        or row.get("error_message") != failure_code.value
    ):
        raise _error()
    return DemucsPreModelTerminalFailure(
        task_id=task_id,
        job_id=job_id,
        attempt_count=attempt_count,
        failure_code=failure_code,
        completed_at=completed_at,
        job_revision=job_revision,
    )


def _schedule_retry(
    cursor: DatabaseCursor,
    *,
    lease: DemucsTaskLease,
    retry_after_seconds: int,
    failure_code: DemucsPreModelRetryCode,
) -> DemucsPreModelRetrySchedule | None:
    """Schedule only a current non-final ``leased`` task for due recovery."""

    cursor.execute(
        SCHEDULE_LEASED_DEMUCS_PRE_MODEL_RETRY_SQL,
        (
            lease.job_id,
            retry_after_seconds,
            failure_code.value,
            lease.task_id,
            lease.input_bucket,
            lease.input_object_key,
            lease.stem_mode,
            lease.attempt_count,
            MAX_DEMUCS_TASK_ATTEMPTS,
            lease.lease_token,
        ),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    if not isinstance(row, Mapping):
        raise _error()
    return _returned_retry_schedule(row, lease=lease, failure_code=failure_code)


def _fail_task_and_job(
    cursor: DatabaseCursor,
    *,
    lease: DemucsTaskLease,
    failure_code: DemucsPreModelTerminalFailureCode | DemucsPreModelRetryExhaustionCode,
) -> DemucsPreModelTerminalFailure | None:
    """Terminally fail one exact lease and its Job together, or return no row."""

    cursor.execute(
        FAIL_LEASED_DEMUCS_PRE_MODEL_TASK_AND_JOB_SQL,
        (
            lease.job_id,
            failure_code.value,
            lease.task_id,
            lease.input_bucket,
            lease.input_object_key,
            lease.stem_mode,
            lease.attempt_count,
            lease.lease_token,
            failure_code.value,
        ),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    if not isinstance(row, Mapping):
        raise _error()
    return _returned_terminal_failure(row, lease=lease, failure_code=failure_code)


def transition_leased_demucs_pre_model_failure(
    cursor: DatabaseCursor,
    *,
    lease: DemucsTaskLease,
    classification: DemucsPreModelFailureClassification,
    retry_after_seconds: int = DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS,
) -> DemucsPreModelFailureTransition | None:
    """Persist one reviewed pre-model decision, or return ``None`` on a race.

    The caller supplies an exception classification only after it has caught a
    failure from the source-preflight path. Immutable source failures become
    terminal immediately, even on attempt one. A known temporary MinIO failure
    schedules attempts one/two, while exactly attempt three receives the
    distinct exhausted-retry terminal code. ``None`` means no current row
    matched: expiry, recovery, Job deletion/expiry, or another state result
    won first. A caller must stop rather than manufacture a duplicate result.

    The outer short transaction must commit before this evidence is reported.
    This function does not catch/classify errors, open the cursor's transaction,
    arrange recovery, or make an AMQP decision.
    """

    if not callable(getattr(cursor, "execute", None)) or not callable(getattr(cursor, "fetchone", None)):
        raise TypeError("cursor must provide execute and fetchone.")
    validated_lease = _validated_lease(lease)
    validated_classification = _validated_classification(classification)
    bounded_retry_seconds = _validated_retry_after_seconds(retry_after_seconds)

    if validated_classification.disposition is DemucsPreModelFailureDisposition.TERMINAL_FAILURE:
        terminal_code = validated_classification.terminal_failure_code
        if not isinstance(terminal_code, DemucsPreModelTerminalFailureCode):
            raise _error()
        terminal_failure = _fail_task_and_job(
            cursor,
            lease=validated_lease,
            failure_code=terminal_code,
        )
        if terminal_failure is None:
            return None
        return DemucsPreModelFailureTransition(
            disposition=DemucsPreModelFailureTransitionDisposition.TERMINAL_FAILURE,
            terminal_failure=terminal_failure,
        )

    retry_code = validated_classification.retry_code
    if not isinstance(retry_code, DemucsPreModelRetryCode):
        raise _error()
    if validated_lease.attempt_count == MAX_DEMUCS_TASK_ATTEMPTS:
        terminal_failure = _fail_task_and_job(
            cursor,
            lease=validated_lease,
            failure_code=DemucsPreModelRetryExhaustionCode.STORAGE_UNAVAILABLE,
        )
        if terminal_failure is None:
            return None
        return DemucsPreModelFailureTransition(
            disposition=DemucsPreModelFailureTransitionDisposition.TERMINAL_FAILURE,
            terminal_failure=terminal_failure,
        )

    retry_schedule = _schedule_retry(
        cursor,
        lease=validated_lease,
        retry_after_seconds=bounded_retry_seconds,
        failure_code=retry_code,
    )
    if retry_schedule is None:
        return None
    return DemucsPreModelFailureTransition(
        disposition=DemucsPreModelFailureTransitionDisposition.RETRY_SCHEDULED,
        retry_schedule=retry_schedule,
    )
