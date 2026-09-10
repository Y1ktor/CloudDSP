"""One bounded PostgreSQL-outbox-to-RabbitMQ dispatch attempt.

This composition module combines the already-separated database lease adapter,
short Psycopg transaction scope, and confirming AMQP publisher. It performs at
most one event attempt; it does not poll, sleep, install signal handlers, keep
a process-wide connection, build an image, or create Kubernetes resources.

The ordering is the important part:

1. claim one due outbox row and commit that lease;
2. publish its validated body with RabbitMQ publisher confirmation; then
3. in a new short PostgreSQL transaction, mark the same lease published or
   schedule a known pre-confirmation failure.

There is no distributed transaction across PostgreSQL and RabbitMQ. A lost
confirmation is deliberately left leased until expiry: the next attempt may
duplicate-publish, which downstream Demucs work must handle idempotently.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from app.amqp_publisher import (
    DispatcherBrokerUnavailable,
    DispatcherPublisherConfirmationUnknown,
    DispatcherPublisherContractError,
    DispatcherPublisherFailure,
    DispatcherPublisherSettings,
    enable_dispatcher_publisher_confirms,
    open_dispatcher_rabbitmq_connection,
    publish_demucs_requested,
)
from app.outbox_lease import (
    MAX_LEASE_SECONDS,
    MAX_RETRY_DELAY_SECONDS,
    MIN_LEASE_SECONDS,
    MIN_RETRY_DELAY_SECONDS,
    DatabaseCursor,
    DispatcherDeadLetterCode,
    claim_due_demucs_outbox_event,
    mark_demucs_outbox_event_dead_lettered,
    mark_demucs_outbox_event_published,
    schedule_demucs_outbox_event_retry,
)


# These are deliberate local-profile timings, not unbounded worker policy. A
# future long-running supervisor will select them explicitly in a Deployment.
DEFAULT_DISPATCH_LEASE_SECONDS = 30
DEFAULT_RETRY_AFTER_SECONDS = 30


class DispatchOnceOutcome(StrEnum):
    """Safe, non-sensitive results a later loop can count or log by category."""

    IDLE = "idle"
    PUBLISHED = "published"
    RETRY_SCHEDULED = "retry_scheduled"
    LEASE_NO_LONGER_OWNED = "lease_no_longer_owned"
    CONFIRMATION_UNKNOWN = "confirmation_unknown"
    EVENT_DEAD_LETTERED = "event_dead_lettered"


@dataclass(frozen=True)
class DispatchOnceResult:
    """The outcome of one attempt without exposing payload/key/identity data."""

    outcome: DispatchOnceOutcome


class DispatcherWriteDatabase(Protocol):
    """The single short PostgreSQL cursor scope this composition requires."""

    def write_cursor(self) -> Any:
        """Return a context manager yielding a dictionary-shaped SQL cursor."""


def _bounded_seconds(*, name: str, value: int, minimum: int, maximum: int) -> int:
    """Reject unsafe timing before it controls a durable lease or retry schedule."""

    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}.")
    return value


def _open_publisher_channel(connection: Any) -> Any:
    """Open one AMQP channel before a publish, when no body has been sent yet."""

    try:
        return connection.channel()
    except Exception as error:
        # This failure occurs before `basic_publish`, so it is known safe to
        # schedule for retry. The publish adapter itself handles post-send
        # exceptions differently and returns an explicitly uncertain category.
        raise DispatcherBrokerUnavailable() from error


def _close_connection_quietly(connection: Any | None) -> None:
    """Release a bounded one-attempt AMQP connection without changing outcome.

    Once a broker confirmation has been received, a close failure must not
    overwrite that known result. The later database action below is still the
    authoritative publication bookkeeping, and a connection object may already
    be closed after a known/uncertain broker failure.
    """

    if connection is None:
        return
    with suppress(Exception):
        connection.close()


def dispatch_once(
    *,
    database: DispatcherWriteDatabase,
    publisher_settings: DispatcherPublisherSettings,
    lease_seconds: int = DEFAULT_DISPATCH_LEASE_SECONDS,
    retry_after_seconds: int = DEFAULT_RETRY_AFTER_SECONDS,
    connection_factory=open_dispatcher_rabbitmq_connection,
) -> DispatchOnceResult:
    """Attempt one durable outbox dispatch without owning a long-running loop.

    ``IDLE`` means no pending/expired Demucs outbox event was claimable.
    ``PUBLISHED`` means RabbitMQ confirmed the message and PostgreSQL accepted
    the matching lease completion. ``LEASE_NO_LONGER_OWNED`` is harmless: a
    competing/recovered dispatcher owns the current state, so this attempt must
    not overwrite it. ``CONFIRMATION_UNKNOWN`` performs no second database
    mutation so lease expiry preserves duplicate-safe recovery.
    ``EVENT_DEAD_LETTERED`` records a fixed terminal reason because its
    incompatible durable body must never reach a worker queue.
    """

    selected_lease_seconds = _bounded_seconds(
        name="lease_seconds",
        value=lease_seconds,
        minimum=MIN_LEASE_SECONDS,
        maximum=MAX_LEASE_SECONDS,
    )
    selected_retry_after_seconds = _bounded_seconds(
        name="retry_after_seconds",
        value=retry_after_seconds,
        minimum=MIN_RETRY_DELAY_SECONDS,
        maximum=MAX_RETRY_DELAY_SECONDS,
    )

    # The first context commits the lease and releases PostgreSQL's row lock
    # before any AMQP network call. Other replicas can now skip locked rows and
    # eventually recover this lease if the current Pod stops mid-publication.
    with database.write_cursor() as cursor:
        event = claim_due_demucs_outbox_event(
            cursor,
            lease_seconds=selected_lease_seconds,
        )
    if event is None:
        return DispatchOnceResult(outcome=DispatchOnceOutcome.IDLE)

    connection: Any | None = None
    try:
        connection = connection_factory(publisher_settings)
        channel = _open_publisher_channel(connection)
        enable_dispatcher_publisher_confirms(channel)
        publish_demucs_requested(
            channel,
            event_id=event.event_id,
            job_id=event.job_id,
            payload=event.payload,
        )
    except DispatcherPublisherFailure as failure:
        # These failure subclasses mean the adapter knows RabbitMQ did not
        # confirm this attempt. Releasing the same lease to `pending` with a
        # bounded delay avoids both a hot loop and a lost durable event.
        _close_connection_quietly(connection)
        with database.write_cursor() as cursor:
            retry_applied = schedule_demucs_outbox_event_retry(
                cursor,
                event_id=event.event_id,
                lease_token=event.lease_token,
                retry_after_seconds=selected_retry_after_seconds,
                failure_code=failure.failure_code,
            )
        return DispatchOnceResult(
            outcome=(
                DispatchOnceOutcome.RETRY_SCHEDULED
                if retry_applied
                else DispatchOnceOutcome.LEASE_NO_LONGER_OWNED
            )
        )
    except DispatcherPublisherConfirmationUnknown:
        # RabbitMQ may have received the body before the socket/confirmation
        # failed. Do not create an eager duplicate by writing it back to
        # pending; the existing lease-expiry query handles recovery safely.
        _close_connection_quietly(connection)
        return DispatchOnceResult(outcome=DispatchOnceOutcome.CONFIRMATION_UNKNOWN)
    except DispatcherPublisherContractError:
        # A malformed durable row must never reach a Demucs worker. This is a
        # PostgreSQL terminal state, not a RabbitMQ DLQ message: the broker has
        # not received a body. The guarded transition retains durable evidence
        # without silently deleting, rewriting, or retrying the bad payload.
        _close_connection_quietly(connection)
        with database.write_cursor() as cursor:
            dead_letter_applied = mark_demucs_outbox_event_dead_lettered(
                cursor,
                event_id=event.event_id,
                lease_token=event.lease_token,
                reason=DispatcherDeadLetterCode.INVALID_EVENT_CONTRACT,
            )
        return DispatchOnceResult(
            outcome=(
                DispatchOnceOutcome.EVENT_DEAD_LETTERED
                if dead_letter_applied
                else DispatchOnceOutcome.LEASE_NO_LONGER_OWNED
            )
        )
    except Exception:
        # The only unwrapped pre-publish operation is channel creation. Treat a
        # future client/library drift here as safely unavailable *before* a body
        # can be sent, while still preserving the original exception as a cause
        # for local diagnosis rather than emitting it in normal worker logs.
        _close_connection_quietly(connection)
        known_failure = DispatcherBrokerUnavailable()
        with database.write_cursor() as cursor:
            retry_applied = schedule_demucs_outbox_event_retry(
                cursor,
                event_id=event.event_id,
                lease_token=event.lease_token,
                retry_after_seconds=selected_retry_after_seconds,
                failure_code=known_failure.failure_code,
            )
        # The raw client diagnostic is not embedded in this safe result or a
        # future supervisor's normal log.
        if not retry_applied:
            return DispatchOnceResult(outcome=DispatchOnceOutcome.LEASE_NO_LONGER_OWNED)
        return DispatchOnceResult(outcome=DispatchOnceOutcome.RETRY_SCHEDULED)
    else:
        _close_connection_quietly(connection)

    # Only a completed publisher-confirmation path reaches this second short
    # transaction. It guards both event ID and lease token, so a stale Pod can
    # never mark a recovered event as published after its lease changed.
    with database.write_cursor() as cursor:
        publication_applied = mark_demucs_outbox_event_published(
            cursor,
            event_id=event.event_id,
            lease_token=event.lease_token,
        )
    return DispatchOnceResult(
        outcome=(
            DispatchOnceOutcome.PUBLISHED
            if publication_applied
            else DispatchOnceOutcome.LEASE_NO_LONGER_OWNED
        )
    )
