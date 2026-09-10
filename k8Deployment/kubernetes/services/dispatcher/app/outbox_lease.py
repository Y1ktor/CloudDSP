"""Atomic PostgreSQL outbox lease operations for the dispatcher.

This module is deliberately a narrow SQL boundary.  It has no Psycopg import,
RabbitMQ client, loop, Kubernetes API call, environment-variable reader, or
container entrypoint.  A later composition task will supply a short database
transaction, publish a leased record with RabbitMQ publisher confirmation, and
call one of the guarded completion helpers below.

Keeping those responsibilities separate is important: PostgreSQL is the source
of truth for publication progress, while RabbitMQ is an at-least-once transport.
The functions here make it impossible for two dispatcher Pods to claim the same
due row concurrently, and prevent one Pod from completing another Pod's lease.

The deployed Demucs runtime continues to call the explicitly Demucs-only claim
helper.  The separate generic helper below is deliberately not wired into that
runtime yet: it makes the approved v004 Basic Pitch/ADTOF rows available for a
later route-aware composition task without risking their publication through
the existing Demucs-only RabbitMQ adapter.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4


# A short lease limits recovery time if a Pod exits mid-publication, while the
# upper bound prevents an accidental configuration from hiding a stuck event for
# hours. The later Deployment will select an explicit value in this range.
MIN_LEASE_SECONDS = 5
MAX_LEASE_SECONDS = 300

# A known RabbitMQ failure waits before another dispatcher attempt. This bound
# is intentionally independent of the Demucs worker retry queue: it governs
# *outbox publication before a message has been confirmed by the broker*.
MIN_RETRY_DELAY_SECONDS = 1
MAX_RETRY_DELAY_SECONDS = 3_600


class DispatcherOutboxProtocolError(RuntimeError):
    """Raise a safe category when PostgreSQL returns an unexpected row shape.

    The original driver error or untrusted payload must not become a normal log
    message: it could contain private object paths or database details. The
    future supervisor can log this fixed category and safely retry/recover the
    lease according to its bounded policy.
    """


class DispatcherPublishFailureCode(StrEnum):
    """Bounded causes for a *known* pre-confirmation RabbitMQ failure.

    These values are the only text persisted in ``outbox_events.last_error_code``
    by the future dispatcher. An uncertain outcome must **not** call the retry
    helper, because RabbitMQ may have accepted the message; its lease must be
    allowed to expire so a later duplicate-safe publication can recover it.
    """

    BROKER_UNAVAILABLE = "broker_unavailable"
    PUBLISHER_NACK = "publisher_nack"
    QUEUE_REJECTED = "queue_rejected"


class DispatcherDeadLetterCode(StrEnum):
    """Bounded terminal reasons for an event that must never reach RabbitMQ.

    This is a PostgreSQL outbox terminal state, not a RabbitMQ queue DLQ. It
    applies before a Demucs message is published when, for example, a durable
    payload violates the immutable version-1 contract. A fixed category keeps
    history useful without persisting a private object key or raw exception.
    """

    INVALID_EVENT_CONTRACT = "invalid_event_contract"


class DatabaseCursor(Protocol):
    """The minimal cursor surface required by this pure SQL adapter."""

    def execute(self, query: str, params: tuple[object, ...]) -> object:
        """Execute parameterized SQL; untrusted values never enter SQL text."""

    def fetchone(self) -> Mapping[str, object] | None:
        """Return one dictionary-shaped ``RETURNING`` row, if present."""


@dataclass(frozen=True)
class LeasedDemucsOutboxEvent:
    """One event atomically owned by this dispatcher attempt.

    ``payload`` is the already-durable version-1 request body. It contains
    stable MinIO identifiers, never a browser presigned URL or credentials.
    The future publisher will validate the full contract before sending it to
    RabbitMQ; this SQL adapter validates only the durable identity/lease shape
    needed to prevent an accidental cross-event completion.
    """

    event_id: str
    job_id: str
    payload: Mapping[str, object]
    delivery_attempts: int
    lease_token: str
    lease_expires_at: datetime


@dataclass(frozen=True)
class LeasedOutboxEvent:
    """One atomically leased event from the finite dispatcher vocabulary.

    Unlike :class:`LeasedDemucsOutboxEvent`, this data model retains the
    durable ``stage``, ``stem_name``, and ``event_type`` triple.  That triple
    is how the later composition layer will choose *one* reviewed serializer
    and AMQP routing contract.  It is not user-controlled routing input: both
    PostgreSQL's v004 CHECK constraint and this adapter's fixed allowlist must
    agree before the record crosses the database boundary.

    The type is intentionally pure data.  It does not imply that the currently
    deployed Demucs-only process may publish a Basic Pitch or ADTOF event.
    """

    event_id: str
    job_id: str
    stage: str
    stem_name: str
    event_type: str
    payload: Mapping[str, object]
    delivery_attempts: int
    lease_token: str
    lease_expires_at: datetime


# Claim both ordinarily due pending events and abandoned leases in one atomic
# statement. ``FOR UPDATE SKIP LOCKED`` means multiple dispatcher replicas
# bypass a row another transaction is already claiming instead of blocking one
# another. Updating the selected row with a new token increments its attempt
# counter and makes any prior owner unable to mark it published afterward.
CLAIM_DUE_DEMUCS_OUTBOX_EVENT_SQL = """
    WITH candidate AS (
      SELECT event_id
      FROM public.outbox_events
      WHERE stage = 'demucs'
        AND stem_name = ''
        AND event_type = 'demucs.requested'
        AND (
          (publication_status = 'pending' AND available_at <= CURRENT_TIMESTAMP)
          OR
          (publication_status = 'leased' AND lease_expires_at <= CURRENT_TIMESTAMP)
        )
      ORDER BY
        CASE
          WHEN publication_status = 'pending' THEN available_at
          ELSE lease_expires_at
        END ASC,
        created_at ASC,
        event_id ASC
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    )
    UPDATE public.outbox_events AS outbox
    SET
      publication_status = 'leased',
      lease_token = %s::uuid,
      lease_expires_at = CURRENT_TIMESTAMP + (%s::integer * INTERVAL '1 second'),
      delivery_attempts = outbox.delivery_attempts + 1,
      last_error_code = NULL
    FROM candidate
    WHERE outbox.event_id = candidate.event_id
    RETURNING
      outbox.event_id::text AS event_id,
      outbox.job_id::text AS job_id,
      outbox.stage,
      outbox.stem_name,
      outbox.event_type,
      outbox.payload,
      outbox.publication_status,
      outbox.delivery_attempts,
      outbox.lease_token::text AS lease_token,
      outbox.lease_expires_at
