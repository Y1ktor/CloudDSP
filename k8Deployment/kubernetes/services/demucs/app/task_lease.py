"""PostgreSQL claim, task-start, lease-renewal, and recovery SQL for Demucs tasks.

This module deliberately contains no Psycopg import, connection setup,
transaction commit, RabbitMQ acknowledgement, MinIO operation, model process,
or Kubernetes API call. A later composition root opens one *short* PostgreSQL
transaction, calls exactly one of these adapters, commits, and only then makes
the next external-work decision. The first-claim path is the only one followed
by an AMQP acknowledgement; task-start is deliberately later, after source
preflight has succeeded.

PostgreSQL is the authoritative answer to “may this logical Demucs stage run?”
RabbitMQ can redeliver the same message, and a worker can restart or lose its
lease. The canonical ``(job_id, 'demucs', '')`` key plus row locks makes those
events converge on one durable task rather than duplicate audio processing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import json
import re
from typing import Callable, Protocol
from uuid import UUID, uuid4

from app.demucs_artifacts import DEMUCS_STEM_FILE_EXTENSION, DEMUCS_STEMS_BY_MODE
from app.demucs_requested_message import DemucsRequestedMessage
from app.demucs_process import DEFAULT_DEMUCS_PROCESS_TIMEOUT_SECONDS, DEMUCS_TIMEOUT_ERROR_CODE


# A normal local CPU or future GPU worker starts with a 15-minute lease and
# renews it at least once per minute. Bounds make a later Deployment typo unable
# to make a task effectively permanent or create an impractically short lease.
DEFAULT_DEMUCS_LEASE_SECONDS = 15 * 60
MIN_DEMUCS_LEASE_SECONDS = 60
MAX_DEMUCS_LEASE_SECONDS = 60 * 60
MAX_DEMUCS_TASK_ATTEMPTS = 3
MAX_DEMUCS_STEMS_DOCUMENT_BYTES = 16 * 1024
# Six is the largest reviewed Demucs output set.  Keeping this limit alongside
# the JSON-byte cap prevents an accidental future caller from turning one task
# completion into an unbounded downstream fan-out transaction.
MAX_DEMUCS_DOWNSTREAM_OUTBOX_EVENTS = 6
MAX_DEMUCS_DOWNSTREAM_OUTBOX_DOCUMENT_BYTES = 16 * 1024

# A Pod can disappear after starting a third attempt, leaving no Python
# exception to classify. PostgreSQL's recovery scan records this distinct,
# bounded fact instead of pretending that a model/storage failure occurred or
# issuing a prohibited fourth lease. The same safe code is written to the
# terminal Demucs task and its still-processable authoritative Job.
DEMUCS_EXHAUSTED_LEASE_ERROR_CODE = "demucs_lease_expired_attempts_exhausted"

# These mappings are the durable routing decision made when Demucs has
# completed—not a RabbitMQ route or a worker implementation.  Drums go only
# to ADTOF, while every non-drum output can be pitch-analysed by Basic Pitch.
# The immutable v004 migration mirrors this exact finite vocabulary in its
# database CHECK constraint, so application and storage reject a widened stage
# independently.
DEMUCS_DOWNSTREAM_STAGE_BY_STEM = {
    "vocals": "basic-pitch",
    "no_vocals": "basic-pitch",
    "drums": "adtof",
    "bass": "basic-pitch",
    "other": "basic-pitch",
    "guitar": "basic-pitch",
    "piano": "basic-pitch",
}
DEMUCS_DOWNSTREAM_EVENT_TYPE_BY_STAGE = {
    "basic-pitch": "basic-pitch.requested",
    "adtof": "adtof.requested",
}


class DemucsTaskLeaseProtocolError(RuntimeError):
    """Raise a safe category for unexpected database row shapes or settings.

    No raw database diagnostic, object key, user identity, or message body is
    included in this exception. A later supervisor can retain the driver cause
    privately while logging only this fixed category.
    """


class DemucsTaskClaimInconsistency(RuntimeError):
    """A parsed delivery disagrees with authoritative durable state.

    This is intentionally distinct from a stale request or harmless duplicate.
    The later result-transition adapter must record a bounded terminal category
    before acknowledging it; this narrow claim module never guesses a failure
    update or sends an AMQP acknowledgement by itself.
    """


class DemucsTaskClaimDisposition(StrEnum):
    """The only safe outcomes of a message-driven first-claim transaction."""

    CLAIMED = "claimed"
    DUPLICATE = "duplicate"
    STALE = "stale"


class DemucsStaleRequestReason(StrEnum):
    """Non-sensitive reasons a valid historic delivery is safe to acknowledge."""

    JOB_MISSING = "job_missing"
    JOB_EXPIRED = "job_expired"
    JOB_TERMINAL = "job_terminal"


class DatabaseCursor(Protocol):
    """The minimal dictionary-row cursor API used by this testable SQL boundary."""

    def execute(self, query: str, params: tuple[object, ...]) -> object:
        """Execute parameterized SQL; data never interpolates into query text."""

    def fetchone(self) -> Mapping[str, object] | None:
        """Return at most one dictionary-shaped PostgreSQL row."""


@dataclass(frozen=True)
class DemucsTaskLease:
    """One durable worker lease returned after a first/recovery claim commits."""

    task_id: str
    job_id: str
    request_event_id: str
    input_bucket: str
    input_object_key: str
    stem_mode: str
    attempt_count: int
    lease_token: str
    lease_expires_at: datetime


@dataclass(frozen=True)
class DemucsTaskCompletion:
    """One committed Demucs success transition returned after its SQL statement.

    ``job_revision`` is the revision written with the complete stem map, and
    ``outbox_event_count`` proves the same SQL statement inserted every
    downstream request.  The outer composition exposes either value only
    after the surrounding short transaction commits, never while Job locks
    remain open.
    """

    task_id: str
    job_id: str
    completed_at: datetime
    job_revision: int
    outbox_event_count: int


@dataclass(frozen=True)
class DemucsExpiredLeaseTerminalization:
    """Evidence that PostgreSQL terminalized an overdue or exhausted task.

    The owner either exceeded the overall model deadline or the final lease
    expired. This result
    proves the task and its Job moved together to their terminal states in one
    committed statement. It excludes model output, source/object coordinates,
    lease tokens, credentials, and raw driver diagnostics.
    """

    task_id: str
    job_id: str
    attempt_count: int
    completed_at: datetime
    job_revision: int
    error_code: str


@dataclass(frozen=True)
class DemucsTaskClaimResult:
    """A claim result that tells the later transport whether an ack is allowed.

    Only ``CLAIMED`` has a lease. ``DUPLICATE`` means another durable task record
    already owns or completed this logical stage and is safe to acknowledge.
    ``STALE`` means a deleted, expired, or terminal Job needs no new state. An
    inconsistency raises instead of yielding a deceptively safe result.
    """

    disposition: DemucsTaskClaimDisposition
    lease: DemucsTaskLease | None = None
    duplicate_status: str | None = None
    stale_reason: DemucsStaleRequestReason | None = None


# Lock an existing task first. When no task exists, the caller locks the Job
# row, then repeats this query: the second read closes the race where another
# worker inserted the task while this worker waited for the Job lock.
LOCK_EXISTING_DEMUCS_TASK_SQL = """
    SELECT
      task_id::text AS task_id,
      job_id::text AS job_id,
      request_event_id::text AS request_event_id,
      status,
      attempt_count,
      input_bucket,
      input_object_key,
      stem_mode,
      lease_token::text AS lease_token,
      lease_expires_at
    FROM public.processing_tasks
    WHERE job_id = %s::uuid
      AND stage = 'demucs'
      AND stem_name = ''
    FOR UPDATE
