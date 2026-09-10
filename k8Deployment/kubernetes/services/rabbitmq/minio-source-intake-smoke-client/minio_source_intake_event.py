"""Validate the one MinIO notification used by the transport smoke test.

This module intentionally has no AMQP, MinIO, Kubernetes, filesystem, clock,
or environment-variable dependency.  It only narrows one untrusted JSON event
body into a yes/no decision, allowing its important acceptance rules to be
unit-tested without a broker or a container image.

The future upload-intake service performs stronger PostgreSQL and MinIO object
verification.  This smoke-test boundary proves only that MinIO emitted the
expected S3-compatible message and RabbitMQ delivered it intact.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final
from urllib.parse import unquote_to_bytes


# A MinIO S3 API `put-object` smoke upload produces this event.  This differs
# deliberately from the first production browser consumer, which will accept
# ObjectCreated:Post. The transport check does not test application intake
# policy; it tests the native MinIO-to-RabbitMQ notification path.
SMOKE_EVENT_NAME: Final = "s3:ObjectCreated:Put"

# Every percent character in an S3 event key must begin a two-digit hexadecimal
# escape. `urllib.parse.unquote` tolerates malformed percent text, which would
# make a strict expected-key comparison ambiguous, so validate first.
_PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")


class SourceIntakeSmokeEventError(ValueError):
    """Raise for a body that cannot be the one expected smoke notification.

    The AMQP client prints only this exception category, never the JSON body,
    object key, or broker details.  That keeps a future production-like smoke
    Job from accidentally reflecting untrusted event data in its logs.
    """


@dataclass(frozen=True)
class ExpectedSourceEvent:
    """The three immutable fields that identify this smoke upload event."""

    bucket_name: str
    object_key: str
    event_name: str = SMOKE_EVENT_NAME


def _strict_url_decode(value: object) -> str:
    """Decode an S3 URL-encoded key exactly once and reject malformed input."""

    if not isinstance(value, str) or not value:
        raise SourceIntakeSmokeEventError("event object key is missing")

    for percent_index, character in enumerate(value):
        if character == "%" and _PERCENT_ESCAPE.match(value, percent_index) is None:
            raise SourceIntakeSmokeEventError("event object key has malformed escape")

    try:
        return unquote_to_bytes(value).decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise SourceIntakeSmokeEventError("event object key is not UTF-8") from error


def _mapping_field(container: Mapping[str, object], name: str) -> Mapping[str, object]:
    """Return a required JSON object field without accepting arbitrary values."""

    candidate = container.get(name)
    if not isinstance(candidate, Mapping):
        raise SourceIntakeSmokeEventError(f"event {name} object is missing")
    return candidate


def assert_expected_source_event(
    body: bytes | bytearray | str,
    expected: ExpectedSourceEvent,
) -> None:
    """Raise unless ``body`` is exactly one expected MinIO ObjectCreated event.

    A single direct S3 PUT creates one MinIO record.  Requiring exactly one
    record gives this transport smoke test a precise assertion: a multi-record
    delivery remains valid for a future production consumer but must not be
    accidentally acknowledged as this test's one known upload.
    """

    if isinstance(body, (bytes, bytearray)):
        try:
            decoded_body = bytes(body).decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise SourceIntakeSmokeEventError("event body is not UTF-8 JSON") from error
    elif isinstance(body, str):
        decoded_body = body
    else:
        raise SourceIntakeSmokeEventError("event body has unsupported type")

    try:
        envelope = json.loads(decoded_body)
    except json.JSONDecodeError as error:
        raise SourceIntakeSmokeEventError("event body is not JSON") from error

    if not isinstance(envelope, Mapping):
        raise SourceIntakeSmokeEventError("event envelope is not an object")

    records = envelope.get("Records")
    if not isinstance(records, list) or len(records) != 1:
        raise SourceIntakeSmokeEventError("event does not contain exactly one record")

    record = records[0]
    if not isinstance(record, Mapping):
        raise SourceIntakeSmokeEventError("event record is not an object")

    if record.get("eventName") != expected.event_name:
        raise SourceIntakeSmokeEventError("event name does not match smoke upload")

    s3 = _mapping_field(record, "s3")
    bucket = _mapping_field(s3, "bucket")
    object_metadata = _mapping_field(s3, "object")
    if bucket.get("name") != expected.bucket_name:
        raise SourceIntakeSmokeEventError("event bucket does not match smoke upload")

    if _strict_url_decode(object_metadata.get("key")) != expected.object_key:
        raise SourceIntakeSmokeEventError("event object key does not match smoke upload")
