"""Validate finite downstream evidence and atomically complete a Demucs task.

Only a current running lease may register the already-uploaded stem set.
Task success, Job revision, and every routed Basic Pitch/ADTOF outbox record
remain one parameterized statement; a stale owner creates none of them.
The caller commits the transaction before exposing completion. This adapter
does not upload artifacts, publish messages, or acknowledge a delivery.
"""

from __future__ import annotations

from datetime import datetime
import json
import re

from app.artifacts.demucs_artifacts import DEMUCS_STEM_FILE_EXTENSION, DEMUCS_STEMS_BY_MODE

from .contracts import (
    DEMUCS_DOWNSTREAM_EVENT_TYPE_BY_STAGE,
    DEMUCS_DOWNSTREAM_STAGE_BY_STEM,
    MAX_DEMUCS_DOWNSTREAM_OUTBOX_DOCUMENT_BYTES,
    MAX_DEMUCS_DOWNSTREAM_OUTBOX_EVENTS,
    MAX_DEMUCS_STEMS_DOCUMENT_BYTES,
    DatabaseCursor,
    DemucsTaskCompletion,
    DemucsTaskLease,
    DemucsTaskLeaseProtocolError,
)
from .validation import _canonical_uuid, _mapping_or_error, _row_text


# The complete, already uploaded stem map and fixed downstream request set are
# JSONB *parameters*, never dynamically assembled SQL.  A Job lock first
# confirms its retained `source_uploaded` state and matching mode.  The task
# update then requires the same current running lease token and PostgreSQL's
# clock; it cannot succeed if another replica recovered/finished the work.
#
# The downstream INSERT depends on the completed Job CTE.  Consequently a
# stale guard creates neither result state nor outbox work; conversely an
# outbox constraint/permission error aborts the whole statement and its outer
# transaction, so a completed Demucs result can never be committed without
# every requested next-stage record.
COMPLETE_RUNNING_DEMUCS_TASK_SQL = """
    WITH locked_job AS MATERIALIZED (
      SELECT job_id
      FROM public.jobs
      WHERE job_id = %s::uuid
        AND source_uploaded = TRUE
        AND stem_mode = %s
        AND status = 'source_uploaded'
        AND expires_at > CURRENT_TIMESTAMP
      FOR UPDATE
    ),
    completed_task AS (
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
        AND task.stage = 'demucs'
        AND task.stem_name = ''
        AND task.status = 'running'
        AND task.lease_token = %s::uuid
        AND task.lease_expires_at > CURRENT_TIMESTAMP
        AND EXISTS (
          SELECT 1
          FROM locked_job
          WHERE locked_job.job_id = task.job_id
        )
      RETURNING
        task.task_id::text AS task_id,
        task.job_id::text AS job_id,
        task.completed_at
    ),
    completed_job AS (
      UPDATE public.jobs AS job
      SET
        stems = %s::jsonb,
        status = 'midi_processing',
        revision = job.revision + 1,
        error_message = NULL
      FROM locked_job, completed_task
      WHERE job.job_id = locked_job.job_id
        -- `completed_task` returns a canonical text value for the Python
        -- completion contract. Convert it back to UUID at this SQL boundary
        -- before comparing it to PostgreSQL's native `jobs.job_id` column.
        -- Without the cast PostgreSQL rejects a valid finished task with
        -- `operator does not exist: uuid = text`, leaving the worker to retry
        -- after it has already done the expensive CPU and MinIO work.
        AND job.job_id = completed_task.job_id::uuid
      RETURNING
        job.job_id::text AS job_id,
        job.revision
    ),
    requested_downstream_events AS MATERIALIZED (
      SELECT
        downstream.event_id,
        downstream.stage,
        downstream.stem_name,
        downstream.event_type,
        downstream.payload
      FROM jsonb_to_recordset(%s::jsonb) AS downstream(
        event_id TEXT,
        stage TEXT,
        stem_name TEXT,
        event_type TEXT,
        payload JSONB
      )
    ),
    inserted_downstream_events AS (
      INSERT INTO public.outbox_events (
        event_id,
        job_id,
        stage,
        stem_name,
        event_type,
        payload
      )
      SELECT
        requested.event_id::uuid,
        completed_job.job_id::uuid,
        requested.stage,
        requested.stem_name,
        requested.event_type,
        requested.payload
      FROM requested_downstream_events AS requested
      CROSS JOIN completed_job
      RETURNING event_id
    )
    SELECT
      completed_task.task_id,
      completed_task.job_id,
      completed_task.completed_at,
      completed_job.revision AS job_revision,
      (
        SELECT count(*)::integer
        FROM inserted_downstream_events
      ) AS outbox_event_count
    FROM completed_task
    JOIN completed_job ON completed_job.job_id = completed_task.job_id
"""


