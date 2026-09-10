"""Strict parser for one private ``demucs.requested`` RabbitMQ delivery.

This pure parser has no Pika import, acknowledgement, PostgreSQL query, MinIO
request, retry loop, model invocation, or Kubernetes API call. A later AMQP
adapter supplies the broker envelope/properties and uses this boundary before
claiming a durable processing task.

RabbitMQ is at-least-once transport, not authorization. Exact route, persistent
properties, canonical IDs, and a small version-1 JSON shape prevent a malformed
delivery from being mistaken for authority to read private audio. PostgreSQL
will still re-read the Job and outbox row before storage or Demucs work begins.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID


# These values mirror the versioned RabbitMQ topology/dispatcher contract. They
# are source-level constants—not Deployment settings—so an environment change
# cannot widen this restricted worker into an arbitrary queue consumer.
PROCESSING_EXCHANGE = "clouddsp.processing-events"
DEMUCS_REQUESTED_ROUTING_KEY = "demucs.requested"
DEMUCS_REQUESTED_EVENT_TYPE = "demucs.requested"
LOCAL_UPLOADS_BUCKET = "clouddsp-uploads"
SUPPORTED_STEM_MODES = frozenset({"2-stems", "4-stems", "6-stems"})

# Version 1 contains small metadata only. This is not the audio size limit;
# storage/FFprobe enforce that later. The bound rejects binary/hostile JSON
# before the Python decoder allocates additional parsing work.
MAX_DEMUCS_REQUEST_BODY_BYTES = 4 * 1024


class DemucsRequestContractError(RuntimeError):
    """Reject a delivery without exposing its body/object-key in normal logs."""


@dataclass(frozen=True)
class DemucsRequestedMessage:
    """Only the immutable identifiers passed to the later PostgreSQL claim.

    ``event_id`` is AMQP ``message_id`` and names the outbox event. ``job_id``
    occurs in JSON and ``correlation_id``. The source is a private stable key,
    never a browser presigned URL. Parsing does not authorize access: the
    database adapter must compare every field to durable authoritative rows.
    """

    event_id: str
    job_id: str
    source_bucket: str
    source_object_key: str
    stem_mode: str


def _fail() -> DemucsRequestContractError:
    """Return the single safe parser category used by malformed deliveries."""

    return DemucsRequestContractError("Demucs request delivery is invalid.")


def _canonical_lowercase_uuid(value: object) -> str:
    """Require lower-case canonical UUID text, not a UUID-like alternative."""

    if not isinstance(value, str):
        raise _fail()
    try:
        normalized = str(UUID(value))
    except (ValueError, AttributeError) as error:
        raise _fail() from error
    if normalized != value:
        raise _fail()
    return normalized


def _safe_nonempty_text(value: object) -> str:
    """Reject NUL/control text and unpaired Unicode surrogates in S3 fields."""

    if not isinstance(value, str) or not value:
        raise _fail()
    if any(ord(character) < 0x20 or 0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise _fail()
    return value


def _parse_unique_json_object_pairs(pairs: list[tuple[object, object]]) -> dict[str, object]:
    """Build a JSON object while rejecting duplicate member names.

    The default Python decoder preserves the last duplicate. Rejecting instead
    prevents parser-dependent choices of job/source identity across services.
    """

    parsed: dict[str, object] = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in parsed:
            raise _fail()
        parsed[key] = value
    return parsed


def _reject_json_constant(_constant: str) -> Any:
    """Reject Python's non-standard JSON NaN/Infinity extensions."""

    raise _fail()


