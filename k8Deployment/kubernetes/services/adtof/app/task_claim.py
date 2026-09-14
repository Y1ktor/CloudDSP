"""Durable first-claim and expired-lease recovery SQL for ADTOF drums work.

This pure PostgreSQL boundary receives a parser-validated
``adtof.requested`` message and makes exactly one durable decision: create the
first lease for ``(job_id, 'adtof', 'drums')``, classify a broker redelivery as
a duplicate, or classify a deleted/expired/terminal Job as stale. PostgreSQL,
not RabbitMQ, is the authority for that idempotency decision.

The module imports no Psycopg connection factory, Pika client, MinIO client,
ADTOF model, or Kubernetes API. A later composition must open a short database
transaction, call :func:`claim_adtof_task_for_delivery`, commit it, and only
then decide how to acknowledge the RabbitMQ delivery. No MinIO check, ML work,
or long-running CPU operation may share this transaction/row-lock scope.

The same module also contains the pure, one-row expired-active-lease recovery
claim. It grants a fresh token only after PostgreSQL's own clock declares a
previous `leased` or `running` attempt expired. Reconstructing the strict
request from the immutable published outbox event remains a separate later
boundary, so this SQL decision cannot accidentally turn a stale broker body
into recovery input.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Callable, Protocol
from uuid import UUID, uuid4

from app.adtof_requested_message import (
    ADTOF_REQUESTED_EVENT_TYPE,
    ADTOF_STAGE,
    ADTOF_STEM_NAME,
    LOCAL_UPLOADS_BUCKET,
    STEM_CONTENT_TYPE,
    ADTOFRequestedMessage,
)


# A local CPU worker needs an explicit, finite ownership window. A later
# preflight/model boundary can renew or recover it under a separately reviewed
# policy, but a bad configuration cannot make a first claim permanent.
DEFAULT_ADTOF_LEASE_SECONDS = 15 * 60
MIN_ADTOF_LEASE_SECONDS = 60
MAX_ADTOF_LEASE_SECONDS = 60 * 60
MAX_ADTOF_TASK_ATTEMPTS = 3

# Demucs produces a drums stem only for these modes. Keeping the finite list in
# this independently deployable service prevents a broker message from asking
# ADTOF to invent a drums task for a 2-stems job.
ADTOF_STEM_MODES = frozenset({"4-stems", "6-stems"})


class ADTOFTaskClaimProtocolError(RuntimeError):
    """An unsafe input, row, UUID factory, or lease setting was supplied.

    Messages deliberately omit database diagnostics, job details, object keys,
    user data, and credentials. A future runtime can retain driver causes in a
    private diagnostic path while emitting only this reviewed category.
    """


class ADTOFTaskClaimInconsistency(RuntimeError):
    """A syntactically valid request conflicts with durable PostgreSQL state.

    This is not a harmless duplicate: a later lifecycle layer must record an
    approved bounded outcome before acknowledging it. This small claim adapter
    never guesses an error code, changes Job status, or creates an outbox row.
    """


class ADTOFTaskClaimDisposition(StrEnum):
    """The three transport-neutral outcomes of a first-claim transaction."""

    CLAIMED = "claimed"
    DUPLICATE = "duplicate"
    STALE = "stale"


class ADTOFStaleRequestReason(StrEnum):
    """Safe no-mutation reasons for historic broker deliveries."""

    JOB_MISSING = "job_missing"
    JOB_EXPIRED = "job_expired"
    JOB_TERMINAL = "job_terminal"


class DatabaseCursor(Protocol):
    """The small dictionary-row cursor surface required by this SQL adapter."""

    def execute(self, query: str, params: tuple[object, ...]) -> object:
        """Execute parameterized SQL; external values never enter query text."""

    def fetchone(self) -> Mapping[str, object] | None:
        """Return at most one dictionary-shaped PostgreSQL row."""


@dataclass(frozen=True)
class ADTOFTaskLease:
    """One committed ownership token for a future drum-transcription attempt."""

    task_id: str
    job_id: str
    stem_name: str
    request_event_id: str
    input_bucket: str
    input_object_key: str
    stem_mode: str
    attempt_count: int
    lease_token: str
    lease_expires_at: datetime


@dataclass(frozen=True)
class ADTOFTaskClaimResult:
    """A durable fact for a later AMQP layer, not an acknowledgement command."""

    disposition: ADTOFTaskClaimDisposition
    lease: ADTOFTaskLease | None = None
    duplicate_status: str | None = None
    stale_reason: ADTOFStaleRequestReason | None = None


# Lock the canonical idempotency coordinate first. A broker duplicate therefore
# reads its already-created task and exits without touching Job/outbox state.
LOCK_EXISTING_ADTOF_TASK_SQL = """
    SELECT
      task_id::text AS task_id,
      job_id::text AS job_id,
      stage,
      stem_name,
      request_event_id::text AS request_event_id,
      input_bucket,
      input_object_key,
      stem_mode,
      status,
      attempt_count,
      lease_token::text AS lease_token,
      lease_expires_at
    FROM public.processing_tasks
    WHERE job_id = %s::uuid
      AND stage = 'adtof'
      AND stem_name = 'drums'
    FOR UPDATE
