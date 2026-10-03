"""Reclaim due work and terminalize expired or overdue Demucs tasks.

Each operation makes one bounded PostgreSQL decision inside a caller-owned
short transaction. PostgreSQL's clock and row locks arbitrate replicas;
terminalization updates the task and its retained Job together. The runtime
checks deadline/exhaustion before recovery can grant another attempt.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Callable
from uuid import UUID, uuid4

from app.processing.demucs_process import (
    DEFAULT_DEMUCS_PROCESS_TIMEOUT_SECONDS,
    DEMUCS_TIMEOUT_ERROR_CODE,
)

from .contracts import (
    DEFAULT_DEMUCS_LEASE_SECONDS,
    DEMUCS_EXHAUSTED_LEASE_ERROR_CODE,
    MAX_DEMUCS_TASK_ATTEMPTS,
    DatabaseCursor,
    DemucsExpiredLeaseTerminalization,
    DemucsTaskLease,
    DemucsTaskLeaseProtocolError,
)
from .validation import (
    _canonical_uuid,
    _mapping_or_error,
    _new_canonical_uuid,
    _row_attempt_count,
    _row_lease,
    _row_text,
    _validated_lease_seconds,
)


# This recovery query takes exactly one due retry or expired active lease. The
# migration's partial indexes keep the candidate scan proportional to due work.
# ``FOR UPDATE SKIP LOCKED`` lets multiple future replicas bypass a task another
# transaction has claimed instead of waiting or doing the same work twice.
# Attempt three is intentionally excluded: a later terminal-failure transition
# handles an exhausted expired task and updates the Job in its own reviewed
# transaction rather than silently granting a fourth Demucs run.
CLAIM_NEXT_RECOVERABLE_DEMUCS_TASK_SQL = """
    WITH candidate AS (
      SELECT task_id
      FROM public.processing_tasks
      WHERE stage = 'demucs'
        AND stem_name = ''
        AND attempt_count < 3
        AND (
          (status = 'retry_scheduled' AND available_at <= CURRENT_TIMESTAMP)
          OR
          (status IN ('leased', 'running') AND lease_expires_at <= CURRENT_TIMESTAMP)
        )
      ORDER BY
        CASE
          WHEN status = 'retry_scheduled' THEN available_at
          ELSE lease_expires_at
        END ASC,
        created_at ASC,
        task_id ASC
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    )
    UPDATE public.processing_tasks AS task
    SET
      status = 'leased',
      attempt_count = task.attempt_count + 1,
      available_at = CURRENT_TIMESTAMP,
      lease_token = %s::uuid,
      lease_expires_at = CURRENT_TIMESTAMP + (%s::integer * INTERVAL '1 second'),
      last_error_code = NULL
    FROM candidate
    WHERE task.task_id = candidate.task_id
    RETURNING
      task.task_id::text AS task_id,
      task.job_id::text AS job_id,
      task.request_event_id::text AS request_event_id,
      task.input_bucket,
      task.input_object_key,
      task.stem_mode,
      task.status,
      task.attempt_count,
      task.lease_token::text AS lease_token,
      task.lease_expires_at
