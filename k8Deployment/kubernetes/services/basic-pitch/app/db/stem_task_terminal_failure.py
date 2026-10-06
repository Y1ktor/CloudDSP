"""Record a permanent pre-model Basic Pitch stem failure durably.

This module owns one narrow PostgreSQL transition for an input stem that is
definitely unsuitable *before* Basic Pitch starts: for example, the MinIO
``HeadObject`` metadata disagrees with the durable request, or the streamed
download bytes do not match the expected SHA-256.  In both cases retrying the
same immutable task automatically would not repair the mismatch.

It deliberately has no PostgreSQL connection factory, RabbitMQ action, MinIO
request, model invocation, retry timer, worker loop, image entrypoint, or
Kubernetes action.  A later failure-classification/runtime layer will decide
when to call this adapter after it catches a reviewed permanent error.  That
later layer must not send the already-acknowledged RabbitMQ message to a retry
queue or DLQ: this durable task result is its authoritative outcome.
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


class BasicPitchStemTerminalFailureCode(StrEnum):
    """The finite safe codes allowed in ``processing_tasks.last_error_code``.

    The first four values deliberately match the existing ``HeadObject``
    verifier's permanent categories, but this pure PostgreSQL boundary does
    not import the MinIO verifier merely to obtain those strings. A later
    classifier makes that mapping explicitly. The final value represents the
    separate time-of-check/time-of-use defense: metadata was acceptable, but
    streamed ``GetObject`` bytes or headers later disagreed with that identity.

    These are machine-readable operational categories, not raw SDK errors,
    MinIO keys, checksums, credentials, stack traces, or user-visible text.
    """

    OBJECT_MISSING = "basic_pitch_stem_object_missing"
    SIZE_MISMATCH = "basic_pitch_stem_size_mismatch"
    CONTENT_TYPE_MISMATCH = "basic_pitch_stem_content_type_mismatch"
    METADATA_MISMATCH = "basic_pitch_stem_metadata_mismatch"
    DOWNLOAD_CHECKSUM_MISMATCH = "basic_pitch_stem_download_checksum_mismatch"


class BasicPitchStemTerminalFailureProtocolError(RuntimeError):
    """The lease, failure code, or returned PostgreSQL evidence is invalid."""


@dataclass(frozen=True)
class BasicPitchStemTerminalFailure:
    """Committed evidence that one pre-model stem task is terminally failed.

    A result exists only after a caller's short transaction commits.  It
    retains no bucket/key, input checksum, raw storage error, lease token, or
    browser identity.  The task itself keeps the stable failure code used by a
    future aggregate to make the overall Job terminal.
    """

    task_id: str
    job_id: str
    failure_code: BasicPitchStemTerminalFailureCode
    completed_at: datetime


# This statement deliberately accepts only a still-current ``leased`` task.
# Metadata and streamed-download verification happen before the guarded model
# start transition, so a failure here must never retrospectively claim that
# Basic Pitch began.  Every immutable task coordinate plus PostgreSQL's clock
# is in the predicate: a stale Pod cannot overwrite recovery, a later attempt,
# or a terminal result chosen by another component.
#
# The state transition clears the active lease, makes completion time explicit,
# and stores only the reviewed code.  It leaves ``jobs.status`` unchanged:
# the future aggregate is the sole component that may turn the complete Job
# from ``midi_processing`` into its final failed state.
FAIL_LEASED_BASIC_PITCH_STEM_TASK_SQL = """
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
      AND task.lease_token = %s::uuid
      AND task.lease_expires_at > CURRENT_TIMESTAMP
    RETURNING
      task.task_id::text AS task_id,
      task.job_id::text AS job_id,
      task.completed_at,
      task.last_error_code
"""


def _error() -> BasicPitchStemTerminalFailureProtocolError:
    """Return one stable error without embedding private task evidence."""

    return BasicPitchStemTerminalFailureProtocolError("Basic Pitch terminal stem failure evidence is invalid.")


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
    """Require the whole immutable pre-model task identity, not only its token."""

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


def _validated_failure_code(value: object) -> BasicPitchStemTerminalFailureCode:
    """Forbid arbitrary error text from becoming durable worker state."""

    if not isinstance(value, BasicPitchStemTerminalFailureCode):
        raise _error()
    return value


def _returned_terminal_failure(
    row: Mapping[str, object],
    *,
    task_id: str,
    job_id: str,
    failure_code: BasicPitchStemTerminalFailureCode,
) -> BasicPitchStemTerminalFailure:
    """Validate only the returned proof of the exact guarded terminal update."""

    returned_task_id = _canonical_uuid(row.get("task_id"))
    returned_job_id = _canonical_uuid(row.get("job_id"))
    completed_at = row.get("completed_at")
    returned_code = row.get("last_error_code")
    if (
        returned_task_id != task_id
        or returned_job_id != job_id
        or returned_code != failure_code.value
        or not isinstance(completed_at, datetime)
        or completed_at.tzinfo is None
    ):
        raise _error()
    return BasicPitchStemTerminalFailure(
        task_id=task_id,
        job_id=job_id,
        failure_code=failure_code,
        completed_at=completed_at,
    )


def fail_leased_basic_pitch_stem_task(
    cursor: DatabaseCursor,
    *,
    lease: BasicPitchTaskLease,
    failure_code: BasicPitchStemTerminalFailureCode,
) -> BasicPitchStemTerminalFailure | None:
    """Fail one current pre-model task, or return ``None`` on ownership loss.

    ``None`` is a normal no-row result: the task may have expired, been
    recovered, entered ``running``, or already reached another terminal state.
    The caller must then stop rather than recording a second outcome.  A
    returned result is merely SQL evidence; the outer short transaction must
    commit before a later runtime reports it as durable.
    """

    if not callable(getattr(cursor, "execute", None)) or not callable(getattr(cursor, "fetchone", None)):
        raise TypeError("cursor must provide execute and fetchone.")
    validated_lease = _validated_lease(lease)
    validated_code = _validated_failure_code(failure_code)
    task_id = _canonical_uuid(validated_lease.task_id)
    job_id = _canonical_uuid(validated_lease.job_id)
    lease_token = _canonical_uuid(validated_lease.lease_token)

    cursor.execute(
        FAIL_LEASED_BASIC_PITCH_STEM_TASK_SQL,
        (
            validated_code.value,
            task_id,
            job_id,
            validated_lease.stem_name,
            validated_lease.input_bucket,
            validated_lease.input_object_key,
            validated_lease.stem_mode,
            lease_token,
        ),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    if not isinstance(row, Mapping):
        raise _error()
    return _returned_terminal_failure(
        row,
        task_id=task_id,
        job_id=job_id,
        failure_code=validated_code,
    )
