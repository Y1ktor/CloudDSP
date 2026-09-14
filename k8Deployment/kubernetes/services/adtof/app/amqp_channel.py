"""Configure one safe RabbitMQ channel for the existing ADTOF request queue.

The connection factory provides a private AMQP socket. This next narrow layer
uses one Pika-shaped channel only to apply ADTOF's one-at-a-time flow control
and passively verify the queue created by the versioned RabbitMQ topology.

It does not create/bind/delete a queue, declare an exchange, receive a
delivery, acknowledge/reject/retry a message, contact PostgreSQL/MinIO, invoke
ADTOF, or change a Kubernetes resource. Separating channel setup from a future
consumer loop makes two guarantees visible: one CPU Pod holds at most one
unacknowledged delivery, and a restricted consumer reports missing topology
instead of attempting privileged self-repair.
"""

from __future__ import annotations

from typing import Any

from app.amqp_connection import (
    ADTOF_PREFETCH_COUNT,
    ADTOF_REQUEST_QUEUE,
    ADTOFAMQPSettings,
    validate_adtof_amqp_settings,
)


class ADTOFAMQPChannelUnavailable(RuntimeError):
    """A retryable channel/topology-read failure without broker diagnostics.

    A later supervisor must close the connection and retry with a bounded
    policy. It must not treat missing topology as idle work or actively declare
    the queue: topology remains owned by its reviewed RabbitMQ bootstrap Job.
    """


def configure_adtof_rabbitmq_channel(channel: Any, *, settings: ADTOFAMQPSettings) -> None:
    """Apply prefetch one and passively verify the fixed ADTOF request queue.

    ``prefetch_count=1`` ensures a CPU-bound ADTOF worker cannot reserve several
    RabbitMQ deliveries and PostgreSQL leases while it processes one drums WAV.
    The later consumer obtains another delivery only after it finishes the
    current durable/acknowledgement decision.

    ``passive=True`` asks RabbitMQ to verify an existing queue and its
    properties without declaring, binding, changing, or repairing it. A broker
    failure becomes one safe retryable category, keeping broker/user/queue
    diagnostics out of future normal worker logs.
    """

    approved = validate_adtof_amqp_settings(settings)
    if not callable(getattr(channel, "basic_qos", None)) or not callable(
        getattr(channel, "queue_declare", None)
    ):
        raise TypeError("channel must provide basic_qos and queue_declare.")

    try:
        channel.basic_qos(prefetch_count=ADTOF_PREFETCH_COUNT)
        # Passive declaration is a topology read only; this user lacks the
        # configure permission that an active declaration would require.
        channel.queue_declare(queue=approved.queue_name, passive=True)
    except Exception as error:
        raise ADTOFAMQPChannelUnavailable("RabbitMQ ADTOF channel is unavailable.") from error
