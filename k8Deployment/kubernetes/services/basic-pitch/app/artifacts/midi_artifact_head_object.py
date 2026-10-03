"""Verify MinIO stored exactly one uploaded Basic Pitch MIDI artifact.

The preceding uploader proved what its S3-compatible client consumed. This
boundary makes one metadata-only ``HeadObject`` request and compares MinIO's
current stored object with both the immutable output plan and that upload
receipt: private bucket/key, ``audio/midi`` type, byte count, SHA-256, and all
versioned provenance metadata must agree.

It deliberately does not read MIDI bytes, create a client, delete/re-upload an
object, mutate PostgreSQL, acknowledge RabbitMQ, invoke Basic Pitch, or use
the Kubernetes API. A later lease-token-guarded completion adapter decides how
to use the returned stored-object evidence.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.messaging.basic_pitch_requested_message import BASIC_PITCH_STEM_NAMES, LOCAL_UPLOADS_BUCKET, MIDI_CONTENT_TYPE
from app.artifacts.midi_artifact_upload import UploadedBasicPitchMidiObject
from app.artifacts.midi_output_object import (
    BASIC_PITCH_MIDI_METADATA_SCHEMA_VERSION,
    BASIC_PITCH_MIDI_PRODUCER,
    BasicPitchMidiOutputObject,
)
from app.db.task_lease import BASIC_PITCH_STEMS_BY_MODE


class BasicPitchMidiHeadObjectClient(Protocol):
    """The one S3-compatible operation needed to verify a stored MIDI artifact."""

    def head_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        """Return current object headers/user metadata without reading its body."""


class BasicPitchMidiHeadObjectUnavailable(RuntimeError):
    """A retryable MinIO/client failure with no endpoint or SDK diagnostic leak."""


class BasicPitchMidiHeadObjectProtocolError(RuntimeError):
    """A caller-built plan/receipt or MinIO response lacks a safe exact shape."""


class BasicPitchMidiHeadObjectFailureCode(StrEnum):
    """Bounded stored-object disagreement categories for later task policy."""

    OBJECT_MISSING = "basic_pitch_midi_object_missing"
    SIZE_MISMATCH = "basic_pitch_midi_object_size_mismatch"
    CONTENT_TYPE_MISMATCH = "basic_pitch_midi_object_content_type_mismatch"
    METADATA_MISMATCH = "basic_pitch_midi_object_metadata_mismatch"


class BasicPitchPermanentMidiHeadObjectError(RuntimeError):
    """Carry only a reviewed stored-object mismatch code, never raw MinIO data."""

    def __init__(self, failure_code: BasicPitchMidiHeadObjectFailureCode) -> None:
        self.failure_code = failure_code
        super().__init__(failure_code.value)


@dataclass(frozen=True)
class VerifiedStoredBasicPitchMidiObject:
    """Stable stored-object evidence for the later PostgreSQL completion boundary.

    No local path, credential, ETag, SDK response, or raw metadata mapping is
    retained. The receipt's byte count and digest remain because the later
    completion statement must bind exact durable result evidence.
    """

    bucket: str
    object_key: str
    content_length: int
    sha256: str


def _protocol_error() -> BasicPitchMidiHeadObjectProtocolError:
    """Return one non-sensitive category for malformed plan/receipt/response data."""

    return BasicPitchMidiHeadObjectProtocolError("Basic Pitch MIDI HeadObject evidence is invalid.")


def _canonical_uuid(value: object) -> str:
    """Require canonical lower-case UUID text before it names a private key."""

    if not isinstance(value, str):
        raise _protocol_error()
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise _protocol_error() from error
    if canonical != value:
        raise _protocol_error()
    return canonical


def _exact_plan_metadata(value: object) -> dict[str, str]:
    """Read the immutable plan metadata as one exact no-duplicate mapping."""

    if not isinstance(value, tuple):
        raise _protocol_error()
    metadata: dict[str, str] = {}
    for entry in value:
        if (
            not isinstance(entry, tuple)
            or len(entry) != 2
            or not isinstance(entry[0], str)
            or not isinstance(entry[1], str)
            or not entry[0]
            or not entry[1]
            or entry[0] in metadata
        ):
            raise _protocol_error()
        metadata[entry[0]] = entry[1]
    return metadata


def _validated_plan_and_receipt(
    *,
    plan: object,
    receipt: object,
) -> tuple[BasicPitchMidiOutputObject, UploadedBasicPitchMidiObject, dict[str, str]]:
    """Require a fixed plan and receipt to name exactly the same private artifact."""

    if not isinstance(plan, BasicPitchMidiOutputObject) or not isinstance(receipt, UploadedBasicPitchMidiObject):
        raise _protocol_error()
    if (
        plan.bucket != LOCAL_UPLOADS_BUCKET
        or plan.content_type != MIDI_CONTENT_TYPE
        or type(plan.content_length) is not int
        or plan.content_length < 1
        or receipt.bucket != plan.bucket
        or receipt.object_key != plan.object_key
        or receipt.content_length != plan.content_length
        or not isinstance(receipt.sha256, str)
    ):
        raise _protocol_error()
    metadata = _exact_plan_metadata(plan.s3_metadata)
    required_names = {
        "schema-version",
        "producer",
        "job-id",
        "task-id",
        "request-event-id",
        "stem-name",
        "stem-mode",
        "size-bytes",
        "sha256",
        "input-stem-sha256",
    }
    if set(metadata) != required_names:
        raise _protocol_error()
    job_id = _canonical_uuid(metadata["job-id"])
    _canonical_uuid(metadata["task-id"])
    _canonical_uuid(metadata["request-event-id"])
    stem_name = metadata["stem-name"]
    stem_mode = metadata["stem-mode"]
    if (
        metadata["schema-version"] != BASIC_PITCH_MIDI_METADATA_SCHEMA_VERSION
        or metadata["producer"] != BASIC_PITCH_MIDI_PRODUCER
        or stem_name not in BASIC_PITCH_STEM_NAMES
        or stem_mode not in BASIC_PITCH_STEMS_BY_MODE
        or stem_name not in BASIC_PITCH_STEMS_BY_MODE[stem_mode]
        or plan.object_key != f"midi/{job_id}/{stem_name}.mid"
        or metadata["size-bytes"] != str(plan.content_length)
        or not re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"])
        or not re.fullmatch(r"[0-9a-f]{64}", metadata["input-stem-sha256"])
        or receipt.sha256 != metadata["sha256"]
    ):
        raise _protocol_error()
    return plan, receipt, metadata


def _error_code(error: BaseException) -> str | None:
    """Read an S3-shaped error code without importing a vendor SDK exception."""

    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return None
    error_mapping = response.get("Error")
    if not isinstance(error_mapping, Mapping):
        return None
    code = error_mapping.get("Code")
    return code if isinstance(code, str) else None


def _head_object_or_raise(
    client: BasicPitchMidiHeadObjectClient,
    *,
    bucket: str,
    object_key: str,
) -> Mapping[str, object]:
    """Perform exactly one HeadObject call and split absence from a retryable outage."""

    try:
        response = client.head_object(Bucket=bucket, Key=object_key)
    except Exception as error:  # S3 SDK exception classes are implementation-specific.
        if _error_code(error) in {"404", "NoSuchKey", "NoSuchObject", "NotFound"}:
            raise BasicPitchPermanentMidiHeadObjectError(
                BasicPitchMidiHeadObjectFailureCode.OBJECT_MISSING
            ) from error
        raise BasicPitchMidiHeadObjectUnavailable("Basic Pitch MIDI HeadObject is unavailable.") from error
    if not isinstance(response, Mapping):
        raise _protocol_error()
    return response


def _content_length(response: Mapping[str, object], *, expected: int) -> int:
    """Require MinIO's current stored byte count to match the upload receipt."""

    value = response.get("ContentLength")
    if type(value) is not int or value < 1:
        raise _protocol_error()
    if value != expected:
        raise BasicPitchPermanentMidiHeadObjectError(
            BasicPitchMidiHeadObjectFailureCode.SIZE_MISMATCH
        )
    return value