"""


# A Job lock serializes a first task claim with an intake/delete/result update.
# ``is_retained`` is computed by PostgreSQL's clock, avoiding a Pod-clock
# decision about expiry. The later parser already rejected arbitrary keys; this
# query repeats every durable identity check before a task row is inserted.
LOCK_JOB_FOR_DEMUCS_CLAIM_SQL = """
    SELECT
      job_id::text AS job_id,
      input_bucket,
      input_object_key,
      source_uploaded,
      stem_mode,
      status,
      revision,
      (expires_at > CURRENT_TIMESTAMP) AS is_retained
    FROM public.jobs
    WHERE job_id = %s::uuid
    FOR UPDATE
"""


# Outbox publication is monotonic after the dispatcher confirms RabbitMQ, so a
# normal read is sufficient. The Demucs role has SELECT only on these identity
# columns—not payload—and cannot change dispatch history or publish work.
READ_PUBLISHED_DEMUCS_OUTBOX_SQL = """
    SELECT
      event_id::text AS event_id,
      job_id::text AS job_id,
      stage,
      stem_name,
      event_type,
      publication_status
    FROM public.outbox_events
    WHERE event_id = %s::uuid
"""


# The first row begins leased but not running: a later source-validation/model
# adapter will switch it to running after its own guarded transition. The SQL
# calculates time inside PostgreSQL and returns the exact generated lease view.
INSERT_FIRST_DEMUCS_TASK_LEASE_SQL = """
    INSERT INTO public.processing_tasks (
      task_id,
      job_id,
      stage,
      stem_name,
      request_event_id,
      input_bucket,
      input_object_key,
      stem_mode,
      status,
      attempt_count,
      available_at,
      lease_token,
      lease_expires_at,
      started_at,
      completed_at,
      last_error_code
    )
    VALUES (
      %s::uuid,
      %s::uuid,
      'demucs',
      '',
      %s::uuid,
      %s,
      %s,
      %s,
      'leased',
      1,
      CURRENT_TIMESTAMP,
      %s::uuid,
      CURRENT_TIMESTAMP + (%s::integer * INTERVAL '1 second'),
      NULL,
      NULL,
      NULL
    )
    RETURNING
      task_id::text AS task_id,
      job_id::text AS job_id,
      request_event_id::text AS request_event_id,
      input_bucket,
      input_object_key,
      stem_mode,
      status,
      attempt_count,
      lease_token::text AS lease_token,
      lease_expires_at
