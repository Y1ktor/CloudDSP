"""Guard one Basic Pitch ``running`` task completion with stored-MIDI evidence.

This pure PostgreSQL adapter sits after the local model, restricted MinIO
upload, and stored-object ``HeadObject`` proof. It validates that evidence
against the exact leased Basic Pitch stem coordinate, then issues one
parameterized SQL statement. PostgreSQL's clock and the current lease token
decide whether the running task can become ``succeeded``.

Basic Pitch completes one non-drum stem, not the whole Job: this module
intentionally leaves ``jobs.status`` as ``midi_processing``. A later aggregate
boundary must wait for every Basic Pitch and ADTOF task before it changes the
Job's overall state. This module opens no connection or transaction, reads no
MinIO object, acknowledges no RabbitMQ delivery, invokes no model, and uses no
Kubernetes API.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.basic_pitch_requested_message import LOCAL_UPLOADS_BUCKET
from app.midi_artifact_head_object import VerifiedStoredBasicPitchMidiObject
from app.task_lease import (
    BASIC_PITCH_STEMS_BY_MODE,
    MAX_BASIC_PITCH_TASK_ATTEMPTS,
    BasicPitchTaskLease,
    DatabaseCursor,
)


# The stored MIDI has already been proven by HeadObject; this statement does
# not duplicate S3 I/O. It nevertheless binds the immutable task input fields
# as defence in depth, so one valid lease cannot complete a different Basic
# Pitch stem. The task row is the only mutation: a future aggregate waits for
# all MIDI-stage work before it updates the Job.
COMPLETE_RUNNING_BASIC_PITCH_TASK_SQL = """
    UPDATE public.processing_tasks AS task
    SET
      status = 'succeeded',
      available_at = CURRENT_TIMESTAMP,
      lease_token = NULL,
      lease_expires_at = NULL,
      completed_at = CURRENT_TIMESTAMP,
      last_error_code = NULL
    WHERE task.task_id = %s::uuid
      AND task.job_id = %s::uuid
      AND task.stage = 'basic-pitch'
      AND task.stem_name = %s
      AND task.input_bucket = %s
      AND task.input_object_key = %s
      AND task.stem_mode = %s
      AND task.status = 'running'
      AND task.lease_token = %s::uuid
      AND task.lease_expires_at > CURRENT_TIMESTAMP
    RETURNING
      task.task_id::text AS task_id,
      task.job_id::text AS job_id,
      task.completed_at
"""


class BasicPitchMidiTaskCompletionProtocolError(RuntimeError):
    """The lease, stored MIDI proof, or returned database row is unusable."""


@dataclass(frozen=True)
class BasicPitchMidiTaskCompletion:
    """Durable task-only completion evidence after the caller commits its transaction."""

    task_id: str
    job_id: str
    completed_at: datetime


def _error() -> BasicPitchMidiTaskCompletionProtocolError:
    """Return a stable category without a key, digest, database row, or token."""

    return BasicPitchMidiTaskCompletionProtocolError("Basic Pitch MIDI task completion evidence is invalid.")


def _canonical_uuid(value: object) -> str:
    """Require canonical lower-case UUID text before it enters a SQL parameter."""

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
    """Require the full immutable per-stem task identity, not only a token."""

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


def _validated_stored_midi(
    value: object,
    *,
    lease: BasicPitchTaskLease,
) -> VerifiedStoredBasicPitchMidiObject:
    """Require stored output proof for exactly the lease's deterministic MIDI key."""

    if not isinstance(value, VerifiedStoredBasicPitchMidiObject):
        raise _error()
    job_id = _canonical_uuid(lease.job_id)
    if (
        value.bucket != LOCAL_UPLOADS_BUCKET
        or value.object_key != f"midi/{job_id}/{lease.stem_name}.mid"
        or type(value.content_length) is not int
        or value.content_length < 1
        or not isinstance(value.sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", value.sha256)
    ):
        raise _error()
    return value


def _returned_completion(
    row: Mapping[str, object],
    *,
    task_id: str,
    job_id: str,
) -> BasicPitchMidiTaskCompletion:
    """Validate the single PostgreSQL RETURNING row before exposing success."""

    returned_task_id = _canonical_uuid(row.get("task_id"))
    returned_job_id = _canonical_uuid(row.get("job_id"))
    completed_at = row.get("completed_at")
    if (
        returned_task_id != task_id
        or returned_job_id != job_id
        or not isinstance(completed_at, datetime)
        or completed_at.tzinfo is None
    ):
        raise _error()
    return BasicPitchMidiTaskCompletion(
        task_id=task_id,
        job_id=job_id,
        completed_at=completed_at,
    )


def complete_running_basic_pitch_task(
    cursor: DatabaseCursor,
    *,
    lease: BasicPitchTaskLease,
    stored_midi: VerifiedStoredBasicPitchMidiObject,
) -> BasicPitchMidiTaskCompletion | None:
    """Mark only the current unexpired running task succeeded, or return ``None``.

    ``None`` is the normal stale-owner outcome: PostgreSQL found that the lease
    expired, a recovery replaced it, the task already became terminal, or its
    immutable coordinate no longer matches. The caller must not acknowledge a
    delivery based on that result. A non-``None`` return means only that this
    one SQL statement produced valid completion evidence; the outer short
    transaction still owns the commit.
    """

    if not callable(getattr(cursor, "execute", None)) or not callable(getattr(cursor, "fetchone", None)):
        raise TypeError("cursor must provide execute and fetchone.")
    validated_lease = _validated_lease(lease)
    _validated_stored_midi(stored_midi, lease=validated_lease)
    task_id = _canonical_uuid(validated_lease.task_id)
    job_id = _canonical_uuid(validated_lease.job_id)
    lease_token = _canonical_uuid(validated_lease.lease_token)
    cursor.execute(
        COMPLETE_RUNNING_BASIC_PITCH_TASK_SQL,
        (
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
    return _returned_completion(row, task_id=task_id, job_id=job_id)