"""


# A third active lease cannot be recovered because the contract permits only
# three real attempts. First lock one expiry candidate with SKIP LOCKED, then
# lock its still-retained source-uploaded Job and change both records in one
# statement. The Job update depends on the returned task row, so a constraint,
# privilege, deletion, retention, or state race cannot expose a browser-visible
# failed Job while the Demucs task still advertises an active owner.
#
# PostgreSQL's clock is the only expiry authority. Clearing both lease fields
# prevents the dead Pod from satisfying any later renewal/completion predicate;
# setting completed_at satisfies the terminal task-state constraint. A Job that
# is already terminal, expired, or no longer source_uploaded produces no row:
# it is not safe for this worker to overwrite later Job history merely because
# an old task still exists.
FINALIZE_NEXT_EXPIRED_EXHAUSTED_DEMUCS_TASK_SQL = """
    WITH candidate AS (
      SELECT task_id, job_id
      FROM public.processing_tasks
      WHERE stage = 'demucs'
        AND stem_name = ''
        AND attempt_count = 3
        AND status IN ('leased', 'running')
        AND lease_expires_at <= CURRENT_TIMESTAMP
      ORDER BY lease_expires_at ASC, created_at ASC, task_id ASC
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    ),
    locked_job AS MATERIALIZED (
      SELECT job.job_id
      FROM public.jobs AS job
      JOIN candidate ON candidate.job_id = job.job_id
      WHERE job.source_uploaded = TRUE
        AND job.status = 'source_uploaded'
        AND job.expires_at > CURRENT_TIMESTAMP
      FOR UPDATE OF job
    ),
    failed_task AS (
      UPDATE public.processing_tasks AS task
      SET
        status = 'failed',
        available_at = CURRENT_TIMESTAMP,
        lease_token = NULL,
        lease_expires_at = NULL,
        completed_at = CURRENT_TIMESTAMP,
        last_error_code = 'demucs_lease_expired_attempts_exhausted'
      FROM candidate, locked_job
      WHERE task.task_id = candidate.task_id
        AND task.job_id = locked_job.job_id
        AND task.stage = 'demucs'
        AND task.stem_name = ''
        AND task.attempt_count = 3
        AND task.status IN ('leased', 'running')
        AND task.lease_expires_at <= CURRENT_TIMESTAMP
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
        error_message = 'demucs_lease_expired_attempts_exhausted'
      FROM locked_job, failed_task
      WHERE job.job_id = locked_job.job_id
        -- `failed_task` intentionally returns text so the outer Python
        -- adapter receives canonical UUID strings rather than driver-specific
        -- UUID objects.  The `jobs` primary key remains UUID inside this CTE,
        -- however, so cast the returned task value back before comparing it.
        -- Without this explicit boundary cast PostgreSQL rejects the entire
        -- recovery-first terminalization query during planning (`uuid = text`)
        -- even when no expired third-attempt task exists.  That would keep a
        -- living worker in recovery backoff and prevent its next AMQP poll.
        AND job.job_id = failed_task.job_id::uuid
      RETURNING
        job.job_id::text AS job_id,
        job.revision,
        -- The fixed terminal error is already proven by `failed_task`.
        -- Do not return `jobs.error_message`: that column can contain broader
        -- operator-facing history, and returning it would require widening
        -- this worker's deliberate column-level SELECT authority merely for a
        -- value the durable result adapter does not need.
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


# A child normally raises DemucsProcessTimedOut and its owner writes the
# terminal result immediately. This second path covers a Pod that vanishes,
# loses its Python exception, or is force-killed after model work began. The
# first started_at is the durable job-wide processing clock, so even a task
# left retry_scheduled after another failure cannot start past this budget.
# The task and Job move together under row locks; a concurrently completing
# worker either wins first or loses its lease token on the next checkpoint.
FINALIZE_NEXT_OVERDUE_DEMUCS_TASK_SQL = """
    WITH candidate AS (
      SELECT task_id, job_id
      FROM public.processing_tasks
      WHERE stage = 'demucs'
        AND stem_name = ''
        AND status IN ('leased', 'running', 'retry_scheduled')
        AND started_at <= CURRENT_TIMESTAMP - (%s::integer * INTERVAL '1 second')
      ORDER BY started_at ASC, created_at ASC, task_id ASC
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    ),
    locked_job AS MATERIALIZED (
      SELECT job.job_id
      FROM public.jobs AS job
      JOIN candidate ON candidate.job_id = job.job_id
      WHERE job.source_uploaded = TRUE
        AND job.status = 'source_uploaded'
        AND job.expires_at > CURRENT_TIMESTAMP
      FOR UPDATE OF job
    ),
    failed_task AS (
      UPDATE public.processing_tasks AS task
      SET status = 'failed',
          available_at = CURRENT_TIMESTAMP,
          lease_token = NULL,
          lease_expires_at = NULL,
          completed_at = CURRENT_TIMESTAMP,
          last_error_code = 'demucs_process_timed_out'
      FROM candidate, locked_job
      WHERE task.task_id = candidate.task_id
        AND task.job_id = locked_job.job_id
        AND task.stage = 'demucs'
        AND task.stem_name = ''
        AND task.status IN ('leased', 'running', 'retry_scheduled')
        AND task.started_at <= CURRENT_TIMESTAMP - (%s::integer * INTERVAL '1 second')
      RETURNING task.task_id::text AS task_id,
                task.job_id::text AS job_id,
                task.attempt_count,
                task.completed_at,
                task.last_error_code
    ),
    failed_job AS (
      UPDATE public.jobs AS job
      SET status = 'failed',
          revision = job.revision + 1,
          error_message = 'demucs_process_timed_out'
      FROM locked_job, failed_task
      WHERE job.job_id = locked_job.job_id
        AND job.job_id = failed_task.job_id::uuid
      RETURNING job.job_id::text AS job_id, job.revision, job.status
    )
    SELECT failed_task.task_id, failed_task.job_id,
           failed_task.attempt_count, failed_task.completed_at,
           failed_task.last_error_code,
           failed_job.revision AS job_revision,
           failed_job.status AS job_status
    FROM failed_task
    JOIN failed_job ON failed_job.job_id = failed_task.job_id
"""


def _row_expired_lease_terminalization(
    row: Mapping[str, object],
) -> DemucsExpiredLeaseTerminalization:
    """Validate the exact task/Job proof returned by final-expiry SQL.

    The query literals constrain the stage, terminal states, and error code,
    but its driver result is still untrusted application input. Revalidating
    every returned field prevents an altered query or cursor adapter from
    reporting unrelated Job progress as a safe expired-lease finalization.
    """

    task_id = _canonical_uuid(_row_text(row, "task_id"))
    job_id = _canonical_uuid(_row_text(row, "job_id"))
    attempt_count = row.get("attempt_count")
    completed_at = row.get("completed_at")
    job_revision = row.get("job_revision")
    if (
        type(attempt_count) is not int
        or attempt_count != MAX_DEMUCS_TASK_ATTEMPTS
        or row.get("last_error_code") != DEMUCS_EXHAUSTED_LEASE_ERROR_CODE
        or not isinstance(completed_at, datetime)
        or completed_at.tzinfo is None
        or type(job_revision) is not int
        or job_revision < 1
        or row.get("job_status") != "failed"
    ):
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return DemucsExpiredLeaseTerminalization(
        task_id=task_id,
        job_id=job_id,
        attempt_count=attempt_count,
        completed_at=completed_at,
        job_revision=job_revision,
        error_code=DEMUCS_EXHAUSTED_LEASE_ERROR_CODE,
    )


def _row_overdue_terminalization(row: Mapping[str, object]) -> DemucsExpiredLeaseTerminalization:
    """Validate one bounded timeout proof without reading private Job errors."""

    task_id = _canonical_uuid(_row_text(row, "task_id"))
    job_id = _canonical_uuid(_row_text(row, "job_id"))
    attempt_count = _row_attempt_count(row)
    completed_at = row.get("completed_at")
    job_revision = row.get("job_revision")
    if (
        row.get("last_error_code") != DEMUCS_TIMEOUT_ERROR_CODE
        or not isinstance(completed_at, datetime)
        or completed_at.tzinfo is None
        or type(job_revision) is not int
        or job_revision < 1
        or row.get("job_status") != "failed"
    ):
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return DemucsExpiredLeaseTerminalization(
        task_id=task_id,
        job_id=job_id,
        attempt_count=attempt_count,
        completed_at=completed_at,
        job_revision=job_revision,
        error_code=DEMUCS_TIMEOUT_ERROR_CODE,
    )


def claim_next_recoverable_demucs_task(
    cursor: DatabaseCursor,
    *,
    lease_seconds: int = DEFAULT_DEMUCS_LEASE_SECONDS,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> DemucsTaskLease | None:
    """Claim one due retry or expired active task for recovery work.

    This must run inside a short transaction. It returns ``None`` when no due
    recoverable task exists. Tasks already at three attempts are deliberately
    excluded for the future terminal-failure transition, which must update both
    task and Job under reviewed state predicates instead of allowing attempt 4.
    """

    bounded_lease_seconds = _validated_lease_seconds(lease_seconds)
    lease_token = _new_canonical_uuid(uuid_factory)
    cursor.execute(
        CLAIM_NEXT_RECOVERABLE_DEMUCS_TASK_SQL,
        (lease_token, bounded_lease_seconds),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    claimed_lease = _row_lease(_mapping_or_error(row))
    if claimed_lease.lease_token != lease_token:
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return claimed_lease


def finalize_next_expired_exhausted_demucs_task(
    cursor: DatabaseCursor,
) -> DemucsExpiredLeaseTerminalization | None:
    """Atomically fail one retained Job and its expired third Demucs attempt.

    Call this pure one-row decision in its own short transaction before trying
    to recover first/second attempts. ``None`` is normal no-progress: no final
    active expired candidate was visible, another scanner owns it, its Job was
    deleted/expired/advanced, or another state transition already won. The
    caller must not infer a new lease or start a model from that result.

    This adapter does not open/commit a transaction, contact MinIO/RabbitMQ,
    run Demucs, delete partial stems, sleep, or call Kubernetes. It records the
    durable task/Job terminal outcome that prevents a stranded final lease from
    being mistaken for reclaimable work.
    """

    if not callable(getattr(cursor, "execute", None)) or not callable(getattr(cursor, "fetchone", None)):
        raise TypeError("cursor must provide execute and fetchone.")
    cursor.execute(FINALIZE_NEXT_EXPIRED_EXHAUSTED_DEMUCS_TASK_SQL, ())
    row = cursor.fetchone()
    if row is None:
        return None
    return _row_expired_lease_terminalization(_mapping_or_error(row))


def finalize_next_overdue_demucs_task(
    cursor: DatabaseCursor,
    *,
    deadline_seconds: int = DEFAULT_DEMUCS_PROCESS_TIMEOUT_SECONDS,
) -> DemucsExpiredLeaseTerminalization | None:
    """Fail one Demucs task/Job after the durable 12-minute start deadline.

    Recovery scans this before issuing another lease. A no-row result means
    either no overdue task exists or a concurrent success/failure won the row
    locks; neither condition authorizes another terminal write here.
    """

    if not callable(getattr(cursor, "execute", None)) or not callable(getattr(cursor, "fetchone", None)):
        raise TypeError("cursor must provide execute and fetchone.")
    if type(deadline_seconds) is not int or deadline_seconds != DEFAULT_DEMUCS_PROCESS_TIMEOUT_SECONDS:
        raise DemucsTaskLeaseProtocolError("Demucs process deadline is invalid.")
    cursor.execute(FINALIZE_NEXT_OVERDUE_DEMUCS_TASK_SQL, (deadline_seconds, deadline_seconds))
    row = cursor.fetchone()
    if row is None:
        return None
    return _row_overdue_terminalization(_mapping_or_error(row))
