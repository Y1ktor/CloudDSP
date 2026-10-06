"""Safe one-channel setup for the existing private Demucs request queue.

The connection factory opens a restricted AMQP socket; this next boundary uses
that socket's Pika channel only to establish safe consumer flow control and to
verify that the already-imported request queue exists.  It does not declare or
modify topology, receive/acknowledge a delivery, publish a retry, start a loop,
or interact with PostgreSQL, MinIO, audio tools, Kubernetes, or model code.
"""

from __future__ import annotations

from typing import Any

from app.messaging.amqp_connection import DemucsAMQPSettings
from app.messaging.amqp_manual_ack import DEMUCS_REQUEST_QUEUE


class DemucsAMQPChannelUnavailable(RuntimeError):
    """A retryable channel setup failure without broker diagnostic details.

    The future supervisor must close this connection and reconnect with bounded
    backoff. It must not treat an unavailable queue/channel as an idle worker
    or try to create the missing broker topology with the restricted consumer
    identity.
    """


def configure_demucs_rabbitmq_channel(
    channel: Any,
    *,
    settings: DemucsAMQPSettings,
) -> None:
    """Limit in-flight work to one and passively verify the fixed queue exists.

    ``prefetch_count=1`` stops RabbitMQ from assigning a second unacknowledged
    source to this worker while it owns the first task lease. This is essential
    for a CPU/GPU-bound single-process worker: it avoids holding several broker
    deliveries and their leases in memory while one long audio job runs.

    A passive queue declaration reads existing queue metadata but never creates
    a queue or changes its quorum/DLQ/delivery-limit arguments. If the versioned
    topology is absent, incompatible, or inaccessible, Pika raises and the
    future supervisor gets a bounded unavailable category instead of proceeding
    against an assumed queue.
    """

    if not isinstance(settings, DemucsAMQPSettings):
        raise TypeError("settings must be DemucsAMQPSettings.")
    if settings.queue_name != DEMUCS_REQUEST_QUEUE:
        # `from_environment()` already enforces this. Retaining the check at
        # this boundary protects a future direct construction in a test/entry
        # point from turning a restricted worker into an arbitrary queue reader.
        raise ValueError("Demucs AMQP settings do not name the reviewed request queue.")

    try:
        channel.basic_qos(prefetch_count=1)
        # `passive=True` is the key safety setting: RabbitMQ checks the existing
        # name but does not create, bind, delete, or alter any queue topology.
        channel.queue_declare(queue=DEMUCS_REQUEST_QUEUE, passive=True)
    except Exception as error:
        raise DemucsAMQPChannelUnavailable("RabbitMQ Demucs channel is unavailable.") from error
