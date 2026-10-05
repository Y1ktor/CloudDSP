"""Strict RabbitMQ configuration and bounded Pika connection setup for Basic Pitch.

The future Basic Pitch Pod receives only the untagged, read-only
``clouddsp-basic-pitch`` RabbitMQ identity from its app-namespace Secret. This
module combines that credential with immutable private-Service/topology values
and opens at most one bounded AMQP connection. It intentionally opens no
channel, receives no message, acknowledges nothing, declares no topology,
publishes no retry, and starts no consumer loop.

The local k3d RabbitMQ profile exposes its private AMQP listener on port 5672
without TLS. There is no AMQPS listener or certificate identity in this local
cluster yet, so this module deliberately never accepts a TLS/SSL environment
toggle or an arbitrary URL. A future TLS profile must add the broker listener,
certificate Secret, and explicit client validation as a separate change rather
than silently changing this worker's transport security.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


# These values name the one private ClusterIP Service and request queue already
# created by the versioned RabbitMQ topology. They are source-level contracts,
# not user-controlled Deployment choices: this worker must never use a Mac
# host port, Traefik/browser route, management listener, or another workload's
# queue merely because an environment variable was misspelled or widened.
DEFAULT_BASIC_PITCH_AMQP_HOST = "clouddsp-rabbitmq.clouddsp-data.svc"
DEFAULT_BASIC_PITCH_AMQP_PORT = 5672
BASIC_PITCH_AMQP_VHOST = "/clouddsp"
BASIC_PITCH_REQUEST_QUEUE = "clouddsp.basic-pitch.requests"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5
DEFAULT_HEARTBEAT_SECONDS = 30


class BasicPitchAMQPConfigurationError(RuntimeError):
    """A Pod setting or direct configuration falls outside the local contract."""


class BasicPitchAMQPConnectionUnavailable(RuntimeError):
    """A retryable AMQP connection fault that intentionally hides broker details."""


def _required_environment_text(name: str, *, default: str | None = None) -> str:
    """Load non-empty, control-free text without printing a mounted Secret value."""

    value = os.environ.get(name, default)
    if (
        value is None
        or not isinstance(value, str)
        or not value
        or any(ord(character) < 0x20 or 0xD800 <= ord(character) <= 0xDFFF for character in value)
    ):
        raise BasicPitchAMQPConfigurationError(f"Required environment variable {name} is absent.")
    return value


def _bounded_positive_integer(*, name: str, value: object, maximum: int) -> int:
    """Parse a finite positive timeout/port before it controls Pika behavior."""

    if not isinstance(value, str):
        raise BasicPitchAMQPConfigurationError(f"{name} must be an integer.")
    try:
        parsed = int(value)
    except ValueError as error:
        raise BasicPitchAMQPConfigurationError(f"{name} must be an integer.") from error
    if not 1 <= parsed <= maximum:
        raise BasicPitchAMQPConfigurationError(f"{name} must be between 1 and {maximum}.")
    return parsed


@dataclass(frozen=True)
class BasicPitchAMQPSettings:
    """The only connection settings available to the Basic Pitch worker.

    The `RABBITMQ_BASIC_PITCH_*` values come from the existing
    `clouddsp-basic-pitch-rabbitmq-credentials` Secret. They name the
    restricted consumer—not RabbitMQ's administrator—and the password is
    omitted from normal ``repr`` output. Host, port, vhost, queue, timeout, and
    heartbeat are held here so tests and a later runtime share one reviewed
    contract; they do not permit selecting another broker resource.
    """

    host: str
    port: int
    username: str
    password: str = field(repr=False)
    virtual_host: str = BASIC_PITCH_AMQP_VHOST
    queue_name: str = BASIC_PITCH_REQUEST_QUEUE
    connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS
    heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS

    @classmethod
    def from_environment(cls) -> "BasicPitchAMQPSettings":
        """Read the fixed private endpoint plus the least-privilege Secret.

        Settings validation makes a bad Deployment fail before Pika is imported
        or a socket is opened. No Secret value is included in errors, logs, or
        return values other than the private dataclass field used for Pika.
        """

        host = _required_environment_text(
            "BASIC_PITCH_AMQP_HOST",
            default=DEFAULT_BASIC_PITCH_AMQP_HOST,
        )
        if host != DEFAULT_BASIC_PITCH_AMQP_HOST:
            raise BasicPitchAMQPConfigurationError(
                "BASIC_PITCH_AMQP_HOST must match the private RabbitMQ Service DNS."
            )
        port = _bounded_positive_integer(
            name="BASIC_PITCH_AMQP_PORT",
            value=os.environ.get("BASIC_PITCH_AMQP_PORT", str(DEFAULT_BASIC_PITCH_AMQP_PORT)),
            maximum=65_535,
        )
        if port != DEFAULT_BASIC_PITCH_AMQP_PORT:
            raise BasicPitchAMQPConfigurationError(
                "BASIC_PITCH_AMQP_PORT must match private RabbitMQ AMQP."
            )
        virtual_host = _required_environment_text(
            "BASIC_PITCH_AMQP_VHOST",
            default=BASIC_PITCH_AMQP_VHOST,
        )
        queue_name = _required_environment_text(
            "BASIC_PITCH_AMQP_QUEUE",
            default=BASIC_PITCH_REQUEST_QUEUE,
        )
        if virtual_host != BASIC_PITCH_AMQP_VHOST or queue_name != BASIC_PITCH_REQUEST_QUEUE:
            raise BasicPitchAMQPConfigurationError(
                "Basic Pitch AMQP topology does not match the reviewed request queue."
            )
        username = _required_environment_text("RABBITMQ_BASIC_PITCH_USERNAME")
        if username != "clouddsp-basic-pitch":
            raise BasicPitchAMQPConfigurationError(
                "RABBITMQ_BASIC_PITCH_USERNAME must match the restricted Basic Pitch consumer."
            )
        settings = cls(
            host=host,
            port=port,
            username=username,
            password=_required_environment_text("RABBITMQ_BASIC_PITCH_PASSWORD"),
            virtual_host=virtual_host,
            queue_name=queue_name,
            connect_timeout_seconds=_bounded_positive_integer(
                name="BASIC_PITCH_AMQP_CONNECT_TIMEOUT_SECONDS",
                value=os.environ.get(
                    "BASIC_PITCH_AMQP_CONNECT_TIMEOUT_SECONDS",
                    str(DEFAULT_CONNECT_TIMEOUT_SECONDS),
                ),
                # A missing Service endpoint must release a future worker slot
                # promptly; supervisor-level retries will have their own bound.
                maximum=30,
            ),
            heartbeat_seconds=_bounded_positive_integer(
                name="BASIC_PITCH_AMQP_HEARTBEAT_SECONDS",
                value=os.environ.get(
                    "BASIC_PITCH_AMQP_HEARTBEAT_SECONDS",
                    str(DEFAULT_HEARTBEAT_SECONDS),
                ),
                maximum=300,
            ),
        )
        return _validated_settings(settings)


def _validated_settings(value: object) -> BasicPitchAMQPSettings:
    """Revalidate direct construction before it can cause a broker connection.

    Frozen dataclasses are intentionally constructible in tests and future
    entry points. Repeating the same fixed-value checks here prevents such a
    direct caller from bypassing ``from_environment()`` and repointing the
    restricted credential at the management port, a different vhost, or a
    foreign queue.
    """

    if not isinstance(value, BasicPitchAMQPSettings):
        raise TypeError("settings must be BasicPitchAMQPSettings.")
    if (
        value.host != DEFAULT_BASIC_PITCH_AMQP_HOST
        or value.port != DEFAULT_BASIC_PITCH_AMQP_PORT
        or value.virtual_host != BASIC_PITCH_AMQP_VHOST
        or value.queue_name != BASIC_PITCH_REQUEST_QUEUE
        or value.username != "clouddsp-basic-pitch"
        or not isinstance(value.password, str)
        or not value.password
        or any(ord(character) < 0x20 or 0xD800 <= ord(character) <= 0xDFFF for character in value.password)
    ):
        raise BasicPitchAMQPConfigurationError("Basic Pitch AMQP settings are outside the local contract.")
    # Convert direct integers to text for the shared finite-bound parser. This
    # permits the normal dataclass API while retaining the same bounds used for
    # environment variables and rejecting booleans (which subclass ``int``).
    if type(value.connect_timeout_seconds) is not int or type(value.heartbeat_seconds) is not int:
        raise BasicPitchAMQPConfigurationError("Basic Pitch AMQP timing is invalid.")
    connect_timeout_seconds = _bounded_positive_integer(
        name="BASIC_PITCH_AMQP_CONNECT_TIMEOUT_SECONDS",
        value=str(value.connect_timeout_seconds),
        maximum=30,
    )
    heartbeat_seconds = _bounded_positive_integer(
        name="BASIC_PITCH_AMQP_HEARTBEAT_SECONDS",
        value=str(value.heartbeat_seconds),
        maximum=300,
    )
    return BasicPitchAMQPSettings(
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
    """Import the future hash-pinned AMQP client only when opening a socket."""

    try:
        import pika
    except ImportError as error:
        raise BasicPitchAMQPConfigurationError(
            "Pinned Basic Pitch AMQP client dependency is unavailable."
        ) from error
    return pika


def open_basic_pitch_rabbitmq_connection(settings: BasicPitchAMQPSettings) -> Any:
    """Open one bounded private AMQP connection without channel/message actions.

    The intentionally absent ``ssl_options`` means this local worker uses the
    reviewed private plain-AMQP listener only. No socket success is a lease,
    message receipt, passive queue check, or acknowledgement; later narrow
    channel and transport layers own those separate responsibilities.
    """

    approved = _validated_settings(settings)
    pika = _load_pika()
    parameters = pika.ConnectionParameters(
        host=approved.host,
        port=approved.port,
        virtual_host=approved.virtual_host,
        credentials=pika.PlainCredentials(approved.username, approved.password),
        # A few short attempts cover Service endpoint propagation without
        # turning a broken Secret/broker into an unbounded worker-side loop.
        connection_attempts=3,
        retry_delay=1,
        socket_timeout=approved.connect_timeout_seconds,
        blocked_connection_timeout=approved.connect_timeout_seconds * 2,
        heartbeat=approved.heartbeat_seconds,
    )
    try:
        return pika.BlockingConnection(parameters)
    except Exception as error:
        raise BasicPitchAMQPConnectionUnavailable(
            "RabbitMQ Basic Pitch connection is unavailable."
        ) from error