def _decode_body(body: object) -> Mapping[str, object]:
    """Decode one non-empty bounded UTF-8 JSON object without permissive rules."""

    if not isinstance(body, bytes) or not body or len(body) > MAX_DEMUCS_REQUEST_BODY_BYTES:
        raise _fail()
    try:
        parsed = json.loads(
            body.decode("utf-8", errors="strict"),
            object_pairs_hook=_parse_unique_json_object_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise _fail() from error
    if not isinstance(parsed, Mapping):
        raise _fail()
    return parsed


def _property(properties: object, name: str) -> object:
    """Read one Pika-style property attribute without a Pika runtime import."""

    # Pika BasicProperties is an attribute object. Accepting this narrow surface
    # lets the later transport pass it through rather than build an unreviewed
    # dictionary from arbitrary AMQP fields.
    try:
        return getattr(properties, name)
    except (AttributeError, TypeError) as error:
        raise _fail() from error


def _validate_envelope(*, delivery_exchange: object, delivery_routing_key: object) -> None:
    """Require the ordinary processing route after any broker redelivery path."""

    if (
        delivery_exchange != PROCESSING_EXCHANGE
        or delivery_routing_key != DEMUCS_REQUESTED_ROUTING_KEY
    ):
        raise _fail()


def _validate_properties(*, properties: object, expected_job_id: str) -> str:
    """Require the persistent AMQP metadata frozen by the dispatcher contract."""

    delivery_mode = _property(properties, "delivery_mode")
    if (
        _property(properties, "content_type") != "application/json"
        or _property(properties, "content_encoding") != "utf-8"
        # ``bool`` subclasses ``int``; type equality prevents True satisfying
        # the persistent delivery-mode requirement by accident.
        or type(delivery_mode) is not int
        or delivery_mode != 2
        or _property(properties, "type") != DEMUCS_REQUESTED_EVENT_TYPE
    ):
        raise _fail()

    event_id = _canonical_lowercase_uuid(_property(properties, "message_id"))
    if _canonical_lowercase_uuid(_property(properties, "correlation_id")) != expected_job_id:
        raise _fail()
    return event_id


def parse_demucs_requested_delivery(
    *,
    delivery_exchange: object,
    delivery_routing_key: object,
    properties: object,
    body: object,
) -> DemucsRequestedMessage:
    """Validate one delivery before the future durable PostgreSQL task claim.

    The consumer will pass ``method_frame.exchange``, ``method_frame.routing_key``,
    Pika ``BasicProperties``, and raw body bytes. This parser never acknowledges
    a delivery; its later caller will DLQ this specific contract error with
    ``basic_nack(requeue=False)``. PostgreSQL/MinIO failures before a durable
    claim remain unacknowledged so RabbitMQ can redeliver safely.
    """

    _validate_envelope(
        delivery_exchange=delivery_exchange,
        delivery_routing_key=delivery_routing_key,
    )
    payload = _decode_body(body)

    # Exact fields prohibit a producer from adding a URL/token, owner identity,
    # arbitrary stage parameter, or silently incompatible future schema.
    if set(payload) != {"schema_version", "job_id", "source", "stem_mode"}:
        raise _fail()
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise _fail()
    job_id = _canonical_lowercase_uuid(payload["job_id"])

    source = payload["source"]
    if not isinstance(source, Mapping) or set(source) != {"bucket", "object_key"}:
        raise _fail()
    source_bucket = _safe_nonempty_text(source["bucket"])
    source_object_key = _safe_nonempty_text(source["object_key"])
    if source_bucket != LOCAL_UPLOADS_BUCKET:
        raise _fail()

    # Direct uploads place exactly one filename below their job prefix. This
    # blocks nested, empty, other-job, and artifact paths before MinIO is used.
    expected_prefix = f"uploads/{job_id}/"
    source_filename = source_object_key.removeprefix(expected_prefix)
    if (
        not source_object_key.startswith(expected_prefix)
        or not source_filename
        or "/" in source_filename
    ):
        raise _fail()

    stem_mode = payload["stem_mode"]
    if not isinstance(stem_mode, str) or stem_mode not in SUPPORTED_STEM_MODES:
        raise _fail()

    event_id = _validate_properties(properties=properties, expected_job_id=job_id)
    return DemucsRequestedMessage(
        event_id=event_id,
        job_id=job_id,
        source_bucket=source_bucket,
        source_object_key=source_object_key,
        stem_mode=stem_mode,
    )
