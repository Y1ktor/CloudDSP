"""Guard one Basic Pitch ``running`` task completion with stored-MIDI evidence.

This pure PostgreSQL adapter sits after the local model, restricted MinIO
upload, and stored-object ``HeadObject`` proof. It validates that evidence
against the exact leased Basic Pitch stem coordinate, then calls one
administrator-owned typed database function. PostgreSQL's clock and the
current lease token decide whether the output reference and task success can
commit atomically. Migration v007's deferred task trigger aggregates terminal
stem rows and changes the parent Job only after its complete output set exists.

This module opens no connection or transaction, reads no MinIO object,
acknowledges no RabbitMQ delivery, invokes no model, and uses no Kubernetes
API.
"""

from __future__ import annotations

import json
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
from app.tempo_candidate import (
    BasicPitchTempoCandidate,
    validate_basic_pitch_tempo_candidate,
)


# The stored MIDI has already been proven by HeadObject; this statement does
# not duplicate S3 I/O. The administrator-owned PostgreSQL function binds the
# lease, output proof, and validated BPM candidate; it records both result
# fields and succeeds the task atomically. Migration v009's row trigger also
# projects the best durable candidate to `jobs.tempo` in that same update. A
# deferred aggregate then decides the parent Job's terminal state.
COMPLETE_RUNNING_BASIC_PITCH_TASK_SQL = """
    SELECT
      completion.task_id::text AS task_id,
      completion.job_id::text AS job_id,
      completion.completed_at
    FROM public.clouddsp_complete_basic_pitch_task(
      %s::uuid,
      %s::uuid,
      %s::text,
      %s::text,
      %s::text,
      %s::text,
      %s::uuid,
      %s::text,
      %s::bigint,
      %s::text,
      %s::jsonb
    ) AS completion
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
    tempo_candidate: BasicPitchTempoCandidate,
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
    validated_stored_midi = _validated_stored_midi(stored_midi, lease=validated_lease)
    validated_tempo_candidate = validate_basic_pitch_tempo_candidate(tempo_candidate)
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
            validated_stored_midi.object_key,
            validated_stored_midi.content_length,
            validated_stored_midi.sha256,
            json.dumps(
                validated_tempo_candidate.as_payload(),
                allow_nan=False,
                separators=(",", ":"),
            ),
        ),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    if not isinstance(row, Mapping):
        raise _error()
    return _returned_completion(row, task_id=task_id, job_id=job_id)
