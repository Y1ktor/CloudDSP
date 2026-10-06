"""Configure one safe RabbitMQ channel for the existing Basic Pitch queue.

The connection boundary provides a restricted AMQP socket. This next narrow
layer prepares one channel only by applying Basic Pitch's one-at-a-time flow
control and passively checking that the versioned request queue already exists.
It does not create/bind/delete a queue, declare an exchange, receive a
delivery, acknowledge/reject/retry a message, contact PostgreSQL or MinIO, run
Basic Pitch, or change any Kubernetes resource.

Keeping this setup separate from the future consumer loop makes the two
important RabbitMQ guarantees visible: the worker holds at most one
unacknowledged lease candidate, and a least-privilege consumer reports missing
topology instead of attempting to repair it with its restricted credentials.
"""

from __future__ import annotations

from typing import Any

from app.messaging.amqp_connection import BASIC_PITCH_REQUEST_QUEUE, BasicPitchAMQPSettings


class BasicPitchAMQPChannelUnavailable(RuntimeError):
    """A retryable channel/topology-read failure without broker diagnostics.

    A future supervisor must close the connection and retry with bounded
    backoff. It must not treat this as an idle worker or actively declare the
    missing queue: queue ownership stays with the reviewed RabbitMQ topology
    bootstrap, not an audio-processing Pod.
    """


def configure_basic_pitch_rabbitmq_channel(
    channel: Any,
    *,
    settings: BasicPitchAMQPSettings,
) -> None:
    """Limit unacknowledged work to one and passively verify the request queue.

    ``prefetch_count=1`` ensures a CPU-bound Basic Pitch process cannot reserve
    several RabbitMQ deliveries and PostgreSQL leases while it works on only
    one stem. The later consumer will deliberately request the next delivery
    only after it makes the current delivery's durable/acknowledgement decision.

    ``passive=True`` is the topology-safety setting: RabbitMQ checks the
    existing queue name and arguments but never creates it. Any broker failure
    becomes one safe retryable category so raw endpoint/user/queue diagnostics
    do not enter future worker logs.
    """

    if not isinstance(settings, BasicPitchAMQPSettings):
        raise TypeError("settings must be BasicPitchAMQPSettings.")
    if settings.queue_name != BASIC_PITCH_REQUEST_QUEUE:
        # ``from_environment()`` and the connection factory already reject
        # widening. Repeat the queue check here so a direct dataclass cannot
        # turn this otherwise narrow channel operation into another queue read.
        raise ValueError("Basic Pitch AMQP settings do not name the reviewed request queue.")
    if not callable(getattr(channel, "basic_qos", None)) or not callable(
        getattr(channel, "queue_declare", None)
    ):
        raise TypeError("channel must provide basic_qos and queue_declare.")

    try:
        channel.basic_qos(prefetch_count=1)
        # Passive declaration reads existing queue metadata only. It cannot
        # create, bind, delete, or alter quorum/DLQ/delivery-limit settings.
        channel.queue_declare(queue=BASIC_PITCH_REQUEST_QUEUE, passive=True)
    except Exception as error:
        raise BasicPitchAMQPChannelUnavailable("RabbitMQ Basic Pitch channel is unavailable.") from error
