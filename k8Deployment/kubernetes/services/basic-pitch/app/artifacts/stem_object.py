"""Lease-bound MinIO ``HeadObject`` verification for one Basic Pitch stem.

This module sits after a Basic Pitch task lease is committed and before any
stem bytes enter Pod scratch or the Basic Pitch model. It makes exactly one
S3-compatible metadata request, then compares MinIO's current object headers
with the delivery evidence that was already cross-checked against PostgreSQL's
published outbox event.

It deliberately imports no Boto3 SDK, PostgreSQL connection, RabbitMQ client,
model runtime, or Kubernetes API. A later composition root injects the private
client, chooses retry/terminal handling, and controls acknowledgement. This
layer neither downloads audio nor mutates a lease/task state.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.messaging.basic_pitch_requested_message import (
    LOCAL_UPLOADS_BUCKET,
    STEM_CONTENT_TYPE,
    BasicPitchRequestContractError,
    BasicPitchRequestedMessage,
    build_basic_pitch_midi_output,
)
from app.db.task_lease import BASIC_PITCH_STEMS_BY_MODE, MAX_BASIC_PITCH_TASK_ATTEMPTS, BasicPitchTaskLease


# These values originate in Demucs's immutable output-object contract. The
# Basic Pitch worker repeats them rather than trusting a caller-built task or
# bare MinIO object: only a reviewed Demucs WAV may become its MIDI input.
DEMUCS_STEM_METADATA_SCHEMA_VERSION = "1"
DEMUCS_STEM_METADATA_PRODUCER = "demucs"
_REQUIRED_DEMUCS_STEM_METADATA_NAMES = frozenset(
    {
        "schema-version",
        "producer",
        "job-id",
        "task-id",
        "stem-name",
        "stem-mode",
        "size-bytes",
        "sha256",
    }
)


class BasicPitchHeadObjectClient(Protocol):
    """The only S3 operation needed before Basic Pitch can read stem bytes."""

    def head_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        """Return S3-compatible object headers/metadata without an object body."""


class BasicPitchStemStorageUnavailable(RuntimeError):
    """A retryable MinIO/client failure whose public text hides S3 diagnostics."""


class BasicPitchStemStorageProtocolError(RuntimeError):
    """MinIO headers or a caller-built lease/message cannot prove input safety."""


class BasicPitchStemVerificationFailureCode(StrEnum):
    """Bounded permanent categories for a present but unsuitable input stem."""

    OBJECT_MISSING = "basic_pitch_stem_object_missing"
    SIZE_MISMATCH = "basic_pitch_stem_size_mismatch"
    CONTENT_TYPE_MISMATCH = "basic_pitch_stem_content_type_mismatch"
    METADATA_MISMATCH = "basic_pitch_stem_metadata_mismatch"


class BasicPitchPermanentStemVerificationError(RuntimeError):
    """Carry one reviewed permanent code without raw object metadata/errors."""

    def __init__(self, failure_code: BasicPitchStemVerificationFailureCode) -> None:
        self.failure_code = failure_code
        super().__init__(failure_code.value)


@dataclass(frozen=True)
class VerifiedBasicPitchStemObject:
    """The minimal durable-safe evidence the later download/model layer receives.

    There is no object body, local path, credential, raw metadata mapping, or
    browser URL here. The checksum is retained because the later bounded
    download must compare the bytes it streams with this established identity.
    """

    bucket_name: str
    object_key: str
    content_type: str
    size_bytes: int
    sha256: str


def _canonical_uuid(value: object) -> str:
    """Require canonical lower-case UUID evidence without exposing its value."""

    if not isinstance(value, str):
        raise BasicPitchStemStorageProtocolError("Basic Pitch stem identity is invalid.")
    try:
        normalized = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise BasicPitchStemStorageProtocolError("Basic Pitch stem identity is invalid.") from error
    if normalized != value:
        raise BasicPitchStemStorageProtocolError("Basic Pitch stem identity is invalid.")
    return normalized


def _validated_message(message: object) -> BasicPitchRequestedMessage:
    """Revalidate a frozen message before it names a private MinIO request."""

    if not isinstance(message, BasicPitchRequestedMessage):
        raise TypeError("message must be BasicPitchRequestedMessage.")
    try:
        # The deterministic MIDI plan itself is not used here. Calling it
        # re-establishes the request module's exact UUID/bucket/key/byte/hash
        # contract, preventing direct dataclass construction from naming an
        # arbitrary input object.
        build_basic_pitch_midi_output(message)
    except BasicPitchRequestContractError as error:
        raise BasicPitchStemStorageProtocolError("Basic Pitch request contract is invalid.") from error
    return message


def _validated_lease(lease: object, *, message: BasicPitchRequestedMessage) -> BasicPitchTaskLease:
    """Require the claimed task to exactly bind this durable delivery and stem."""

    if not isinstance(lease, BasicPitchTaskLease):
        raise TypeError("lease must be BasicPitchTaskLease.")
    job_id = _canonical_uuid(lease.job_id)
    _canonical_uuid(lease.task_id)
    event_id = _canonical_uuid(lease.request_event_id)
    _canonical_uuid(lease.lease_token)
    expected_key = f"stems/{message.job_id}/{message.stem_name}.wav"
    if (
        job_id != message.job_id
        or event_id != message.event_id
        or lease.stem_name != message.stem_name
        or lease.input_bucket != LOCAL_UPLOADS_BUCKET
        or lease.input_bucket != message.stem_bucket
        or lease.input_object_key != expected_key
        or lease.input_object_key != message.stem_object_key
        or lease.stem_mode not in BASIC_PITCH_STEMS_BY_MODE
        or lease.stem_name not in BASIC_PITCH_STEMS_BY_MODE[lease.stem_mode]
        or type(lease.attempt_count) is not int
        or not 1 <= lease.attempt_count <= MAX_BASIC_PITCH_TASK_ATTEMPTS
    ):
        raise BasicPitchStemStorageProtocolError("Basic Pitch claimed stem identity is invalid.")
    return lease


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
    client: BasicPitchHeadObjectClient,
    *,
    bucket_name: str,
    object_key: str,
) -> Mapping[str, object]:
    """Make one metadata request and distinguish definite absence from outages."""

    try:
        response = client.head_object(Bucket=bucket_name, Key=object_key)
    except Exception as error:  # S3 SDKs expose incompatible concrete error classes.
        if _error_code(error) in {"404", "NoSuchKey", "NoSuchObject", "NotFound"}:
            raise BasicPitchPermanentStemVerificationError(
                BasicPitchStemVerificationFailureCode.OBJECT_MISSING
            ) from error
        raise BasicPitchStemStorageUnavailable("Basic Pitch stem HeadObject is unavailable.") from error
    if not isinstance(response, Mapping):
        raise BasicPitchStemStorageProtocolError("MinIO returned an invalid Basic Pitch HeadObject response.")
    return response


def _content_length(response: Mapping[str, object], *, expected_size: int) -> int:
    """Require MinIO's current byte length to equal durable Demucs evidence."""

    length = response.get("ContentLength")
    if isinstance(length, bool) or not isinstance(length, int) or length < 1:
        raise BasicPitchStemStorageProtocolError("MinIO returned an invalid Basic Pitch ContentLength.")
    if length != expected_size:
        raise BasicPitchPermanentStemVerificationError(
            BasicPitchStemVerificationFailureCode.SIZE_MISMATCH
        )
    return length