"""


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


# A worker must renew only the lease token it still owns and only before expiry.
# Once another replica has recovered a task, this guarded update returns no row
# and the stale worker must stop without writing stems or database state.
RENEW_DEMUCS_TASK_LEASE_SQL = """
    UPDATE public.processing_tasks
    SET lease_expires_at = CURRENT_TIMESTAMP + (%s::integer * INTERVAL '1 second')
    WHERE task_id = %s::uuid
      AND stage = 'demucs'
      AND stem_name = ''
      AND status IN ('leased', 'running')
      AND lease_token = %s::uuid
      AND lease_expires_at > CURRENT_TIMESTAMP
    RETURNING lease_expires_at
"""


# Source validation happens while the task remains `leased`: a permanent media
# rejection can therefore finish without pretending Demucs model execution ever
# began. Only after that preflight succeeds may the owner atomically move into
# `running`. The token, current active state, PostgreSQL clock, and immutable
# job/stage identity all remain in the predicate so a stale worker sees no row
# after expiry or recovery instead of overwriting another replica's task.
# `COALESCE` keeps the first genuine start timestamp across a later recovery:
# recovery moves an expired `running` task back to `leased`, but it must not
# erase the fact that an earlier attempt had started the model stage.
START_LEASED_DEMUCS_TASK_SQL = """
    UPDATE public.processing_tasks
    SET
      status = 'running',
      started_at = COALESCE(started_at, CURRENT_TIMESTAMP)
    WHERE task_id = %s::uuid
      AND job_id = %s::uuid
      AND stage = 'demucs'
      AND stem_name = ''
      AND status = 'leased'
      AND lease_token = %s::uuid
      AND lease_expires_at > CURRENT_TIMESTAMP
    RETURNING started_at
"""


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


def _canonical_uuid(value: object) -> str:
    """Require canonical lower-case UUID text from message, database, or factory."""

    if not isinstance(value, str):
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    try:
        normalized = str(UUID(value))
    except (ValueError, AttributeError) as error:
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.") from error
    if normalized != value:
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return normalized


def _new_canonical_uuid(factory: Callable[[], UUID] = uuid4) -> str:
    """Generate an explicit application UUID rather than relying on DB defaults."""

    generated = factory()
    if not isinstance(generated, UUID):
        raise DemucsTaskLeaseProtocolError("Demucs task identifier factory is invalid.")
    return str(generated)


def _validated_lease_seconds(lease_seconds: int) -> int:
    """Keep lease duration in the contract's bounded range before SQL binding."""

    if type(lease_seconds) is not int or not MIN_DEMUCS_LEASE_SECONDS <= lease_seconds <= MAX_DEMUCS_LEASE_SECONDS:
        raise DemucsTaskLeaseProtocolError("Demucs task lease duration is invalid.")
    return lease_seconds


def _mapping_or_error(row: Mapping[str, object] | None) -> Mapping[str, object]:
    """Reject a driver row shape the adapter cannot safely interpret."""

    if not isinstance(row, Mapping):
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return row


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


def _row_text(row: Mapping[str, object], name: str) -> str:
    """Read a safe non-empty text field from a dictionary cursor row."""

    value = row.get(name)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return value


def _row_attempt_count(row: Mapping[str, object]) -> int:
    """Validate PostgreSQL's bounded task-attempt counter before using it."""

    value = row.get("attempt_count")
    if type(value) is not int or not 1 <= value <= MAX_DEMUCS_TASK_ATTEMPTS:
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return value


