"""Strict RabbitMQ connection settings for the future ADTOF consumer.

The future Pod receives only the untagged, read-only ``clouddsp-adtof`` broker
identity from its app-namespace Secret. This pure module combines that pair
with immutable private-Service/topology values and validates them before a
later Pika-specific factory may open a socket.

It deliberately does not import Pika, connect to RabbitMQ, create a channel,
receive/acknowledge/reject/retry a delivery, declare topology, access
PostgreSQL/MinIO, invoke ADTOF, build an image, or use Kubernetes. The local
k3d profile has private plain AMQP on port 5672 only; a TLS/AMQPS profile needs
its own broker listener, certificate Secret, and explicit client-validation
task rather than an unreviewed environment toggle here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


# These values identify the one ClusterIP Service and one queue created by the
# v002 downstream topology. They are source-level contracts: the worker must
# not accept a Mac host port, Traefik route, management listener, another vhost,
# or a queue belonging to a different processing stage.
DEFAULT_ADTOF_AMQP_HOST = "clouddsp-rabbitmq.clouddsp-data.svc"
DEFAULT_ADTOF_AMQP_PORT = 5672
ADTOF_AMQP_VHOST = "/clouddsp"
ADTOF_REQUEST_QUEUE = "clouddsp.adtof.requests"
ADTOF_PREFETCH_COUNT = 1
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5
DEFAULT_HEARTBEAT_SECONDS = 30
LOCAL_ADTOF_RABBITMQ_USERNAME = "clouddsp-adtof"


class ADTOFAMQPConfigurationError(RuntimeError):
    """A Pod setting or direct configuration falls outside the local contract."""


class ADTOFAMQPConnectionUnavailable(RuntimeError):
    """A retryable AMQP connection fault that hides private broker diagnostics."""


def _required_environment_text(name: str, *, default: str | None = None) -> str:
    """Read non-empty control-free text without printing mounted Secret values."""

    value = os.environ.get(name, default)
    if (
        value is None
        or not isinstance(value, str)
        or not value
        or any(ord(character) < 0x20 or 0xD800 <= ord(character) <= 0xDFFF for character in value)
    ):
        raise ADTOFAMQPConfigurationError(f"Required environment variable {name} is absent.")
    return value


def _bounded_positive_integer(*, name: str, value: object, maximum: int) -> int:
    """Parse a finite positive integer before it controls a future Pika client."""

    if not isinstance(value, str):
        raise ADTOFAMQPConfigurationError(f"{name} must be an integer.")
    try:
        parsed = int(value)
    except ValueError as error:
        raise ADTOFAMQPConfigurationError(f"{name} must be an integer.") from error
    if not 1 <= parsed <= maximum:
        raise ADTOFAMQPConfigurationError(f"{name} must be between 1 and {maximum}.")
    return parsed


@dataclass(frozen=True)
class ADTOFAMQPSettings:
    """The only AMQP connection/flow-control settings available to ADTOF.

    `RABBITMQ_ADTOF_*` values come from the already-applied restricted runtime
    Secret, never RabbitMQ's administrator Secret. The password is excluded
    from ordinary ``repr`` output. Queue and prefetch are validated alongside
    endpoint settings so later connection/channel helpers share one exact
    contract rather than accepting arbitrary worker-owned broker resources.
    """

    host: str
    port: int
    username: str
    password: str = field(repr=False)
    virtual_host: str = ADTOF_AMQP_VHOST
    queue_name: str = ADTOF_REQUEST_QUEUE
    prefetch_count: int = ADTOF_PREFETCH_COUNT
    connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS
    heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS

    @classmethod
    def from_environment(cls) -> "ADTOFAMQPSettings":
        """Load fixed private endpoint/topology settings and one runtime Secret.

        This validates before importing an AMQP client or opening a socket. No
        Secret is included in public errors or logs; it remains only in the
        private dataclass field that a later Pika factory will need.
        """

        host = _required_environment_text("ADTOF_AMQP_HOST", default=DEFAULT_ADTOF_AMQP_HOST)
        if host != DEFAULT_ADTOF_AMQP_HOST:
            raise ADTOFAMQPConfigurationError(
                "ADTOF_AMQP_HOST must match the private RabbitMQ Service DNS."
            )
        port = _bounded_positive_integer(
            name="ADTOF_AMQP_PORT",
            value=os.environ.get("ADTOF_AMQP_PORT", str(DEFAULT_ADTOF_AMQP_PORT)),
            maximum=65_535,
        )
        if port != DEFAULT_ADTOF_AMQP_PORT:
            raise ADTOFAMQPConfigurationError(
                "ADTOF_AMQP_PORT must match private RabbitMQ AMQP."
            )
        virtual_host = _required_environment_text("ADTOF_AMQP_VHOST", default=ADTOF_AMQP_VHOST)
        queue_name = _required_environment_text("ADTOF_AMQP_QUEUE", default=ADTOF_REQUEST_QUEUE)
        if virtual_host != ADTOF_AMQP_VHOST or queue_name != ADTOF_REQUEST_QUEUE:
            raise ADTOFAMQPConfigurationError(
                "ADTOF AMQP topology does not match the reviewed request queue."
            )
        prefetch_count = _bounded_positive_integer(
            name="ADTOF_AMQP_PREFETCH_COUNT",
            value=os.environ.get("ADTOF_AMQP_PREFETCH_COUNT", str(ADTOF_PREFETCH_COUNT)),
            maximum=16,
        )
        if prefetch_count != ADTOF_PREFETCH_COUNT:
            raise ADTOFAMQPConfigurationError("ADTOF AMQP prefetch must remain one.")
        username = _required_environment_text("RABBITMQ_ADTOF_USERNAME")
        if username != LOCAL_ADTOF_RABBITMQ_USERNAME:
            raise ADTOFAMQPConfigurationError(
                "RABBITMQ_ADTOF_USERNAME must match the restricted ADTOF consumer."
            )
        settings = cls(
            host=host,
            port=port,
            username=username,
            password=_required_environment_text("RABBITMQ_ADTOF_PASSWORD"),
            virtual_host=virtual_host,
            queue_name=queue_name,
            prefetch_count=prefetch_count,
            connect_timeout_seconds=_bounded_positive_integer(
                name="ADTOF_AMQP_CONNECT_TIMEOUT_SECONDS",
                value=os.environ.get(
                    "ADTOF_AMQP_CONNECT_TIMEOUT_SECONDS",
                    str(DEFAULT_CONNECT_TIMEOUT_SECONDS),
                ),
                # A missing Service endpoint must release a future worker slot
                # promptly; supervisor retry policy is a later separate layer.
                maximum=30,
            ),
            heartbeat_seconds=_bounded_positive_integer(
                name="ADTOF_AMQP_HEARTBEAT_SECONDS",
                value=os.environ.get(
                    "ADTOF_AMQP_HEARTBEAT_SECONDS",
                    str(DEFAULT_HEARTBEAT_SECONDS),
                ),
                maximum=300,
            ),
        )
        return validate_adtof_amqp_settings(settings)


def validate_adtof_amqp_settings(value: object) -> ADTOFAMQPSettings:
    """Revalidate direct dataclass construction before later broker I/O.

    Frozen settings can intentionally be built in tests and future entry points.
    Repeating every fixed endpoint/topology/identity rule prevents a caller from
    bypassing ``from_environment()`` and redirecting the restricted credential
    toward a management port, foreign queue, or multiple in-flight deliveries.
    """

    if not isinstance(value, ADTOFAMQPSettings):
        raise TypeError("settings must be ADTOFAMQPSettings.")
    if (
        value.host != DEFAULT_ADTOF_AMQP_HOST
        or value.port != DEFAULT_ADTOF_AMQP_PORT
        or value.virtual_host != ADTOF_AMQP_VHOST
        or value.queue_name != ADTOF_REQUEST_QUEUE
        or value.prefetch_count != ADTOF_PREFETCH_COUNT
        or value.username != LOCAL_ADTOF_RABBITMQ_USERNAME
        or not isinstance(value.password, str)
        or not value.password
        or any(ord(character) < 0x20 or 0xD800 <= ord(character) <= 0xDFFF for character in value.password)
    ):
        raise ADTOFAMQPConfigurationError("ADTOF AMQP settings are outside the local contract.")

    # Direct integer fields share the environment-variable bounds. Exact type
    # checks reject Python booleans, which otherwise behave like 0/1 integers.
    if (
        type(value.connect_timeout_seconds) is not int
        or type(value.heartbeat_seconds) is not int
    ):
        raise ADTOFAMQPConfigurationError("ADTOF AMQP timing is invalid.")
    connect_timeout_seconds = _bounded_positive_integer(
        name="ADTOF_AMQP_CONNECT_TIMEOUT_SECONDS",
        value=str(value.connect_timeout_seconds),
        maximum=30,
    )
    heartbeat_seconds = _bounded_positive_integer(
        name="ADTOF_AMQP_HEARTBEAT_SECONDS",
        value=str(value.heartbeat_seconds),
        maximum=300,
    )
    return ADTOFAMQPSettings(
        host=value.host,
        port=value.port,
        username=value.username,
        password=value.password,
        virtual_host=value.virtual_host,
        queue_name=value.queue_name,
        prefetch_count=ADTOF_PREFETCH_COUNT,
        connect_timeout_seconds=connect_timeout_seconds,
        heartbeat_seconds=heartbeat_seconds,
    )


def _load_pika() -> Any:
    """Import the hash-pinned AMQP client only when a socket is requested."""

    try:
        import pika
    except ImportError as error:
        raise ADTOFAMQPConfigurationError(
            "Pinned ADTOF AMQP client dependency is unavailable."
        ) from error
    return pika


def open_adtof_rabbitmq_connection(settings: ADTOFAMQPSettings) -> Any:
    """Open one bounded private AMQP connection without channel/message actions.

    The missing ``ssl_options`` is deliberate: the local k3d broker exposes the
    reviewed internal plain-AMQP listener only. A successful socket is not a
    task lease, queue declaration, delivery receipt, acknowledgement, or grant
    to publish. Later channel and manual-ack layers own those separate actions.
    """

    approved = validate_adtof_amqp_settings(settings)
    pika = _load_pika()
    parameters = pika.ConnectionParameters(
        host=approved.host,
        port=approved.port,
        virtual_host=approved.virtual_host,
        credentials=pika.PlainCredentials(approved.username, approved.password),
        # A few short attempts tolerate Service endpoint propagation without
        # converting a bad broker/Secret into an unbounded worker-side loop.
        connection_attempts=3,
        retry_delay=1,
        socket_timeout=approved.connect_timeout_seconds,
        blocked_connection_timeout=approved.connect_timeout_seconds * 2,
        heartbeat=approved.heartbeat_seconds,
    )
    try:
        return pika.BlockingConnection(parameters)
    except Exception as error:
        raise ADTOFAMQPConnectionUnavailable(
            "RabbitMQ ADTOF connection is unavailable."
        ) from error
