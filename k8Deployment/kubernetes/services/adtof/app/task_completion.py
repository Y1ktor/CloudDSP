"""Prepare one atomic ADTOF drums-task completion transaction.

This pure PostgreSQL adapter sits after local tempo validation, restricted
MinIO upload, and the two stored-object ``HeadObject`` checks. It validates
those facts against one running ADTOF lease, then invokes one typed,
administrator-owned PostgreSQL function. That function locks the current task
and Job together, writes the reviewed ``jobs.midi.drums`` entry, increments the
Job revision, and marks the task succeeded only while PostgreSQL still
recognizes the exact unexpired lease token.

The Job deliberately remains ``midi_processing``. A later aggregate must wait
for every Basic Pitch and ADTOF task before it can mark a Job completed or
failed. This module creates no database connection or transaction, reads no
MinIO data, handles no RabbitMQ acknowledgement, invokes no model, and uses no
Kubernetes API. The database bootstrap owns the function definition and grants
its isolated execute capability; this source module only validates and
parameterizes the exact call.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.adtof_requested_message import ADTOF_STEM_NAME, LOCAL_UPLOADS_BUCKET
from app.output_artifact import ADTOFTempoCandidate, parse_adtof_tempo_candidate
from app.output_artifact_head_object import VerifiedStoredADTOFObject, VerifiedStoredADTOFObjects
from app.output_object_plan import ADTOF_MIDI_CONTENT_TYPE, ADTOF_TEMPO_CONTENT_TYPE
from app.task_claim import ADTOF_STEM_MODES, MAX_ADTOF_TASK_ATTEMPTS, ADTOFTaskLease, DatabaseCursor


# The runtime role has no direct UPDATE privilege on `public.jobs`. Instead,
# this typed function is the one capability granted by the bootstrap. Its
# administrator-owned body performs the task-and-Job mutation atomically after
# it rechecks all fixed input/output coordinates and strict tempo JSON. Every
# dynamic value remains a database parameter; neither a token nor a private key
# is interpolated into SQL text.
ADTOF_TASK_COMPLETION_FUNCTION = "public.clouddsp_complete_adtof_task"

COMPLETE_RUNNING_ADTOF_TASK_SQL = """
    SELECT
      completion.task_id::text AS task_id,
      completion.job_id::text AS job_id,
      completion.completed_at,
      completion.revision
    FROM public.clouddsp_complete_adtof_task(
      %s::uuid,
      %s::uuid,
      %s::text,
      %s::text,
      %s::text,
      %s::uuid,
      %s::text,
      %s::text,
      %s::jsonb
    ) AS completion
"""


class ADTOFTaskCompletionProtocolError(RuntimeError):
    """Lease/result/returned-row evidence cannot safely form a completion call."""


@dataclass(frozen=True)
class ADTOFTaskCompletion:
    """Durable evidence exposed only after the outer short transaction commits."""

    task_id: str
    job_id: str
    completed_at: datetime
    resulting_revision: int


def _error() -> ADTOFTaskCompletionProtocolError:
    """Return one bounded category without tokens, keys, hashes, or row data."""

    return ADTOFTaskCompletionProtocolError("ADTOF task completion evidence is invalid.")


def _canonical_uuid(value: object) -> str:
    """Require canonical durable UUID text before passing it as an SQL parameter."""

    if not isinstance(value, str):
        raise _error()
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise _error() from error
    if canonical != value:
        raise _error()
    return canonical


def _validated_lease(value: object) -> ADTOFTaskLease:
    """Require the full immutable drums-task coordinate, not only its token."""

    if not isinstance(value, ADTOFTaskLease):
        raise _error()
    job_id = _canonical_uuid(value.job_id)
    _canonical_uuid(value.task_id)
    _canonical_uuid(value.request_event_id)
    _canonical_uuid(value.lease_token)
    if (
        value.stem_name != ADTOF_STEM_NAME
        or value.input_bucket != LOCAL_UPLOADS_BUCKET
        or value.input_object_key != f"stems/{job_id}/drums.wav"
        or value.stem_mode not in ADTOF_STEM_MODES
        or type(value.attempt_count) is not int
        or not 1 <= value.attempt_count <= MAX_ADTOF_TASK_ATTEMPTS
        or not isinstance(value.lease_expires_at, datetime)
        or value.lease_expires_at.tzinfo is None
    ):
        raise _error()
    return value


def _validated_stored_object(
    value: object,
    *,
    bucket: str,
    object_key: str,
    content_type: str,
) -> VerifiedStoredADTOFObject:
    """Require safe HeadObject evidence for one fixed drum result coordinate."""

    if (
        not isinstance(value, VerifiedStoredADTOFObject)
        or value.bucket != bucket
        or value.object_key != object_key
        or value.content_type != content_type
        or type(value.content_length) is not int
        or value.content_length < 1
        or not isinstance(value.sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", value.sha256)
    ):
        raise _error()
    return value


def _validated_stored_outputs(
    value: object,
    *,
    lease: ADTOFTaskLease,
) -> VerifiedStoredADTOFObjects:
    """Bind both stored outputs to the lease's deterministic private Job keys."""

    if not isinstance(value, VerifiedStoredADTOFObjects):
        raise _error()
    job_id = _canonical_uuid(lease.job_id)
    _validated_stored_object(
        value.midi,
        bucket=LOCAL_UPLOADS_BUCKET,
        object_key=f"midi/{job_id}/drums.mid",
        content_type=ADTOF_MIDI_CONTENT_TYPE,
    )
    _validated_stored_object(
        value.tempo_candidate,
        bucket=LOCAL_UPLOADS_BUCKET,
        object_key=f"midi/{job_id}/drums_bpm.json",
        content_type=ADTOF_TEMPO_CONTENT_TYPE,
    )
    return value


