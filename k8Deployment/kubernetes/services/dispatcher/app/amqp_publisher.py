"""RabbitMQ publisher boundary for one reviewed durable outbox request.

This module owns only the AMQP side of dispatch. It opens a bounded connection,
enables publisher confirmations, accepts a prevalidated immutable version-1
request, and publishes it with the required persistent AMQP properties. It
deliberately does not open PostgreSQL, lease an outbox row, update publication
state, retry in a loop, create RabbitMQ topology, build a container, or call
the Kubernetes API.

That separation matters at the database/broker boundary: a later runtime must
decide whether a known broker failure can be scheduled for retry, or whether an
uncertain confirmation result must instead leave the PostgreSQL lease to
expire. This adapter makes that distinction explicit rather than pretending a
network failure proves that RabbitMQ did not receive a message.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping
from uuid import UUID

from app.downstream_outbox_request import DownstreamAMQPRequest
from app.outbox_lease import DispatcherPublishFailureCode


# These fixed values match the imported, versioned RabbitMQ processing
# topology. They are source-level contract constants rather than configurable
# arbitrary exchange/route names: changing either requires a reviewed v2
# topology and message-compatibility decision.
DEFAULT_AMQP_HOST = "clouddsp-rabbitmq.clouddsp-data.svc"
DEFAULT_AMQP_PORT = 5672
PROCESSING_VHOST = "/clouddsp"
PROCESSING_EXCHANGE = "clouddsp.processing-events"
DEMUCS_REQUESTED_ROUTING_KEY = "demucs.requested"
DEMUCS_REQUESTED_EVENT_TYPE = "demucs.requested"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5
DEFAULT_HEARTBEAT_SECONDS = 30

# These values are part of the durable message schema, not deployment tuning.
LOCAL_UPLOADS_BUCKET = "clouddsp-uploads"
SUPPORTED_STEM_MODES = frozenset({"2-stems", "4-stems", "6-stems"})


class DispatcherPublisherConfigurationError(RuntimeError):
    """Raise a safe category for invalid/missing publisher Pod configuration."""


class DispatcherPublisherContractError(RuntimeError):
    """Reject an incompatible durable event before any AMQP publish attempt.

    The exception intentionally carries no payload/object-key detail. A future
    runtime can log the fixed category and leave/reconcile the leased outbox
    row without exposing private storage identifiers in normal Pod logs.
    """


class DispatcherPublisherFailure(RuntimeError):
    """A broker outcome known to have occurred before a confirmed publish.

    ``failure_code`` is deliberately the same bounded enum accepted by the
    outbox retry helper. The later composition root may schedule only these
    known failures; it must not convert an uncertain outcome into a retry.
    """

    failure_code: DispatcherPublishFailureCode

    def __init__(self, message: str, *, failure_code: DispatcherPublishFailureCode) -> None:
        super().__init__(message)
        self.failure_code = failure_code


class DispatcherBrokerUnavailable(DispatcherPublisherFailure):
    """The adapter could not open/configure a publisher before sending work."""

    def __init__(self) -> None:
        super().__init__(
            "RabbitMQ dispatcher publisher is unavailable.",
            failure_code=DispatcherPublishFailureCode.BROKER_UNAVAILABLE,
        )


class DispatcherPublisherNack(DispatcherPublisherFailure):
    """RabbitMQ negatively confirmed a publish that did reach the broker."""

    def __init__(self) -> None:
        super().__init__(
            "RabbitMQ negatively confirmed the dispatcher publish.",
            failure_code=DispatcherPublishFailureCode.PUBLISHER_NACK,
        )


class DispatcherQueueRejected(DispatcherPublisherFailure):
    """RabbitMQ definitively rejected the route, for example as unroutable."""

    def __init__(self) -> None:
        super().__init__(
            "RabbitMQ rejected the dispatcher publish route.",
            failure_code=DispatcherPublishFailureCode.QUEUE_REJECTED,
        )


class DispatcherPublisherConfirmationUnknown(RuntimeError):
    """The connection failed while confirmation could still be in doubt.

    A later runtime must **not** call the outbox retry helper for this outcome:
    RabbitMQ may have persisted the message just before the client lost its
    confirmation. Leaving the lease to expire permits duplicate-safe recovery
    and avoids a false guarantee that the message was never published.
    """


@dataclass(frozen=True)
class DemucsAMQPRequest:
    """One validated, publisher-ready Demucs request without a Pika client.

    This record deliberately mirrors the ordinary AMQP properties rather than
    exposing Pika's mutable ``BasicProperties`` object.  The generic route
    selector can therefore choose the existing Demucs contract or a downstream
    contract without opening a connection or letting a caller choose an
    arbitrary exchange/routing key.
    """

    exchange: str
    routing_key: str
    body: bytes
    content_type: str
    content_encoding: str
    delivery_mode: int
    message_type: str
    message_id: str
    correlation_id: str


# The generic publisher accepts only the two immutable request data models made
# by the strict contract builders.  Their common fields are checked again at
# the AMQP boundary below, so a future caller cannot use this restricted broker
# identity to choose an arbitrary exchange or routing key.
DispatchableAMQPRequest = DemucsAMQPRequest | DownstreamAMQPRequest


_PERMITTED_AMQP_ROUTES = frozenset(
    {
        (PROCESSING_EXCHANGE, DEMUCS_REQUESTED_ROUTING_KEY, DEMUCS_REQUESTED_EVENT_TYPE),
        (PROCESSING_EXCHANGE, "basic-pitch.requested", "basic-pitch.requested"),
        (PROCESSING_EXCHANGE, "adtof.requested", "adtof.requested"),
    }
)


def _required_environment_text(name: str, *, default: str | None = None) -> str:
    """Read one non-empty setting without exposing its value in an error."""

    value = os.environ.get(name, default)
    if value is None or not isinstance(value, str) or not value or "\x00" in value:
        raise DispatcherPublisherConfigurationError(f"Required environment variable {name} is absent.")
    return value


def _bounded_positive_integer(*, name: str, value: str, maximum: int) -> int:
    """Parse a bounded port/timeout before supplying it to the AMQP client."""

    try:
        parsed = int(value)
    except ValueError as error:
        raise DispatcherPublisherConfigurationError(f"{name} must be an integer.") from error
    if not 1 <= parsed <= maximum:
        raise DispatcherPublisherConfigurationError(f"{name} must be between 1 and {maximum}.")
    return parsed


@dataclass(frozen=True)
class DispatcherPublisherSettings:
    """One restricted, private AMQP publisher connection configuration.

    The username/password originate from the application-namespace
    ``clouddsp-dispatcher-rabbitmq-credentials`` Secret. The role can publish
    only to ``PROCESSING_EXCHANGE`` and cannot configure/consume broker
    resources. The password is excluded from ``repr`` so accidental structured
    logging of this settings object does not disclose it.
    """

    host: str
    port: int
    username: str
    password: str = field(repr=False)
    virtual_host: str = PROCESSING_VHOST
    exchange_name: str = PROCESSING_EXCHANGE
    routing_key: str = DEMUCS_REQUESTED_ROUTING_KEY
    connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS
    heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS

    @classmethod
    def from_environment(cls) -> "DispatcherPublisherSettings":
        """Load fixed private topology plus Secret-backed publisher credentials."""

        host = _required_environment_text("DISPATCHER_AMQP_HOST", default=DEFAULT_AMQP_HOST)
        # `.localhost` resolves on the developer's Mac/browser path. A Pod
        # must use private Service DNS and never the Traefik/browser endpoint.
        if host == "localhost" or host.endswith(".localhost"):
            raise DispatcherPublisherConfigurationError("DISPATCHER_AMQP_HOST must be private Service DNS.")

        virtual_host = _required_environment_text("DISPATCHER_AMQP_VHOST", default=PROCESSING_VHOST)
        exchange_name = _required_environment_text(
            "DISPATCHER_AMQP_EXCHANGE",
            default=PROCESSING_EXCHANGE,
        )
        routing_key = _required_environment_text(
            "DISPATCHER_AMQP_ROUTING_KEY",
            default=DEMUCS_REQUESTED_ROUTING_KEY,
        )
        # Environment variables must not widen a least-privilege identity into
        # an arbitrary event publisher. Topology changes are versioned source
        # changes and must update this module and the broker definition first.
        if (
            virtual_host != PROCESSING_VHOST
            or exchange_name != PROCESSING_EXCHANGE
            or routing_key != DEMUCS_REQUESTED_ROUTING_KEY
        ):
            raise DispatcherPublisherConfigurationError(
                "Dispatcher AMQP topology does not match the reviewed processing route."
            )

        return cls(
            host=host,
            port=_bounded_positive_integer(
                name="DISPATCHER_AMQP_PORT",
                value=os.environ.get("DISPATCHER_AMQP_PORT", str(DEFAULT_AMQP_PORT)),
                maximum=65_535,
            ),
            username=_required_environment_text("RABBITMQ_DISPATCHER_USERNAME"),
            password=_required_environment_text("RABBITMQ_DISPATCHER_PASSWORD"),
            virtual_host=virtual_host,
            exchange_name=exchange_name,
            routing_key=routing_key,
            connect_timeout_seconds=_bounded_positive_integer(
                name="DISPATCHER_AMQP_CONNECT_TIMEOUT_SECONDS",
                value=os.environ.get(
                    "DISPATCHER_AMQP_CONNECT_TIMEOUT_SECONDS",
                    str(DEFAULT_CONNECT_TIMEOUT_SECONDS),
                ),
                maximum=30,
            ),
            heartbeat_seconds=_bounded_positive_integer(
                name="DISPATCHER_AMQP_HEARTBEAT_SECONDS",
                value=os.environ.get(
                    "DISPATCHER_AMQP_HEARTBEAT_SECONDS",
                    str(DEFAULT_HEARTBEAT_SECONDS),
                ),
                maximum=300,
            ),
        )


def _load_pika() -> Any:
    """Import the pinned client only when a real broker operation is requested."""

    try:
        import pika
    except ImportError as error:
        raise DispatcherPublisherConfigurationError("Pinned AMQP client dependency is unavailable.") from error
    return pika


def open_dispatcher_rabbitmq_connection(settings: DispatcherPublisherSettings) -> Any:
    """Open one bounded connection using only the restricted publisher identity.

    This creates no exchange/queue and publishes no work. Failing before a
    publish attempt is known-safe to retry, because no durable event body has
    been handed to RabbitMQ at this point.
    """

    if not isinstance(settings, DispatcherPublisherSettings):
        raise TypeError("settings must be DispatcherPublisherSettings.")
    pika = _load_pika()
    parameters = pika.ConnectionParameters(
        host=settings.host,
        port=settings.port,
        virtual_host=settings.virtual_host,
        credentials=pika.PlainCredentials(settings.username, settings.password),
        # A few short connection attempts tolerate transient Service Endpoint
        # propagation but avoid an unbounded client retry loop inside one call.
        connection_attempts=3,
        retry_delay=1,
        socket_timeout=settings.connect_timeout_seconds,
        blocked_connection_timeout=settings.connect_timeout_seconds * 2,
        heartbeat=settings.heartbeat_seconds,
    )
    try:
        return pika.BlockingConnection(parameters)
    except Exception as error:
        raise DispatcherBrokerUnavailable() from error


def enable_dispatcher_publisher_confirms(channel: Any) -> None:
    """Enable RabbitMQ publisher confirmations before any durable publish.

    Confirmation mode is channel state. It is intentionally enabled once when
    a future runtime opens a channel rather than invoking a topology declaration
    that the restricted account lacks permission to perform.
    """

    try:
        channel.confirm_delivery()
    except Exception as error:
        # No publish was attempted while enabling confirm mode, so this is a
        # known broker-unavailable outcome rather than an uncertain delivery.
        raise DispatcherBrokerUnavailable() from error


def _canonical_lowercase_uuid(*, name: str, value: object) -> str:
    """Require the canonical UUID spelling shared by outbox and AMQP metadata."""

    if not isinstance(value, str):
        raise DispatcherPublisherContractError("Dispatcher event identity is invalid.")
    try:
        normalized = str(UUID(value))
    except (ValueError, AttributeError) as error:
        raise DispatcherPublisherContractError("Dispatcher event identity is invalid.") from error
    if normalized != value:
        raise DispatcherPublisherContractError("Dispatcher event identity is invalid.")
    return normalized


def _required_nonempty_text(*, value: object) -> str:
    """Accept one safe text field without leaking which private value failed."""

    if not isinstance(value, str) or not value or "\x00" in value:
        raise DispatcherPublisherContractError("Dispatcher event payload is invalid.")
    return value


def _encode_demucs_requested_body(*, job_id: str, payload: Mapping[str, object]) -> bytes:
    """Validate and deterministically encode the immutable version-1 body.

    PostgreSQL ``jsonb`` has already normalized object order, so byte-for-byte
    original upload text is not meaningful. The important invariant is that the
    dispatcher neither adds fields nor changes field values: unknown fields are
    rejected and the validated durable values are encoded as compact UTF-8 JSON.
    """

    if not isinstance(payload, Mapping) or set(payload) != {
        "schema_version",
        "job_id",
        "source",
        "stem_mode",
    }:
        raise DispatcherPublisherContractError("Dispatcher event payload is invalid.")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise DispatcherPublisherContractError("Dispatcher event payload is invalid.")
    if _canonical_lowercase_uuid(name="payload job_id", value=payload["job_id"]) != job_id:
        raise DispatcherPublisherContractError("Dispatcher event payload is invalid.")

    source = payload["source"]
    if not isinstance(source, Mapping) or set(source) != {"bucket", "object_key"}:
        raise DispatcherPublisherContractError("Dispatcher event payload is invalid.")
    bucket = _required_nonempty_text(value=source["bucket"])
    object_key = _required_nonempty_text(value=source["object_key"])
    if bucket != LOCAL_UPLOADS_BUCKET:
        raise DispatcherPublisherContractError("Dispatcher event payload is invalid.")
    expected_prefix = f"uploads/{job_id}/"
    filename = object_key.removeprefix(expected_prefix)
    # Version 1 direct uploads use one filename below the job-specific prefix.
    # This stops a malformed durable row from publishing a path traversal-like
    # nested key or a worker-output key as a new source request.
    if not filename or "/" in filename or not object_key.startswith(expected_prefix):
        raise DispatcherPublisherContractError("Dispatcher event payload is invalid.")

    stem_mode = payload["stem_mode"]
    if not isinstance(stem_mode, str) or stem_mode not in SUPPORTED_STEM_MODES:
        raise DispatcherPublisherContractError("Dispatcher event payload is invalid.")

    # Build a plain dictionary so any exotic Mapping implementation cannot
    # change JSON serialization. These are exactly the accepted input values;
    # no access token, URL, credential, filename copy, or error field is added.
    normalized_body = {
        "schema_version": 1,
        "job_id": job_id,
        "source": {"bucket": bucket, "object_key": object_key},
        "stem_mode": stem_mode,
    }
    return json.dumps(
        normalized_body,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def build_demucs_amqp_request(
    *,
    event_id: object,
    job_id: object,
    payload: Mapping[str, object],
) -> DemucsAMQPRequest:
    """Validate one durable Demucs row and return its fixed AMQP request.

    This is the pure-contract portion of the original Demucs publisher.  It
    does not load Pika, read an environment variable, open a broker connection,
    or publish anything.  Keeping it separate lets the later generic selector
    share the exact existing validation rules rather than reimplementing the
    private source-object contract in a second module.
    """

    canonical_event_id = _canonical_lowercase_uuid(name="event_id", value=event_id)
    canonical_job_id = _canonical_lowercase_uuid(name="job_id", value=job_id)
    body = _encode_demucs_requested_body(job_id=canonical_job_id, payload=payload)
    return DemucsAMQPRequest(
        exchange=PROCESSING_EXCHANGE,
        routing_key=DEMUCS_REQUESTED_ROUTING_KEY,
        body=body,
        content_type="application/json",
        content_encoding="utf-8",
        delivery_mode=2,
        message_type=DEMUCS_REQUESTED_EVENT_TYPE,
        message_id=canonical_event_id,
        correlation_id=canonical_job_id,
    )


def _is_pika_exception(error: Exception, *, pika: Any, name: str) -> bool:
    """Check an optional Pika exception class without importing Pika eagerly."""

    candidate = getattr(getattr(pika, "exceptions", None), name, None)
    return isinstance(candidate, type) and isinstance(error, candidate)


def _validated_dispatchable_request(request: object) -> DispatchableAMQPRequest:
    """Defend the restricted publisher against a forged/widened request record.

    The route selector already supplies a contract-validated immutable request,
    but this boundary is deliberately defensive because it is the last point
    before a body reaches RabbitMQ.  It verifies the finite exchange/route/type
    vocabulary and the standard persistent metadata; individual payload schema
    validation remains owned by the strict Demucs or downstream contract
    builder that created the record.
    """

    if not isinstance(request, (DemucsAMQPRequest, DownstreamAMQPRequest)):
        raise DispatcherPublisherContractError("Dispatcher AMQP request is invalid.")
    if (
        (request.exchange, request.routing_key, request.message_type) not in _PERMITTED_AMQP_ROUTES
        or request.content_type != "application/json"
        or request.content_encoding != "utf-8"
        or request.delivery_mode != 2
        or not isinstance(request.body, bytes)
        or not request.body
    ):
        raise DispatcherPublisherContractError("Dispatcher AMQP request is invalid.")
    _canonical_lowercase_uuid(name="message_id", value=request.message_id)
    _canonical_lowercase_uuid(name="correlation_id", value=request.correlation_id)
    return request


def publish_dispatchable_amqp_request(
    channel: Any,
    *,
    request: DispatchableAMQPRequest,
) -> None:
    """Publish one selector-built request and wait for broker confirmation.

    The caller must enable publisher-confirmation mode on this same channel
    first.  ``mandatory=True`` turns a missing binding into a definitive
    `DispatcherQueueRejected` result.  A timeout or connection loss during the
    call stays deliberately uncertain: PostgreSQL must retain the lease until
    expiry rather than eagerly schedule a second possibly-duplicate publish.
    """

    validated_request = _validated_dispatchable_request(request)
    pika = _load_pika()
    properties = pika.BasicProperties(
        content_type=validated_request.content_type,
        content_encoding=validated_request.content_encoding,
        delivery_mode=validated_request.delivery_mode,
        type=validated_request.message_type,
        message_id=validated_request.message_id,
        correlation_id=validated_request.correlation_id,
    )
    try:
        confirmation = channel.basic_publish(
            exchange=validated_request.exchange,
            routing_key=validated_request.routing_key,
            body=validated_request.body,
            properties=properties,
            # A missing binding must never look like a successful publish. The
            # broker returns it to Pika as a definitive unroutable outcome.
            mandatory=True,
        )
    except Exception as error:
        if _is_pika_exception(error, pika=pika, name="NackError"):
            raise DispatcherPublisherNack() from error
        if _is_pika_exception(error, pika=pika, name="UnroutableError"):
            raise DispatcherQueueRejected() from error
        raise DispatcherPublisherConfirmationUnknown(
            "RabbitMQ dispatcher publish confirmation is uncertain."
        ) from error
    if confirmation is False:
        raise DispatcherPublisherNack()


def publish_demucs_requested(
    channel: Any,
    *,
    event_id: str,
    job_id: str,
    payload: Mapping[str, object],
) -> None:
    """Publish one validated event and wait for the channel's broker confirm.

    The caller must first invoke :func:`enable_dispatcher_publisher_confirms`
    on this same channel. A ``False`` return or Pika ``NackError`` proves a
    negative broker confirmation; an ``UnroutableError`` proves mandatory
    routing failed. Other publish-time exceptions are deliberately uncertain:
    RabbitMQ may have accepted the body immediately before the client lost its
    confirmation, so the caller must let its PostgreSQL lease expire.
    """

    request = build_demucs_amqp_request(
        event_id=event_id,
        job_id=job_id,
        payload=payload,
    )
    publish_dispatchable_amqp_request(channel, request=request)