def _content_type(response: Mapping[str, object]) -> str:
    """Require the canonical Basic Pitch MIDI MIME type before completion is possible."""

    value = response.get("ContentType")
    if not isinstance(value, str) or not value or "\x00" in value:
        raise _protocol_error()
    if value != MIDI_CONTENT_TYPE:
        raise BasicPitchPermanentMidiHeadObjectError(
            BasicPitchMidiHeadObjectFailureCode.CONTENT_TYPE_MISMATCH
        )
    return value


def _stored_metadata(response: Mapping[str, object], *, expected: Mapping[str, str]) -> None:
    """Require a complete case-normalized stored user-metadata inventory to agree."""

    raw_metadata = response.get("Metadata")
    if not isinstance(raw_metadata, Mapping):
        raise _protocol_error()
    normalized: dict[str, str] = {}
    for raw_name, raw_value in raw_metadata.items():
        if (
            not isinstance(raw_name, str)
            or not isinstance(raw_value, str)
            or not raw_name
            or not raw_value
            or "\x00" in raw_name
            or "\x00" in raw_value
        ):
            raise _protocol_error()
        name = raw_name.lower()
        if name in normalized:
            raise _protocol_error()
        normalized[name] = raw_value
    # Boto3 supplies MinIO user metadata as lower-case names. Normalizing only
    # stored names makes an equivalent SDK capitalization safe, while requiring
    # the plan's reviewed names/values to remain exact.
    if normalized != dict(expected):
        raise BasicPitchPermanentMidiHeadObjectError(
            BasicPitchMidiHeadObjectFailureCode.METADATA_MISMATCH
        )


def verify_uploaded_basic_pitch_midi_head_object(
    client: BasicPitchMidiHeadObjectClient,
    *,
    output_object: BasicPitchMidiOutputObject,
    upload_receipt: UploadedBasicPitchMidiObject,
) -> VerifiedStoredBasicPitchMidiObject:
    """Verify MinIO stores the exact uploaded MIDI with one metadata-only request.

    A successful result proves present stored metadata, not just local upload
    intent. It does not read the object body again because the uploader's
    second streaming SHA-256 is already the byte-level proof for its request;
    the next database transition still needs its own current-lease predicate.
    """

    plan, receipt, metadata = _validated_plan_and_receipt(
        plan=output_object,
        receipt=upload_receipt,
    )
    response = _head_object_or_raise(client, bucket=plan.bucket, object_key=plan.object_key)
    size_bytes = _content_length(response, expected=receipt.content_length)
    _content_type(response)
    _stored_metadata(response, expected=metadata)
    return VerifiedStoredBasicPitchMidiObject(
        bucket=receipt.bucket,
        object_key=receipt.object_key,
        content_length=size_bytes,
        sha256=receipt.sha256,
    )
