"""Restricted RabbitMQ settings and bounded Pika connection factory for Demucs.

The future Demucs Pod receives the no-tag ``clouddsp-demucs`` RabbitMQ identity
from its application-namespace Secret.  This module turns that narrow Secret
plus fixed private Service/topology settings into one bounded AMQP connection.
It opens no channel, consumes no message, acknowledges nothing, declares no
topology, publishes no retry, and starts no loop; those responsibilities remain
outside the connection boundary.

Keeping configuration validation here means a Deployment typo cannot quietly
turn the worker into a Mac-host, browser-ingress, management-port, or arbitrary
queue client.  The Pika import is lazy so all settings tests run without a
broker socket or installed runtime dependency on the developer machine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from app.amqp_manual_ack import DEMUCS_REQUEST_QUEUE


# These are the private RabbitMQ ClusterIP endpoint and fixed vhost already
# created by the reviewed local topology/bootstrap work. They are not browser
# routes and must never be replaced with `localhost`, `*.localhost`, or 15672.
DEFAULT_DEMUCS_AMQP_HOST = "clouddsp-rabbitmq.clouddsp-data.svc"
DEFAULT_DEMUCS_AMQP_PORT = 5672
DEMUCS_AMQP_VHOST = "/clouddsp"
LOCAL_DEMUCS_RABBITMQ_USERNAME = "clouddsp-demucs"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5
DEFAULT_HEARTBEAT_SECONDS = 30


class DemucsAMQPConfigurationError(RuntimeError):
    """A missing, malformed, or widened local worker AMQP configuration."""


class DemucsAMQPConnectionUnavailable(RuntimeError):
    """A retryable AMQP connection failure with no broker/Secret diagnostic."""


def _required_environment_text(name: str, *, default: str | None = None) -> str:
    """Read required text while keeping a mounted Secret out of error output."""

    value = os.environ.get(name, default)
    if value is None or not isinstance(value, str) or not value or "\x00" in value:
        raise DemucsAMQPConfigurationError(f"Required environment variable {name} is absent.")
    return value


def _bounded_positive_integer(*, name: str, value: str, maximum: int) -> int:
    """Parse a small positive integer before it controls a client timeout."""

    try:
        parsed = int(value)
    except ValueError as error:
        raise DemucsAMQPConfigurationError(f"{name} must be an integer.") from error
    if not 1 <= parsed <= maximum:
        raise DemucsAMQPConfigurationError(f"{name} must be between 1 and {maximum}.")
    return parsed


@dataclass(frozen=True)
class DemucsAMQPSettings:
    """The only AMQP connection values a future Demucs worker may use.

    ``RABBITMQ_DEMUCS_USERNAME`` and ``RABBITMQ_DEMUCS_PASSWORD`` are mounted
    from `clouddsp-demucs-rabbitmq-credentials` in `clouddsp-app`. The username
    must be the restricted consumer—not RabbitMQ's administrator—and the
    password is excluded from ``repr`` so ordinary debugging cannot disclose
    it. Host, port, vhost, and queue are fixed contract values rather than a
    mechanism for widening this worker's existing read-only broker authority.
    """

    host: str
    port: int
    username: str
    password: str = field(repr=False)
    virtual_host: str = DEMUCS_AMQP_VHOST
    queue_name: str = DEMUCS_REQUEST_QUEUE
    connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS
    heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS

    @classmethod
    def from_environment(cls) -> "DemucsAMQPSettings":
        """Load the reviewed private endpoint and restricted Secret values."""

        host = _required_environment_text("DEMUCS_AMQP_HOST", default=DEFAULT_DEMUCS_AMQP_HOST)
        if host != DEFAULT_DEMUCS_AMQP_HOST:
            raise DemucsAMQPConfigurationError(
                "DEMUCS_AMQP_HOST must match the private RabbitMQ Service DNS."
            )

        port = _bounded_positive_integer(
            name="DEMUCS_AMQP_PORT",
            value=os.environ.get("DEMUCS_AMQP_PORT", str(DEFAULT_DEMUCS_AMQP_PORT)),
            maximum=65_535,
        )
        if port != DEFAULT_DEMUCS_AMQP_PORT:
            raise DemucsAMQPConfigurationError("DEMUCS_AMQP_PORT must match private RabbitMQ AMQP.")

        virtual_host = _required_environment_text("DEMUCS_AMQP_VHOST", default=DEMUCS_AMQP_VHOST)
        queue_name = _required_environment_text("DEMUCS_AMQP_QUEUE", default=DEMUCS_REQUEST_QUEUE)
        if virtual_host != DEMUCS_AMQP_VHOST or queue_name != DEMUCS_REQUEST_QUEUE:
            raise DemucsAMQPConfigurationError("Demucs AMQP topology does not match the reviewed request queue.")

        username = _required_environment_text("RABBITMQ_DEMUCS_USERNAME")
        if username != LOCAL_DEMUCS_RABBITMQ_USERNAME:
            raise DemucsAMQPConfigurationError(
                "RABBITMQ_DEMUCS_USERNAME must match the restricted Demucs consumer."
            )
        settings = cls(
            host=host,
            port=port,
            username=username,
            password=_required_environment_text("RABBITMQ_DEMUCS_PASSWORD"),
            virtual_host=virtual_host,
            queue_name=queue_name,
            connect_timeout_seconds=_bounded_positive_integer(
                name="DEMUCS_AMQP_CONNECT_TIMEOUT_SECONDS",
                value=os.environ.get(
                    "DEMUCS_AMQP_CONNECT_TIMEOUT_SECONDS",
                    str(DEFAULT_CONNECT_TIMEOUT_SECONDS),
                ),
                # A failed private Service lookup should return control to a
                # later supervisor quickly rather than occupying a worker slot.
                maximum=30,
            ),
            heartbeat_seconds=_bounded_positive_integer(
                name="DEMUCS_AMQP_HEARTBEAT_SECONDS",
                value=os.environ.get("DEMUCS_AMQP_HEARTBEAT_SECONDS", str(DEFAULT_HEARTBEAT_SECONDS)),
                maximum=300,
            ),
        )
        # Reuse the direct-construction guard so mounted settings and test or
        # future entrypoint-built settings have exactly the same authority.
        return validate_demucs_amqp_settings(settings)


def validate_demucs_amqp_settings(value: object) -> DemucsAMQPSettings:
    """Revalidate direct settings construction before any broker I/O.

    Frozen dataclasses may be built deliberately in tests and future entry
    points. Repeating every fixed identity/endpoint/topology rule prevents a
    caller from bypassing ``from_environment()`` and redirecting the restricted
    credentials to a management port, foreign queue, or administrator account.
    """

    if not isinstance(value, DemucsAMQPSettings):
        raise TypeError("settings must be DemucsAMQPSettings.")
    if (
        value.host != DEFAULT_DEMUCS_AMQP_HOST
        or value.port != DEFAULT_DEMUCS_AMQP_PORT
        or value.virtual_host != DEMUCS_AMQP_VHOST
        or value.queue_name != DEMUCS_REQUEST_QUEUE
        or value.username != LOCAL_DEMUCS_RABBITMQ_USERNAME
        or not isinstance(value.password, str)
        or not value.password
        or any(ord(character) < 0x20 or 0xD800 <= ord(character) <= 0xDFFF for character in value.password)
    ):
        raise DemucsAMQPConfigurationError("Demucs AMQP settings are outside the local contract.")

    # Exact types reject Python booleans before they act as integers. The
    # parser keeps direct construction bound to the environment's time limits.
    if type(value.connect_timeout_seconds) is not int or type(value.heartbeat_seconds) is not int:
        raise DemucsAMQPConfigurationError("Demucs AMQP timing is invalid.")
    connect_timeout_seconds = _bounded_positive_integer(
        name="DEMUCS_AMQP_CONNECT_TIMEOUT_SECONDS",
        value=str(value.connect_timeout_seconds),
        maximum=30,
    )
    heartbeat_seconds = _bounded_positive_integer(
        name="DEMUCS_AMQP_HEARTBEAT_SECONDS",
        value=str(value.heartbeat_seconds),
        maximum=300,
    )
    return DemucsAMQPSettings(
        host=value.host,
        port=value.port,
        username=value.username,
        password=value.password,
        virtual_host=value.virtual_host,
        queue_name=value.queue_name,
        connect_timeout_seconds=connect_timeout_seconds,
        heartbeat_seconds=heartbeat_seconds,
    )


def _load_pika() -> Any:
    """Load the hash-pinned AMQP client only when a connection is requested."""

    try:
        import pika
    except ImportError as error:
        raise DemucsAMQPConfigurationError("Pinned Demucs AMQP client dependency is unavailable.") from error
    return pika


def open_demucs_rabbitmq_connection(settings: DemucsAMQPSettings) -> Any:
    """Open one bounded connection with the restricted worker credentials.

    No message has been received or acknowledged when this function returns or
    fails. The later channel/supervisor layer owns passive topology verification,
    prefetch, reconnect timing, and every `basic_*` operation.
    """

    approved = validate_demucs_amqp_settings(settings)
    pika = _load_pika()
    parameters = pika.ConnectionParameters(
        host=approved.host,
        port=approved.port,
        virtual_host=approved.virtual_host,
        credentials=pika.PlainCredentials(approved.username, approved.password),
        # A few short retries absorb local Service endpoint propagation without
        # hiding a broken Secret/broker behind an unbounded connection loop.
        connection_attempts=3,
        retry_delay=1,
        socket_timeout=approved.connect_timeout_seconds,
        blocked_connection_timeout=approved.connect_timeout_seconds * 2,
        heartbeat=approved.heartbeat_seconds,
    )
    try:
        return pika.BlockingConnection(parameters)
    except Exception as error:
        raise DemucsAMQPConnectionUnavailable("RabbitMQ Demucs connection is unavailable.") from error
