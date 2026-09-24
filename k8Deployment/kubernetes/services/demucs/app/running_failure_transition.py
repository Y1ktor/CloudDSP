"""Persist one reviewed failure after Demucs has entered ``running``.

This is the running-phase counterpart to
``pre_model_failure_transition.py``. The model may have consumed CPU and
written some private deterministic stem keys before it raises a reviewed error,
so the SQL predicate requires a still-current ``running`` lease rather than the
earlier ``leased`` state. The two durable outcomes are:

* attempts one/two: clear ownership and make the task ``retry_scheduled`` at a
  PostgreSQL-clock retry time; or
* attempt three: atomically mark both this one Demucs task and its authoritative
  Job ``failed`` with the paired bounded exhaustion category; or
* a 12-minute process timeout on any attempt: fail the task and Job immediately.

Private stems written before a failure remain unreachable to the browser and
can be safely overwritten at their deterministic key by a later attempt. The
task/Job terminal CTE ensures a browser never sees the Job failed while its only
Demucs task still claims to run, and a stale worker gets no row rather than
overwriting recovery or success.

This module is a pure parameterized cursor adapter. It opens no connection or
transaction, catches no runtime exception, calls no MinIO/RabbitMQ/model API,
sleeps/retries nothing, and changes no Kubernetes or KEDA resource. A separate
short transaction composition and runtime handoff own those boundaries.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.demucs_artifacts import DEMUCS_STEMS_BY_MODE
from app.running_failure_classification import (
    DemucsRunningFailureClassification,
    DemucsRunningFailureDisposition,
    DemucsRunningRetryCode,
    DemucsRunningRetryExhaustionCode,
    DemucsRunningTerminalCode,
    retry_exhaustion_code_for_running_failure,
)
from app.source_object import LOCAL_UPLOADS_BUCKET
from app.task_lease import MAX_DEMUCS_TASK_ATTEMPTS, DatabaseCursor, DemucsTaskLease


DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS = 30
MIN_DEMUCS_RUNNING_RETRY_AFTER_SECONDS = 1
MAX_DEMUCS_RUNNING_RETRY_AFTER_SECONDS = 3_600


class DemucsRunningFailureTransitionDisposition(StrEnum):
    """The durable outcomes available for a reviewed running-phase failure."""

    RETRY_SCHEDULED = "retry_scheduled"
    TERMINAL_FAILURE = "terminal_failure"


class DemucsRunningFailureTransitionProtocolError(RuntimeError):
    """The running lease/classification or PostgreSQL return evidence is invalid."""


@dataclass(frozen=True)
class DemucsRunningRetrySchedule:
    """Committed evidence that a running failure may be recovered later."""

    task_id: str
    job_id: str
    attempt_count: int
    failure_code: DemucsRunningRetryCode
    available_at: datetime


@dataclass(frozen=True)
class DemucsRunningRetryExhaustion:
    """Committed terminal evidence from timeout or final retry exhaustion."""

    task_id: str
    job_id: str
    attempt_count: int
    failure_code: DemucsRunningRetryExhaustionCode | DemucsRunningTerminalCode
    completed_at: datetime
    job_revision: int


@dataclass(frozen=True)
class DemucsRunningFailureTransition:
    """One running-phase result exposed only after an outer commit succeeds."""

    disposition: DemucsRunningFailureTransitionDisposition
    retry_schedule: DemucsRunningRetrySchedule | None = None
    retry_exhaustion: DemucsRunningRetryExhaustion | None = None

    def __post_init__(self) -> None:
        """Require exactly the committed evidence matching this disposition."""

        if self.disposition is DemucsRunningFailureTransitionDisposition.RETRY_SCHEDULED:
            if not isinstance(self.retry_schedule, DemucsRunningRetrySchedule) or self.retry_exhaustion is not None:
                raise TypeError("A running Demucs retry requires only retry evidence.")
            return
        if self.disposition is DemucsRunningFailureTransitionDisposition.TERMINAL_FAILURE:
            if self.retry_schedule is not None or not isinstance(
                self.retry_exhaustion,
                DemucsRunningRetryExhaustion,
            ):
                raise TypeError("A terminal running Demucs failure requires only terminal evidence.")
            return
        raise TypeError("Demucs running-failure transition disposition is invalid.")


# The Job lock uses the same state predicate as success and pre-model failure
# adapters. All Demucs attempts still work on the original validated source;
# Job status advances only on complete success or a terminal result. The task
# predicate is more specific: a failure after CPU/output work must never clear
# a task that is still preflight ``leased`` or that a recovery replica re-leased.
SCHEDULE_RUNNING_DEMUCS_TASK_RETRY_SQL = """
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
      AND task.status = 'running'
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