"""


# PostgreSQL needs UPDATE privilege for SELECT ... FOR UPDATE. Giving a worker
# broad Job UPDATE permission would let it mutate job-wide state outside an
# approved completion transaction, so the bootstrap installed this typed,
# schema-qualified SECURITY DEFINER function as the only Job-row lock path.
# Its transaction-scoped lock belongs to this connection, allowing the second
# task lookup below to close the concurrent first-insert race safely.
ADTOF_JOB_CLAIM_LOCK_FUNCTION = "public.clouddsp_lock_adtof_job_for_claim"


LOCK_JOB_FOR_ADTOF_CLAIM_SQL = f"""
    SELECT
      job_id::text AS job_id,
      stem_mode,
      status,
      revision,
      is_retained
    FROM {ADTOF_JOB_CLAIM_LOCK_FUNCTION}(%s::uuid)
"""


# Broker metadata is transport input, not authorization. The restricted role
# rereads only its own immutable published outbox evidence before inserting.
READ_PUBLISHED_ADTOF_OUTBOX_SQL = """
    SELECT
      event_id::text AS event_id,
      job_id::text AS job_id,
      stage,
      stem_name,
      event_type,
      payload,
      publication_status
    FROM public.outbox_events
    WHERE event_id = %s::uuid
"""


# PostgreSQL's clock creates the initial short lease. The task remains `leased`
# rather than `running`: MinIO checks and model-start admission are distinct,
# later steps and may not falsely record that ADTOF began CPU inference.
INSERT_FIRST_ADTOF_TASK_LEASE_SQL = """
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
      'adtof',
      'drums',
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
      stage,
      stem_name,
      request_event_id::text AS request_event_id,
      input_bucket,
      input_object_key,
      stem_mode,
      status,
      attempt_count,
      lease_token::text AS lease_token,
      lease_expires_at
