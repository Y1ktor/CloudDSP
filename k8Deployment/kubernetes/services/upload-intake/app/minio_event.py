"""Parse untrusted MinIO AMQP notifications without doing any I/O.

MinIO publishes S3-compatible ObjectCreated notifications to RabbitMQ.  A
message is only a hint that a storage action might have happened: it does not
prove that CloudDSP owns the object, that the object still exists, or that its
metadata is valid.  This module therefore performs only syntactic narrowing.
It returns candidates which a later consumer will re-check with PostgreSQL and
MinIO ``HeadObject`` before changing a durable job.

There is deliberately no AMQP, MinIO, PostgreSQL, network, clock, filesystem,
or logging dependency here.  Keeping this boundary pure lets unit tests prove
that malformed event data cannot reach a future database query or processing
request.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final
from urllib.parse import unquote_to_bytes
from uuid import UUID


# These constants mirror the already-live Job API direct-upload contract.  The
# database migration enforces the same ``uploads/{job_id}/...`` ownership shape.
# Defining them here makes the future AMQP consumer reject output/stem paths
# even if a MinIO bucket notification is accidentally configured too broadly.
UPLOADS_BUCKET: Final = "clouddsp-uploads"
UPLOADS_PREFIX: Final = "uploads"
DIRECT_UPLOAD_EVENT_NAME: Final = "s3:ObjectCreated:Post"

# S3 event object keys use form-style URL encoding: spaces become `+`, while
# a literal plus becomes `%2B`. ``urllib.parse.unquote`` leaves a malformed
# ``%`` unchanged, which is unsafe for a boundary that promises a strict
# one-time decode. Check every percent sign first, then decode exactly once.
# A remaining percent sequence is allowed: a user can name a file
# ``mix%2Ffinal.wav`` without that text becoming a path separator.
_PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")


class MinioEventEnvelopeError(ValueError):
    """Raised when an AMQP body is not a usable MinIO event envelope.

    The future consumer will convert this fixed category into a safe
    acknowledge-and-observe outcome.  The exception message contains no raw
    event JSON, object key, credential, or broker detail because those values
    must not be copied into application logs.
    """


@dataclass(frozen=True)
class SourceUploadCandidate:
    """One syntactically valid direct-upload notification record.

    These values are still untrusted notification data.  In particular,
    ``job_id`` has only been parsed from the key; the future durable step must
    require a PostgreSQL row whose stored bucket and object key exactly match
    before it can update ``source_uploaded``.
    """

    record_index: int
    event_name: str
    bucket_name: str
    object_key: str
    job_id: str
    source_filename: str


@dataclass(frozen=True)
class IgnoredMinioEventRecord:
    """A record that cannot start direct-upload intake.

    ``reason`` is a small fixed category instead of an error string containing
    untrusted data.  A future consumer can count/log the category while safely
    acknowledging events that cannot become valid through an immediate retry.
    """

    record_index: int
    reason: str


@dataclass(frozen=True)
class ParsedMinioEvent:
    """The complete, explicit result of parsing one AMQP message body.

    Keeping ignored records in the result is important: a message containing a
    valid record plus a malformed record must not quietly appear wholly valid.
    The later consumer will decide its queue acknowledgement only after it has
    handled every candidate and recorded any ignored categories safely.
    """

    candidates: tuple[SourceUploadCandidate, ...]
    ignored_records: tuple[IgnoredMinioEventRecord, ...]


def _decode_json_body(body: str | bytes | bytearray) -> Mapping[str, object]:
    """Decode one UTF-8 JSON object without accepting arbitrary Python data.

    RabbitMQ clients normally expose a message body as bytes.  Accepting text
    as well keeps focused unit tests readable, but accepting an already-parsed
    dictionary would blur the message boundary and let a later caller bypass
    JSON/shape validation accidentally.
    """

    if isinstance(body, (bytes, bytearray)):
        try:
            decoded_body = bytes(body).decode("utf-8")
        except UnicodeDecodeError as error:
            raise MinioEventEnvelopeError("MinIO event body is not UTF-8 JSON.") from error
    elif isinstance(body, str):
        decoded_body = body
    else:
        raise MinioEventEnvelopeError("MinIO event body must be text or bytes.")

    try:
        decoded_json = json.loads(decoded_body)
    except (TypeError, json.JSONDecodeError) as error:
        raise MinioEventEnvelopeError("MinIO event body is not valid JSON.") from error

    if not isinstance(decoded_json, Mapping):
        raise MinioEventEnvelopeError("MinIO event body must be a JSON object.")
    return decoded_json


def _strictly_decode_s3_key_once(raw_key: str) -> str:
    """Decode one S3 event key with form-style spaces and strict UTF-8.

    S3 notifications encode a space as ``+`` and a *literal* plus as ``%2B``.
    Replacing raw plus signs before percent-decoding distinguishes the two;
    replacing afterward would corrupt a real plus. ``unquote_to_bytes`` then
    performs one byte-level decode, so a filename containing literal ``%2F``
    text cannot acquire an unexpected slash through a second pass.
    """

    if not isinstance(raw_key, str) or not raw_key:
        raise ValueError("Object key is absent.")

    for position, character in enumerate(raw_key):
        if character == "%":
            candidate_escape = raw_key[position : position + 3]
            if _PERCENT_ESCAPE.fullmatch(candidate_escape) is None:
                raise ValueError("Object key has a malformed percent escape.")

    try:
        decoded_key = unquote_to_bytes(raw_key.replace("+", " ")).decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Object key is not valid UTF-8 after decoding.") from error

    if not decoded_key or "\x00" in decoded_key:
        raise ValueError("Object key is empty or contains NUL.")
    return decoded_key


def _canonical_job_id(job_id: str) -> str:
    """Require the lowercase UUID spelling emitted by the Job API.

    PostgreSQL accepts several UUID spellings, but accepting them here could
    make a notification key differ textually from the canonical object key
    generated when the job was created.  This pure parser therefore rejects
    uppercase, braces, and other alternative forms before any future lookup.
    """

    if not isinstance(job_id, str):
        raise ValueError("Job ID is not text.")
    try:
        normalized_job_id = str(UUID(job_id))
    except (ValueError, AttributeError) as error:
        raise ValueError("Job ID is not a UUID.") from error
    if normalized_job_id != job_id:
        raise ValueError("Job ID is not lowercase canonical UUID text.")
    return normalized_job_id


def _candidate_from_object_key(
    *,
    record_index: int,
    event_name: str,
    bucket_name: str,
    object_key: str,
) -> SourceUploadCandidate | IgnoredMinioEventRecord:
    """Turn one allowed key shape into a candidate without querying storage.

    The shape permits exactly three slash-delimited components.  It does not
    permit a directory below the job ID, a second job ID, an output prefix, or
    a filename containing a slash/backslash.  PostgreSQL later repeats the
    stronger equality comparison against its server-generated object key.
    """

    components = object_key.split("/")
    if len(components) != 3 or components[0] != UPLOADS_PREFIX:
        return IgnoredMinioEventRecord(record_index, "unsupported_object_key")

    _, raw_job_id, source_filename = components
    if (
        not source_filename
        or source_filename in {".", ".."}
        or "\\" in source_filename
        or "\x00" in source_filename
        or len(source_filename) > 255
    ):
        return IgnoredMinioEventRecord(record_index, "unsupported_object_key")

    try:
        job_id = _canonical_job_id(raw_job_id)
    except ValueError:
        return IgnoredMinioEventRecord(record_index, "noncanonical_job_id")

    return SourceUploadCandidate(
        record_index=record_index,
        event_name=event_name,
        bucket_name=bucket_name,
        object_key=object_key,
        job_id=job_id,
        source_filename=source_filename,
    )


def _string_from_nested_mapping(
    mapping: Mapping[str, object],
    *path: str,
) -> str | None:
    """Read one nested text value without raising on an untrusted record."""

    current: object = mapping
    for part in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current if isinstance(current, str) else None


def _parse_record(
    record: object,
    *,
    record_index: int,
    expected_bucket: str,
) -> SourceUploadCandidate | IgnoredMinioEventRecord:
    """Classify one record independently from every other envelope record."""

    if not isinstance(record, Mapping):
        return IgnoredMinioEventRecord(record_index, "invalid_record_shape")

    event_name = record.get("eventName")
    if not isinstance(event_name, str):
        return IgnoredMinioEventRecord(record_index, "invalid_event_name")
    if event_name != DIRECT_UPLOAD_EVENT_NAME:
        return IgnoredMinioEventRecord(record_index, "unsupported_event_name")

    bucket_name = _string_from_nested_mapping(record, "s3", "bucket", "name")
    if bucket_name is None:
        return IgnoredMinioEventRecord(record_index, "invalid_bucket_name")
    if bucket_name != expected_bucket:
        return IgnoredMinioEventRecord(record_index, "unexpected_bucket")

    raw_object_key = _string_from_nested_mapping(record, "s3", "object", "key")
    if raw_object_key is None:
        return IgnoredMinioEventRecord(record_index, "invalid_object_key")
    try:
        object_key = _strictly_decode_s3_key_once(raw_object_key)
    except ValueError:
        return IgnoredMinioEventRecord(record_index, "invalid_object_key")

    return _candidate_from_object_key(
        record_index=record_index,
        event_name=event_name,
        bucket_name=bucket_name,
        object_key=object_key,
    )


def parse_direct_upload_event(
    body: str | bytes | bytearray,
    *,
    expected_bucket: str = UPLOADS_BUCKET,
) -> ParsedMinioEvent:
    """Parse one MinIO AMQP message into candidates and ignored categories.

    This is the sole public function for the first upload-intake task.  It
    performs neither object existence validation nor a database query.  The
    future AMQP consumer will call it before its own dependency-injected
    MinIO/PostgreSQL operations, making this syntax-only boundary easy to unit
    test and reuse from the later PostgreSQL-driven reconciler.
    """

    if not isinstance(expected_bucket, str) or not expected_bucket.strip():
        raise ValueError("expected_bucket must be non-empty text.")

    envelope = _decode_json_body(body)
    records = envelope.get("Records")
    if not isinstance(records, list) or not records:
        raise MinioEventEnvelopeError("MinIO event body must contain a non-empty Records array.")

    candidates: list[SourceUploadCandidate] = []
    ignored_records: list[IgnoredMinioEventRecord] = []
    for record_index, record in enumerate(records):
        parsed_record = _parse_record(
            record,
            record_index=record_index,
            expected_bucket=expected_bucket,
        )
        if isinstance(parsed_record, SourceUploadCandidate):
            candidates.append(parsed_record)
        else:
            ignored_records.append(parsed_record)

    return ParsedMinioEvent(
        candidates=tuple(candidates),
        ignored_records=tuple(ignored_records),
    )