# A timeout is terminal on any attempt; other reviewed failures are terminal
# only on attempt three. The Job update depends on the exact task row inside one
# statement. The task retains its original ``started_at`` value: it truthfully
# records that a model attempt began even though this task ultimately failed.
FAIL_FINAL_ATTEMPT_RUNNING_DEMUCS_TASK_AND_JOB_SQL = """
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
        AND task.status = 'running'
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
        -- The task CTE returns canonical text for the Python proof, whereas
        -- jobs.job_id remains UUID inside PostgreSQL.
        AND job.job_id = failed_task.job_id::uuid
      RETURNING
        job.job_id::text AS job_id,
        job.revision,
        -- The restricted worker may UPDATE error_message but does not have
        -- SELECT authority for arbitrary historical Job error text. The
        -- task-side returned code already proves the finite value written.
        job.status
    )
    SELECT
      failed_task.task_id,
      failed_task.job_id,
      failed_task.attempt_count,
      failed_task.completed_at,
      failed_task.last_error_code,
      failed_job.revision AS job_revision,
      failed_job.status AS job_status
    FROM failed_task
    JOIN failed_job ON failed_job.job_id = failed_task.job_id
"""


def _error() -> DemucsRunningFailureTransitionProtocolError:
    """Return one stable protocol error without task/database diagnostics."""

    return DemucsRunningFailureTransitionProtocolError("Demucs running-failure transition is invalid.")


def _canonical_uuid(value: object) -> str:
    """Require canonical UUID text before it becomes a SQL guard parameter."""

    if not isinstance(value, str):
        raise _error()
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise _error() from error
    if canonical != value:
        raise _error()
    return canonical


def _validated_running_lease(value: object) -> DemucsTaskLease:
    """Require the full immutable task coordinate rather than only a token."""

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
    """Allow only bounded whole-second durable retry delays."""

    if (
        type(value) is not int
        or not MIN_DEMUCS_RUNNING_RETRY_AFTER_SECONDS <= value <= MAX_DEMUCS_RUNNING_RETRY_AFTER_SECONDS
    ):
        raise _error()
    return value


def _validated_classification(value: object) -> DemucsRunningFailureClassification:
    """Accept only finite retry or immediate-timeout terminal evidence."""

    if (
        not isinstance(value, DemucsRunningFailureClassification)
        or not (
            (value.disposition is DemucsRunningFailureDisposition.RETRY_SCHEDULED
             and isinstance(value.retry_code, DemucsRunningRetryCode)
             and value.terminal_code is None)
            or (value.disposition is DemucsRunningFailureDisposition.TERMINAL_FAILURE
                and value.retry_code is None
                and value.terminal_code is DemucsRunningTerminalCode.PROCESS_TIMED_OUT)
        )
    ):
        raise _error()
    return value


def _returned_retry_schedule(
    row: Mapping[str, object],
    *,
    lease: DemucsTaskLease,
    failure_code: DemucsRunningRetryCode,
) -> DemucsRunningRetrySchedule:
    """Validate only proof that the exact current running task was scheduled."""

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
    return DemucsRunningRetrySchedule(
        task_id=task_id,
        job_id=job_id,
        attempt_count=attempt_count,
        failure_code=failure_code,
        available_at=available_at,
    )