def _content_type(response: Mapping[str, object]) -> str:
    """Require the canonical Demucs WAV MIME type before any download/model work."""

    content_type = response.get("ContentType")
    if not isinstance(content_type, str) or not content_type or "\x00" in content_type:
        raise BasicPitchStemStorageProtocolError("MinIO returned an invalid Basic Pitch ContentType.")
    if content_type != STEM_CONTENT_TYPE:
        raise BasicPitchPermanentStemVerificationError(
            BasicPitchStemVerificationFailureCode.CONTENT_TYPE_MISMATCH
        )
    return content_type


def _exact_metadata(response: Mapping[str, object]) -> Mapping[str, str]:
    """Read one complete, unambiguous Demucs metadata inventory from MinIO."""

    metadata = response.get("Metadata")
    if not isinstance(metadata, Mapping):
        raise BasicPitchStemStorageProtocolError("MinIO returned invalid Basic Pitch stem metadata.")
    normalized: dict[str, str] = {}
    for raw_name, raw_value in metadata.items():
        if (
            not isinstance(raw_name, str)
            or not isinstance(raw_value, str)
            or not raw_name
            or not raw_value
            or "\x00" in raw_name
            or "\x00" in raw_value
        ):
            raise BasicPitchStemStorageProtocolError("MinIO returned invalid Basic Pitch stem metadata.")
        name = raw_name.lower()
        if name in normalized:
            raise BasicPitchStemStorageProtocolError("MinIO returned ambiguous Basic Pitch stem metadata.")
        normalized[name] = raw_value
    if set(normalized) != _REQUIRED_DEMUCS_STEM_METADATA_NAMES:
        raise BasicPitchStemStorageProtocolError("MinIO returned incomplete Basic Pitch stem metadata.")
    return normalized


