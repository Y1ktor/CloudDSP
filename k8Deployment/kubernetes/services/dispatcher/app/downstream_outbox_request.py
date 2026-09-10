"""Validate post-Demucs outbox rows and render their fixed AMQP requests.

Demucs records one durable outbox event per verified private WAV stem.  This
module is the dispatcher-side counterpart of that contract: it accepts only
the v004 Basic Pitch/ADTOF stage/type/stem combinations and turns them into a
fully specified AMQP publication record.  It is deliberately dependency-free
and does not import Pika, open PostgreSQL, claim/update an outbox row, publish
to RabbitMQ, create queue topology, start a loop, or touch Kubernetes.

Keeping request validation separate from the existing Demucs-only dispatcher
lets the next small task add RabbitMQ v002 topology and generic lease selection
without a broker-facing implementation guessing what a downstream payload
means.  The future publisher will pass this record to Pika only after it has
committed its PostgreSQL lease.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID


# These topology names are source-level version-2 contract constants, not Pod
# environment variables.  A Deployment setting must never turn the restricted
# dispatcher into a general RabbitMQ publisher. A later RabbitMQ topology task
# will create matching queues/bindings before a runtime is taught to use them.
PROCESSING_EXCHANGE = "clouddsp.processing-events"
BASIC_PITCH_REQUESTED_ROUTING_KEY = "basic-pitch.requested"
ADTOF_REQUESTED_ROUTING_KEY = "adtof.requested"
LOCAL_UPLOADS_BUCKET = "clouddsp-uploads"
STEM_CONTENT_TYPE = "audio/wav"


class DownstreamOutboxRequestContractError(RuntimeError):
    """A durable downstream record must not be published to RabbitMQ.

    The exception intentionally exposes no event payload, object key, identity,
    or broker detail.  The later dispatcher can use its existing bounded
    ``invalid_event_contract`` terminal state without turning normal Pod logs
    into a private-object inventory.
    """


@dataclass(frozen=True)
class DownstreamRoute:
    """The one permitted stage/type/stem vocabulary entry for an AMQP route."""

    stage: str
    event_type: str
    routing_key: str


@dataclass(frozen=True)
class DownstreamAMQPRequest:
    """A publisher-ready immutable request without a broker client dependency.

    ``body`` is compact UTF-8 JSON generated from the validated durable
    payload.  The remaining fields mirror RabbitMQ's BasicProperties values;
    keeping them plain data makes the versioned message contract unit-testable
    before a future Pika integration and prevents the body from duplicating the
    outbox event ID or AMQP correlation identifiers.
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


# The mapping intentionally lists every approved non-drum output individually.
# A database CHECK constraint in v004 independently enforces the same finite
# vocabulary, while the future Basic Pitch worker will repeat it before acting
# on a delivery. The dispatcher does not infer a route from a free-text value.
_ROUTE_BY_STAGE_AND_STEM = {
    ("basic-pitch", "vocals"): DownstreamRoute(
        stage="basic-pitch",
        event_type="basic-pitch.requested",
        routing_key=BASIC_PITCH_REQUESTED_ROUTING_KEY,
    ),
    ("basic-pitch", "no_vocals"): DownstreamRoute(
        stage="basic-pitch",
        event_type="basic-pitch.requested",
        routing_key=BASIC_PITCH_REQUESTED_ROUTING_KEY,
    ),
    ("basic-pitch", "bass"): DownstreamRoute(
        stage="basic-pitch",
        event_type="basic-pitch.requested",
        routing_key=BASIC_PITCH_REQUESTED_ROUTING_KEY,
    ),
    ("basic-pitch", "other"): DownstreamRoute(
        stage="basic-pitch",
        event_type="basic-pitch.requested",
        routing_key=BASIC_PITCH_REQUESTED_ROUTING_KEY,
    ),
    ("basic-pitch", "guitar"): DownstreamRoute(
        stage="basic-pitch",
        event_type="basic-pitch.requested",
        routing_key=BASIC_PITCH_REQUESTED_ROUTING_KEY,
    ),
    ("basic-pitch", "piano"): DownstreamRoute(
        stage="basic-pitch",
        event_type="basic-pitch.requested",
        routing_key=BASIC_PITCH_REQUESTED_ROUTING_KEY,
    ),
    ("adtof", "drums"): DownstreamRoute(
        stage="adtof",
        event_type="adtof.requested",
        routing_key=ADTOF_REQUESTED_ROUTING_KEY,
    ),
}


