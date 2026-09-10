"""Manual-ack RabbitMQ adapter for one upload-intake source notification.

This transport adapter receives a message from the pre-created
``clouddsp.source-intake`` queue, calls the transaction-aware application
handler, and acknowledges only after that handler returns a durable-safe
result. It does not create topology, publish Demucs work, manage delayed
retries, open PostgreSQL, call MinIO, or run a Deployment loop. Those remain
separate responsibilities so the acknowledgement rule stays auditable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

from app.message_handler import SourceIntakeMessageResult


# These values are the reviewed RabbitMQ ClusterIP and topology created by the
# completed source-intake bootstrap. The future Deployment may repeat them as
# non-secret environment configuration, but a Pod must never use the Mac port
# forward or RabbitMQ management endpoint for normal AMQP processing.
DEFAULT_AMQP_HOST = "clouddsp-rabbitmq.clouddsp-data.svc"
DEFAULT_AMQP_PORT = 5672
SOURCE_INTAKE_VHOST = "/clouddsp"
SOURCE_INTAKE_QUEUE = "clouddsp.source-intake"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5
DEFAULT_HEARTBEAT_SECONDS = 30


class RabbitMQConfigurationError(RuntimeError):
    """Raise a safe category for invalid/missing AMQP Pod configuration."""


class RabbitMQUnavailable(RuntimeError):
    """Raise a retryable category without exposing broker/credential details."""


def _required_environment_text(name: str, *, default: str | None = None) -> str:
    """Read a non-empty config/Secret value without logging its value."""

    value = os.environ.get(name, default)
    if value is None or not isinstance(value, str) or not value or "\x00" in value:
        raise RabbitMQConfigurationError(f"Required environment variable {name} is absent.")
    return value


def _bounded_positive_integer(*, name: str, value: str, maximum: int) -> int:
    """Parse a small bounded port/timeout setting before using the AMQP client."""

    try:
        parsed = int(value)
    except ValueError as error:
        raise RabbitMQConfigurationError(f"{name} must be an integer.") from error
    if not 1 <= parsed <= maximum:
        raise RabbitMQConfigurationError(f"{name} must be between 1 and {maximum}.")
    return parsed


@dataclass(frozen=True)
class RabbitMQSettings:
    """One restricted AMQP connection configuration for the future consumer Pod.

    The username/password come from the existing application-namespace
    `clouddsp-upload-intake-rabbitmq-credentials` Secret. The role is allowed
    to read only the source-intake queue and has no queue/exchange management
    authority. The password is hidden from the generated object representation.
    """

    host: str
    port: int
    username: str
    password: str = field(repr=False)
    virtual_host: str = SOURCE_INTAKE_VHOST
    queue_name: str = SOURCE_INTAKE_QUEUE
    connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS
    heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS

    @classmethod
    def from_environment(cls) -> "RabbitMQSettings":
        """Load private Service DNS plus restricted application credentials."""

        host = _required_environment_text("UPLOAD_INTAKE_AMQP_HOST", default=DEFAULT_AMQP_HOST)
        # `.localhost` points at the Mac/browser ingress path. A Kubernetes Pod
        # must connect directly to its private ClusterIP Service instead.
        if host == "localhost" or host.endswith(".localhost"):
            raise RabbitMQConfigurationError("UPLOAD_INTAKE_AMQP_HOST must be private Service DNS.")

        virtual_host = _required_environment_text(
            "UPLOAD_INTAKE_AMQP_VHOST",
            default=SOURCE_INTAKE_VHOST,
        )
        queue_name = _required_environment_text(
            "UPLOAD_INTAKE_AMQP_QUEUE",
            default=SOURCE_INTAKE_QUEUE,
        )
        # The consumer must not become a configurable arbitrary-queue reader
        # merely because a Deployment environment value was changed. A topology
        # version change is a separate reviewed task and must update this code.
        if virtual_host != SOURCE_INTAKE_VHOST or queue_name != SOURCE_INTAKE_QUEUE:
            raise RabbitMQConfigurationError("Upload-intake AMQP topology does not match the reviewed queue.")

        return cls(
            host=host,
            port=_bounded_positive_integer(
                name="UPLOAD_INTAKE_AMQP_PORT",
                value=os.environ.get("UPLOAD_INTAKE_AMQP_PORT", str(DEFAULT_AMQP_PORT)),
                maximum=65_535,
            ),
            username=_required_environment_text("RABBITMQ_UPLOAD_INTAKE_USERNAME"),
            password=_required_environment_text("RABBITMQ_UPLOAD_INTAKE_PASSWORD"),
            virtual_host=virtual_host,
            queue_name=queue_name,
            connect_timeout_seconds=_bounded_positive_integer(
                name="UPLOAD_INTAKE_AMQP_CONNECT_TIMEOUT_SECONDS",
                value=os.environ.get(
                    "UPLOAD_INTAKE_AMQP_CONNECT_TIMEOUT_SECONDS",
                    str(DEFAULT_CONNECT_TIMEOUT_SECONDS),
                ),
                maximum=30,
            ),
            heartbeat_seconds=_bounded_positive_integer(
                name="UPLOAD_INTAKE_AMQP_HEARTBEAT_SECONDS",
                value=os.environ.get(
                    "UPLOAD_INTAKE_AMQP_HEARTBEAT_SECONDS",
                    str(DEFAULT_HEARTBEAT_SECONDS),
                ),
                maximum=300,
            ),
        )


def _load_pika() -> Any:
    """Load the pinned AMQP library only when a real connection is requested."""

    try:
        import pika
    except ImportError as error:
        raise RabbitMQConfigurationError("Pinned AMQP client dependency is unavailable.") from error
    return pika


def open_rabbitmq_connection(settings: RabbitMQSettings) -> Any:
    """Create one bounded Pika connection using the restricted intake login.

    This function opens a broker socket but does not receive/acknowledge a
    message. The later container entrypoint will own reconnect/backoff policy;
    the first adapter stays small enough to make its connection contract clear.
    """

    if not isinstance(settings, RabbitMQSettings):
        raise TypeError("settings must be RabbitMQSettings.")
    pika = _load_pika()
    parameters = pika.ConnectionParameters(
        host=settings.host,
        port=settings.port,
        virtual_host=settings.virtual_host,
        credentials=pika.PlainCredentials(settings.username, settings.password),
        # Bounded initial retries tolerate brief Service endpoint propagation
        # without making a broken Secret or broker outage a tight hot loop.
        connection_attempts=3,
        retry_delay=1,
        socket_timeout=settings.connect_timeout_seconds,
        blocked_connection_timeout=settings.connect_timeout_seconds * 2,
        heartbeat=settings.heartbeat_seconds,
    )
    try:
        return pika.BlockingConnection(parameters)
    except Exception as error:
        # This boundary calls only Pika connection code, so a broad catch cannot
        # mask an application handler error. Preserve the cause privately but
        # publish only the safe category to a future process supervisor.
        raise RabbitMQUnavailable("RabbitMQ upload-intake connection is unavailable.") from error


def configure_source_intake_channel(channel: Any, *, settings: RabbitMQSettings) -> None:
    """Apply safe consumer flow control and prove the reviewed queue exists.

    ``prefetch_count=1`` means RabbitMQ gives this Pod no second unacknowledged
    job while it is verifying the first. A passive declaration reads existing
    queue metadata without creating/changing topology; the restricted account
    lacks authority to declare arbitrary queues or exchanges.
    """

    try:
        channel.basic_qos(prefetch_count=1)
        channel.queue_declare(queue=settings.queue_name, passive=True)
    except Exception as error:
        raise RabbitMQUnavailable("RabbitMQ source-intake channel is unavailable.") from error


def consume_one_source_intake_delivery(
    channel: Any,
    *,
    settings: RabbitMQSettings,
    handle_message: Callable[[bytes], SourceIntakeMessageResult],
) -> bool:
    """Receive, durably handle, then manually acknowledge at most one delivery.

    Returns ``False`` when the queue is empty and ``True`` only after one
    message handler returned an acknowledgement-safe result and ``basic_ack``
    succeeded. The function uses ``auto_ack=False``: a received message stays
    unacknowledged while PostgreSQL/MinIO work occurs.

    If the handler raises—for example, a transient MinIO or PostgreSQL
    outage—this function deliberately does **not** acknowledge or requeue the
    message. A naive immediate requeue would make a hot loop. The later
    long-running entrypoint will close/reconnect after the error, causing
    RabbitMQ to redeliver safely; a separate task will replace that temporary
    failure path with the reviewed delayed-retry/DLQ publish flow.
    """

    try:
        method_frame, _properties, body = channel.basic_get(
            queue=settings.queue_name,
            auto_ack=False,
        )
    except Exception as error:
        raise RabbitMQUnavailable("RabbitMQ source-intake receive is unavailable.") from error

    if method_frame is None:
        return False
    if not isinstance(body, bytes):
        # Pika normally supplies bytes. Do not hand an unexpected runtime type
        # to the application handler and then accidentally acknowledge it.
        raise RabbitMQUnavailable("RabbitMQ source-intake delivery body is invalid.")

    # Do not catch handler exceptions: no acknowledgement occurs below and the
    # caller can close the connection, allowing the broker to retain/redeliver
    # this at-least-once message without an immediate requeue loop.
    handling_result = handle_message(body)
    if not handling_result.safe_to_acknowledge:
        # The current handler intentionally never returns false. Retain this
        # guard so a future implementation cannot accidentally broaden the
        # acknowledgement condition without making that decision explicit.
        raise RuntimeError("Source-intake message is not safe to acknowledge.")

    try:
        channel.basic_ack(method_frame.delivery_tag)
    except Exception as error:
        # PostgreSQL may already be committed at this point. An ack failure is
        # safe: RabbitMQ redelivers later and the revision-guarded SQL becomes
        # a no-op instead of duplicating work or rolling state backward.
        raise RabbitMQUnavailable("RabbitMQ source-intake acknowledgement is unavailable.") from error
    return True
