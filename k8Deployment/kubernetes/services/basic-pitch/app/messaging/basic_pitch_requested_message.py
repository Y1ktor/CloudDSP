"""Strict contract for one private ``basic-pitch.requested`` RabbitMQ delivery.

This pure module defines the boundary that a future Basic Pitch worker must
cross before it may claim durable work or read a private Demucs stem.  It
validates the exact v004 outbox/dispatcher message, then maps that approved
input to one deterministic MIDI object coordinate:

    stems/{job_id}/{stem_name}.wav -> midi/{job_id}/{stem_name}.mid

It imports neither Pika, Boto3, Psycopg, Basic Pitch, nor a Kubernetes client.
It does not acknowledge a RabbitMQ delivery, access MinIO, run ML, mutate a
Job/task, build an image, or change a Kubernetes resource.  Those are separate
layers because RabbitMQ at-least-once delivery is not authorization: a later
runtime must parse this contract, acquire its PostgreSQL task lease, verify the
private MinIO object, and only then decide whether to acknowledge/retry/DLQ.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID


# These fixed source-level values mirror the versioned RabbitMQ topology and
# generic dispatcher. They are deliberately not Deployment environment
# variables: a Secret/config change must not turn this worker into a consumer
# for another stage or allow it to read arbitrary private object keys.
PROCESSING_EXCHANGE = "clouddsp.processing-events"
BASIC_PITCH_REQUESTED_ROUTING_KEY = "basic-pitch.requested"
BASIC_PITCH_REQUESTED_EVENT_TYPE = "basic-pitch.requested"
BASIC_PITCH_STAGE = "basic-pitch"
LOCAL_UPLOADS_BUCKET = "clouddsp-uploads"
STEM_CONTENT_TYPE = "audio/wav"
MIDI_CONTENT_TYPE = "audio/midi"
MIDI_OBJECT_KEY_PREFIX = "midi"

# Demucs emits a downstream Basic Pitch request for every approved non-drum
# stem. `drums` belongs solely to ADTOF, so accepting it here would create an
# invalid MIDI extractor choice even when the AMQP envelope looks well-formed.
BASIC_PITCH_STEM_NAMES = frozenset(
    {"vocals", "no_vocals", "bass", "other", "guitar", "piano"}
)

# Message bodies contain only JSON metadata/evidence, never audio. This bound
# rejects a hostile or accidentally binary delivery before JSON parsing can
# allocate work in a future long-running consumer.
MAX_BASIC_PITCH_REQUEST_BODY_BYTES = 4 * 1024


class BasicPitchRequestContractError(RuntimeError):
    """Reject an untrusted delivery without exposing private content in logs."""


@dataclass(frozen=True)
class BasicPitchRequestedMessage:
    """The validated durable input a later task-claim layer may correlate.

    ``event_id`` is the AMQP `message_id` and names the immutable outbox event.
    ``job_id`` occurs independently in the body and AMQP `correlation_id`.
    The stem object is a stable private key plus size/checksum evidence—not a
    presigned URL. Parsing verifies syntax and scope only; PostgreSQL remains
    authoritative when a later worker claims the task.
    """

    event_id: str
    job_id: str
    stem_name: str
    stem_bucket: str
    stem_object_key: str
    stem_content_length: int
    stem_sha256: str


@dataclass(frozen=True)
class BasicPitchMidiOutput:
    """The deterministic private MIDI coordinate for one validated input stem.

    The future Basic Pitch process will create bytes locally, validate/hash
    them, and add task-specific metadata before MinIO upload. This early plan
    deliberately does *not* claim a byte length/checksum for MIDI that has not
    yet been generated. The input event ID/checksum preserve its provenance for
    those later boundaries without granting arbitrary output-key selection.
    """

    request_event_id: str
    job_id: str
    stem_name: str
    bucket: str
    object_key: str
    content_type: str
    input_stem_sha256: str


def _fail() -> BasicPitchRequestContractError:
    """Return the one safe public category for every malformed request shape."""

    return BasicPitchRequestContractError("Basic Pitch request delivery is invalid.")


def _canonical_lowercase_uuid(value: object) -> str:
    """Require the canonical UUID spelling used by durable/outbox identity."""

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
    """Reject control text/unpaired surrogates before a value enters an object key."""

    if not isinstance(value, str) or not value:
        raise _fail()
    if any(ord(character) < 0x20 or 0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise _fail()
    return value


def _sha256(value: object) -> str:
    """Accept only lower-case 64-hex evidence supplied by Demucs completion."""

    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise _fail()
    return value


def _positive_byte_count(value: object) -> int:
    """Reject booleans, zero, and negative values before a MinIO comparison."""

    if type(value) is not int or value < 1:
        raise _fail()
    return value


def _parse_unique_json_object_pairs(pairs: list[tuple[object, object]]) -> dict[str, object]:
    """Reject duplicate JSON member names instead of silently keeping the last."""

    parsed: dict[str, object] = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in parsed:
            raise _fail()
        parsed[key] = value
    return parsed


def _reject_json_constant(_constant: str) -> Any:
    """Reject Python's permissive NaN/Infinity JSON extensions."""

    raise _fail()


def _decode_body(body: object) -> Mapping[str, object]:
    """Decode one bounded UTF-8 object with strict JSON semantics."""

    if not isinstance(body, bytes) or not body or len(body) > MAX_BASIC_PITCH_REQUEST_BODY_BYTES:
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
    """Read only the Pika-like AMQP property attributes required by the contract."""

    try:
        return getattr(properties, name)
    except (AttributeError, TypeError) as error:
        raise _fail() from error


