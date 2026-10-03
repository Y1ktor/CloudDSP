"""Private MinIO ``HeadObject`` verification for one claimed Demucs source.

This module consumes a durable :class:`DemucsTaskLease` only after a future
worker has committed it. It calls S3-compatible ``HeadObject`` exactly once and
validates object headers/metadata without downloading an audio byte. The next
separate layer will download to a bounded work directory and run FFprobe; this
module intentionally does neither.

It has no Boto3 import, connection factory, PostgreSQL query, RabbitMQ ack,
lease mutation, model invocation, or Kubernetes API call. A future composition
root injects a restricted MinIO client using the dedicated Demucs S3 identity.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.db.task_lease import DemucsTaskLease


# This is the preserved direct-upload hard limit. A MinIO notification, AMQP
# delivery, and browser File.size are hints; HeadObject ContentLength is the
# storage-side size that a worker must trust before allocating model resources.
MAX_DEMUCS_SOURCE_SIZE_BYTES = 256 * 1024 * 1024
LOCAL_UPLOADS_BUCKET = "clouddsp-uploads"

# The Job API writes one canonical type based on the approved source extension.
# The worker accepts that stored canonical set, then a later FFprobe step proves
# the bytes really contain audio; an S3 ContentType header is not codec proof.
SUPPORTED_SOURCE_CONTENT_TYPES = frozenset(
    {
        "audio/wav",
        "audio/mpeg",
        "audio/flac",
        "audio/mp4",
        "audio/aac",
        "audio/ogg",
        "audio/aiff",
        "audio/webm",
    }
)


class DemucsHeadObjectClient(Protocol):
    """The narrow S3 API surface required before Demucs reads source bytes."""

    def head_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        """Return S3-compatible headers/metadata without returning object bytes."""


class DemucsSourceStorageUnavailable(RuntimeError):
    """A retryable MinIO/client failure whose public text leaks no S3 details."""


class DemucsSourceStorageProtocolError(RuntimeError):
    """MinIO returned a malformed response that cannot establish source safety."""


class DemucsSourceVerificationFailureCode(StrEnum):
    """Bounded permanent categories for a verified-but-unsuitable source object."""

    OBJECT_MISSING = "source_object_missing"
    SIZE_LIMIT_EXCEEDED = "source_size_limit_exceeded"
    UNSUPPORTED_CONTENT_TYPE = "source_content_type_unsupported"
    METADATA_MISMATCH = "source_metadata_mismatch"


class DemucsPermanentSourceVerificationError(RuntimeError):
    """Carry a reviewed permanent category, never raw MinIO headers/errors."""

    def __init__(self, failure_code: DemucsSourceVerificationFailureCode) -> None:
        self.failure_code = failure_code
        super().__init__(failure_code.value)


@dataclass(frozen=True)
class VerifiedDemucsSourceObject:
    """The minimal evidence supplied to the later FFprobe/download boundary.

    This holds only already-authorized stable coordinates and headers. It never
    exposes an object body, browser URL, S3 key pair, owner identity, or raw
    metadata mapping to a later normal log statement.
    """

    bucket_name: str
    object_key: str
    content_type: str
    size_bytes: int


def _error_code(error: BaseException) -> str | None:
    """Read an S3-shaped error code without importing a particular SDK class."""

    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return None
    error_mapping = response.get("Error")
    if not isinstance(error_mapping, Mapping):
        return None
    code = error_mapping.get("Code")
    return code if isinstance(code, str) else None


def _head_object_or_raise(
    client: DemucsHeadObjectClient,
    *,
    bucket_name: str,
    object_key: str,
) -> Mapping[str, object]:
    """Perform one HeadObject call and split definite absence from an outage."""

    try:
        response = client.head_object(Bucket=bucket_name, Key=object_key)
    except Exception as error:  # SDK transport errors have vendor-specific classes.
        # A known 404 proves there is no source object for this retained task.
        # Any other failure may be a transient MinIO/DNS/auth/network problem
        # and must not be persisted as a false permanent media failure.
        if _error_code(error) in {"404", "NoSuchKey", "NoSuchObject", "NotFound"}:
            raise DemucsPermanentSourceVerificationError(
                DemucsSourceVerificationFailureCode.OBJECT_MISSING
            ) from error
        raise DemucsSourceStorageUnavailable("Demucs source HeadObject is unavailable.") from error
    if not isinstance(response, Mapping):
        raise DemucsSourceStorageProtocolError("MinIO returned an invalid Demucs HeadObject response.")
    return response


def _metadata_value(metadata: object, *, name: str) -> str:
    """Return exactly one case-insensitive MinIO user-metadata value.

    Boto3 exposes user metadata without an ``x-amz-meta-`` prefix, while key
    casing can differ by S3 implementation. Ambiguous duplicate spellings,
    missing values, controls, or non-text values are protocol failures rather
    than guesses about a potentially different job's audio object.
    """

    if not isinstance(metadata, Mapping):
        raise DemucsSourceStorageProtocolError("MinIO returned invalid Demucs source metadata.")
    matches: list[str] = []
    for raw_name, raw_value in metadata.items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            raise DemucsSourceStorageProtocolError("MinIO returned invalid Demucs source metadata.")
        if raw_name.lower() == name:
            matches.append(raw_value)
    if len(matches) != 1 or not matches[0] or "\x00" in matches[0]:
        raise DemucsSourceStorageProtocolError("MinIO returned invalid Demucs source metadata.")
    return matches[0]


def _content_length(response: Mapping[str, object]) -> int:
    """Validate storage-side source size before any byte download/model memory use."""

    length = response.get("ContentLength")
    if isinstance(length, bool) or not isinstance(length, int) or length < 1:
        raise DemucsSourceStorageProtocolError("MinIO returned an invalid Demucs ContentLength.")
    if length > MAX_DEMUCS_SOURCE_SIZE_BYTES:
        raise DemucsPermanentSourceVerificationError(
            DemucsSourceVerificationFailureCode.SIZE_LIMIT_EXCEEDED
        )
    return length


def _content_type(response: Mapping[str, object]) -> str:
    """Require a canonical approved audio content type before later FFprobe."""

    content_type = response.get("ContentType")
    if not isinstance(content_type, str) or not content_type or "\x00" in content_type:
        raise DemucsSourceStorageProtocolError("MinIO returned an invalid Demucs ContentType.")
    if content_type not in SUPPORTED_SOURCE_CONTENT_TYPES:
        raise DemucsPermanentSourceVerificationError(
            DemucsSourceVerificationFailureCode.UNSUPPORTED_CONTENT_TYPE
        )
    return content_type


def _validate_lease_source_identity(lease: DemucsTaskLease) -> None:
    """Repeat the private source-prefix boundary before making an S3 request."""

    if lease.input_bucket != LOCAL_UPLOADS_BUCKET:
        raise DemucsSourceStorageProtocolError("Demucs task uses an unexpected source bucket.")
    expected_prefix = f"uploads/{lease.job_id}/"
    filename = lease.input_object_key.removeprefix(expected_prefix)
    if (
        not lease.input_object_key.startswith(expected_prefix)
        or not filename
        or "/" in filename
    ):
        raise DemucsSourceStorageProtocolError("Demucs task uses an invalid source key.")


def verify_claimed_demucs_source_head_object(
    client: DemucsHeadObjectClient,
    *,
    lease: DemucsTaskLease,
) -> VerifiedDemucsSourceObject:
    """Verify one claimed private source using exactly one metadata-only S3 call.

    A successful return is evidence that MinIO currently stores the task's own
    source with the upload-time job/stem metadata, a nonzero allowed size, and
    canonical audio MIME type. It does **not** prove an audio stream/length;
    FFprobe owns that later check. It does **not** renew the lease, download,
    acknowledge AMQP, or mutate PostgreSQL.
    """

    if not isinstance(lease, DemucsTaskLease):
        raise TypeError("lease must be DemucsTaskLease.")
    _validate_lease_source_identity(lease)
    response = _head_object_or_raise(
        client,
        bucket_name=lease.input_bucket,
        object_key=lease.input_object_key,
    )
    size_bytes = _content_length(response)
    content_type = _content_type(response)
    metadata = response.get("Metadata")
    if _metadata_value(metadata, name="job-id") != lease.job_id:
        raise DemucsPermanentSourceVerificationError(
            DemucsSourceVerificationFailureCode.METADATA_MISMATCH
        )
    if _metadata_value(metadata, name="stem-mode") != lease.stem_mode:
        raise DemucsPermanentSourceVerificationError(
            DemucsSourceVerificationFailureCode.METADATA_MISMATCH
        )
    return VerifiedDemucsSourceObject(
        bucket_name=lease.input_bucket,
        object_key=lease.input_object_key,
        content_type=content_type,
        size_bytes=size_bytes,
    )
