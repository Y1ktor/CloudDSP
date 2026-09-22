"""Open one closeable prepared private AMQP session for the Demucs worker.

The connection factory validates the restricted identity and opens one private
RabbitMQ socket. The passive-channel adapter applies prefetch one and verifies
the bootstrap-owned Demucs request queue. This context manager combines only
those setup layers and yields the prepared channel. It does not receive,
acknowledge, reject, requeue, publish, or declare topology; the existing worker
cycle owns all delivery actions.

Pika connections/channels are client resources, not Kubernetes resources. They
must close channel-first then connection on setup failure, normal completion,
supervisor-loop error, or cleanup error. Callers receive no connection object,
keeping close/publish/topology capabilities inside this lifecycle boundary.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from app.amqp_channel import DemucsAMQPChannelUnavailable, configure_demucs_rabbitmq_channel
from app.amqp_connection import (
    DemucsAMQPSettings,
    open_demucs_rabbitmq_connection,
    validate_demucs_amqp_settings,
)


def _close_amqp_resource(resource: Any, *, kind: str) -> None:
    """Close one Pika-shaped resource without closing an already-closed one.

    ``is_open`` is honored only when it is the actual boolean ``False``. Pika
    versions/test doubles may omit it, but then must still expose ``close``.
    This helper makes no broker delivery action and never exposes a raw client
    diagnostic through the session's stable outer error category.
    """

    if resource is None:
        return
    is_open = getattr(resource, "is_open", None)
    if is_open is False:
        return
    close = getattr(resource, "close", None)
    if not callable(close):
        raise TypeError(f"Demucs AMQP {kind} does not provide close.")
    close()


@contextmanager
def opened_demucs_rabbitmq_session(
    *,
    settings: DemucsAMQPSettings | None = None,
) -> Generator[Any, None, None]:
    """Yield one passively verified channel and always close its AMQP session.

    The supervisor loop may run only inside this context. Setup failure closes
    any acquired resource before propagating its reviewed category. If caller
    work fails, cleanup preserves that original failure; channel/connection
    close failure after otherwise normal exit becomes the existing redacted
    retryable channel-unavailable category.
    """

    approved = (
        DemucsAMQPSettings.from_environment()
        if settings is None
        else validate_demucs_amqp_settings(settings)
    )
    connection: Any | None = None
    channel: Any | None = None
    primary_error: BaseException | None = None

    try:
        connection = open_demucs_rabbitmq_connection(approved)
        create_channel = getattr(connection, "channel", None)
        if not callable(create_channel):
            raise TypeError("Demucs AMQP connection does not provide channel.")
        try:
            channel = create_channel()
        except Exception as error:
            raise DemucsAMQPChannelUnavailable("RabbitMQ Demucs channel is unavailable.") from error

        # Prefetch one/passive declaration complete before a later loop may
        # call its first basic_get; this cannot create or alter topology.
        configure_demucs_rabbitmq_channel(channel, settings=approved)
        yield channel
    except BaseException as error:
        primary_error = error
        raise
    finally:
        cleanup_error: BaseException | None = None
        # Close in ownership nesting order. Attempt socket cleanup even when
        # channel close fails, so termination does not leak a connection.
        for resource, kind in ((channel, "channel"), (connection, "connection")):
            try:
                _close_amqp_resource(resource, kind=kind)
            except BaseException as error:
                if cleanup_error is None:
                    cleanup_error = error
        if primary_error is None and cleanup_error is not None:
            raise DemucsAMQPChannelUnavailable("RabbitMQ Demucs channel is unavailable.") from cleanup_error