def _row_lease(row: Mapping[str, object]) -> DemucsTaskLease:
    """Convert a returned newly owned lease row into immutable application data."""

    if _row_text(row, "status") != "leased":
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    lease_expires_at = row.get("lease_expires_at")
    if not isinstance(lease_expires_at, datetime) or lease_expires_at.tzinfo is None:
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return DemucsTaskLease(
        task_id=_canonical_uuid(_row_text(row, "task_id")),
        job_id=_canonical_uuid(_row_text(row, "job_id")),
        request_event_id=_canonical_uuid(_row_text(row, "request_event_id")),
        input_bucket=_row_text(row, "input_bucket"),
        input_object_key=_row_text(row, "input_object_key"),
        stem_mode=_row_text(row, "stem_mode"),
        attempt_count=_row_attempt_count(row),
        lease_token=_canonical_uuid(_row_text(row, "lease_token")),
        lease_expires_at=lease_expires_at,
    )


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


def _existing_task_result(row: Mapping[str, object], *, message: DemucsRequestedMessage) -> DemucsTaskClaimResult:
    """Classify a pre-existing canonical task without mutating duplicate state."""

    if _canonical_uuid(_row_text(row, "job_id")) != message.job_id:
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    if _canonical_uuid(_row_text(row, "request_event_id")) != message.event_id:
        # One job/stage may map only to its one immutable outbox event. Treat a
        # corrupt or spoofed mismatch as unsafe—not as an innocent duplicate.
        raise DemucsTaskClaimInconsistency("Demucs request conflicts with its durable task.")
    status = _row_text(row, "status")
    if status not in {"leased", "running", "retry_scheduled", "succeeded", "failed"}:
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return DemucsTaskClaimResult(
        disposition=DemucsTaskClaimDisposition.DUPLICATE,
        duplicate_status=status,
    )


def _validate_locked_job(row: Mapping[str, object], *, message: DemucsRequestedMessage) -> DemucsStaleRequestReason | None:
    """Return a safe stale reason or raise when an active Job disagrees with AMQP."""

    status = _row_text(row, "status")
    if row.get("is_retained") is not True:
        return DemucsStaleRequestReason.JOB_EXPIRED
    if status in {"completed", "failed"}:
        return DemucsStaleRequestReason.JOB_TERMINAL
    if (
        row.get("source_uploaded") is not True
        or status != "source_uploaded"
        or _canonical_uuid(_row_text(row, "job_id")) != message.job_id
        or _row_text(row, "input_bucket") != message.source_bucket
        or _row_text(row, "input_object_key") != message.source_object_key
        or _row_text(row, "stem_mode") != message.stem_mode
    ):
        raise DemucsTaskClaimInconsistency("Demucs request conflicts with its durable Job.")
    return None


def _validate_outbox_event(row: Mapping[str, object] | None, *, message: DemucsRequestedMessage) -> None:
    """Require the matching immutable outbox event to be broker-confirmed published."""

    event = _mapping_or_error(row)
    if (
        _canonical_uuid(_row_text(event, "event_id")) != message.event_id
        or _canonical_uuid(_row_text(event, "job_id")) != message.job_id
        or _row_text(event, "stage") != "demucs"
        or event.get("stem_name") != ""
        or _row_text(event, "event_type") != "demucs.requested"
        or _row_text(event, "publication_status") != "published"
    ):
        raise DemucsTaskClaimInconsistency("Demucs request conflicts with its durable outbox event.")