"""


# This query is the future route-aware dispatcher's claim boundary.  The
# parenthesized allowlist mirrors the finite v004 table constraint instead of
# accepting arbitrary stage/type text from a durable row.  It includes the
# existing job-wide Demucs request and every valid post-Demucs stem request.
#
# Keep this separate from `CLAIM_DUE_DEMUCS_OUTBOX_EVENT_SQL` until the
# composition layer can validate and publish all three event families.  If the
# existing Demucs-only runtime used this query today, it would correctly lease
# a downstream event but then incorrectly pass it to its Demucs-only publisher.
CLAIM_DUE_DISPATCHABLE_OUTBOX_EVENT_SQL = """
    WITH candidate AS (
      SELECT event_id
      FROM public.outbox_events
      WHERE (
        (stage = 'demucs' AND stem_name = '' AND event_type = 'demucs.requested')
        OR
        (
          stage = 'basic-pitch'
          AND stem_name IN ('vocals', 'no_vocals', 'bass', 'other', 'guitar', 'piano')
          AND event_type = 'basic-pitch.requested'
        )
        OR
        (stage = 'adtof' AND stem_name = 'drums' AND event_type = 'adtof.requested')
      )
      AND (
        (publication_status = 'pending' AND available_at <= CURRENT_TIMESTAMP)
        OR
        (publication_status = 'leased' AND lease_expires_at <= CURRENT_TIMESTAMP)
      )
      ORDER BY
        CASE
          WHEN publication_status = 'pending' THEN available_at
          ELSE lease_expires_at
        END ASC,
        created_at ASC,
        event_id ASC
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    )
    UPDATE public.outbox_events AS outbox
    SET
      publication_status = 'leased',
      lease_token = %s::uuid,
      lease_expires_at = CURRENT_TIMESTAMP + (%s::integer * INTERVAL '1 second'),
      delivery_attempts = outbox.delivery_attempts + 1,
      last_error_code = NULL
    FROM candidate
    WHERE outbox.event_id = candidate.event_id
    RETURNING
      outbox.event_id::text AS event_id,
      outbox.job_id::text AS job_id,
      outbox.stage,
      outbox.stem_name,
      outbox.event_type,
      outbox.payload,
      outbox.publication_status,
      outbox.delivery_attempts,
      outbox.lease_token::text AS lease_token,
      outbox.lease_expires_at