def _validate_metadata(
    metadata: Mapping[str, str],
    *,
    lease: BasicPitchTaskLease,
    message: BasicPitchRequestedMessage,
) -> str:
    """Compare all stable Demucs evidence before exposing a verified stem."""

    task_id = metadata["task-id"]
    try:
        _canonical_uuid(task_id)
    except BasicPitchStemStorageProtocolError as error:
        raise BasicPitchPermanentStemVerificationError(
            BasicPitchStemVerificationFailureCode.METADATA_MISMATCH
        ) from error
    if (
        metadata["schema-version"] != DEMUCS_STEM_METADATA_SCHEMA_VERSION
        or metadata["producer"] != DEMUCS_STEM_METADATA_PRODUCER
        or metadata["job-id"] != message.job_id
        or metadata["stem-name"] != message.stem_name
        or metadata["stem-mode"] != lease.stem_mode
        or metadata["size-bytes"] != str(message.stem_content_length)
        or metadata["sha256"] != message.stem_sha256
        or not re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"])
    ):
        raise BasicPitchPermanentStemVerificationError(
            BasicPitchStemVerificationFailureCode.METADATA_MISMATCH
        )
    return metadata["sha256"]


def verify_claimed_basic_pitch_stem_head_object(
    client: BasicPitchHeadObjectClient,
    *,
    lease: BasicPitchTaskLease,
    message: BasicPitchRequestedMessage,
) -> VerifiedBasicPitchStemObject:
    """Verify one claimed Demucs stem with exactly one metadata-only S3 request.

    On success, MinIO currently holds the exact durable stem key with its
    expected WAV type, byte length, and full Demucs metadata/checksum evidence.
    This is not byte-level proof: the next bounded download must hash the
    streamed object again. This function does not download, renew/start a task,
    acknowledge RabbitMQ, invoke Basic Pitch, upload MIDI, or update PostgreSQL.
    """

    validated_message = _validated_message(message)
    validated_lease = _validated_lease(lease, message=validated_message)
    response = _head_object_or_raise(
        client,
        bucket_name=validated_lease.input_bucket,
        object_key=validated_lease.input_object_key,
    )
    size_bytes = _content_length(response, expected_size=validated_message.stem_content_length)
    content_type = _content_type(response)
    sha256 = _validate_metadata(
        _exact_metadata(response),
        lease=validated_lease,
        message=validated_message,
    )
    return VerifiedBasicPitchStemObject(
        bucket_name=validated_lease.input_bucket,
        object_key=validated_lease.input_object_key,
        content_type=content_type,
        size_bytes=size_bytes,
        sha256=sha256,
    )
