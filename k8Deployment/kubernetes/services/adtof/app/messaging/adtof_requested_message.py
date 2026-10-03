"""Strict contract for one private ``adtof.requested`` RabbitMQ delivery.

This pure module is the first runtime boundary for the future Kubernetes-local
ADTOF worker. It admits only the exact immutable downstream request produced
from a Demucs-completed drums stem:

    stems/{job_id}/drums.wav

It imports neither Pika, Boto3, Psycopg, ADTOF, nor a Kubernetes client. It
does not acknowledge a delivery, claim/update PostgreSQL work, read/write
MinIO, run ML, build an image, or change a Kubernetes resource. Those later
layers must first receive this validated evidence, then use the durable task
lease and private-object integrity checks described in the ADTOF contract.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID


# These names are source-level protocol constants, not Deployment environment
# variables. A Secret or ConfigMap must never redirect a restricted ADTOF
# consumer to another stage, arbitrary queue, or arbitrary private object key.
PROCESSING_EXCHANGE = "clouddsp.processing-events"
ADTOF_REQUESTED_ROUTING_KEY = "adtof.requested"
ADTOF_REQUESTED_EVENT_TYPE = "adtof.requested"
ADTOF_STAGE = "adtof"
ADTOF_STEM_NAME = "drums"
LOCAL_UPLOADS_BUCKET = "clouddsp-uploads"
STEM_CONTENT_TYPE = "audio/wav"

# The RabbitMQ body is metadata only, never audio bytes. The fixed four-KiB
# bound prevents a hostile delivery from creating unbounded JSON-decoding work
# in the later long-running consumer.
MAX_ADTOF_REQUEST_BODY_BYTES = 4 * 1024


class ADTOFRequestContractError(RuntimeError):
    """Reject an untrusted delivery without revealing private contents in logs."""


@dataclass(frozen=True)
class ADTOFRequestedMessage:
    """The validated evidence a later ADTOF task-claim layer may correlate.

    ``event_id`` is RabbitMQ's persistent ``message_id`` and identifies one
    immutable PostgreSQL outbox event. ``job_id`` independently appears in the
    JSON body and AMQP ``correlation_id``. The drums object remains a stable
    private key plus size/checksum evidence, never a presigned URL or path the
    delivery author can choose freely.
    """

    event_id: str
    job_id: str
    stem_name: str
    stem_bucket: str
    stem_object_key: str
    stem_content_length: int
    stem_sha256: str


def _fail() -> ADTOFRequestContractError:
    """Return the one safe public category for every malformed request shape."""

    return ADTOFRequestContractError("ADTOF request delivery is invalid.")


def _canonical_lowercase_uuid(value: object) -> str:
    """Require the canonical lower-case UUID spelling used by durable identity."""

    if not isinstance(value, str):
        raise _fail()
    try:
        normalized = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise _fail() from error
    if value != normalized:
        raise _fail()
    return normalized


def _safe_nonempty_text(value: object) -> str:
    """Reject unsafe text before it participates in a private object key."""

    if not isinstance(value, str) or not value:
        raise _fail()
    if any(ord(character) < 0x20 or 0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise _fail()
    return value


def _sha256(value: object) -> str:
    """Accept only lower-case 64-hex Demucs checksum evidence."""

    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise _fail()
    return value


def _positive_byte_count(value: object) -> int:
    """Reject booleans, zero, and negatives before later MinIO comparison."""

    # ``bool`` is an ``int`` subclass in Python, so exact type equality matters.
    if type(value) is not int or value < 1:
        raise _fail()
    return value


def _parse_unique_json_object_pairs(pairs: list[tuple[object, object]]) -> dict[str, object]:
    """Reject duplicate JSON member names instead of accepting the last one."""

    parsed: dict[str, object] = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in parsed:
            raise _fail()
        parsed[key] = value
    return parsed


def _reject_json_constant(_constant: str) -> Any:
    """Reject Python's non-standard NaN/Infinity JSON extensions."""

    raise _fail()


