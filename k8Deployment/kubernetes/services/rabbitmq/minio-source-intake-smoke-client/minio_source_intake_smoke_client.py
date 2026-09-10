"""Preflight and consume one expected native MinIO event from RabbitMQ.

This is a disposable integration-test client, not the future upload-intake
runtime. It validates only one deliberate smoke event after proving the intake
queue is empty. That preflight ensures the test never consumes an unrelated
real upload; users must not run the smoke test while normal uploads are active.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import pika

from minio_source_intake_event import (
    ExpectedSourceEvent,
    SourceIntakeSmokeEventError,
    assert_expected_source_event,
)


def _required_environment(name: str) -> str:
    """Read one required setting without ever printing its value."""

    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required environment variable {name} is absent")
    return value


def _connection_parameters() -> pika.ConnectionParameters:
    """Build one bounded AMQP connection using Kubernetes-provided settings."""

    return pika.ConnectionParameters(
        host=_required_environment("AMQP_HOST"),
        port=int(os.environ.get("AMQP_PORT", "5672")),
        virtual_host=_required_environment("AMQP_VHOST"),
        credentials=pika.PlainCredentials(
            _required_environment("AMQP_USER"),
            _required_environment("AMQP_PASSWORD"),
        ),
        # Bounded retries make a Service Endpoint propagation delay harmless
        # without turning this short-lived smoke check into a worker process.
        connection_attempts=3,
        retry_delay=2,
        socket_timeout=5,
        blocked_connection_timeout=10,
        heartbeat=10,
    )


def _queue_name() -> str:
    """Return the fixed production intake queue the smoke test observes."""

    return _required_environment("AMQP_SOURCE_INTAKE_QUEUE")


def _preflight_queue_is_empty(channel: Any) -> None:
    """Fail without consuming anything when normal intake work already exists."""

    # Passive declaration asks RabbitMQ for metadata without creating or
    # modifying the queue. The restricted intake identity has enough authority
    # to observe its own queue but cannot declare arbitrary topology.
    declaration = channel.queue_declare(queue=_queue_name(), passive=True)
    if declaration.method.message_count != 0:
        raise RuntimeError("source-intake queue is not empty; smoke test would be unsafe")


def _wait_for_expected_delivery(channel: Any, expected: ExpectedSourceEvent) -> None:
    """Consume and acknowledge exactly the known smoke delivery before timeout."""

    timeout_seconds = int(os.environ.get("AMQP_SMOKE_TIMEOUT_SECONDS", "45"))
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        # Manual acknowledgement prevents a transient test failure from losing
        # a message. The earlier empty-queue preflight means any first delivery
        # must be this test's own upload unless a concurrent normal upload was
        # started, in which case it is safely requeued below.
        method_frame, _properties, body = channel.basic_get(
            queue=_queue_name(),
            auto_ack=False,
        )
        if method_frame is None:
            time.sleep(1)
            continue

        try:
            assert_expected_source_event(body, expected)
        except SourceIntakeSmokeEventError:
            # Preserve an unexpected event for the real intake consumer. The
            # test fails immediately rather than cycling/requeueing it forever.
            channel.basic_nack(method_frame.delivery_tag, requeue=True)
            raise

        channel.basic_ack(method_frame.delivery_tag)
        print("MinIO source-created event reached RabbitMQ and was acknowledged")
        return

    raise TimeoutError("timed out waiting for the expected MinIO source event")


def main() -> None:
    """Run either the non-mutating preflight or the one-event consume phase."""

    mode = _required_environment("MINIO_SOURCE_INTAKE_SMOKE_MODE")
    if mode not in {"preflight", "consume"}:
        raise RuntimeError("smoke mode must be preflight or consume")

    connection: pika.BlockingConnection | None = None
    channel: Any | None = None
    try:
        connection = pika.BlockingConnection(_connection_parameters())
        channel = connection.channel()

        if mode == "preflight":
            _preflight_queue_is_empty(channel)
            print("MinIO source-intake queue preflight passed: no message was consumed")
            return

        expected = ExpectedSourceEvent(
            bucket_name=_required_environment("MINIO_SMOKE_BUCKET"),
            object_key=_required_environment("MINIO_SMOKE_OBJECT_KEY"),
        )
        _wait_for_expected_delivery(channel, expected)
    except Exception as error:
        # Error category is enough to diagnose this disposable test. Avoid
        # emitting broker exceptions because those can contain endpoint or
        # credential context, and never print a received event body.
        print(f"MinIO source-intake smoke test failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        if channel is not None and channel.is_open:
            channel.close()
        if connection is not None and connection.is_open:
            connection.close()


if __name__ == "__main__":
    main()
