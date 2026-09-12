"""Reconstruct strict Basic Pitch request evidence for one recovered retry.

RabbitMQ has already delivered and been acknowledged for a task that later
entered durable ``retry_scheduled`` state.  When PostgreSQL grants that task a
fresh recovery lease, there is no new AMQP frame to parse.  This module reads
the *same published immutable outbox event* that originally authorized the
task and rebuilds :class:`BasicPitchRequestedMessage` from that durable
evidence.

It is deliberately a small, read-only PostgreSQL boundary.  It neither claims
work nor opens/commits a transaction; it does not publish a replacement
RabbitMQ message, call MinIO, start Basic Pitch, sleep, or create Kubernetes
resources.  A later composition must call the due-retry claim and this reader
inside one short transaction, commit their matching result, and only then use
the ordinary pre-model execution path.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final
from uuid import UUID

from app.basic_pitch_requested_message import (
    BASIC_PITCH_REQUESTED_EVENT_TYPE,
    BASIC_PITCH_STAGE,
    BASIC_PITCH_STEM_NAMES,
    LOCAL_UPLOADS_BUCKET,
    STEM_CONTENT_TYPE,
    BasicPitchRequestContractError,
    BasicPitchRequestedMessage,
    build_basic_pitch_midi_output,
)
from app.task_lease import (
    BASIC_PITCH_STEMS_BY_MODE,
    MAX_BASIC_PITCH_TASK_ATTEMPTS,
    BasicPitchTaskLease,
    DatabaseCursor,
)


class BasicPitchRecoveryRequestProtocolError(RuntimeError):
    """The fresh lease or its matching durable outbox evidence is unusable.

    The public category deliberately omits object keys, raw JSONB, credentials,
    and database driver diagnostics.  A caller must roll back its outer
    recovery transaction rather than run a model from incomplete evidence.
    """


# Joining the task row to the outbox event means the recovery lease is the
# authority for both *which* event is read and whether this reader still owns
# it.  Every lease coordinate is rebound in the predicate: a stale Pod cannot
# revive a task after another attempt, terminal transition, expiry, or token
# replacement.  The immutable outbox event is required to have a confirmed
# publication record; a merely pending event never becomes recovery input.
#
# This is intentionally a SELECT, not a lock or lifecycle change.  The caller
# runs it immediately after the `FOR UPDATE SKIP LOCKED` claim in the same
# short transaction.  Later `leased -> running` SQL remains the final guarded
# permission before the model starts, which also covers ownership loss after
# this evidence read commits.
READ_CURRENT_BASIC_PITCH_RECOVERY_REQUEST_SQL: Final[str] = """
    SELECT
      event.event_id::text AS event_id,
      event.job_id::text AS job_id,
      event.stage,
      event.stem_name,
      event.event_type,
      event.payload,
      event.publication_status
    FROM public.processing_tasks AS task
    JOIN public.outbox_events AS event
      ON event.event_id = task.request_event_id
      AND event.job_id = task.job_id
      AND event.stage = task.stage
      AND event.stem_name = task.stem_name
    WHERE task.task_id = %s::uuid
      AND task.job_id = %s::uuid
      AND task.stage = 'basic-pitch'
      AND task.stem_name = %s
      AND task.request_event_id = %s::uuid
      AND task.input_bucket = %s
      AND task.input_object_key = %s
      AND task.stem_mode = %s
      AND task.status = 'leased'
      AND task.attempt_count = %s::integer
      AND task.lease_token = %s::uuid
      AND task.lease_expires_at > CURRENT_TIMESTAMP
      AND event.event_type = 'basic-pitch.requested'
      AND event.publication_status = 'published'
