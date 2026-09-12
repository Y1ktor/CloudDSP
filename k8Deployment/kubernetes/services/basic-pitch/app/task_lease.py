"""Durable first-claim SQL for one Basic Pitch processing task.

This is deliberately a pure PostgreSQL boundary: it imports no Psycopg
connection factory, Pika consumer, Boto3 client, Basic Pitch model, or
Kubernetes API. A later composition layer must open one *short* transaction,
call :func:`claim_basic_pitch_task_for_delivery`, commit a claimed lease, and
only then acknowledge the RabbitMQ delivery. That order makes at-least-once
broker redelivery converge on one durable task rather than duplicate MIDI work.

The table's v005 compound constraint independently limits this stage to a
non-drum ``stems/{job_id}/{stem_name}.wav`` input. This module repeats the
same evidence against the authoritative Job/outbox rows before it inserts the
lease. PostgreSQL is therefore the authority for whether the logical
``(job_id, 'basic-pitch', stem_name)`` task may begin; RabbitMQ only transports
a request to examine.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Callable, Protocol
from uuid import UUID, uuid4

from app.basic_pitch_requested_message import (
    BASIC_PITCH_REQUESTED_EVENT_TYPE,
    BASIC_PITCH_STAGE,
    BASIC_PITCH_STEM_NAMES,
    BasicPitchRequestContractError,
    BasicPitchRequestedMessage,
    LOCAL_UPLOADS_BUCKET,
    STEM_CONTENT_TYPE,
    build_basic_pitch_midi_output,
)


# Basic Pitch is CPU-bound locally, but its first lease is deliberately the
# same bounded 15-minute ownership window as Demucs. A later runtime renews it
# before model work exceeds the lease; an invalid Deployment setting cannot
# make a task effectively permanent or too short for ordinary scheduling.
DEFAULT_BASIC_PITCH_LEASE_SECONDS = 15 * 60
MIN_BASIC_PITCH_LEASE_SECONDS = 60
MAX_BASIC_PITCH_LEASE_SECONDS = 60 * 60
MAX_BASIC_PITCH_TASK_ATTEMPTS = 3

# This is a local copy of the reviewed Demucs output vocabulary, not an import
# from the Demucs service. It keeps the Basic Pitch worker independently
# deployable while proving that an event's requested non-drum stem can exist in
# the authoritative Job's selected separation mode.
BASIC_PITCH_STEMS_BY_MODE: dict[str, frozenset[str]] = {
    "2-stems": frozenset({"vocals", "no_vocals"}),
    "4-stems": frozenset({"vocals", "bass", "other"}),
    "6-stems": frozenset({"vocals", "bass", "other", "guitar", "piano"}),
}


class BasicPitchTaskLeaseProtocolError(RuntimeError):
    """Raise a safe category for an unusable database row or lease setting.

    The exception intentionally omits database diagnostics, object keys,
    payloads, subjects, and credentials. A later runtime may retain the driver
    cause privately while emitting only this reviewed operational category.
    """


class BasicPitchTaskClaimInconsistency(RuntimeError):
    """A syntactically valid AMQP message conflicts with durable state.

    This is neither a harmless duplicate nor stale history. A later result
    transition must record a bounded durable outcome before it can acknowledge
    the delivery; this first-claim layer never invents an error update itself.
    """


class BasicPitchTaskClaimDisposition(StrEnum):
    """The only safe outcomes of a Basic Pitch first-claim transaction."""

    CLAIMED = "claimed"
    DUPLICATE = "duplicate"
    STALE = "stale"


class BasicPitchStaleRequestReason(StrEnum):
    """Non-sensitive cases where an old delivery needs no new task row."""

    JOB_MISSING = "job_missing"
    JOB_EXPIRED = "job_expired"
    JOB_TERMINAL = "job_terminal"


class DatabaseCursor(Protocol):
    """The small dictionary-row cursor surface this SQL boundary requires."""

    def execute(self, query: str, params: tuple[object, ...]) -> object:
        """Execute parameterized SQL; no external value enters query text."""

    def fetchone(self) -> Mapping[str, object] | None:
        """Return at most one dictionary-shaped PostgreSQL row."""


@dataclass(frozen=True)
class BasicPitchTaskLease:
    """One committed ownership token for a single non-drum MIDI task."""

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
class BasicPitchTaskClaimResult:
    """A transport-independent claim result for the later AMQP composition."""

    disposition: BasicPitchTaskClaimDisposition
    lease: BasicPitchTaskLease | None = None
    duplicate_status: str | None = None
    stale_reason: BasicPitchStaleRequestReason | None = None


# Lock precisely one per-stem task. The v005 idempotency key enforces the same
# identity in storage; selecting it first keeps a broker duplicate read-only.
LOCK_EXISTING_BASIC_PITCH_TASK_SQL = """
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
      AND stage = 'basic-pitch'
      AND stem_name = %s
    FOR UPDATE