def _tempo_candidate_json(value: object) -> str:
    """Round-trip the candidate through its strict JSON parser before SQL sees it.

    A frozen dataclass can be constructed directly, so accepting it by type
    alone would let a caller write non-finite numbers, an incompatible source,
    or a confidence/credibility contradiction. Re-rendering the documented
    JSON shape and parsing it through the same validator used for local output
    makes this database boundary independently reject those widened values.
    """

    if not isinstance(value, ADTOFTempoCandidate):
        raise _error()
    payload = {
        "extractor": "adtof",
        "bpm": value.bpm,
        "beat_count": value.beat_count,
        "duration_seconds": value.duration_seconds,
        "interval_consistency": value.interval_consistency,
        "drum_event_count": value.drum_event_count,
        "credible": value.credible,
        "confidence": value.confidence,
        "source": value.source,
    }
    try:
        encoded = json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True)
        parsed = parse_adtof_tempo_candidate(encoded.encode("utf-8"))
    except (TypeError, ValueError) as error:
        raise _error() from error
    except Exception as error:
        # The shared parser deliberately owns all output-shape details. Its
        # public diagnostic belongs to the local artifact boundary, not here.
        raise _error() from error
    if parsed != value:
        raise _error()
    return encoded


def _returned_completion(
    row: Mapping[str, object],
    *,
    task_id: str,
    job_id: str,
) -> ADTOFTaskCompletion:
    """Validate the one `RETURNING` row before exposing a durable success fact."""

    returned_task_id = _canonical_uuid(row.get("task_id"))
    returned_job_id = _canonical_uuid(row.get("job_id"))
    completed_at = row.get("completed_at")
    resulting_revision = row.get("revision")
    if (
        returned_task_id != task_id
        or returned_job_id != job_id
        or not isinstance(completed_at, datetime)
        or completed_at.tzinfo is None
        or type(resulting_revision) is not int
        or resulting_revision < 2
    ):
        raise _error()
    return ADTOFTaskCompletion(
        task_id=task_id,
        job_id=job_id,
        completed_at=completed_at,
        resulting_revision=resulting_revision,
    )


def complete_running_adtof_task(
    cursor: DatabaseCursor,
    *,
    lease: ADTOFTaskLease,
    stored_outputs: VerifiedStoredADTOFObjects,
    tempo_candidate: ADTOFTempoCandidate,
) -> ADTOFTaskCompletion | None:
    """Atomically record one current ADTOF result, or return stale ownership loss.

    ``None`` is normal when recovery replaced/expired the lease, the Job moved
    on, retention expired it, or an unexpected prior `midi.drums` result exists.
    The caller must stop without an acknowledgement or broader write. A result
    means this statement returned internally consistent evidence only; its
    enclosing short database context must commit before a caller may report
    durable success.
    """

    if not callable(getattr(cursor, "execute", None)) or not callable(getattr(cursor, "fetchone", None)):
        raise TypeError("cursor must provide execute and fetchone.")
    validated_lease = _validated_lease(lease)
    validated_outputs = _validated_stored_outputs(stored_outputs, lease=validated_lease)
    tempo_json = _tempo_candidate_json(tempo_candidate)
    task_id = _canonical_uuid(validated_lease.task_id)
    job_id = _canonical_uuid(validated_lease.job_id)
    lease_token = _canonical_uuid(validated_lease.lease_token)
    cursor.execute(
        COMPLETE_RUNNING_ADTOF_TASK_SQL,
        (
            task_id,
            job_id,
            validated_lease.input_bucket,
            validated_lease.input_object_key,
            validated_lease.stem_mode,
            lease_token,
            validated_outputs.midi.object_key,
            validated_outputs.tempo_candidate.object_key,
            tempo_json,
        ),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    if not isinstance(row, Mapping):
        raise _error()
    return _returned_completion(row, task_id=task_id, job_id=job_id)