"""


# Only the current lease owner may record broker-confirmed publication. The
# lease token appears in the WHERE clause as well as the event ID, so a stale
# Pod cannot overwrite a recovered/released event after a slow network call.
MARK_OUTBOX_EVENT_PUBLISHED_SQL = """
    UPDATE public.outbox_events
    SET
      publication_status = 'published',
      lease_token = NULL,
      lease_expires_at = NULL,
      published_at = CURRENT_TIMESTAMP,
      last_error_code = NULL
    WHERE event_id = %s::uuid
      AND publication_status = 'leased'
      AND lease_token = %s::uuid
    RETURNING
      event_id::text AS event_id,
      publication_status,
      published_at
"""


# This helper applies only after the publisher knows RabbitMQ did **not** accept
# the message. It releases the matching lease, retains the event as pending,
# and schedules the next dispatcher attempt without a busy loop. A broker
# timeout with uncertain acceptance is intentionally handled by lease expiry,
# not by this SQL, because a duplicate publish is safer than a possible loss.
SCHEDULE_OUTBOX_EVENT_RETRY_SQL = """
    UPDATE public.outbox_events
    SET
      publication_status = 'pending',
      lease_token = NULL,
      lease_expires_at = NULL,
      available_at = CURRENT_TIMESTAMP + (%s::integer * INTERVAL '1 second'),
      last_error_code = %s
    WHERE event_id = %s::uuid
      AND publication_status = 'leased'
      AND lease_token = %s::uuid
    RETURNING
      event_id::text AS event_id,
      publication_status,
      available_at,
      last_error_code
"""


# A malformed durable event must not cycle through lease recovery forever. Only
# the current lease owner can make this terminal transition, so a stale
# dispatcher cannot overwrite work recovered by a newer attempt. `published_at`
# remains NULL, proving that no publisher-confirmed message was recorded.
MARK_OUTBOX_EVENT_DEAD_LETTERED_SQL = """
    UPDATE public.outbox_events
    SET
      publication_status = 'dead_lettered',
      lease_token = NULL,
      lease_expires_at = NULL,
      last_error_code = %s
    WHERE event_id = %s::uuid
      AND publication_status = 'leased'
      AND lease_token = %s::uuid
    RETURNING
      event_id::text AS event_id,
      publication_status,
      last_error_code