"""


def _error() -> BasicPitchRecoveryRequestProtocolError:
    """Return one stable failure without putting private evidence in logs."""

    return BasicPitchRecoveryRequestProtocolError("Basic Pitch recovery evidence is invalid.")


def _canonical_uuid(value: object) -> str:
    """Require canonical lower-case UUID text before it reaches SQL or a message."""

    if not isinstance(value, str):
        raise _error()
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise _error() from error
    if canonical != value:
        raise _error()
    return canonical


def _text(row: Mapping[str, object], name: str) -> str:
    """Read a non-empty, NUL-free database text field without leaking its value."""

    value = row.get(name)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise _error()
    return value


def _validated_recovery_lease(value: object) -> BasicPitchTaskLease:
    """Accept only a fresh *retry* lease, never an arbitrary hand-built lease.

    Attempt one belongs to the normal RabbitMQ delivery path.  A retry claim
    begins at attempt two, so requiring that range prevents a caller from
    using this recovery-only reader to bypass AMQP parsing for first attempts.
    PostgreSQL's current clock is checked by the SQL predicate; local Python
    time is intentionally not a second scheduling authority.
    """

    if not isinstance(value, BasicPitchTaskLease):
        raise _error()
    job_id = _canonical_uuid(value.job_id)
    _canonical_uuid(value.task_id)
    _canonical_uuid(value.request_event_id)
    _canonical_uuid(value.lease_token)
    if (
        value.stem_name not in BASIC_PITCH_STEM_NAMES
        or value.stem_mode not in BASIC_PITCH_STEMS_BY_MODE
        or value.stem_name not in BASIC_PITCH_STEMS_BY_MODE[value.stem_mode]
        or value.input_bucket != LOCAL_UPLOADS_BUCKET
        or value.input_object_key != f"stems/{job_id}/{value.stem_name}.wav"
        or type(value.attempt_count) is not int
        or not 2 <= value.attempt_count <= MAX_BASIC_PITCH_TASK_ATTEMPTS
    ):
        raise _error()
    return value


def _message_from_row(
    row: Mapping[str, object], *, lease: BasicPitchTaskLease
) -> BasicPitchRequestedMessage:
    """Rebuild one strict request only when the event and JSONB shape agree.

    PostgreSQL JSONB is expected to arrive as a mapping from the restricted
    database adapter.  The exact-field checks intentionally mirror the AMQP
    parser's document contract.  Passing the rebuilt value through the public
    MIDI-output planner reuses its UUID/stem/bucket/key/size/SHA-256 validation
    rather than allowing this alternate ingress path to weaken it.
    """

    event_id = _canonical_uuid(_text(row, "event_id"))
    job_id = _canonical_uuid(_text(row, "job_id"))
    stem_name = _text(row, "stem_name")
    if (
        event_id != lease.request_event_id
        or job_id != lease.job_id
        or stem_name != lease.stem_name
        or _text(row, "stage") != BASIC_PITCH_STAGE
        or _text(row, "event_type") != BASIC_PITCH_REQUESTED_EVENT_TYPE
        or _text(row, "publication_status") != "published"
    ):
        raise _error()

    payload = row.get("payload")
    if not isinstance(payload, Mapping) or set(payload) != {
        "schema_version",
        "job_id",
        "stem_name",
        "stem",
    }:
        raise _error()
    stem = payload.get("stem")
    if not isinstance(stem, Mapping) or set(stem) != {
        "bucket",
        "object_key",
        "content_type",
        "size_bytes",
        "sha256",
    }:
        raise _error()
    if (
        type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
        or payload.get("job_id") != job_id
        or payload.get("stem_name") != stem_name
        or stem.get("bucket") != lease.input_bucket
        or stem.get("object_key") != lease.input_object_key
        or stem.get("content_type") != STEM_CONTENT_TYPE
        or type(stem.get("size_bytes")) is not int
    ):
        raise _error()

    message = BasicPitchRequestedMessage(
        event_id=event_id,
        job_id=job_id,
        stem_name=stem_name,
        stem_bucket=lease.input_bucket,
        stem_object_key=lease.input_object_key,
        stem_content_length=stem["size_bytes"],
        stem_sha256=stem.get("sha256"),  # The public validator proves exact SHA syntax.
    )
    try:
        # The MIDI result itself is not used here.  Calling the planner solely
        # revalidates a direct dataclass construction using the established
        # normal-delivery contract before any storage/model boundary exists.
        build_basic_pitch_midi_output(message)
    except BasicPitchRequestContractError as error:
        raise _error() from error
    return message


def read_current_basic_pitch_recovery_request(
    cursor: DatabaseCursor,
    *,
    lease: BasicPitchTaskLease,
) -> BasicPitchRequestedMessage | None:
    """Return strict durable request evidence for a current fresh retry lease.

    ``None`` is a normal ownership-loss result: no matching current,
    unexpired ``leased`` task/event pair remained when PostgreSQL evaluated
    the query.  The caller must stop and must not contact MinIO or run Basic
    Pitch.  A returned request has been reconstructed from the published
    outbox record, not from an old RabbitMQ delivery or caller-provided JSON.

    This function is read-only and expects the same short transaction that
    claimed the retry lease to still be open.  It intentionally does not start
    a transaction, commit, acknowledge/publish RabbitMQ, or invoke MinIO/the
    model/Kubernetes APIs.
    """

    if not callable(getattr(cursor, "execute", None)) or not callable(getattr(cursor, "fetchone", None)):
        raise TypeError("cursor must provide execute and fetchone.")
    validated_lease = _validated_recovery_lease(lease)
    task_id = _canonical_uuid(validated_lease.task_id)
    job_id = _canonical_uuid(validated_lease.job_id)
    request_event_id = _canonical_uuid(validated_lease.request_event_id)
    lease_token = _canonical_uuid(validated_lease.lease_token)
    cursor.execute(
        READ_CURRENT_BASIC_PITCH_RECOVERY_REQUEST_SQL,
        (
            task_id,
            job_id,
            validated_lease.stem_name,
            request_event_id,
            validated_lease.input_bucket,
            validated_lease.input_object_key,
            validated_lease.stem_mode,
            validated_lease.attempt_count,
            lease_token,
        ),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    if not isinstance(row, Mapping):
        raise _error()
    return _message_from_row(row, lease=validated_lease)