def claim_demucs_task_for_delivery(
    cursor: DatabaseCursor,
    *,
    message: DemucsRequestedMessage,
    lease_seconds: int = DEFAULT_DEMUCS_LEASE_SECONDS,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> DemucsTaskClaimResult:
    """Create one first task lease or classify duplicate/stale delivery safely.

    The caller must invoke this inside one short database transaction and commit
    a ``CLAIMED`` lease *before* acknowledging RabbitMQ. On duplicate/stale
    outcomes it also commits no mutation, then may acknowledge. An inconsistency
    deliberately raises because a later terminal-transition adapter must record
    a safe durable failure before acknowledgement.
    """

    if not isinstance(message, DemucsRequestedMessage):
        raise TypeError("message must be DemucsRequestedMessage.")
    bounded_lease_seconds = _validated_lease_seconds(lease_seconds)

    cursor.execute(LOCK_EXISTING_DEMUCS_TASK_SQL, (message.job_id,))
    existing_task = cursor.fetchone()
    if existing_task is not None:
        return _existing_task_result(_mapping_or_error(existing_task), message=message)

    # A missing task does not lock a gap. Locking the Job serializes first-task
    # creation, then repeating the task read observes work inserted while this
    # transaction waited on that Job lock.
    cursor.execute(LOCK_JOB_FOR_DEMUCS_CLAIM_SQL, (message.job_id,))
    locked_job = cursor.fetchone()
    if locked_job is None:
        return DemucsTaskClaimResult(
            disposition=DemucsTaskClaimDisposition.STALE,
            stale_reason=DemucsStaleRequestReason.JOB_MISSING,
        )

    cursor.execute(LOCK_EXISTING_DEMUCS_TASK_SQL, (message.job_id,))
    existing_after_job_lock = cursor.fetchone()
    if existing_after_job_lock is not None:
        return _existing_task_result(_mapping_or_error(existing_after_job_lock), message=message)

    stale_reason = _validate_locked_job(_mapping_or_error(locked_job), message=message)
    if stale_reason is not None:
        return DemucsTaskClaimResult(
            disposition=DemucsTaskClaimDisposition.STALE,
            stale_reason=stale_reason,
        )

    cursor.execute(READ_PUBLISHED_DEMUCS_OUTBOX_SQL, (message.event_id,))
    _validate_outbox_event(cursor.fetchone(), message=message)

    task_id = _new_canonical_uuid(uuid_factory)
    lease_token = _new_canonical_uuid(uuid_factory)
    cursor.execute(
        INSERT_FIRST_DEMUCS_TASK_LEASE_SQL,
        (
            task_id,
            message.job_id,
            message.event_id,
            message.source_bucket,
            message.source_object_key,
            message.stem_mode,
            lease_token,
            bounded_lease_seconds,
        ),
    )
    claimed_lease = _row_lease(_mapping_or_error(cursor.fetchone()))
    if (
        claimed_lease.task_id != task_id
        or claimed_lease.job_id != message.job_id
        or claimed_lease.request_event_id != message.event_id
        or claimed_lease.lease_token != lease_token
        or claimed_lease.attempt_count != 1
    ):
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return DemucsTaskClaimResult(
        disposition=DemucsTaskClaimDisposition.CLAIMED,
        lease=claimed_lease,
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


def renew_demucs_task_lease(
    cursor: DatabaseCursor,
    *,
    task_id: str,
    lease_token: str,
    lease_seconds: int = DEFAULT_DEMUCS_LEASE_SECONDS,
) -> datetime | None:
    """Extend only the still-current active lease and return its new expiry.

    ``None`` means the lease expired, a recovery worker replaced its token, or
    the task became inactive. The caller must stop processing immediately; it
    must never keep writing results under an ownership token PostgreSQL no
    longer recognizes.
    """

    bounded_lease_seconds = _validated_lease_seconds(lease_seconds)
    canonical_task_id = _canonical_uuid(task_id)
    canonical_lease_token = _canonical_uuid(lease_token)
    cursor.execute(
        RENEW_DEMUCS_TASK_LEASE_SQL,
        (bounded_lease_seconds, canonical_task_id, canonical_lease_token),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    returned = _mapping_or_error(row).get("lease_expires_at")
    if not isinstance(returned, datetime) or returned.tzinfo is None:
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return returned


def start_leased_demucs_task(
    cursor: DatabaseCursor,
    *,
    lease: DemucsTaskLease,
) -> datetime | None:
    """Atomically mark one preflight-validated lease as running, or stop safely.

    The caller must invoke this only after its acknowledged-lease source
    preflight has returned valid evidence, and inside one short write
    transaction. A timestamp means PostgreSQL still recognizes this exact
    unexpired token and the future runtime may *then* begin the Demucs model.
    ``None`` is a normal ownership-loss signal: the task may have expired,
    been recovered by another replica, or become inactive. The caller must not
    start the model, write artifacts, or issue a result transition in that case.

    This pure adapter does not read MinIO, inspect preflight evidence, create a
    connection, commit, renew a lease, invoke a model, or retry. Separating the
    source handoff from this short SQL decision keeps no database lock alive
    while media bytes are downloaded or inspected.
    """

    if not isinstance(lease, DemucsTaskLease):
        raise TypeError("lease must be DemucsTaskLease.")
    canonical_task_id = _canonical_uuid(lease.task_id)
    canonical_job_id = _canonical_uuid(lease.job_id)
    canonical_lease_token = _canonical_uuid(lease.lease_token)
    cursor.execute(
        START_LEASED_DEMUCS_TASK_SQL,
        (canonical_task_id, canonical_job_id, canonical_lease_token),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    started_at = _mapping_or_error(row).get("started_at")
    if not isinstance(started_at, datetime) or started_at.tzinfo is None:
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return started_at


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
