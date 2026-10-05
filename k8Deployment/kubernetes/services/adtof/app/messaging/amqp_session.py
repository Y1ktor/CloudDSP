"""Open one closeable, prepared private AMQP session for the ADTOF worker.

The connection factory validates the restricted runtime identity and opens the
private RabbitMQ socket. The passive-channel adapter then applies prefetch one
and verifies the bootstrap-owned ADTOF request queue. This context manager
combines only those two setup layers and gives its caller the prepared channel.
It intentionally does not receive, acknowledge, reject, requeue, publish, or
declare topology; the existing worker cycle still owns all delivery actions.

Pika connections and channels are client-side resources, not Kubernetes
resources. They must be closed in channel-then-connection order whenever
setup, the caller's supervisor loop, or cleanup itself ends. A caller receives
no connection reference, helping keep connection close/publish/topology work
inside this narrow lifecycle boundary.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from app.messaging.amqp_channel import ADTOFAMQPChannelUnavailable, configure_adtof_rabbitmq_channel
from app.messaging.amqp_connection import ADTOFAMQPSettings, open_adtof_rabbitmq_connection, validate_adtof_amqp_settings


def _close_amqp_resource(resource: Any, *, kind: str) -> None:
    """Close one Pika-shaped resource without treating an already-closed one as open.

    ``is_open`` is read only when it is an actual boolean. Test doubles and
    compatible clients need not expose it; they still must provide ``close``.
    This helper makes no broker delivery action and never includes raw driver
    details in the stable outer error emitted by the session context.
    """

    if resource is None:
        return
    is_open = getattr(resource, "is_open", None)
    if is_open is False:
        return
    close = getattr(resource, "close", None)
    if not callable(close):
        raise TypeError(f"ADTOF AMQP {kind} does not provide close.")
    close()


@contextmanager
def opened_adtof_rabbitmq_session(
    *,
    settings: ADTOFAMQPSettings | None = None,
) -> Generator[Any, None, None]:
    """Yield one passively verified ADTOF channel and always close its session.

    The caller may run the long-lived supervisor loop only inside this context.
    Setup failures close any already-open resource before propagating a reviewed
    connection/channel category. If the caller raises, cleanup preserves that
    original exception; a close failure is not allowed to hide task/model/
    shutdown evidence. A close failure after normal context exit becomes the
    existing redacted retryable channel-availability category.
    """

    approved = (
        ADTOFAMQPSettings.from_environment()
        if settings is None
        else validate_adtof_amqp_settings(settings)
    )
    connection: Any | None = None
    channel: Any | None = None
    primary_error: BaseException | None = None

    try:
        connection = open_adtof_rabbitmq_connection(approved)
        create_channel = getattr(connection, "channel", None)
        if not callable(create_channel):
            raise TypeError("ADTOF AMQP connection does not provide channel.")
        try:
            channel = create_channel()
        except Exception as error:
            raise ADTOFAMQPChannelUnavailable("RabbitMQ ADTOF channel is unavailable.") from error

        # This applies prefetch one and a passive queue declaration only. It
        # must finish before a later loop can call its first `basic_get`.
        configure_adtof_rabbitmq_channel(channel, settings=approved)
        yield channel
    except BaseException as error:
        primary_error = error
        raise
    finally:
        cleanup_error: BaseException | None = None
        # Close in the same nesting order in which Pika resources are owned.
        # Attempt connection cleanup even if channel cleanup failed, preventing
        # one broken channel close from leaking a socket on Pod termination.
        for resource, kind in ((channel, "channel"), (connection, "connection")):
            try:
                _close_amqp_resource(resource, kind=kind)
            except BaseException as error:
                if cleanup_error is None:
                    cleanup_error = error
        if primary_error is None and cleanup_error is not None:
            raise ADTOFAMQPChannelUnavailable("RabbitMQ ADTOF channel is unavailable.") from cleanup_error
