"""Long-running composition root for the local upload-intake consumer.

This module is the small bridge between the reviewed application boundaries:

* RabbitMQ supplies one manual-ack source notification at a time;
* the message handler reads/writes PostgreSQL and verifies MinIO metadata; and
* this runtime owns the connection lifetime, idle polling, reconnection, and
  graceful process-stop boundary.

It deliberately does **not** create RabbitMQ topology, publish retry/DLQ or
Demucs messages, expose HTTP, call the Kubernetes API, or define a Deployment.
Those are separate, reviewable tasks.  In particular, a transient failure
causes the AMQP connection to close without acknowledging the delivery.  The
broker can then redeliver the at-least-once message after the next connection.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from typing import Any

from app.amqp_consumer import (
    RabbitMQSettings,
    configure_source_intake_channel,
    consume_one_source_intake_delivery,
    open_rabbitmq_connection,
)
from app.message_handler import SourceIntakeMessageResult, handle_source_intake_message
from app.object_storage import ObjectStorageSettings, create_boto3_head_object_client
from app.postgresql import PsycopgSourceIntakeDatabase


# These settings are intentionally short and bounded.  ``basic_get`` performs
# a non-blocking broker poll, so a short idle pause avoids a CPU hot loop while
# still letting a terminating Kubernetes Pod notice SIGTERM promptly.  The
# longer reconnect pause prevents a broken broker/Secret/MinIO/DB route from
# spinning connection attempts continuously.
DEFAULT_IDLE_POLL_SECONDS = 1
DEFAULT_RECONNECT_DELAY_SECONDS = 5
MAX_IDLE_POLL_SECONDS = 10
MAX_RECONNECT_DELAY_SECONDS = 60


class ConsumerRuntimeConfigurationError(RuntimeError):
    """Raise a safe category for invalid non-secret consumer loop settings."""


def _bounded_positive_integer(*, name: str, value: str, maximum: int) -> int:
    """Parse an integer Pod setting before it controls a wait loop."""

    try:
        parsed = int(value)
    except ValueError as error:
        raise ConsumerRuntimeConfigurationError(f"{name} must be an integer.") from error
    if not 1 <= parsed <= maximum:
        raise ConsumerRuntimeConfigurationError(f"{name} must be between 1 and {maximum}.")
    return parsed


@dataclass(frozen=True)
class ConsumerRuntimeSettings:
    """Non-secret timing settings for one upload-intake Pod process.

    The future Deployment may leave these defaults unchanged or set the same
    values in a reviewed ConfigMap.  They must never contain a broker URI,
    password, token, job ID, object key, or other per-message data.
    """

    idle_poll_seconds: int = DEFAULT_IDLE_POLL_SECONDS
    reconnect_delay_seconds: int = DEFAULT_RECONNECT_DELAY_SECONDS

    @classmethod
    def from_environment(cls) -> "ConsumerRuntimeSettings":
        """Read bounded, non-secret wait controls from the process environment."""

        return cls(
            idle_poll_seconds=_bounded_positive_integer(
                name="UPLOAD_INTAKE_IDLE_POLL_SECONDS",
                value=os.environ.get(
                    "UPLOAD_INTAKE_IDLE_POLL_SECONDS",
                    str(DEFAULT_IDLE_POLL_SECONDS),
                ),
                maximum=MAX_IDLE_POLL_SECONDS,
            ),
            reconnect_delay_seconds=_bounded_positive_integer(
                name="UPLOAD_INTAKE_RECONNECT_DELAY_SECONDS",
                value=os.environ.get(
                    "UPLOAD_INTAKE_RECONNECT_DELAY_SECONDS",
                    str(DEFAULT_RECONNECT_DELAY_SECONDS),
                ),
                maximum=MAX_RECONNECT_DELAY_SECONDS,
            ),
        )


# A single callable keeps the AMQP adapter independent of PostgreSQL and
# MinIO.  Its result remains intentionally small and cannot accidentally carry
# raw events, secrets, owner identities, or presigned URLs into runtime logs.
SourceIntakeHandler = Callable[[bytes], SourceIntakeMessageResult]
SleepFunction = Callable[[float], None]
StopRequested = Callable[[], bool]
FailureNotifier = Callable[[], None]


def build_source_intake_handler() -> SourceIntakeHandler:
    """Assemble one fixed database/S3 handler from Pod configuration.

    Settings and client objects are constructed once per consumer process, not
    once per RabbitMQ message.  PostgreSQL connections still open only within
    the handler's short read/write transaction contexts; the concrete database
    adapter intentionally does not keep a transaction open while MinIO is
    queried.  Boto3's S3 client can safely be reused for multiple HeadObject
    calls because it does not contain a job-specific state transition.
    """

    database = PsycopgSourceIntakeDatabase()
    object_storage_settings = ObjectStorageSettings.from_environment()
    object_client = create_boto3_head_object_client(object_storage_settings)

    def handle_message(body: bytes) -> SourceIntakeMessageResult:
        """Pass one untrusted AMQP body through the reviewed durable handler."""

        return handle_source_intake_message(
            body,
            database=database,
            object_client=object_client,
            object_storage_settings=object_storage_settings,
        )

    return handle_message


def _close_connection_quietly(connection: Any | None) -> None:
    """Release one AMQP connection without masking its original failure.

    Closing an unacknowledged channel makes RabbitMQ retain/redeliver the
    delivery.  A close can itself fail during a broker outage, but the original
    handler/receive error is more useful to the supervisor and no additional
    action can durably acknowledge a message at this point.
    """

    if connection is None:
        return
    try:
        connection.close()
    except Exception:
        # Never print a driver diagnostic, AMQP endpoint, or credentials.  The
        # next supervisor iteration performs the bounded reconnect attempt.
        pass


def consume_connection_cycle(
    *,
    amqp_settings: RabbitMQSettings,
    handle_message: SourceIntakeHandler,
    idle_poll_seconds: int,
    stop_requested: StopRequested,
    sleep_function: SleepFunction = time.sleep,
    connection_factory: Callable[[RabbitMQSettings], Any] = open_rabbitmq_connection,
    configure_channel: Callable[..., None] = configure_source_intake_channel,
    consume_one: Callable[..., bool] = consume_one_source_intake_delivery,
) -> None:
    """Consume until shutdown or one failure, then always close the connection.

    A normal empty-queue poll pauses for ``idle_poll_seconds``.  A received
    message is handled by ``consume_one``; that function performs the crucial
    manual acknowledgement only after the durable handler succeeds.  Any
    exception intentionally escapes this cycle.  The outer supervisor closes
    the now-failed connection and reconnects later, leaving an unacknowledged
    message available for at-least-once redelivery.
    """

    if idle_poll_seconds < 1:
        raise ValueError("idle_poll_seconds must be positive.")

    connection: Any | None = None
    try:
        connection = connection_factory(amqp_settings)
        channel = connection.channel()
        # The adapter only configures flow control and passively checks the
        # pre-existing queue; it cannot create/change broker topology.
        configure_channel(channel, settings=amqp_settings)

        while not stop_requested():
            received_delivery = consume_one(
                channel,
                settings=amqp_settings,
                handle_message=handle_message,
            )
            if not received_delivery:
                # ``basic_get`` is deliberately non-blocking.  This pause is
                # not a correctness timer: a message arriving after the poll
                # remains durable in RabbitMQ until the next poll.
                sleep_function(idle_poll_seconds)
    finally:
        # This also runs when a handler fails before its manual ack.  Closing
        # the channel/connection returns that delivery to the broker safely.
        _close_connection_quietly(connection)


def _default_failure_notifier() -> None:
    """Emit one stable operational signal without printing sensitive details."""

    print(
        "upload-intake consumer reconnecting after a failed broker or processing cycle.",
        file=sys.stderr,
    )


def run_consumer_forever(
    *,
    amqp_settings: RabbitMQSettings,
    handle_message: SourceIntakeHandler,
    runtime_settings: ConsumerRuntimeSettings,
    stop_requested: StopRequested,
    sleep_function: SleepFunction = time.sleep,
    failure_notifier: FailureNotifier = _default_failure_notifier,
    connection_factory: Callable[[RabbitMQSettings], Any] = open_rabbitmq_connection,
    configure_channel: Callable[..., None] = configure_source_intake_channel,
    consume_one: Callable[..., bool] = consume_one_source_intake_delivery,
) -> None:
    """Supervise sequential AMQP connection cycles until a stop is requested.

    Catching a cycle exception here is deliberate: the manual-ack adapter has
    already avoided acknowledgement, and the cycle's ``finally`` block closes
    the connection.  The bounded pause then avoids an immediate reconnect hot
    loop.  The exception text is never logged because SDK diagnostics can
    include endpoint details or other operational information we do not need
    in a teaching deployment's normal Pod logs.

    This is temporary reconnect semantics, *not* the final retry policy.  The
    next reliability task must explicitly publish transient failures through
    the pre-created 30-second retry queue and send exhausted messages to the
    DLQ before acknowledging their original delivery.
    """

    while not stop_requested():
        try:
            consume_connection_cycle(
                amqp_settings=amqp_settings,
                handle_message=handle_message,
                idle_poll_seconds=runtime_settings.idle_poll_seconds,
                stop_requested=stop_requested,
                sleep_function=sleep_function,
                connection_factory=connection_factory,
                configure_channel=configure_channel,
                consume_one=consume_one,
            )
        except Exception:
            # The connection cycle has already closed its connection.  Do not
            # acknowledge, requeue, or inspect the body here; doing any of
            # those could turn a transient error into a lost/looping message.
            if not stop_requested():
                failure_notifier()
                sleep_function(runtime_settings.reconnect_delay_seconds)


def _install_shutdown_handlers(stop_event: Event) -> None:
    """Convert Docker/Kubernetes SIGTERM into a cooperative loop shutdown.

    Kubernetes sends SIGTERM before its termination grace period.  The handler
    only sets an in-memory flag; it does not run database/RabbitMQ operations
    inside signal context.  The non-blocking poll loop notices it, exits, and
    closes its AMQP connection in normal Python control flow.
    """

    def request_stop(_signal_number: int, _current_frame: Any) -> None:
        """Record shutdown without serializing any potentially sensitive state."""

        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)


def main() -> int:
    """Run the one-process worker; suitable for ``python -m`` in a later image."""

    try:
        # Configuration errors are process-start failures, not reconnectable
        # runtime outages.  A future Deployment's restart policy makes them
        # visible without silently looping forever on a missing Secret.
        runtime_settings = ConsumerRuntimeSettings.from_environment()
        amqp_settings = RabbitMQSettings.from_environment()
        handler = build_source_intake_handler()
    except Exception:
        print("upload-intake consumer has invalid or incomplete configuration.", file=sys.stderr)
        return 2

    stop_event = Event()
    _install_shutdown_handlers(stop_event)
    run_consumer_forever(
        amqp_settings=amqp_settings,
        handle_message=handler,
        runtime_settings=runtime_settings,
        stop_requested=stop_event.is_set,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
