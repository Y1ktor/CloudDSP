"""Lease-bound MinIO ``HeadObject`` verification for one ADTOF drums stem.

This module runs after PostgreSQL committed an ADTOF task lease and before a
drums WAV enters Pod scratch or the ADTOF model. It makes exactly one
S3-compatible metadata request, comparing MinIO's current headers against
request evidence already cross-checked with the immutable published outbox row.

It imports no Boto3 SDK, PostgreSQL connection, RabbitMQ client, model runtime,
or Kubernetes API. A later composition root injects the private client and
decides durable retry/terminal handling. This layer neither downloads bytes nor
renews/starts/completes a task or makes a broker acknowledgement decision.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.messaging.adtof_requested_message import (
    ADTOF_STEM_NAME,
    LOCAL_UPLOADS_BUCKET,
    STEM_CONTENT_TYPE,
    ADTOFRequestedMessage,
)
from app.db.task_claim import (
    ADTOF_STEM_MODES,
    MAX_ADTOF_TASK_ATTEMPTS,
    ADTOFTaskLease,
)


# These constants originate in Demucs's immutable output-object contract. The
# ADTOF worker repeats them instead of trusting a task or bare MinIO object, so
# only a reviewed Demucs drums WAV may become an ADTOF transcription input.
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


class ADTOFHeadObjectClient(Protocol):
    """The sole S3 operation needed before ADTOF can read input bytes."""

    def head_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        """Return S3-compatible headers/metadata without an object body."""


class ADTOFStemStorageUnavailable(RuntimeError):
    """A retryable MinIO/client failure whose public text hides S3 diagnostics."""


class ADTOFStemStorageProtocolError(RuntimeError):
    """A caller-built lease/request or MinIO header cannot prove input safety."""


class ADTOFStemVerificationFailureCode(StrEnum):
    """Bounded permanent categories for a present but unsuitable drums stem."""

    OBJECT_MISSING = "adtof_stem_object_missing"
    SIZE_MISMATCH = "adtof_stem_size_mismatch"
    CONTENT_TYPE_MISMATCH = "adtof_stem_content_type_mismatch"
    METADATA_MISMATCH = "adtof_stem_metadata_mismatch"


class ADTOFPermanentStemVerificationError(RuntimeError):
    """Carry one reviewed permanent code without raw object metadata/errors."""

    def __init__(self, failure_code: ADTOFStemVerificationFailureCode) -> None:
        self.failure_code = failure_code
        super().__init__(failure_code.value)


@dataclass(frozen=True)
class VerifiedADTOFStemObject:
    """Minimal verified evidence available to a later download/model adapter.

    There is no object body, local path, credential, raw metadata mapping, or
    browser URL here. The checksum is retained so the later bounded download
    can compare streamed bytes with this established object identity.
    """

    bucket_name: str
    object_key: str
    content_type: str
    size_bytes: int
    sha256: str


def _canonical_uuid(value: object) -> str:
    """Require canonical lower-case UUID evidence without exposing its value."""

    if not isinstance(value, str):
        raise ADTOFStemStorageProtocolError("ADTOF stem identity is invalid.")
    try:
        normalized = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise ADTOFStemStorageProtocolError("ADTOF stem identity is invalid.") from error
    if normalized != value:
        raise ADTOFStemStorageProtocolError("ADTOF stem identity is invalid.")
    return normalized


def _validated_message(message: object) -> ADTOFRequestedMessage:
    """Revalidate frozen parser evidence before it can name a MinIO request."""

    if not isinstance(message, ADTOFRequestedMessage):
        raise TypeError("message must be ADTOFRequestedMessage.")
    job_id = _canonical_uuid(message.job_id)
    _canonical_uuid(message.event_id)
    if (
        message.stem_name != ADTOF_STEM_NAME
        or message.stem_bucket != LOCAL_UPLOADS_BUCKET
        or message.stem_object_key != f"stems/{job_id}/{ADTOF_STEM_NAME}.wav"
        or type(message.stem_content_length) is not int
        or message.stem_content_length < 1
        or not isinstance(message.stem_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", message.stem_sha256)
    ):
        raise ADTOFStemStorageProtocolError("ADTOF request contract is invalid.")
    return message


def _validated_lease(lease: object, *, message: ADTOFRequestedMessage) -> ADTOFTaskLease:
    """Require the claimed task to bind exactly this durable drums request."""

    if not isinstance(lease, ADTOFTaskLease):
        raise TypeError("lease must be ADTOFTaskLease.")
    job_id = _canonical_uuid(lease.job_id)
    _canonical_uuid(lease.task_id)
    event_id = _canonical_uuid(lease.request_event_id)
    _canonical_uuid(lease.lease_token)
    expected_key = f"stems/{message.job_id}/{ADTOF_STEM_NAME}.wav"
    if (
        job_id != message.job_id
        or event_id != message.event_id
        or lease.stem_name != ADTOF_STEM_NAME
        or lease.input_bucket != LOCAL_UPLOADS_BUCKET
        or lease.input_bucket != message.stem_bucket
        or lease.input_object_key != expected_key
        or lease.input_object_key != message.stem_object_key
        or lease.stem_mode not in ADTOF_STEM_MODES
        or type(lease.attempt_count) is not int
        or not 1 <= lease.attempt_count <= MAX_ADTOF_TASK_ATTEMPTS
    ):
        raise ADTOFStemStorageProtocolError("ADTOF claimed stem identity is invalid.")
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
    client: ADTOFHeadObjectClient,
    *,
    bucket_name: str,
    object_key: str,
) -> Mapping[str, object]:
    """Make one metadata call and distinguish definite absence from an outage."""

    try:
        response = client.head_object(Bucket=bucket_name, Key=object_key)
    except Exception as error:  # S3 SDKs expose incompatible concrete error types.
        if _error_code(error) in {"404", "NoSuchKey", "NoSuchObject", "NotFound"}:
            raise ADTOFPermanentStemVerificationError(
                ADTOFStemVerificationFailureCode.OBJECT_MISSING
            ) from error
        raise ADTOFStemStorageUnavailable("ADTOF stem HeadObject is unavailable.") from error
    if not isinstance(response, Mapping):
        raise ADTOFStemStorageProtocolError("MinIO returned an invalid ADTOF HeadObject response.")
    return response


def _content_length(response: Mapping[str, object], *, expected_size: int) -> int:
    """Require current MinIO byte length to equal durable Demucs evidence."""

    length = response.get("ContentLength")
    if isinstance(length, bool) or not isinstance(length, int) or length < 1:
        raise ADTOFStemStorageProtocolError("MinIO returned an invalid ADTOF ContentLength.")
    if length != expected_size:
        raise ADTOFPermanentStemVerificationError(ADTOFStemVerificationFailureCode.SIZE_MISMATCH)
    return length


def _content_type(response: Mapping[str, object]) -> str:
    """Require canonical Demucs WAV MIME type before download/model work."""

    content_type = response.get("ContentType")
    if not isinstance(content_type, str) or not content_type or "\x00" in content_type:
        raise ADTOFStemStorageProtocolError("MinIO returned an invalid ADTOF ContentType.")
    if content_type != STEM_CONTENT_TYPE:
        raise ADTOFPermanentStemVerificationError(
            ADTOFStemVerificationFailureCode.CONTENT_TYPE_MISMATCH
        )
    return content_type


def _exact_metadata(response: Mapping[str, object]) -> Mapping[str, str]:
    """Read one complete, unambiguous Demucs metadata inventory from MinIO."""

    metadata = response.get("Metadata")
    if not isinstance(metadata, Mapping):
        raise ADTOFStemStorageProtocolError("MinIO returned invalid ADTOF stem metadata.")
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
            raise ADTOFStemStorageProtocolError("MinIO returned invalid ADTOF stem metadata.")
        name = raw_name.lower()
        if name in normalized:
            raise ADTOFStemStorageProtocolError("MinIO returned ambiguous ADTOF stem metadata.")
        normalized[name] = raw_value
    if set(normalized) != _REQUIRED_DEMUCS_STEM_METADATA_NAMES:
        raise ADTOFStemStorageProtocolError("MinIO returned incomplete ADTOF stem metadata.")
    return normalized


def _validate_metadata(
    metadata: Mapping[str, str],
    *,
    lease: ADTOFTaskLease,
    message: ADTOFRequestedMessage,
) -> str:
    """Compare all stable Demucs evidence before exposing a verified input."""

    task_id = metadata["task-id"]
    try:
        _canonical_uuid(task_id)
    except ADTOFStemStorageProtocolError as error:
        raise ADTOFPermanentStemVerificationError(
            ADTOFStemVerificationFailureCode.METADATA_MISMATCH
        ) from error
    if (
        metadata["schema-version"] != DEMUCS_STEM_METADATA_SCHEMA_VERSION
        or metadata["producer"] != DEMUCS_STEM_METADATA_PRODUCER
        or metadata["job-id"] != message.job_id
        or metadata["stem-name"] != ADTOF_STEM_NAME
        or metadata["stem-mode"] != lease.stem_mode
        or metadata["size-bytes"] != str(message.stem_content_length)
        or metadata["sha256"] != message.stem_sha256
        or not re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"])
    ):
        raise ADTOFPermanentStemVerificationError(
            ADTOFStemVerificationFailureCode.METADATA_MISMATCH
        )
    return metadata["sha256"]


def verify_claimed_adtof_stem_head_object(
    client: ADTOFHeadObjectClient,
    *,
    lease: ADTOFTaskLease,
    message: ADTOFRequestedMessage,
) -> VerifiedADTOFStemObject:
    """Verify one claimed drums WAV with exactly one metadata-only S3 request.

    On success, MinIO currently holds the exact durable key with its expected
    WAV type, byte length, and full Demucs metadata/checksum evidence. This is
    not byte-level proof: the next bounded download must hash the stream again.
    This function does not download, renew/start a task, acknowledge RabbitMQ,
    invoke ADTOF, upload outputs, or update PostgreSQL.
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
    return VerifiedADTOFStemObject(
        bucket_name=validated_lease.input_bucket,
        object_key=validated_lease.input_object_key,
        content_type=content_type,
        size_bytes=size_bytes,
        sha256=sha256,
    )