"""


# The SQL never limited completion to the Demucs stage: ownership is identified
# by the event UUID plus its current lease UUID.  Keep these compatibility
# aliases while the existing Demucs-only `dispatch_once` module still imports
# the old names.  The later generic composition uses the stage-neutral names.
MARK_DEMUCS_OUTBOX_EVENT_PUBLISHED_SQL = MARK_OUTBOX_EVENT_PUBLISHED_SQL
SCHEDULE_DEMUCS_OUTBOX_EVENT_RETRY_SQL = SCHEDULE_OUTBOX_EVENT_RETRY_SQL
MARK_DEMUCS_OUTBOX_EVENT_DEAD_LETTERED_SQL = MARK_OUTBOX_EVENT_DEAD_LETTERED_SQL


# This source-level allowlist is intentionally duplicated in the generic row
# validator and in the v004 SQL claim predicate.  The database protects stored
# state, while this check protects the Python boundary if a driver, migration,
# or test double returns a row outside the reviewed contract.
_DISPATCHABLE_EVENT_TUPLES = frozenset(
    {
        ("demucs", "", "demucs.requested"),
        ("basic-pitch", "vocals", "basic-pitch.requested"),
        ("basic-pitch", "no_vocals", "basic-pitch.requested"),
        ("basic-pitch", "bass", "basic-pitch.requested"),
        ("basic-pitch", "other", "basic-pitch.requested"),
        ("basic-pitch", "guitar", "basic-pitch.requested"),
        ("basic-pitch", "piano", "basic-pitch.requested"),
        ("adtof", "drums", "adtof.requested"),
    }
)


def _canonical_uuid(*, name: str, value: str) -> str:
    """Require the canonical lowercase UUID spelling used by durable records."""

    if not isinstance(value, str):
        raise ValueError(f"{name} must be canonical lowercase UUID text.")
    try:
        normalized = str(UUID(value))
    except (ValueError, AttributeError) as error:
        raise ValueError(f"{name} must be canonical lowercase UUID text.") from error
    if normalized != value:
        raise ValueError(f"{name} must be canonical lowercase UUID text.")
    return normalized


def _bounded_seconds(*, name: str, value: int, minimum: int, maximum: int) -> int:
    """Validate a bounded positive duration before it controls SQL scheduling."""

    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}.")
    return value


def _required_row_text(row: Mapping[str, object], *, name: str) -> str:
    """Read one non-empty returned text field while failing closed on driver drift."""

    value = row.get(name)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise DispatcherOutboxProtocolError("PostgreSQL returned an invalid dispatcher outbox row.")
    return value


def _leased_dispatchable_event_from_row(
    row: Mapping[str, object],
    *,
    expected_lease_token: str,
) -> LeasedOutboxEvent:
    """Validate a generic v004 claim result before route selection can begin.

    This validates ownership and the finite stage/stem/type tuple, but not the
    event-family payload schema.  The later route-aware composition layer must
    pass the returned row through either the Demucs or downstream contract
    validator *before* it opens a RabbitMQ publish operation.
    """

    event_id = _canonical_uuid(name="returned event_id", value=_required_row_text(row, name="event_id"))
    job_id = _canonical_uuid(name="returned job_id", value=_required_row_text(row, name="job_id"))
    lease_token = _canonical_uuid(
        name="returned lease_token",
        value=_required_row_text(row, name="lease_token"),
    )
    stage = _required_row_text(row, name="stage")
    stem_name = _required_row_text(row, name="stem_name") if row.get("stem_name") != "" else ""
    event_type = _required_row_text(row, name="event_type")
    if (
        lease_token != expected_lease_token
        or (stage, stem_name, event_type) not in _DISPATCHABLE_EVENT_TUPLES
        or row.get("publication_status") != "leased"
    ):
        raise DispatcherOutboxProtocolError("PostgreSQL returned an invalid dispatcher outbox row.")

    payload = row.get("payload")
    if not isinstance(payload, Mapping) or payload.get("job_id") != job_id:
        raise DispatcherOutboxProtocolError("PostgreSQL returned an invalid dispatcher outbox row.")
    delivery_attempts = row.get("delivery_attempts")
    if isinstance(delivery_attempts, bool) or not isinstance(delivery_attempts, int) or delivery_attempts < 1:
        raise DispatcherOutboxProtocolError("PostgreSQL returned an invalid dispatcher outbox row.")
    lease_expires_at = row.get("lease_expires_at")
    if not isinstance(lease_expires_at, datetime) or lease_expires_at.tzinfo is None:
        raise DispatcherOutboxProtocolError("PostgreSQL returned an invalid dispatcher outbox row.")

    return LeasedOutboxEvent(
        event_id=event_id,
        job_id=job_id,
        stage=stage,
        stem_name=stem_name,
        event_type=event_type,
        payload=payload,
        delivery_attempts=delivery_attempts,
        lease_token=lease_token,
        lease_expires_at=lease_expires_at,
    )


def _leased_event_from_row(
    row: Mapping[str, object],
    *,
    expected_lease_token: str,
) -> LeasedDemucsOutboxEvent:
    """Validate one claim result before a publisher is allowed to use it."""

    event_id = _canonical_uuid(name="returned event_id", value=_required_row_text(row, name="event_id"))
    job_id = _canonical_uuid(name="returned job_id", value=_required_row_text(row, name="job_id"))
    lease_token = _canonical_uuid(
        name="returned lease_token",
        value=_required_row_text(row, name="lease_token"),
    )
    if lease_token != expected_lease_token:
        raise DispatcherOutboxProtocolError("PostgreSQL returned an invalid dispatcher outbox row.")
    if (
        row.get("stage") != "demucs"
        or row.get("stem_name") != ""
        or row.get("event_type") != "demucs.requested"
        or row.get("publication_status") != "leased"
    ):
        raise DispatcherOutboxProtocolError("PostgreSQL returned an invalid dispatcher outbox row.")

    payload = row.get("payload")
    if not isinstance(payload, Mapping):
        raise DispatcherOutboxProtocolError("PostgreSQL returned an invalid dispatcher outbox row.")
    # The future publisher's full schema validator will reject an incompatible
    # body. This lightweight identity check catches a dangerous row/JSON mixup
    # before the adapter returns a lease to that publisher.
    if payload.get("job_id") != job_id:
        raise DispatcherOutboxProtocolError("PostgreSQL returned an invalid dispatcher outbox row.")

    delivery_attempts = row.get("delivery_attempts")
    if isinstance(delivery_attempts, bool) or not isinstance(delivery_attempts, int) or delivery_attempts < 1:
        raise DispatcherOutboxProtocolError("PostgreSQL returned an invalid dispatcher outbox row.")
    lease_expires_at = row.get("lease_expires_at")
    if not isinstance(lease_expires_at, datetime) or lease_expires_at.tzinfo is None:
        raise DispatcherOutboxProtocolError("PostgreSQL returned an invalid dispatcher outbox row.")

    return LeasedDemucsOutboxEvent(
        event_id=event_id,
        job_id=job_id,
        payload=payload,
        delivery_attempts=delivery_attempts,
        lease_token=lease_token,
        lease_expires_at=lease_expires_at,
    )


def claim_due_dispatchable_outbox_event(
    cursor: DatabaseCursor,
    *,
    lease_seconds: int,
    lease_token: str | None = None,
) -> LeasedOutboxEvent | None:
    """Claim one reviewed Demucs, Basic Pitch, or ADTOF event.

    The selected event has already persisted in PostgreSQL and has no browser
    URL or credential.  ``None`` means the combined finite vocabulary has no
    due row.  A future route-aware dispatcher can call this function, inspect
    the returned immutable triple, and choose the matching strict AMQP request
    builder.  The existing Demucs-only runtime must continue to use its
    dedicated helper below until that route-aware composition is implemented.
    """

    bounded_lease_seconds = _bounded_seconds(
        name="lease_seconds",
        value=lease_seconds,
        minimum=MIN_LEASE_SECONDS,
        maximum=MAX_LEASE_SECONDS,
    )
    selected_lease_token = (
        str(uuid4())
        if lease_token is None
        else _canonical_uuid(name="lease_token", value=lease_token)
    )
    cursor.execute(
        CLAIM_DUE_DISPATCHABLE_OUTBOX_EVENT_SQL,
        (selected_lease_token, bounded_lease_seconds),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    if not isinstance(row, Mapping):
        raise DispatcherOutboxProtocolError("PostgreSQL returned an invalid dispatcher outbox row.")
    return _leased_dispatchable_event_from_row(row, expected_lease_token=selected_lease_token)


def claim_due_demucs_outbox_event(
    cursor: DatabaseCursor,
    *,
    lease_seconds: int,
    lease_token: str | None = None,
) -> LeasedDemucsOutboxEvent | None:
    """Claim one due event or expired lease without blocking another dispatcher.

    A ``None`` return means there is no currently claimable work; it is not an
    error and the later long-running Deployment can briefly sleep. Supplying a
    lease token is useful for deterministic tests; ordinary runtime callers let
    this helper generate a fresh UUID for each ownership attempt.
    """

    bounded_lease_seconds = _bounded_seconds(
        name="lease_seconds",
        value=lease_seconds,
        minimum=MIN_LEASE_SECONDS,
        maximum=MAX_LEASE_SECONDS,
    )
    selected_lease_token = (
        str(uuid4())
        if lease_token is None
        else _canonical_uuid(name="lease_token", value=lease_token)
    )
    cursor.execute(
        CLAIM_DUE_DEMUCS_OUTBOX_EVENT_SQL,
        (selected_lease_token, bounded_lease_seconds),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    if not isinstance(row, Mapping):
        raise DispatcherOutboxProtocolError("PostgreSQL returned an invalid dispatcher outbox row.")
    return _leased_event_from_row(row, expected_lease_token=selected_lease_token)


def mark_outbox_event_published(
    cursor: DatabaseCursor,
    *,
    event_id: str,
    lease_token: str,
) -> bool:
    """Record any publisher-confirmed event only if this dispatcher owns it.

    ``False`` means the lease was already recovered, released, or published by
    another attempt. The future caller must not issue a second update or infer
    a broker result from that absence; duplicate-safe lease recovery preserves
    correctness across that uncertain boundary.
    """

    canonical_event_id = _canonical_uuid(name="event_id", value=event_id)
    canonical_lease_token = _canonical_uuid(name="lease_token", value=lease_token)
    cursor.execute(
        MARK_OUTBOX_EVENT_PUBLISHED_SQL,
        (canonical_event_id, canonical_lease_token),
    )
    row = cursor.fetchone()
    if row is None:
        return False
    if not isinstance(row, Mapping):
        raise DispatcherOutboxProtocolError("PostgreSQL returned an invalid dispatcher publication result.")
    if (
        _canonical_uuid(name="returned event_id", value=_required_row_text(row, name="event_id"))
        != canonical_event_id
        or row.get("publication_status") != "published"
        or not isinstance(row.get("published_at"), datetime)
    ):
        raise DispatcherOutboxProtocolError("PostgreSQL returned an invalid dispatcher publication result.")
    return True


def schedule_outbox_event_retry(
    cursor: DatabaseCursor,
    *,
    event_id: str,
    lease_token: str,
    retry_after_seconds: int,
    failure_code: DispatcherPublishFailureCode,
) -> bool:
    """Release any known-unpublished lease for a bounded later retry.

    The caller may use this only when RabbitMQ definitively rejected or never
    received the message. A timeout or dropped confirmation is uncertain, so
    allowing the lease to expire is the safe duplicate-tolerant recovery path.
    """

    if not isinstance(failure_code, DispatcherPublishFailureCode):
        raise TypeError("failure_code must be DispatcherPublishFailureCode.")
    canonical_event_id = _canonical_uuid(name="event_id", value=event_id)
    canonical_lease_token = _canonical_uuid(name="lease_token", value=lease_token)
    bounded_retry_seconds = _bounded_seconds(
        name="retry_after_seconds",
        value=retry_after_seconds,
        minimum=MIN_RETRY_DELAY_SECONDS,
        maximum=MAX_RETRY_DELAY_SECONDS,
    )
    cursor.execute(
        SCHEDULE_OUTBOX_EVENT_RETRY_SQL,
        (
            bounded_retry_seconds,
            failure_code.value,
            canonical_event_id,
            canonical_lease_token,
        ),
    )
    row = cursor.fetchone()
    if row is None:
        return False
    if not isinstance(row, Mapping):
        raise DispatcherOutboxProtocolError("PostgreSQL returned an invalid dispatcher retry result.")
    if (
        _canonical_uuid(name="returned event_id", value=_required_row_text(row, name="event_id"))
        != canonical_event_id
        or row.get("publication_status") != "pending"
        or row.get("last_error_code") != failure_code.value
        or not isinstance(row.get("available_at"), datetime)
    ):
        raise DispatcherOutboxProtocolError("PostgreSQL returned an invalid dispatcher retry result.")
    return True


def mark_outbox_event_dead_lettered(
    cursor: DatabaseCursor,
    *,
    event_id: str,
    lease_token: str,
    reason: DispatcherDeadLetterCode,
) -> bool:
    """Terminally retain any invalid event without publishing it to RabbitMQ.

    ``False`` means this dispatcher no longer owns the lease. That is a normal
    concurrent-recovery result, not permission to issue a second terminal
    update. Accepting only an enum reason prevents raw payload/exception text
    from entering the durable ``last_error_code`` field.
    """

    if not isinstance(reason, DispatcherDeadLetterCode):
        raise TypeError("reason must be DispatcherDeadLetterCode.")
    canonical_event_id = _canonical_uuid(name="event_id", value=event_id)
    canonical_lease_token = _canonical_uuid(name="lease_token", value=lease_token)
    cursor.execute(
        MARK_OUTBOX_EVENT_DEAD_LETTERED_SQL,
        (reason.value, canonical_event_id, canonical_lease_token),
    )
    row = cursor.fetchone()
    if row is None:
        return False
    if not isinstance(row, Mapping):
        raise DispatcherOutboxProtocolError("PostgreSQL returned an invalid dispatcher dead-letter result.")
    if (
        _canonical_uuid(name="returned event_id", value=_required_row_text(row, name="event_id"))
        != canonical_event_id
        or row.get("publication_status") != "dead_lettered"
        or row.get("last_error_code") != reason.value
    ):
        raise DispatcherOutboxProtocolError("PostgreSQL returned an invalid dispatcher dead-letter result.")
    return True


# These wrappers preserve the current Demucs-only composition API.  They do
# not encode a stage predicate, because the guarded SQL has always operated on
# the unique `(event_id, lease_token)` ownership pair.  Keeping wrappers rather
# than silently changing call sites prevents this small naming task from also
# switching the deployed runtime to generic event selection.
def mark_demucs_outbox_event_published(
    cursor: DatabaseCursor,
    *,
    event_id: str,
    lease_token: str,
) -> bool:
    """Compatibility wrapper for the stage-neutral publish completion helper."""

    return mark_outbox_event_published(
        cursor,
        event_id=event_id,
        lease_token=lease_token,
    )


def schedule_demucs_outbox_event_retry(
    cursor: DatabaseCursor,
    *,
    event_id: str,
    lease_token: str,
    retry_after_seconds: int,
    failure_code: DispatcherPublishFailureCode,
) -> bool:
    """Compatibility wrapper for the stage-neutral retry scheduling helper."""

    return schedule_outbox_event_retry(
        cursor,
        event_id=event_id,
        lease_token=lease_token,
        retry_after_seconds=retry_after_seconds,
        failure_code=failure_code,
    )


def mark_demucs_outbox_event_dead_lettered(
    cursor: DatabaseCursor,
    *,
    event_id: str,
    lease_token: str,
    reason: DispatcherDeadLetterCode,
) -> bool:
    """Compatibility wrapper for the stage-neutral terminal-state helper."""

    return mark_outbox_event_dead_lettered(
        cursor,
        event_id=event_id,
        lease_token=lease_token,
        reason=reason,
    )