"""


# A broker delivery is acknowledged after its first lease commits. If the Pod
# then crashes or a post-ack operation fails, RabbitMQ alone cannot recreate
# that exact task. This indexed query therefore recovers one *expired active*
# row directly from PostgreSQL. `FOR UPDATE SKIP LOCKED` gives overlapping Pods
# a non-blocking, one-owner decision: a concurrent recovery skips the locked
# row instead of creating a second token for it.
#
# Attempt three is excluded. A later focused terminal-expiry transition must
# record that exhausted durable outcome rather than silently granting attempt
# four. Retry-scheduled work is also excluded here: this small adapter covers
# only crash/post-ack active-lease recovery, not a future retry-scheduling
# policy.
CLAIM_NEXT_EXPIRED_ADTOF_TASK_SQL = """
    WITH candidate AS (
      SELECT task_id
      FROM public.processing_tasks
      WHERE stage = 'adtof'
        AND stem_name = 'drums'
        AND attempt_count < 3
        AND status IN ('leased', 'running')
        AND lease_expires_at <= CURRENT_TIMESTAMP
      ORDER BY lease_expires_at ASC, created_at ASC, task_id ASC
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
      task.stage,
      task.stem_name,
      task.request_event_id::text AS request_event_id,
      task.input_bucket,
      task.input_object_key,
      task.stem_mode,
      task.status,
      task.attempt_count,
      task.lease_token::text AS lease_token,
      task.lease_expires_at
"""


# MinIO metadata/download verification deliberately happens while the task is
# still `leased`: a bad drums object can later become a retry/terminal input
# outcome without falsely recording that ADTOF CPU inference began. Only after
# all preflight evidence is complete may the same worker promote its exact,
# unexpired lease to `running`.
#
# Every durable identity field and PostgreSQL's own clock remain in this
# predicate. An expired/recovered/replaced lease returns no row, preventing one
# worker replica from overwriting a newer owner's lifecycle state. `COALESCE`
# preserves the first actual model-start time through a later recovery attempt.
START_LEASED_ADTOF_TASK_SQL = """
    UPDATE public.processing_tasks
    SET
      status = 'running',
      started_at = COALESCE(started_at, CURRENT_TIMESTAMP)
    WHERE task_id = %s::uuid
      AND job_id = %s::uuid
      AND stage = 'adtof'
      AND stem_name = 'drums'
      AND status = 'leased'
      AND lease_token = %s::uuid
      AND lease_expires_at > CURRENT_TIMESTAMP
    RETURNING started_at
"""


def _protocol_error(message: str = "ADTOF task database state is invalid.") -> ADTOFTaskClaimProtocolError:
    """Create a deliberately non-sensitive protocol/row validation error."""

    return ADTOFTaskClaimProtocolError(message)


def _canonical_uuid(value: object) -> str:
    """Require canonical lower-case UUID text from a message or database row."""

    if not isinstance(value, str):
        raise _protocol_error()
    try:
        normalized = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise _protocol_error() from error
    if normalized != value:
        raise _protocol_error()
    return normalized


def _new_canonical_uuid(factory: Callable[[], UUID]) -> str:
    """Use explicit application UUIDs rather than a hidden database default."""

    generated = factory()
    if not isinstance(generated, UUID):
        raise _protocol_error("ADTOF task identifier factory is invalid.")
    return str(generated)


def _validated_lease_seconds(lease_seconds: int) -> int:
    """Keep one first lease inside the finite reviewed interval."""

    if (
        type(lease_seconds) is not int
        or not MIN_ADTOF_LEASE_SECONDS <= lease_seconds <= MAX_ADTOF_LEASE_SECONDS
    ):
        raise _protocol_error("ADTOF task lease duration is invalid.")
    return lease_seconds


def _mapping_or_error(row: Mapping[str, object] | None) -> Mapping[str, object]:
    """Reject a driver row the adapter cannot safely interpret."""

    if not isinstance(row, Mapping):
        raise _protocol_error()
    return row


def _row_text(row: Mapping[str, object], name: str) -> str:
    """Read required non-empty/control-free text without exposing it on error."""

    value = row.get(name)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise _protocol_error()
    return value


def _row_attempt_count(row: Mapping[str, object]) -> int:
    """Validate the database-enforced bounded attempt count."""

    value = row.get("attempt_count")
    if type(value) is not int or not 1 <= value <= MAX_ADTOF_TASK_ATTEMPTS:
        raise _protocol_error()
    return value


def validate_adtof_requested_message(message: object) -> ADTOFRequestedMessage:
    """Revalidate a direct request before it reaches task SQL or recovery I/O.

    Frozen dataclasses may still be created by arbitrary Python code. Repeating
    the parser's durable identity/bucket/key/size/checksum invariants keeps this
    shared validator from becoming an arbitrary private-object-key insertion or
    recovery-reader primitive.
    """

    if not isinstance(message, ADTOFRequestedMessage):
        raise TypeError("message must be ADTOFRequestedMessage.")
    event_id = _canonical_uuid(message.event_id)
    job_id = _canonical_uuid(message.job_id)
    if (
        message.stem_name != ADTOF_STEM_NAME
        or message.stem_bucket != LOCAL_UPLOADS_BUCKET
        or message.stem_object_key != f"stems/{job_id}/{ADTOF_STEM_NAME}.wav"
        or type(message.stem_content_length) is not int
        or message.stem_content_length < 1
        or not isinstance(message.stem_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", message.stem_sha256)
    ):
        raise _protocol_error("ADTOF request contract is invalid.")
    return ADTOFRequestedMessage(
        event_id=event_id,
        job_id=job_id,
        stem_name=ADTOF_STEM_NAME,
        stem_bucket=LOCAL_UPLOADS_BUCKET,
        stem_object_key=f"stems/{job_id}/{ADTOF_STEM_NAME}.wav",
        stem_content_length=message.stem_content_length,
        stem_sha256=message.stem_sha256,
    )


def _row_lease(row: Mapping[str, object]) -> ADTOFTaskLease:
    """Convert a returned first-claim lease into immutable application data."""

    if (
        _row_text(row, "status") != "leased"
        or _row_text(row, "stage") != ADTOF_STAGE
        or _row_text(row, "stem_name") != ADTOF_STEM_NAME
    ):
        raise _protocol_error()
    lease_expires_at = row.get("lease_expires_at")
    if not isinstance(lease_expires_at, datetime) or lease_expires_at.tzinfo is None:
        raise _protocol_error()
    stem_mode = _row_text(row, "stem_mode")
    if stem_mode not in ADTOF_STEM_MODES:
        raise _protocol_error()
    lease = ADTOFTaskLease(
        task_id=_canonical_uuid(_row_text(row, "task_id")),
        job_id=_canonical_uuid(_row_text(row, "job_id")),
        stem_name=ADTOF_STEM_NAME,
        request_event_id=_canonical_uuid(_row_text(row, "request_event_id")),
        input_bucket=_row_text(row, "input_bucket"),
        input_object_key=_row_text(row, "input_object_key"),
        stem_mode=stem_mode,
        attempt_count=_row_attempt_count(row),
        lease_token=_canonical_uuid(_row_text(row, "lease_token")),
        lease_expires_at=lease_expires_at,
    )
    if (
        lease.input_bucket != LOCAL_UPLOADS_BUCKET
        or lease.input_object_key != f"stems/{lease.job_id}/{ADTOF_STEM_NAME}.wav"
    ):
        raise _protocol_error()
    return lease


def _existing_task_result(
    row: Mapping[str, object], *, message: ADTOFRequestedMessage
) -> ADTOFTaskClaimResult:
    """Classify one existing durable task without mutating a broker duplicate."""

    if (
        _canonical_uuid(_row_text(row, "job_id")) != message.job_id
        or _row_text(row, "stage") != ADTOF_STAGE
        or _row_text(row, "stem_name") != ADTOF_STEM_NAME
        or _canonical_uuid(_row_text(row, "request_event_id")) != message.event_id
        or _row_text(row, "input_bucket") != message.stem_bucket
        or _row_text(row, "input_object_key") != message.stem_object_key
        or _row_text(row, "stem_mode") not in ADTOF_STEM_MODES
    ):
        raise ADTOFTaskClaimInconsistency("ADTOF request conflicts with its durable task.")
    status = _row_text(row, "status")
    if status not in {"leased", "running", "retry_scheduled", "succeeded", "failed"}:
        raise _protocol_error()
    return ADTOFTaskClaimResult(
        disposition=ADTOFTaskClaimDisposition.DUPLICATE,
        duplicate_status=status,
    )


def _validate_locked_job(
    row: Mapping[str, object], *, message: ADTOFRequestedMessage
) -> ADTOFStaleRequestReason | None:
    """Require one retained 4/6-stems Job in the MIDI-processing phase."""

    if _canonical_uuid(_row_text(row, "job_id")) != message.job_id:
        raise ADTOFTaskClaimInconsistency("ADTOF request conflicts with its durable Job.")
    if row.get("is_retained") is not True:
        return ADTOFStaleRequestReason.JOB_EXPIRED
    status = _row_text(row, "status")
    if status in {"completed", "failed"}:
        return ADTOFStaleRequestReason.JOB_TERMINAL
    if status != "midi_processing" or _row_text(row, "stem_mode") not in ADTOF_STEM_MODES:
        raise ADTOFTaskClaimInconsistency("ADTOF request conflicts with its durable Job.")
    return None


def _validate_outbox_payload(payload: object, *, message: ADTOFRequestedMessage) -> None:
    """Require durable JSONB evidence to equal the parser-approved message."""

    if not isinstance(payload, Mapping) or set(payload) != {
        "schema_version",
        "job_id",
        "stem_name",
        "stem",
    }:
        raise ADTOFTaskClaimInconsistency("ADTOF request conflicts with its durable outbox event.")
    stem = payload.get("stem")
    if not isinstance(stem, Mapping) or set(stem) != {
        "bucket",
        "object_key",
        "content_type",
        "size_bytes",
        "sha256",
    }:
        raise ADTOFTaskClaimInconsistency("ADTOF request conflicts with its durable outbox event.")
    if (
        payload.get("schema_version") != 1
        or payload.get("job_id") != message.job_id
        or payload.get("stem_name") != ADTOF_STEM_NAME
        or stem.get("bucket") != message.stem_bucket
        or stem.get("object_key") != message.stem_object_key
        or stem.get("content_type") != STEM_CONTENT_TYPE
        or type(stem.get("size_bytes")) is not int
        or stem.get("size_bytes") != message.stem_content_length
        or stem.get("sha256") != message.stem_sha256
    ):
        raise ADTOFTaskClaimInconsistency("ADTOF request conflicts with its durable outbox event.")


def _validate_outbox_event(
    row: Mapping[str, object] | None, *, message: ADTOFRequestedMessage
) -> None:
    """Require a matching immutable event whose broker publication completed."""

    event = _mapping_or_error(row)
    if (
        _canonical_uuid(_row_text(event, "event_id")) != message.event_id
        or _canonical_uuid(_row_text(event, "job_id")) != message.job_id
        or _row_text(event, "stage") != ADTOF_STAGE
        or _row_text(event, "stem_name") != ADTOF_STEM_NAME
        or _row_text(event, "event_type") != ADTOF_REQUESTED_EVENT_TYPE
        or _row_text(event, "publication_status") != "published"
    ):
        raise ADTOFTaskClaimInconsistency("ADTOF request conflicts with its durable outbox event.")
    _validate_outbox_payload(event.get("payload"), message=message)


def claim_adtof_task_for_delivery(
    cursor: DatabaseCursor,
    *,
    message: ADTOFRequestedMessage,
    lease_seconds: int = DEFAULT_ADTOF_LEASE_SECONDS,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> ADTOFTaskClaimResult:
    """Claim one first drums lease or classify a duplicate/stale delivery.

    Call this inside one short PostgreSQL transaction. A returned ``CLAIMED``
    result is acknowledgement-safe only after the surrounding transaction
    commits. ``DUPLICATE`` and ``STALE`` make no mutation; a durable conflict
    raises so a later lifecycle adapter, not this function, decides any terminal
    update. The function itself does not open a connection or transaction.
    """

    validated_message = validate_adtof_requested_message(message)
    bounded_lease_seconds = _validated_lease_seconds(lease_seconds)

    cursor.execute(LOCK_EXISTING_ADTOF_TASK_SQL, (validated_message.job_id,))
    existing_task = cursor.fetchone()
    if existing_task is not None:
        return _existing_task_result(_mapping_or_error(existing_task), message=validated_message)

    cursor.execute(LOCK_JOB_FOR_ADTOF_CLAIM_SQL, (validated_message.job_id,))
    locked_job = cursor.fetchone()
    if locked_job is None:
        return ADTOFTaskClaimResult(
            disposition=ADTOFTaskClaimDisposition.STALE,
            stale_reason=ADTOFStaleRequestReason.JOB_MISSING,
        )

    # Re-read while holding the Job lock. A second consumer that inserted the
    # same canonical task while we waited now becomes a read-only duplicate.
    cursor.execute(LOCK_EXISTING_ADTOF_TASK_SQL, (validated_message.job_id,))
    existing_after_job_lock = cursor.fetchone()
    if existing_after_job_lock is not None:
        return _existing_task_result(
            _mapping_or_error(existing_after_job_lock),
            message=validated_message,
        )

    stale_reason = _validate_locked_job(_mapping_or_error(locked_job), message=validated_message)
    if stale_reason is not None:
        return ADTOFTaskClaimResult(
            disposition=ADTOFTaskClaimDisposition.STALE,
            stale_reason=stale_reason,
        )

    cursor.execute(READ_PUBLISHED_ADTOF_OUTBOX_SQL, (validated_message.event_id,))
    _validate_outbox_event(cursor.fetchone(), message=validated_message)

    task_id = _new_canonical_uuid(uuid_factory)
    lease_token = _new_canonical_uuid(uuid_factory)
    stem_mode = _row_text(_mapping_or_error(locked_job), "stem_mode")
    cursor.execute(
        INSERT_FIRST_ADTOF_TASK_LEASE_SQL,
        (
            task_id,
            validated_message.job_id,
            validated_message.event_id,
            validated_message.stem_bucket,
            validated_message.stem_object_key,
            stem_mode,
            lease_token,
            bounded_lease_seconds,
        ),
    )
    lease = _row_lease(_mapping_or_error(cursor.fetchone()))
    if (
        lease.task_id != task_id
        or lease.job_id != validated_message.job_id
        or lease.stem_name != ADTOF_STEM_NAME
        or lease.request_event_id != validated_message.event_id
        or lease.input_bucket != validated_message.stem_bucket
        or lease.input_object_key != validated_message.stem_object_key
        or lease.stem_mode != stem_mode
        or lease.lease_token != lease_token
        or lease.attempt_count != 1
    ):
        raise _protocol_error()
    return ADTOFTaskClaimResult(
        disposition=ADTOFTaskClaimDisposition.CLAIMED,
        lease=lease,
    )


def claim_next_expired_adtof_task(
    cursor: DatabaseCursor,
    *,
    lease_seconds: int = DEFAULT_ADTOF_LEASE_SECONDS,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> ADTOFTaskLease | None:
    """Claim one expired active ADTOF task with a fresh, bounded lease token.

    Invoke this pure SQL decision inside one short PostgreSQL transaction.
    `None` is a normal indexed idle result: no expired ADTOF `leased`/`running`
    task was eligible when PostgreSQL evaluated the candidate query. A returned
    lease is only recovery *candidate* evidence. The following read-only
    boundary must reconstruct its matching strict request from the published
    outbox event before any MinIO, ADTOF, or broker action occurs.

    This function opens no connection or transaction, does not inspect an
    outbox payload, acknowledge RabbitMQ, run ADTOF, schedule a retry, sleep,
    build an image, or call Kubernetes. A final exhausted third attempt is
    deliberately left for a separate guarded terminal-state policy.
    """

    if not callable(getattr(cursor, "execute", None)) or not callable(getattr(cursor, "fetchone", None)):
        raise TypeError("cursor must provide execute and fetchone.")
    bounded_lease_seconds = _validated_lease_seconds(lease_seconds)
    lease_token = _new_canonical_uuid(uuid_factory)
    cursor.execute(
        CLAIM_NEXT_EXPIRED_ADTOF_TASK_SQL,
        (lease_token, bounded_lease_seconds),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    lease = _row_lease(_mapping_or_error(row))
    # The query increments an existing attempt, so one returned attempt proves
    # an expired first attempt was reclaimed; it must never be a new first
    # lease or an impossible fourth attempt.
    if (
        lease.lease_token != lease_token
        or not 2 <= lease.attempt_count <= MAX_ADTOF_TASK_ATTEMPTS
    ):
        raise _protocol_error()
    return lease


def start_leased_adtof_task(
    cursor: DatabaseCursor,
    *,
    lease: ADTOFTaskLease,
) -> datetime | None:
    """Atomically promote one verified lease to model-eligible ``running``.

    Call inside a short PostgreSQL write transaction only after the task's
    private drums object has passed metadata, streamed byte-count, and SHA-256
    checks. A returned timestamp proves PostgreSQL still recognizes this exact
    unexpired token; only then may a later composition invoke ADTOF. ``None``
    is normal ownership loss caused by expiry, recovery, or another lifecycle
    transition. The caller must stop without model work, output writes, or a
    task-completion transition in that case.

    This pure SQL adapter opens no connection or transaction. It does not
    contact MinIO, renew a lease, receive/acknowledge RabbitMQ, invoke ADTOF,
    write an object, build an image, or use Kubernetes.
    """

    if not isinstance(lease, ADTOFTaskLease):
        raise TypeError("lease must be ADTOFTaskLease.")
    canonical_task_id = _canonical_uuid(lease.task_id)
    canonical_job_id = _canonical_uuid(lease.job_id)
    _canonical_uuid(lease.request_event_id)
    canonical_lease_token = _canonical_uuid(lease.lease_token)
    expected_key = f"stems/{canonical_job_id}/{ADTOF_STEM_NAME}.wav"
    if (
        lease.stem_name != ADTOF_STEM_NAME
        or lease.stem_mode not in ADTOF_STEM_MODES
        or lease.input_bucket != LOCAL_UPLOADS_BUCKET
        or lease.input_object_key != expected_key
        or type(lease.attempt_count) is not int
        or not 1 <= lease.attempt_count <= MAX_ADTOF_TASK_ATTEMPTS
        or not isinstance(lease.lease_expires_at, datetime)
        or lease.lease_expires_at.tzinfo is None
    ):
        raise _protocol_error("ADTOF task lease is invalid.")

    cursor.execute(
        START_LEASED_ADTOF_TASK_SQL,
        (canonical_task_id, canonical_job_id, canonical_lease_token),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    started_at = _mapping_or_error(row).get("started_at")
    if not isinstance(started_at, datetime) or started_at.tzinfo is None:
        raise _protocol_error()
    return started_at