"""


# A missing task is a gap and cannot itself be locked. Locking the Job row
# serializes this insertion with completion/deletion changes, then a second
# task lookup closes the race where another consumer inserted it while waiting.
LOCK_JOB_FOR_BASIC_PITCH_CLAIM_SQL = """
    SELECT
      job_id::text AS job_id,
      stem_mode,
      status,
      revision,
      (expires_at > CURRENT_TIMESTAMP) AS is_retained
    FROM public.jobs
    WHERE job_id = %s::uuid
    FOR UPDATE
"""


# The immutable outbox payload is re-read because AMQP metadata is transport
# input, not durable authorization. The Basic Pitch database role has SELECT
# only on the small identity/route/payload/publication fields in this query.
READ_PUBLISHED_BASIC_PITCH_OUTBOX_SQL = """
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


# PostgreSQL calculates lease time, avoiding a worker-clock decision. The row
# begins `leased`, not `running`: a later MinIO verification and model-start
# boundary must own the guarded transition to `running` before it invokes ML.
INSERT_FIRST_BASIC_PITCH_TASK_LEASE_SQL = """
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
      'basic-pitch',
      %s,
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


# A scheduled retry is PostgreSQL-authoritative after the original RabbitMQ
# delivery was acknowledged. This indexed one-row claim gives a later recovery
# component a fresh lease without trusting an old delivery or scanning task
# history. ``FOR UPDATE SKIP LOCKED`` permits multiple future replicas to pass
# over a candidate another transaction is already leasing, instead of waiting
# or granting the same task to two workers.
#
# Attempt three is deliberately excluded. A temporary storage failure on that
# final permitted lease must follow the separate guarded terminal-exhaustion
# transition rather than silently creating attempt four. Expired active-task
# recovery is also intentionally outside this first narrow retry-schedule
# adapter; it needs its own reviewed policy because a model may have started.
CLAIM_NEXT_DUE_BASIC_PITCH_RETRY_SQL = """
    WITH candidate AS (
      SELECT task_id
      FROM public.processing_tasks
      WHERE stage = 'basic-pitch'
        AND status = 'retry_scheduled'
        AND attempt_count < %s::integer
        AND available_at <= CURRENT_TIMESTAMP
      ORDER BY available_at ASC, created_at ASC, task_id ASC
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


# MinIO metadata/download verification happens while the task is still
# `leased`: an unsuitable stem can later reach a terminal/retry decision
# without falsely recording that the Basic Pitch model began. Only after all
# preflight work succeeds may the same worker atomically enter `running`.
#
# Every durable identity field and PostgreSQL's clock remain in the predicate.
# A worker that lost/recovered/expired its token gets no row back instead of
# overwriting another replica's state. `COALESCE` preserves the first actual
# model-start time through a later recovery/retry attempt.
START_LEASED_BASIC_PITCH_TASK_SQL = """
    UPDATE public.processing_tasks
    SET
      status = 'running',
      started_at = COALESCE(started_at, CURRENT_TIMESTAMP)
    WHERE task_id = %s::uuid
      AND job_id = %s::uuid
      AND stage = 'basic-pitch'
      AND stem_name = %s
      AND status = 'leased'
      AND lease_token = %s::uuid
      AND lease_expires_at > CURRENT_TIMESTAMP
    RETURNING started_at
"""


def _canonical_uuid(value: object) -> str:
    """Require canonical lower-case UUID text from a row or UUID factory."""

    if not isinstance(value, str):
        raise BasicPitchTaskLeaseProtocolError("Basic Pitch task database state is invalid.")
    try:
        normalized = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise BasicPitchTaskLeaseProtocolError("Basic Pitch task database state is invalid.") from error
    if normalized != value:
        raise BasicPitchTaskLeaseProtocolError("Basic Pitch task database state is invalid.")
    return normalized


def _new_canonical_uuid(factory: Callable[[], UUID]) -> str:
    """Generate an explicit application UUID rather than using a DB default."""

    generated = factory()
    if not isinstance(generated, UUID):
        raise BasicPitchTaskLeaseProtocolError("Basic Pitch task identifier factory is invalid.")
    return str(generated)


def _validated_lease_seconds(lease_seconds: int) -> int:
    """Keep one worker lease within the reviewed finite range before binding."""

    if (
        type(lease_seconds) is not int
        or not MIN_BASIC_PITCH_LEASE_SECONDS <= lease_seconds <= MAX_BASIC_PITCH_LEASE_SECONDS
    ):
        raise BasicPitchTaskLeaseProtocolError("Basic Pitch task lease duration is invalid.")
    return lease_seconds


def _mapping_or_error(row: Mapping[str, object] | None) -> Mapping[str, object]:
    """Reject a driver row the adapter cannot safely interpret."""

    if not isinstance(row, Mapping):
        raise BasicPitchTaskLeaseProtocolError("Basic Pitch task database state is invalid.")
    return row


def _row_text(row: Mapping[str, object], name: str) -> str:
    """Read one safe non-empty text field without exposing its value on error."""

    value = row.get(name)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise BasicPitchTaskLeaseProtocolError("Basic Pitch task database state is invalid.")
    return value


def _row_attempt_count(row: Mapping[str, object]) -> int:
    """Validate the database-enforced bounded attempt count before using it."""

    value = row.get("attempt_count")
    if type(value) is not int or not 1 <= value <= MAX_BASIC_PITCH_TASK_ATTEMPTS:
        raise BasicPitchTaskLeaseProtocolError("Basic Pitch task database state is invalid.")
    return value


def _row_lease(row: Mapping[str, object]) -> BasicPitchTaskLease:
    """Convert a returned first-claim lease into immutable application data."""

    if _row_text(row, "status") != "leased" or _row_text(row, "stage") != BASIC_PITCH_STAGE:
        raise BasicPitchTaskLeaseProtocolError("Basic Pitch task database state is invalid.")
    stem_name = _row_text(row, "stem_name")
    if stem_name not in BASIC_PITCH_STEM_NAMES:
        raise BasicPitchTaskLeaseProtocolError("Basic Pitch task database state is invalid.")
    lease_expires_at = row.get("lease_expires_at")
    if not isinstance(lease_expires_at, datetime) or lease_expires_at.tzinfo is None:
        raise BasicPitchTaskLeaseProtocolError("Basic Pitch task database state is invalid.")
    return BasicPitchTaskLease(
        task_id=_canonical_uuid(_row_text(row, "task_id")),
        job_id=_canonical_uuid(_row_text(row, "job_id")),
        stem_name=stem_name,
        request_event_id=_canonical_uuid(_row_text(row, "request_event_id")),
        input_bucket=_row_text(row, "input_bucket"),
        input_object_key=_row_text(row, "input_object_key"),
        stem_mode=_row_text(row, "stem_mode"),
        attempt_count=_row_attempt_count(row),
        lease_token=_canonical_uuid(_row_text(row, "lease_token")),
        lease_expires_at=lease_expires_at,
    )


def _existing_task_result(
    row: Mapping[str, object], *, message: BasicPitchRequestedMessage
) -> BasicPitchTaskClaimResult:
    """Classify one existing task without mutating a broker duplicate."""

    expected_key = f"stems/{message.job_id}/{message.stem_name}.wav"
    if (
        _canonical_uuid(_row_text(row, "job_id")) != message.job_id
        or _row_text(row, "stage") != BASIC_PITCH_STAGE
        or _row_text(row, "stem_name") != message.stem_name
        or _canonical_uuid(_row_text(row, "request_event_id")) != message.event_id
        or _row_text(row, "input_bucket") != message.stem_bucket
        or _row_text(row, "input_object_key") != expected_key
    ):
        raise BasicPitchTaskClaimInconsistency("Basic Pitch request conflicts with its durable task.")
    status = _row_text(row, "status")
    if status not in {"leased", "running", "retry_scheduled", "succeeded", "failed"}:
        raise BasicPitchTaskLeaseProtocolError("Basic Pitch task database state is invalid.")
    return BasicPitchTaskClaimResult(
        disposition=BasicPitchTaskClaimDisposition.DUPLICATE,
        duplicate_status=status,
    )


def _validate_locked_job(
    row: Mapping[str, object], *, message: BasicPitchRequestedMessage
) -> BasicPitchStaleRequestReason | None:
    """Check one locked Job is retained and has legitimately entered MIDI work."""

    if _canonical_uuid(_row_text(row, "job_id")) != message.job_id:
        raise BasicPitchTaskClaimInconsistency("Basic Pitch request conflicts with its durable Job.")
    if row.get("is_retained") is not True:
        return BasicPitchStaleRequestReason.JOB_EXPIRED
    status = _row_text(row, "status")
    if status in {"completed", "failed"}:
        return BasicPitchStaleRequestReason.JOB_TERMINAL
    stem_mode = _row_text(row, "stem_mode")
    if (
        status != "midi_processing"
        or stem_mode not in BASIC_PITCH_STEMS_BY_MODE
        or message.stem_name not in BASIC_PITCH_STEMS_BY_MODE[stem_mode]
    ):
        raise BasicPitchTaskClaimInconsistency("Basic Pitch request conflicts with its durable Job.")
    return None


def _validate_outbox_payload(payload: object, *, message: BasicPitchRequestedMessage) -> None:
    """Require durable JSONB evidence to be byte-for-byte-contract equivalent.

    Psycopg decodes PostgreSQL JSONB into a mapping. This deliberately rejects
    a string or arbitrary object rather than adding permissive decoding here;
    the future database connection adapter must preserve that expected JSONB
    mapping behavior before it can invoke this security-sensitive boundary.
    """

    if not isinstance(payload, Mapping) or set(payload) != {
        "schema_version",
        "job_id",
        "stem_name",
        "stem",
    }:
        raise BasicPitchTaskClaimInconsistency("Basic Pitch request conflicts with its durable outbox event.")
    stem = payload.get("stem")
    if not isinstance(stem, Mapping) or set(stem) != {
        "bucket",
        "object_key",
        "content_type",
        "size_bytes",
        "sha256",
    }:
        raise BasicPitchTaskClaimInconsistency("Basic Pitch request conflicts with its durable outbox event.")
    if (
        payload.get("schema_version") != 1
        or payload.get("job_id") != message.job_id
        or payload.get("stem_name") != message.stem_name
        or stem.get("bucket") != message.stem_bucket
        or stem.get("object_key") != message.stem_object_key
        or stem.get("content_type") != STEM_CONTENT_TYPE
        or type(stem.get("size_bytes")) is not int
        or stem.get("size_bytes") != message.stem_content_length
        or stem.get("sha256") != message.stem_sha256
    ):
        raise BasicPitchTaskClaimInconsistency("Basic Pitch request conflicts with its durable outbox event.")


def _validate_outbox_event(
    row: Mapping[str, object] | None, *, message: BasicPitchRequestedMessage
) -> None:
    """Require the one matching immutable event to have broker-confirmed publish."""

    event = _mapping_or_error(row)
    if (
        _canonical_uuid(_row_text(event, "event_id")) != message.event_id
        or _canonical_uuid(_row_text(event, "job_id")) != message.job_id
        or _row_text(event, "stage") != BASIC_PITCH_STAGE
        or _row_text(event, "stem_name") != message.stem_name
        or _row_text(event, "event_type") != BASIC_PITCH_REQUESTED_EVENT_TYPE
        or _row_text(event, "publication_status") != "published"
    ):
        raise BasicPitchTaskClaimInconsistency("Basic Pitch request conflicts with its durable outbox event.")
    _validate_outbox_payload(event.get("payload"), message=message)


def claim_basic_pitch_task_for_delivery(
    cursor: DatabaseCursor,
    *,
    message: BasicPitchRequestedMessage,
    lease_seconds: int = DEFAULT_BASIC_PITCH_LEASE_SECONDS,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> BasicPitchTaskClaimResult:
    """Claim one first Basic Pitch lease or classify duplicate/stale delivery.

    Invoke this inside one short database transaction. Only after a
    ``CLAIMED`` result commits may a later AMQP composition acknowledge the
    delivery. Duplicate/stale results make no mutation and can also be safely
    acknowledged. Any inconsistency raises so a later guarded terminal-state
    layer—not this adapter—can decide the durable outcome.
    """

    if not isinstance(message, BasicPitchRequestedMessage):
        raise TypeError("message must be BasicPitchRequestedMessage.")
    # A frozen dataclass can still be constructed directly by a Python caller.
    # Reusing the public output-plan validator prevents such a hand-built value
    # from bypassing the parser's UUID/stem/bucket/key/size/checksum contract.
    # The returned MIDI coordinate is intentionally not used yet: this task
    # claims input ownership only and must not imply that output was created.
    try:
        build_basic_pitch_midi_output(message)
    except BasicPitchRequestContractError as error:
        raise BasicPitchTaskLeaseProtocolError("Basic Pitch request contract is invalid.") from error
    bounded_lease_seconds = _validated_lease_seconds(lease_seconds)

    cursor.execute(LOCK_EXISTING_BASIC_PITCH_TASK_SQL, (message.job_id, message.stem_name))
    existing_task = cursor.fetchone()
    if existing_task is not None:
        return _existing_task_result(_mapping_or_error(existing_task), message=message)

    cursor.execute(LOCK_JOB_FOR_BASIC_PITCH_CLAIM_SQL, (message.job_id,))
    locked_job = cursor.fetchone()
    if locked_job is None:
        return BasicPitchTaskClaimResult(
            disposition=BasicPitchTaskClaimDisposition.STALE,
            stale_reason=BasicPitchStaleRequestReason.JOB_MISSING,
        )

    # Re-read under the Job lock to close a concurrent first-claim race.
    cursor.execute(LOCK_EXISTING_BASIC_PITCH_TASK_SQL, (message.job_id, message.stem_name))
    existing_after_job_lock = cursor.fetchone()
    if existing_after_job_lock is not None:
        return _existing_task_result(_mapping_or_error(existing_after_job_lock), message=message)

    stale_reason = _validate_locked_job(_mapping_or_error(locked_job), message=message)
    if stale_reason is not None:
        return BasicPitchTaskClaimResult(
            disposition=BasicPitchTaskClaimDisposition.STALE,
            stale_reason=stale_reason,
        )

    cursor.execute(READ_PUBLISHED_BASIC_PITCH_OUTBOX_SQL, (message.event_id,))
    _validate_outbox_event(cursor.fetchone(), message=message)

    task_id = _new_canonical_uuid(uuid_factory)
    lease_token = _new_canonical_uuid(uuid_factory)
    cursor.execute(
        INSERT_FIRST_BASIC_PITCH_TASK_LEASE_SQL,
        (
            task_id,
            message.job_id,
            message.stem_name,
            message.event_id,
            message.stem_bucket,
            message.stem_object_key,
            _row_text(_mapping_or_error(locked_job), "stem_mode"),
            lease_token,
            bounded_lease_seconds,
        ),
    )
    lease = _row_lease(_mapping_or_error(cursor.fetchone()))
    if (
        lease.task_id != task_id
        or lease.job_id != message.job_id
        or lease.stem_name != message.stem_name
        or lease.request_event_id != message.event_id
        or lease.input_bucket != message.stem_bucket
        or lease.input_object_key != message.stem_object_key
        or lease.lease_token != lease_token
        or lease.attempt_count != 1
    ):
        raise BasicPitchTaskLeaseProtocolError("Basic Pitch task database state is invalid.")
    return BasicPitchTaskClaimResult(
        disposition=BasicPitchTaskClaimDisposition.CLAIMED,
        lease=lease,
    )


def claim_next_due_basic_pitch_retry(
    cursor: DatabaseCursor,
    *,
    lease_seconds: int = DEFAULT_BASIC_PITCH_LEASE_SECONDS,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> BasicPitchTaskLease | None:
    """Claim one due retry-scheduled Basic Pitch task with a fresh lease.

    Invoke this in one short PostgreSQL transaction. ``None`` is a normal
    indexed idle result: no Basic Pitch retry was due when PostgreSQL evaluated
    the candidate query. A returned lease carries a new token and incremented
    attempt count; it is not permission to run the model until a later recovery
    boundary reconstructs the strict request evidence and completes the normal
    pre-model validation/start transition.

    This pure decision creates no connection, transaction, RabbitMQ message,
    MinIO request, model process, sleep, loop, or Kubernetes resource. It also
    deliberately ignores expired ``leased``/``running`` rows: recovering a
    possibly started model attempt requires a separate reviewed policy.
    """

    bounded_lease_seconds = _validated_lease_seconds(lease_seconds)
    lease_token = _new_canonical_uuid(uuid_factory)
    cursor.execute(
        CLAIM_NEXT_DUE_BASIC_PITCH_RETRY_SQL,
        (
            MAX_BASIC_PITCH_TASK_ATTEMPTS,
            lease_token,
            bounded_lease_seconds,
        ),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    lease = _row_lease(_mapping_or_error(row))
    expected_key = f"stems/{lease.job_id}/{lease.stem_name}.wav"
    if (
        lease.lease_token != lease_token
        or not 2 <= lease.attempt_count <= MAX_BASIC_PITCH_TASK_ATTEMPTS
        or lease.input_bucket != LOCAL_UPLOADS_BUCKET
        or lease.input_object_key != expected_key
        or lease.stem_mode not in BASIC_PITCH_STEMS_BY_MODE
        or lease.stem_name not in BASIC_PITCH_STEMS_BY_MODE[lease.stem_mode]
    ):
        raise BasicPitchTaskLeaseProtocolError("Basic Pitch task database state is invalid.")
    return lease


def start_leased_basic_pitch_task(
    cursor: DatabaseCursor,
    *,
    lease: BasicPitchTaskLease,
) -> datetime | None:
    """Atomically promote one verified lease to model-eligible ``running``.

    Call this inside a short PostgreSQL write transaction only after the
    claimed stem has passed the MinIO metadata/download/hash boundaries. A
    returned timestamp proves PostgreSQL still recognizes this exact,
    unexpired token; only then may a later composition start Basic Pitch.
    ``None`` is a normal ownership-loss signal caused by expiry, recovery, or
    another lifecycle transition. The caller must stop without model work,
    object writes, or a result transition in that case.

    This pure SQL adapter creates no connection or transaction and does not
    inspect MinIO evidence, renew a lease, call Basic Pitch, acknowledge
    RabbitMQ, or communicate with Kubernetes.
    """

    if not isinstance(lease, BasicPitchTaskLease):
        raise TypeError("lease must be BasicPitchTaskLease.")
    canonical_task_id = _canonical_uuid(lease.task_id)
    canonical_job_id = _canonical_uuid(lease.job_id)
    _canonical_uuid(lease.request_event_id)
    canonical_lease_token = _canonical_uuid(lease.lease_token)
    expected_key = f"stems/{canonical_job_id}/{lease.stem_name}.wav"
    if (
        lease.stem_name not in BASIC_PITCH_STEM_NAMES
        or lease.stem_mode not in BASIC_PITCH_STEMS_BY_MODE
        or lease.stem_name not in BASIC_PITCH_STEMS_BY_MODE[lease.stem_mode]
        or lease.input_bucket != LOCAL_UPLOADS_BUCKET
        or lease.input_object_key != expected_key
        or type(lease.attempt_count) is not int
        or not 1 <= lease.attempt_count <= MAX_BASIC_PITCH_TASK_ATTEMPTS
        or not isinstance(lease.lease_expires_at, datetime)
        or lease.lease_expires_at.tzinfo is None
    ):
        raise BasicPitchTaskLeaseProtocolError("Basic Pitch task lease is invalid.")

    cursor.execute(
        START_LEASED_BASIC_PITCH_TASK_SQL,
        (canonical_task_id, canonical_job_id, lease.stem_name, canonical_lease_token),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    started_at = _mapping_or_error(row).get("started_at")
    if not isinstance(started_at, datetime) or started_at.tzinfo is None:
        raise BasicPitchTaskLeaseProtocolError("Basic Pitch task database state is invalid.")
    return started_at
