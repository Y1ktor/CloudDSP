"""Record exhaustion of the Basic Pitch pre-model stem-storage retry budget.

This module owns one narrow PostgreSQL terminal transition for the case where
the current Basic Pitch worker still cannot reach MinIO before model start and
the task is already on its final permitted attempt. Retrying would violate the
durable attempt cap, while leaving the task merely ``leased`` would defer an
inevitable decision until expiry. The task therefore becomes terminally
``failed`` with one finite operational code.

It deliberately has no PostgreSQL connection factory, exception classifier,
MinIO/RabbitMQ action, model invocation, retry timer, worker loop, image
entrypoint, or Kubernetes action. A later small composition will commit this
statement after a reviewed transient storage exception is classified and found
to be on the final attempt.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.basic_pitch_requested_message import LOCAL_UPLOADS_BUCKET
from app.task_lease import (
    BASIC_PITCH_STEMS_BY_MODE,
    MAX_BASIC_PITCH_TASK_ATTEMPTS,
    BasicPitchTaskLease,
    DatabaseCursor,
)


class BasicPitchStemRetryExhaustionCode(StrEnum):
    """The finite safe code allowed for exhausted transient storage retries.

    This code says only that the bounded automatic retry budget was spent while
    storage remained unavailable. It is not a raw MinIO/SDK error, endpoint,
    object key, credential, checksum, stack trace, or user-facing message.
    """

    STORAGE_UNAVAILABLE = "basic_pitch_stem_storage_retry_exhausted"


class BasicPitchStemRetryExhaustionProtocolError(RuntimeError):
    """The final lease, code, or returned PostgreSQL evidence is invalid."""


@dataclass(frozen=True)
class BasicPitchStemRetryExhaustion:
    """Committed evidence that a final pre-model storage attempt is terminal.

    A result exists only after a caller's short transaction commits. It retains
    no bucket/key, checksum, raw storage response, lease token, or browser
    identity. A future aggregate alone decides whether every task outcome now
    makes the containing Job terminal.
    """

    task_id: str
    job_id: str
    failure_code: BasicPitchStemRetryExhaustionCode
    completed_at: datetime


# Only a current final-attempt ``leased`` task may take this terminal path.
# The failure occurred before the separate `leased` -> `running` permission, so
# it must never claim Basic Pitch started. Every immutable task coordinate,
# ownership token, exact final attempt count, and PostgreSQL's clock are in the
# predicate. A stale Pod cannot overwrite recovery, another owner, or another
# terminal decision.
#
# The statement clears active lease fields and records PostgreSQL completion
# time. It leaves the task's `started_at` and the containing `jobs` row alone:
# this is pre-model task evidence, not model evidence or aggregate Job policy.
FAIL_FINAL_ATTEMPT_LEASED_BASIC_PITCH_STEM_SQL = """
    UPDATE public.processing_tasks AS task
    SET
      status = 'failed',
      available_at = CURRENT_TIMESTAMP,
      lease_token = NULL,
      lease_expires_at = NULL,
      completed_at = CURRENT_TIMESTAMP,
      last_error_code = %s
    WHERE task.task_id = %s::uuid
      AND task.job_id = %s::uuid
      AND task.stage = 'basic-pitch'
      AND task.stem_name = %s
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
"""


def _error() -> BasicPitchStemRetryExhaustionProtocolError:
    """Return one stable error without embedding private task evidence."""

    return BasicPitchStemRetryExhaustionProtocolError("Basic Pitch retry exhaustion evidence is invalid.")


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


def _validated_final_attempt_lease(value: object) -> BasicPitchTaskLease:
    """Require a complete immutable identity on exactly the final attempt."""

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
        or value.attempt_count != MAX_BASIC_PITCH_TASK_ATTEMPTS
    ):
        raise _error()
    return value


def _validated_failure_code(value: object) -> BasicPitchStemRetryExhaustionCode:
    """Forbid raw dependency diagnostics from becoming durable task state."""

    if not isinstance(value, BasicPitchStemRetryExhaustionCode):
        raise _error()
    return value


def _returned_retry_exhaustion(
    row: Mapping[str, object],
    *,
    task_id: str,
    job_id: str,
    failure_code: BasicPitchStemRetryExhaustionCode,
) -> BasicPitchStemRetryExhaustion:
    """Validate only proof of the exact guarded final-attempt terminal update."""

    returned_task_id = _canonical_uuid(row.get("task_id"))
    returned_job_id = _canonical_uuid(row.get("job_id"))
    returned_attempt_count = row.get("attempt_count")
    completed_at = row.get("completed_at")
    returned_code = row.get("last_error_code")
    if (
        returned_task_id != task_id
        or returned_job_id != job_id
        or returned_attempt_count != MAX_BASIC_PITCH_TASK_ATTEMPTS
        or type(returned_attempt_count) is not int
        or returned_code != failure_code.value
        or not isinstance(completed_at, datetime)
        or completed_at.tzinfo is None
    ):
        raise _error()
    return BasicPitchStemRetryExhaustion(
        task_id=task_id,
        job_id=job_id,
        failure_code=failure_code,
        completed_at=completed_at,
    )


def fail_final_attempt_leased_basic_pitch_stem(
    cursor: DatabaseCursor,
    *,
    lease: BasicPitchTaskLease,
    failure_code: BasicPitchStemRetryExhaustionCode,
) -> BasicPitchStemRetryExhaustion | None:
    """Terminally fail one current final storage attempt, or return ``None``.

    ``None`` is a normal no-row result: the worker may have lost its lease,
    reached expiry, been recovered, entered ``running``, or already received a
    different terminal outcome. The caller must stop rather than write a
    second result. A returned value is SQL evidence only; its short outer
    transaction must commit before a later worker reports it as durable.
    """

    if not callable(getattr(cursor, "execute", None)) or not callable(getattr(cursor, "fetchone", None)):
        raise TypeError("cursor must provide execute and fetchone.")
    validated_lease = _validated_final_attempt_lease(lease)
    validated_code = _validated_failure_code(failure_code)
    task_id = _canonical_uuid(validated_lease.task_id)
    job_id = _canonical_uuid(validated_lease.job_id)
    lease_token = _canonical_uuid(validated_lease.lease_token)

    cursor.execute(
        FAIL_FINAL_ATTEMPT_LEASED_BASIC_PITCH_STEM_SQL,
        (
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
    return _returned_retry_exhaustion(
        row,
        task_id=task_id,
        job_id=job_id,
        failure_code=validated_code,
    )