def _returned_retry_exhaustion(
    row: Mapping[str, object],
    *,
    lease: DemucsTaskLease,
    failure_code: DemucsRunningRetryExhaustionCode | DemucsRunningTerminalCode,
) -> DemucsRunningRetryExhaustion:
    """Validate proof that the selected running attempt and Job failed together."""

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
        or not 1 <= attempt_count <= MAX_DEMUCS_TASK_ATTEMPTS
        or (isinstance(failure_code, DemucsRunningRetryExhaustionCode)
            and attempt_count != MAX_DEMUCS_TASK_ATTEMPTS)
        or row.get("last_error_code") != failure_code.value
        or not isinstance(completed_at, datetime)
        or completed_at.tzinfo is None
        or type(job_revision) is not int
        or job_revision < 1
        or row.get("job_status") != "failed"
    ):
        raise _error()
    return DemucsRunningRetryExhaustion(
        task_id=task_id,
        job_id=job_id,
        attempt_count=attempt_count,
        failure_code=failure_code,
        completed_at=completed_at,
        job_revision=job_revision,
    )


def _schedule_running_retry(
    cursor: DatabaseCursor,
    *,
    lease: DemucsTaskLease,
    retry_after_seconds: int,
    failure_code: DemucsRunningRetryCode,
) -> DemucsRunningRetrySchedule | None:
    """Schedule one current non-final running task, or return a no-row race."""

    cursor.execute(
        SCHEDULE_RUNNING_DEMUCS_TASK_RETRY_SQL,
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


def _fail_final_running_attempt(
    cursor: DatabaseCursor,
    *,
    lease: DemucsTaskLease,
    failure_code: DemucsRunningRetryExhaustionCode | DemucsRunningTerminalCode,
) -> DemucsRunningRetryExhaustion | None:
    """Fail the exact running lease and matching Job in one statement."""

    cursor.execute(
        FAIL_FINAL_ATTEMPT_RUNNING_DEMUCS_TASK_AND_JOB_SQL,
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
    return _returned_retry_exhaustion(row, lease=lease, failure_code=failure_code)


def transition_running_demucs_failure(
    cursor: DatabaseCursor,
    *,
    lease: DemucsTaskLease,
    classification: DemucsRunningFailureClassification,
    retry_after_seconds: int = DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS,
) -> DemucsRunningFailureTransition | None:
    """Commit one classified running failure, or return normal ownership loss.

    A reviewed process timeout is terminal on any attempt. Other reviewed
    failures schedule a retry on attempts one/two and become terminal on
    attempt three; no branch may create an attempt four. ``None``
    means expiry, recovery, retention/job-state change, or another terminal
    result already won the token guard. Callers must stop rather than create a
    second durable result.
    """

    if not callable(getattr(cursor, "execute", None)) or not callable(getattr(cursor, "fetchone", None)):
        raise TypeError("cursor must provide execute and fetchone.")
    validated_lease = _validated_running_lease(lease)
    validated_classification = _validated_classification(classification)
    bounded_retry_seconds = _validated_retry_after_seconds(retry_after_seconds)
    if validated_classification.disposition is DemucsRunningFailureDisposition.TERMINAL_FAILURE:
        timeout_code = validated_classification.terminal_code
        if timeout_code is not DemucsRunningTerminalCode.PROCESS_TIMED_OUT:
            raise _error()
        terminal = _fail_final_running_attempt(
            cursor, lease=validated_lease, failure_code=timeout_code,
        )
        if terminal is None:
            return None
        return DemucsRunningFailureTransition(
            disposition=DemucsRunningFailureTransitionDisposition.TERMINAL_FAILURE,
            retry_exhaustion=terminal,
        )

    retry_code = validated_classification.retry_code
    # Do not let an altered dataclass instance silently broaden retry codes.
    if not isinstance(retry_code, DemucsRunningRetryCode):
        raise _error()
    if validated_lease.attempt_count == MAX_DEMUCS_TASK_ATTEMPTS:
        exhaustion = _fail_final_running_attempt(
            cursor,
            lease=validated_lease,
            failure_code=retry_exhaustion_code_for_running_failure(retry_code),
        )
        if exhaustion is None:
            return None
        return DemucsRunningFailureTransition(
            disposition=DemucsRunningFailureTransitionDisposition.TERMINAL_FAILURE,
            retry_exhaustion=exhaustion,
        )

    scheduled = _schedule_running_retry(
        cursor,
        lease=validated_lease,
        retry_after_seconds=bounded_retry_seconds,
        failure_code=retry_code,
    )
    if scheduled is None:
        return None
    return DemucsRunningFailureTransition(
        disposition=DemucsRunningFailureTransitionDisposition.RETRY_SCHEDULED,
        retry_schedule=scheduled,
    )