def _reject_duplicate_json_object(pairs: list[tuple[object, object]]) -> dict[str, object]:
    """Build a JSON object while rejecting duplicate keys at every nesting level.

    PostgreSQL JSONB normalizes duplicate object keys, but accepting such a
    document in the worker boundary would make the Python review see a
    different meaning than the stored payload.  This object-pairs hook keeps
    the in-memory contract and durable payload definition identical.
    """

    object_value: dict[str, object] = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in object_value:
            raise DemucsTaskLeaseProtocolError("Demucs downstream outbox evidence is invalid.")
        object_value[key] = value
    return object_value


def _validated_downstream_events_document(
    value: object,
    *,
    lease: DemucsTaskLease,
) -> int:
    """Validate the full finite downstream fan-out before one SQL statement.

    The event list must contain exactly the stems produced by the leased
    Demucs mode.  This means a programming error cannot mark a job
    ``midi_processing`` with a missing Basic Pitch/ADTOF request, even before
    PostgreSQL mirrors the stage/type/stem vocabulary through v004 constraints.
    """

    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > MAX_DEMUCS_DOWNSTREAM_OUTBOX_DOCUMENT_BYTES
    ):
        raise DemucsTaskLeaseProtocolError("Demucs downstream outbox evidence is invalid.")
    try:
        decoded = json.loads(value, object_pairs_hook=_reject_duplicate_json_object)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise DemucsTaskLeaseProtocolError("Demucs downstream outbox evidence is invalid.") from error
    if not isinstance(decoded, list):
        raise DemucsTaskLeaseProtocolError("Demucs downstream outbox evidence is invalid.")
    try:
        expected_stems = DEMUCS_STEMS_BY_MODE[lease.stem_mode][1]
    except (KeyError, TypeError) as error:
        raise DemucsTaskLeaseProtocolError("Demucs downstream outbox evidence is invalid.") from error
    if not 1 <= len(decoded) <= MAX_DEMUCS_DOWNSTREAM_OUTBOX_EVENTS or len(decoded) != len(expected_stems):
        raise DemucsTaskLeaseProtocolError("Demucs downstream outbox evidence is invalid.")

    canonical_job_id = _canonical_uuid(lease.job_id)
    observed_stems: set[str] = set()
    for entry in decoded:
        if not isinstance(entry, dict) or set(entry) != {
            "event_id",
            "stage",
            "stem_name",
            "event_type",
            "payload",
        }:
            raise DemucsTaskLeaseProtocolError("Demucs downstream outbox evidence is invalid.")
        _canonical_uuid(entry.get("event_id"))
        stem_name = entry.get("stem_name")
        stage = entry.get("stage")
        event_type = entry.get("event_type")
        if (
            not isinstance(stem_name, str)
            or stem_name not in expected_stems
            or stem_name in observed_stems
            or stage != DEMUCS_DOWNSTREAM_STAGE_BY_STEM.get(stem_name)
            or event_type != DEMUCS_DOWNSTREAM_EVENT_TYPE_BY_STAGE.get(stage)
        ):
            raise DemucsTaskLeaseProtocolError("Demucs downstream outbox evidence is invalid.")
        observed_stems.add(stem_name)

        payload = entry.get("payload")
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "job_id",
            "stem_name",
            "stem",
        }:
            raise DemucsTaskLeaseProtocolError("Demucs downstream outbox evidence is invalid.")
        if (
            payload.get("schema_version") != 1
            or _canonical_uuid(payload.get("job_id")) != canonical_job_id
            or payload.get("stem_name") != stem_name
        ):
            raise DemucsTaskLeaseProtocolError("Demucs downstream outbox evidence is invalid.")
        stem = payload.get("stem")
        expected_object_key = f"stems/{canonical_job_id}/{stem_name}{DEMUCS_STEM_FILE_EXTENSION}"
        if (
            not isinstance(stem, dict)
            or set(stem) != {"bucket", "object_key", "content_type", "size_bytes", "sha256"}
            or stem.get("bucket") != "clouddsp-uploads"
            or stem.get("object_key") != expected_object_key
            or stem.get("content_type") != "audio/wav"
            or type(stem.get("size_bytes")) is not int
            or stem["size_bytes"] < 1
            or not isinstance(stem.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", stem["sha256"])
        ):
            raise DemucsTaskLeaseProtocolError("Demucs downstream outbox evidence is invalid.")
    if observed_stems != set(expected_stems):
        raise DemucsTaskLeaseProtocolError("Demucs downstream outbox evidence is invalid.")
    return len(decoded)


def complete_running_demucs_task(
    cursor: DatabaseCursor,
    *,
    lease: DemucsTaskLease,
    stems_document: str,
    downstream_events_document: str,
) -> DemucsTaskCompletion | None:
    """Commit one complete stem map only for the current unexpired running lease.

    ``stems_document`` is a compact JSON object assembled by the separate
    published-stem-set boundary.  It is deliberately passed as a PostgreSQL
    parameter, never interpolated into SQL.  ``None`` is the normal stop
    signal: expiry/recovery, Job deletion/retention expiry, an unexpected Job
    status, or another terminal transition made the worker stale before its
    result could commit.  The caller must not create downstream work when that
    happens.  ``downstream_events_document`` must list exactly one request for
    every completed stem; PostgreSQL inserts those rows in this same statement.

    This pure function issues one statement only.  Its outer composition owns
    the short transaction and exposes a successful completion only after that
    transaction commits.  It does not inspect MinIO, acknowledge RabbitMQ,
    publish an event, retry a failure, or expose database diagnostics.
    """

    if not isinstance(lease, DemucsTaskLease):
        raise TypeError("lease must be DemucsTaskLease.")
    if (
        not isinstance(stems_document, str)
        or not stems_document
        or len(stems_document.encode("utf-8")) > MAX_DEMUCS_STEMS_DOCUMENT_BYTES
    ):
        raise DemucsTaskLeaseProtocolError("Demucs task completion evidence is invalid.")
    canonical_task_id = _canonical_uuid(lease.task_id)
    canonical_job_id = _canonical_uuid(lease.job_id)
    _canonical_uuid(lease.request_event_id)
    canonical_lease_token = _canonical_uuid(lease.lease_token)
    if lease.stem_mode not in {"2-stems", "4-stems", "6-stems"}:
        raise DemucsTaskLeaseProtocolError("Demucs task completion evidence is invalid.")
    expected_outbox_event_count = _validated_downstream_events_document(
        downstream_events_document,
        lease=lease,
    )

    cursor.execute(
        COMPLETE_RUNNING_DEMUCS_TASK_SQL,
        (
            canonical_job_id,
            lease.stem_mode,
            canonical_task_id,
            canonical_job_id,
            canonical_lease_token,
            stems_document,
            downstream_events_document,
        ),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    returned = _mapping_or_error(row)
    completed_at = returned.get("completed_at")
    job_revision = returned.get("job_revision")
    outbox_event_count = returned.get("outbox_event_count")
    if (
        _canonical_uuid(_row_text(returned, "task_id")) != canonical_task_id
        or _canonical_uuid(_row_text(returned, "job_id")) != canonical_job_id
        or not isinstance(completed_at, datetime)
        or completed_at.tzinfo is None
        or type(job_revision) is not int
        or job_revision < 1
        or type(outbox_event_count) is not int
        or outbox_event_count != expected_outbox_event_count
    ):
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return DemucsTaskCompletion(
        task_id=canonical_task_id,
        job_id=canonical_job_id,
        completed_at=completed_at,
        job_revision=job_revision,
        outbox_event_count=outbox_event_count,
    )
