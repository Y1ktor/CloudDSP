"""One bounded generic PostgreSQL-outbox-to-RabbitMQ dispatch attempt.

This source-only composition is the route-aware counterpart to the existing
Demucs-only ``app.dispatch_once``.  It joins four small boundaries in a strict
order:

1. atomically claim one reviewed Demucs/Basic-Pitch/ADTOF outbox row and commit;
2. select and validate its only permitted AMQP request without broker I/O;
3. publish that request with RabbitMQ publisher confirmation; then
4. in a new short transaction, complete, retry, or terminally retain that same
   ``(event_id, lease_token)`` ownership pair.

It performs one attempt only.  It does not poll, sleep, read environment
variables, install signal handlers, create a Kubernetes resource, or replace
the currently running Demucs-only dispatcher.  The later runtime-switch task
must deliberately choose this composition and rebuild/roll out its image.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from app.amqp_publisher import (
    DispatcherBrokerUnavailable,
    DispatcherPublisherConfirmationUnknown,
    DispatcherPublisherContractError,
    DispatcherPublisherFailure,
    DispatcherPublisherSettings,
    enable_dispatcher_publisher_confirms,
    open_dispatcher_rabbitmq_connection,
    publish_dispatchable_amqp_request,
)
from app.dispatch_once import (
    DEFAULT_DISPATCH_LEASE_SECONDS,
    DEFAULT_RETRY_AFTER_SECONDS,
    DispatchOnceOutcome,
    DispatchOnceResult,
    DispatcherWriteDatabase,
)
from app.dispatchable_outbox_request import (
    DispatchableOutboxRequestContractError,
    build_dispatchable_amqp_request,
)
from app.outbox_lease import (
    MAX_LEASE_SECONDS,
    MAX_RETRY_DELAY_SECONDS,
    MIN_LEASE_SECONDS,
    MIN_RETRY_DELAY_SECONDS,
    DispatcherDeadLetterCode,
    claim_due_dispatchable_outbox_event,
    mark_outbox_event_dead_lettered,
    mark_outbox_event_published,
    schedule_outbox_event_retry,
)


def _bounded_seconds(*, name: str, value: int, minimum: int, maximum: int) -> int:
    """Reject unsafe timing before it controls durable lease/retry behavior."""

    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}.")
    return value


def _open_publisher_channel(connection: Any) -> Any:
    """Open a channel before a body can reach RabbitMQ.

    Channel creation is known to precede ``basic_publish``.  Therefore a
    failure here is safe to return to ``pending`` with bounded retry delay;
    it is not the uncertain post-send confirmation case.
    """

    try:
        return connection.channel()
    except Exception as error:
        raise DispatcherBrokerUnavailable() from error


def _close_connection_quietly(connection: Any | None) -> None:
    """Release one attempt's private AMQP connection without changing state."""

    if connection is None:
        return
    with suppress(Exception):
        connection.close()


def dispatch_dispatchable_once(
    *,
    database: DispatcherWriteDatabase,
    publisher_settings: DispatcherPublisherSettings,
    lease_seconds: int = DEFAULT_DISPATCH_LEASE_SECONDS,
    retry_after_seconds: int = DEFAULT_RETRY_AFTER_SECONDS,
    connection_factory=open_dispatcher_rabbitmq_connection,
) -> DispatchOnceResult:
    """Attempt one finite-vocabulary dispatch without owning a runtime loop.

    The result categories are shared with the existing one-attempt composition
    so a future supervisor can use the same safe counters and sleep policy.
    This function does not itself make the generic path live: no current
    runtime imports or calls it.
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

    # Commit/release the row lock before any broker connection. Other Pods can
    # claim another due row via SKIP LOCKED, and a stopped Pod's lease can later
    # be safely recovered without a distributed transaction across DB/broker.
    with database.write_cursor() as cursor:
        event = claim_due_dispatchable_outbox_event(
            cursor,
            lease_seconds=selected_lease_seconds,
        )
    if event is None:
        return DispatchOnceResult(outcome=DispatchOnceOutcome.IDLE)

    connection: Any | None = None
    try:
        # Validate and choose the route before opening a network connection.
        # A malformed durable row is deterministic and must become terminal;
        # opening RabbitMQ first would create needless operational noise.
        request = build_dispatchable_amqp_request(event)
        connection = connection_factory(publisher_settings)
        channel = _open_publisher_channel(connection)
        enable_dispatcher_publisher_confirms(channel)
        publish_dispatchable_amqp_request(channel, request=request)
    except DispatcherPublisherFailure as failure:
        # This branch proves no confirmed publish occurred. Releasing the same
        # guarded lease with a bounded delay avoids hot loops without losing a
        # durable event. It applies equally to every approved worker stage.
        _close_connection_quietly(connection)
        with database.write_cursor() as cursor:
            retry_applied = schedule_outbox_event_retry(
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
        # RabbitMQ might have accepted the persistent body just before a lost
        # confirmation. Leave this lease untouched; expiry-based recovery can
        # duplicate-publish, while an eager retry could obscure that boundary.
        _close_connection_quietly(connection)
        return DispatchOnceResult(outcome=DispatchOnceOutcome.CONFIRMATION_UNKNOWN)
    except (DispatchableOutboxRequestContractError, DispatcherPublisherContractError):
        # No route/payload contract error may reach any worker queue. Persist a
        # fixed terminal category rather than exception text or a private key.
        _close_connection_quietly(connection)
        with database.write_cursor() as cursor:
            dead_letter_applied = mark_outbox_event_dead_lettered(
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
        # At this point the only unexpected operation before publish is future
        # channel/client drift. Treat it as known broker-unavailable only
        # because no generic publisher call successfully began with a body.
        _close_connection_quietly(connection)
        known_failure = DispatcherBrokerUnavailable()
        with database.write_cursor() as cursor:
            retry_applied = schedule_outbox_event_retry(
                cursor,
                event_id=event.event_id,
                lease_token=event.lease_token,
                retry_after_seconds=selected_retry_after_seconds,
                failure_code=known_failure.failure_code,
            )
        return DispatchOnceResult(
            outcome=(
                DispatchOnceOutcome.RETRY_SCHEDULED
                if retry_applied
                else DispatchOnceOutcome.LEASE_NO_LONGER_OWNED
            )
        )
    else:
        _close_connection_quietly(connection)

    # Only a broker-confirmed publish reaches this second database transaction.
    # The token guard means a delayed/stale Pod can never publish the state of a
    # lease another dispatcher has already recovered or released.
    with database.write_cursor() as cursor:
        publication_applied = mark_outbox_event_published(
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
