"""Perform one isolated AMQP publish/consume/acknowledge round trip.

This program is intentionally a smoke-test client, not CloudDSP's future worker
runtime.  Kubernetes supplies all connection settings as environment variables;
the program never embeds a broker address, username, password, or production
queue name in the container image.  Its later Job will point it at the internal
RabbitMQ ClusterIP Service on port 5672.
"""

import os
import sys
from typing import Any

import pika


def required_environment(name: str) -> str:
    """Return one non-empty required variable without ever logging its value."""

    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required environment variable is absent: {name}")
    return value


def main() -> None:
    """Use a temporary exclusive queue to prove the AMQP client path end-to-end."""

    connection: pika.BlockingConnection | None = None
    # Pika constructs this channel dynamically, so `Any` avoids evaluating an
    # implementation-specific Pika type while importing this small CLI script.
    channel: Any | None = None
    queue_name: str | None = None
    queue_declared = False

    try:
        host = required_environment("AMQP_HOST")
        username = required_environment("AMQP_USER")
        password = required_environment("AMQP_PASSWORD")
        virtual_host = os.environ.get("AMQP_VHOST", "/")
        queue_name = required_environment("AMQP_SMOKE_QUEUE")
        marker = required_environment("AMQP_SMOKE_MARKER").encode("utf-8")

        # Pika sends AMQP 0-9-1 directly to the configured host/port.  A small,
        # bounded connection retry handles a momentary DNS/endpoints propagation
        # delay without turning this one-shot smoke test into an unbounded worker.
        parameters = pika.ConnectionParameters(
            host=host,
            port=int(os.environ.get("AMQP_PORT", "5672")),
            virtual_host=virtual_host,
            credentials=pika.PlainCredentials(username, password),
            connection_attempts=3,
            retry_delay=2,
            socket_timeout=5,
            blocked_connection_timeout=10,
            heartbeat=10,
        )

        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()

        # An exclusive, auto-delete queue belongs only to this connection.  It
        # cannot collide with CloudDSP topology and disappears when the client
        # disconnects, even if a later cleanup request cannot complete.
        channel.queue_declare(
            queue=queue_name,
            durable=False,
            exclusive=True,
            auto_delete=True,
        )
        queue_declared = True

        # `delivery_mode=1` is intentional: this is a disposable test marker,
        # not a durable CloudDSP processing message.  Future pipeline queues
        # will use durable topology and publisher/consumer idempotency rules.
        channel.basic_publish(
            exchange="",
            routing_key=queue_name,
            body=marker,
            properties=pika.BasicProperties(
                content_type="text/plain",
                delivery_mode=1,
            ),
        )

        # `basic_get` is a one-message pull consumer.  With `auto_ack=False`,
        # RabbitMQ retains the delivery as unacknowledged until the explicit
        # `basic_ack` below, proving the acknowledgement portion of AMQP flow.
        method_frame, _properties, body = channel.basic_get(
            queue=queue_name,
            auto_ack=False,
        )
        if method_frame is None:
            raise RuntimeError("published AMQP marker was not consumable")
        if body != marker:
            # Return an unexpected delivery before failing, rather than losing
            # it.  This should never occur for the isolated exclusive queue.
            channel.basic_nack(method_frame.delivery_tag, requeue=True)
            raise RuntimeError("consumed AMQP marker did not match the published bytes")

        channel.basic_ack(method_frame.delivery_tag)
        print("RabbitMQ AMQP publish/consume/acknowledge smoke test passed")
    except Exception as error:
        # Do not print exception text: connection errors can include endpoint
        # context, and future changes must never risk putting credentials into
        # the Job log.  The exception class is sufficient for first diagnosis.
        print(f"RabbitMQ AMQP smoke test failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        if channel is not None and channel.is_open and queue_declared and queue_name:
            try:
                channel.queue_delete(queue=queue_name)
            except Exception:
                # A closed connection already destroys this exclusive queue.
                # Cleanup must not hide an earlier publish/consume failure.
                pass
        if connection is not None and connection.is_open:
            connection.close()


if __name__ == "__main__":
    main()