def _contract_error() -> DownstreamOutboxRequestContractError:
    """Return one safe category for every malformed durable event shape."""

    return DownstreamOutboxRequestContractError("Downstream outbox event contract is invalid.")


def _canonical_uuid(value: object) -> str:
    """Require the canonical lower-case UUID representation used by AMQP IDs."""

    if not isinstance(value, str):
        raise _contract_error()
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise _contract_error() from error
    if value != canonical:
        raise _contract_error()
    return canonical


def _required_text(value: object) -> str:
    """Accept one non-empty, control-character-free durable text value."""

    if not isinstance(value, str) or not value or "\x00" in value:
        raise _contract_error()
    return value


def _validated_route(*, stage: object, stem_name: object, event_type: object) -> DownstreamRoute:
    """Return the one route allowed for a database stage/stem/type triple."""

    if not isinstance(stage, str) or not isinstance(stem_name, str) or not isinstance(event_type, str):
        raise _contract_error()
    route = _ROUTE_BY_STAGE_AND_STEM.get((stage, stem_name))
    if route is None or route.event_type != event_type:
        raise _contract_error()
    return route


def _validated_body(*, job_id: str, stem_name: str, payload: object) -> dict[str, object]:
    """Validate the exact v004 payload and rebuild a plain JSON-safe mapping."""

    if not isinstance(payload, Mapping) or set(payload) != {
        "schema_version",
        "job_id",
        "stem_name",
        "stem",
    }:
        raise _contract_error()
    if (
        type(payload["schema_version"]) is not int
        or payload["schema_version"] != 1
        or _canonical_uuid(payload["job_id"]) != job_id
        or payload["stem_name"] != stem_name
    ):
        raise _contract_error()

    stem = payload["stem"]
    if not isinstance(stem, Mapping) or set(stem) != {
        "bucket",
        "object_key",
        "content_type",
        "size_bytes",
        "sha256",
    }:
        raise _contract_error()
    bucket = _required_text(stem["bucket"])
    object_key = _required_text(stem["object_key"])
    content_type = _required_text(stem["content_type"])
    size_bytes = stem["size_bytes"]
    sha256 = stem["sha256"]
    expected_object_key = f"stems/{job_id}/{stem_name}.wav"
    if (
        bucket != LOCAL_UPLOADS_BUCKET
        or object_key != expected_object_key
        or content_type != STEM_CONTENT_TYPE
        or type(size_bytes) is not int
        or size_bytes < 1
        or not isinstance(sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", sha256)
    ):
        raise _contract_error()

    # Rebuild nested dictionaries from concrete strings/integers so an exotic
    # Mapping supplied by a driver cannot affect deterministic JSON encoding.
    return {
        "schema_version": 1,
        "job_id": job_id,
        "stem_name": stem_name,
        "stem": {
            "bucket": bucket,
            "object_key": object_key,
            "content_type": content_type,
            "size_bytes": size_bytes,
            "sha256": sha256,
        },
    }


def build_downstream_amqp_request(
    *,
    event_id: object,
    job_id: object,
    stage: object,
    stem_name: object,
    event_type: object,
    payload: object,
) -> DownstreamAMQPRequest:
    """Build one persistent, fully validated AMQP request from a v004 row.

    A future generic dispatcher must call this *after* it has leased the row
    and before it calls its Pika publisher.  It cannot select a queue directly:
    the direct exchange plus fixed route allows RabbitMQ topology to own queue
    binding, retry, and dead-letter policy.  The AMQP ``message_id`` is the
    outbox UUID, whereas ``correlation_id`` is the Job UUID; neither is copied
    into the body, avoiding two competing sources of truth for those values.
    """

    canonical_event_id = _canonical_uuid(event_id)
    canonical_job_id = _canonical_uuid(job_id)
    route = _validated_route(stage=stage, stem_name=stem_name, event_type=event_type)
    # `_validated_route` has already rejected all non-string stem names. Repeat
    # the narrow type guard here rather than relying on an `assert`, which can
    # be disabled when Python runs with optimisation flags.
    if not isinstance(stem_name, str):
        raise _contract_error()
    canonical_body = _validated_body(
        job_id=canonical_job_id,
        stem_name=stem_name,
        payload=payload,
    )
    body = json.dumps(
        canonical_body,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return DownstreamAMQPRequest(
        exchange=PROCESSING_EXCHANGE,
        routing_key=route.routing_key,
        body=body,
        content_type="application/json",
        content_encoding="utf-8",
        delivery_mode=2,
        message_type=route.event_type,
        message_id=canonical_event_id,
        correlation_id=canonical_job_id,
    )