def _validate_envelope(*, delivery_exchange: object, delivery_routing_key: object) -> None:
    """Require the exact direct-exchange route selected by generic dispatcher."""

    if (
        delivery_exchange != PROCESSING_EXCHANGE
        or delivery_routing_key != BASIC_PITCH_REQUESTED_ROUTING_KEY
    ):
        raise _fail()


def _validate_properties(*, properties: object, expected_job_id: str) -> str:
    """Require persistent AMQP metadata before later durable work can begin."""

    delivery_mode = _property(properties, "delivery_mode")
    if (
        _property(properties, "content_type") != "application/json"
        or _property(properties, "content_encoding") != "utf-8"
        # `bool` subclasses `int`, so use exact type equality here.
        or type(delivery_mode) is not int
        or delivery_mode != 2
        or _property(properties, "type") != BASIC_PITCH_REQUESTED_EVENT_TYPE
    ):
        raise _fail()
    event_id = _canonical_lowercase_uuid(_property(properties, "message_id"))
    if _canonical_lowercase_uuid(_property(properties, "correlation_id")) != expected_job_id:
        raise _fail()
    return event_id


def _validate_message_fields(message: object) -> BasicPitchRequestedMessage:
    """Re-check a frozen message before it can name a later MIDI output key.

    Frozen dataclasses can still be instantiated directly by Python callers.
    Revalidating here prevents a hand-built object from bypassing the strict
    broker parser and turning the output-plan helper into arbitrary key choice.
    """

    if not isinstance(message, BasicPitchRequestedMessage):
        raise _fail()
    event_id = _canonical_lowercase_uuid(message.event_id)
    job_id = _canonical_lowercase_uuid(message.job_id)
    stem_name = _safe_nonempty_text(message.stem_name)
    stem_bucket = _safe_nonempty_text(message.stem_bucket)
    stem_object_key = _safe_nonempty_text(message.stem_object_key)
    if (
        stem_name not in BASIC_PITCH_STEM_NAMES
        or stem_bucket != LOCAL_UPLOADS_BUCKET
        or stem_object_key != f"stems/{job_id}/{stem_name}.wav"
    ):
        raise _fail()
    return BasicPitchRequestedMessage(
        event_id=event_id,
        job_id=job_id,
        stem_name=stem_name,
        stem_bucket=stem_bucket,
        stem_object_key=stem_object_key,
        stem_content_length=_positive_byte_count(message.stem_content_length),
        stem_sha256=_sha256(message.stem_sha256),
    )


def parse_basic_pitch_requested_delivery(
    *,
    delivery_exchange: object,
    delivery_routing_key: object,
    properties: object,
    body: object,
) -> BasicPitchRequestedMessage:
    """Parse one strict v004 delivery before task/MinIO/ML work exists.

    The future AMQP runtime passes the method-frame exchange/routing key,
    BasicProperties object, and raw body to this function. A contract failure
    is suitable for a later `basic_nack(requeue=False)`/DLQ policy. A storage,
    database-claim, or model fault occurs after this boundary and must remain
    unacknowledged until that later idempotent retry design is implemented.
    """

    _validate_envelope(
        delivery_exchange=delivery_exchange,
        delivery_routing_key=delivery_routing_key,
    )
    payload = _decode_body(body)

    # Exact top-level/nested shapes block surprise URLs, owner fields, task
    # parameters, alternate output prefixes, and implicit schema evolution.
    if set(payload) != {"schema_version", "job_id", "stem_name", "stem"}:
        raise _fail()
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise _fail()
    job_id = _canonical_lowercase_uuid(payload["job_id"])
    stem_name = _safe_nonempty_text(payload["stem_name"])
    if stem_name not in BASIC_PITCH_STEM_NAMES:
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
        or stem_object_key != f"stems/{job_id}/{stem_name}.wav"
        or stem["content_type"] != STEM_CONTENT_TYPE
    ):
        raise _fail()

    message = BasicPitchRequestedMessage(
        event_id=_validate_properties(properties=properties, expected_job_id=job_id),
        job_id=job_id,
        stem_name=stem_name,
        stem_bucket=stem_bucket,
        stem_object_key=stem_object_key,
        stem_content_length=_positive_byte_count(stem["size_bytes"]),
        stem_sha256=_sha256(stem["sha256"]),
    )
    # Return a revalidated copy, not the initial constructor values, so this
    # function and the output-plan helper share the same narrow constraints.
    return _validate_message_fields(message)


def build_basic_pitch_midi_output(message: object) -> BasicPitchMidiOutput:
    """Map a validated non-drum stem to its one stable private MIDI coordinate.

    Task attempts never appear in the object key: redelivery/recovery of the
    same `(job_id, basic-pitch, stem_name)` task targets the same artifact.
    Later PostgreSQL ownership guards decide whether a successful upload is
    retained; this pure plan never uploads or marks a task complete.
    """

    validated = _validate_message_fields(message)
    return BasicPitchMidiOutput(
        request_event_id=validated.event_id,
        job_id=validated.job_id,
        stem_name=validated.stem_name,
        bucket=LOCAL_UPLOADS_BUCKET,
        object_key=f"{MIDI_OBJECT_KEY_PREFIX}/{validated.job_id}/{validated.stem_name}.mid",
        content_type=MIDI_CONTENT_TYPE,
        input_stem_sha256=validated.stem_sha256,
    )