def _decode_body(body: object) -> Mapping[str, object]:
    """Decode one bounded UTF-8 object with strict JSON semantics."""

    if not isinstance(body, bytes) or not body or len(body) > MAX_ADTOF_REQUEST_BODY_BYTES:
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
    """Read only the Pika-like AMQP properties required by this contract."""

    try:
        return getattr(properties, name)
    except (AttributeError, TypeError) as error:
        raise _fail() from error


def _validate_envelope(*, delivery_exchange: object, delivery_routing_key: object) -> None:
    """Require the exact direct-exchange route selected by the dispatcher."""

    if (
        delivery_exchange != PROCESSING_EXCHANGE
        or delivery_routing_key != ADTOF_REQUESTED_ROUTING_KEY
    ):
        raise _fail()


def _validate_properties(*, properties: object, expected_job_id: str) -> str:
    """Require persistent AMQP identity/properties before durable work begins."""

    delivery_mode = _property(properties, "delivery_mode")
    if (
        _property(properties, "content_type") != "application/json"
        or _property(properties, "content_encoding") != "utf-8"
        or type(delivery_mode) is not int
        or delivery_mode != 2
        or _property(properties, "type") != ADTOF_REQUESTED_EVENT_TYPE
    ):
        raise _fail()
    event_id = _canonical_lowercase_uuid(_property(properties, "message_id"))
    if _canonical_lowercase_uuid(_property(properties, "correlation_id")) != expected_job_id:
        raise _fail()
    return event_id


def parse_adtof_requested_delivery(
    *,
    delivery_exchange: object,
    delivery_routing_key: object,
    properties: object,
    body: object,
) -> ADTOFRequestedMessage:
    """Parse one strict v004 ADTOF delivery before task, storage, or ML work.

    A future AMQP adapter supplies method-frame exchange/routing evidence,
    Pika's ``BasicProperties``, and raw bytes. A contract failure can later
    become a non-requeued ``basic_nack``/DLQ action. Storage, task-claim, and
    model failures happen after this boundary and must remain governed by the
    PostgreSQL idempotency and retry design rather than this parser.
    """

    _validate_envelope(
        delivery_exchange=delivery_exchange,
        delivery_routing_key=delivery_routing_key,
    )
    payload = _decode_body(body)

    # Exact top-level/nested shapes block surprise model arguments, owner
    # fields, URLs, output prefixes, and unreviewed schema evolution.
    if set(payload) != {"schema_version", "job_id", "stem_name", "stem"}:
        raise _fail()
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise _fail()
    job_id = _canonical_lowercase_uuid(payload["job_id"])
    stem_name = _safe_nonempty_text(payload["stem_name"])
    if stem_name != ADTOF_STEM_NAME:
        raise _fail()

    stem = payload["stem"]
    if not isinstance(stem, Mapping) or set(stem) != {
        "bucket",
        "object_key",
        "content_type",
        "size_bytes",
        "sha256",
    }:
        raise _fail()
    stem_bucket = _safe_nonempty_text(stem["bucket"])
    stem_object_key = _safe_nonempty_text(stem["object_key"])
    if (
        stem_bucket != LOCAL_UPLOADS_BUCKET
        or stem_object_key != f"stems/{job_id}/{ADTOF_STEM_NAME}.wav"
        or stem["content_type"] != STEM_CONTENT_TYPE
    ):
        raise _fail()

    return ADTOFRequestedMessage(
        event_id=_validate_properties(properties=properties, expected_job_id=job_id),
        job_id=job_id,
        stem_name=stem_name,
        stem_bucket=stem_bucket,
        stem_object_key=stem_object_key,
        stem_content_length=_positive_byte_count(stem["size_bytes"]),
        stem_sha256=_sha256(stem["sha256"]),
    )
