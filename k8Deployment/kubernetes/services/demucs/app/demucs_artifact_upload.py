"""Stream one reviewed Demucs stem plan to private MinIO with byte evidence.

The preceding output-object contract determines the only bucket/key/metadata
combination this adapter may receive.  This module gives a later worker runtime
one narrow S3 ``PutObject`` operation: it checks that plan again, hashes the
current regular local WAV before transmission, streams it as the request body
while hashing a second time, and reports success only after MinIO consumed the
planned byte count and SHA-256 digest.

It intentionally does not create a Boto3 client, scan a bucket, delete a
partial object, update PostgreSQL, renew a lease, publish/acknowledge RabbitMQ,
run Demucs, build an image, or apply a Kubernetes resource.  A later result
transaction decides what a successfully uploaded complete stem set means.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.demucs_artifact_hash import DEMUCS_ARTIFACT_HASH_CHUNK_BYTES
from app.demucs_artifacts import DEMUCS_STEM_FILE_EXTENSION, DEMUCS_STEMS_BY_MODE
from app.demucs_output_object import (
    DEMUCS_ARTIFACT_CONTENT_TYPE,
    DEMUCS_ARTIFACT_KEY_PREFIX,
    DEMUCS_ARTIFACT_PRODUCER,
    DEMUCS_ARTIFACT_SCHEMA_VERSION,
    LOCAL_DEMUCS_ARTIFACT_BUCKET,
    DemucsStemOutputObject,
)


class DemucsPutObjectClient(Protocol):
    """The small Boto3-compatible surface needed for one private write."""

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: object,
        ContentLength: int,
        ContentType: str,
        Metadata: dict[str, str],
    ) -> object:
        """Stream one S3 object; success means the request completed without error."""


class DemucsArtifactUploadError(RuntimeError):
    """Base safe category for a planned Demucs stem upload failure."""


class DemucsArtifactUploadContractError(DemucsArtifactUploadError):
    """A caller-built object plan is inconsistent or outside the reviewed scope."""


class DemucsArtifactUploadPathError(DemucsArtifactUploadError):
    """The planned local stem cannot be read as a regular file."""


class DemucsArtifactUploadConsistencyError(DemucsArtifactUploadError):
    """The local bytes or client consumption differs from the planned evidence."""


class DemucsArtifactUploadUnavailable(DemucsArtifactUploadError):
    """MinIO/S3 could not complete the bounded private PutObject request."""


@dataclass(frozen=True)
class UploadedDemucsStemObject:
    """Durable-safe upload evidence for a later guarded PostgreSQL transaction.

    The receipt intentionally contains no Pod-local path, mounted credential,
    raw SDK response, or ETag.  An ETag is not a portable SHA-256 proof across
    S3 multipart implementations; the reviewed SHA-256 metadata is the stable
    artifact identity instead.
    """

    bucket: str
    object_key: str
    content_length: int
    sha256: str


def _contract_error() -> DemucsArtifactUploadContractError:
    """Use one non-sensitive error category for all invalid object plans."""

    return DemucsArtifactUploadContractError("Demucs artifact upload contract is invalid.")


def _canonical_uuid(value: object) -> str:
    """Require canonical lower-case UUID text before it participates in a key."""

    if not isinstance(value, str):
        raise _contract_error()
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise _contract_error() from error
    if canonical != value:
        raise _contract_error()
    return canonical


def _exact_metadata(value: object) -> dict[str, str]:
    """Turn the immutable metadata tuple into one exact no-duplicate mapping."""

    if not isinstance(value, tuple):
        raise _contract_error()
    metadata: dict[str, str] = {}
    for entry in value:
        if (
            not isinstance(entry, tuple)
            or len(entry) != 2
            or not isinstance(entry[0], str)
            or not isinstance(entry[1], str)
            or entry[0] in metadata
        ):
            raise _contract_error()
        metadata[entry[0]] = entry[1]
    return metadata


def _validated_output_object(plan: object) -> tuple[DemucsStemOutputObject, dict[str, str]]:
    """Require self-consistent fixed S3 coordinates and metadata before file I/O."""

    if not isinstance(plan, DemucsStemOutputObject):
        raise _contract_error()
    if (
        plan.bucket != LOCAL_DEMUCS_ARTIFACT_BUCKET
        or plan.content_type != DEMUCS_ARTIFACT_CONTENT_TYPE
        or type(plan.content_length) is not int
        or plan.content_length < 1
        or not isinstance(plan.local_path, Path)
    ):
        raise _contract_error()
    metadata = _exact_metadata(plan.s3_metadata)
    required_metadata_names = {
        "schema-version",
        "producer",
        "job-id",
        "task-id",
        "stem-name",
        "stem-mode",
        "size-bytes",
        "sha256",
    }
    if set(metadata) != required_metadata_names:
        raise _contract_error()
    if (
        metadata["schema-version"] != DEMUCS_ARTIFACT_SCHEMA_VERSION
        or metadata["producer"] != DEMUCS_ARTIFACT_PRODUCER
        or metadata["size-bytes"] != str(plan.content_length)
        or not re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"])
    ):
        raise _contract_error()
    job_id = _canonical_uuid(metadata["job-id"])
    _canonical_uuid(metadata["task-id"])
    stem_mode = metadata["stem-mode"]
    if stem_mode not in DEMUCS_STEMS_BY_MODE:
        raise _contract_error()
    stem_name = metadata["stem-name"]
    if stem_name not in DEMUCS_STEMS_BY_MODE[stem_mode][1]:
        raise _contract_error()
    expected_key = f"{DEMUCS_ARTIFACT_KEY_PREFIX}/{job_id}/{stem_name}{DEMUCS_STEM_FILE_EXTENSION}"
    if plan.object_key != expected_key:
        raise _contract_error()
    return plan, metadata


def _hash_current_regular_file(path: Path, *, expected_size: int) -> str:
    """Hash a current regular file without retaining its contents in memory."""

    try:
        if path.is_symlink():
            raise DemucsArtifactUploadPathError("Demucs artifact upload path is invalid.")
        with path.open("rb", buffering=0) as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
                raise DemucsArtifactUploadConsistencyError(
                    "Demucs artifact changed before upload."
                )
            hasher = hashlib.sha256()
            bytes_read = 0
            while chunk := source.read(DEMUCS_ARTIFACT_HASH_CHUNK_BYTES):
                hasher.update(chunk)
                bytes_read += len(chunk)
            after = os.fstat(source.fileno())
    except DemucsArtifactUploadError:
        raise
    except OSError as error:
        raise DemucsArtifactUploadPathError("Demucs artifact upload path is invalid.") from error
    if bytes_read != expected_size or after.st_size != expected_size:
        raise DemucsArtifactUploadConsistencyError("Demucs artifact changed before upload.")
    return hasher.hexdigest()


class _HashingUploadBody:
    """File-like S3 body that records exactly what a client consumed.

    Boto3 may rewind a file body to retry a request or calculate an HTTP
    checksum.  Rewinding to byte zero resets the local evidence, so the final
    completed request still has one full-byte-count/SHA-256 result.  Seeking to
    any other coordinate is rejected because it would make the proof ambiguous.
    """

    def __init__(self, source: object) -> None:
        self._source = source
        self._hasher = hashlib.sha256()
        self._bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        """Read and hash the next body bytes requested by the S3 client."""

        try:
            chunk = self._source.read(size)
        except OSError as error:
            raise DemucsArtifactUploadPathError("Demucs artifact upload path is invalid.") from error
        if not isinstance(chunk, bytes):
            raise DemucsArtifactUploadConsistencyError("Demucs artifact upload body is invalid.")
        self._hasher.update(chunk)
        self._bytes_read += len(chunk)
        return chunk

    def tell(self) -> int:
        """Expose the current file position required by some Boto3 retry paths."""

        try:
            position = self._source.tell()
        except OSError as error:
            raise DemucsArtifactUploadPathError("Demucs artifact upload path is invalid.") from error
        if type(position) is not int:
            raise DemucsArtifactUploadConsistencyError("Demucs artifact upload body is invalid.")
        return position

    def seek(self, offset: int, whence: int = 0) -> int:
        """Permit only a rewind to the start, resetting evidence for a retry."""

        try:
            position = self._source.seek(offset, whence)
        except OSError as error:
            raise DemucsArtifactUploadPathError("Demucs artifact upload path is invalid.") from error
        if position != 0:
            raise DemucsArtifactUploadConsistencyError("Demucs artifact upload body is invalid.")
        self._hasher = hashlib.sha256()
        self._bytes_read = 0
        return position

    def completed_evidence(self) -> tuple[int, str]:
        """Return body evidence only after the client has finished its request."""

        return self._bytes_read, self._hasher.hexdigest()


def upload_demucs_stem_object(
    *,
    client: DemucsPutObjectClient,
    output_object: DemucsStemOutputObject,
) -> UploadedDemucsStemObject:
    """Put exactly one verified Demucs stem to its deterministic private key.

    The first local hash blocks a file changed since the output plan.  The
    second hash occurs as the Boto3-compatible client consumes its request
    body; a success return is trusted only if it read precisely the planned
    bytes and digest.  A future caller may retry a safe unavailable/consistency
    category, but must not write PostgreSQL success state from this adapter.
    """

    # Runtime-checkable Protocols are intentionally avoided: an SDK client is
    # structural, and this explicit method check keeps no Boto3 implementation
    # type in the source boundary while still rejecting an absent client method.
    if not callable(getattr(client, "put_object", None)):
        raise TypeError("client must provide put_object.")
    plan, metadata = _validated_output_object(output_object)
    initial_sha256 = _hash_current_regular_file(
        plan.local_path,
        expected_size=plan.content_length,
    )
    if initial_sha256 != metadata["sha256"]:
        raise DemucsArtifactUploadConsistencyError("Demucs artifact changed before upload.")

    try:
        if plan.local_path.is_symlink():
            raise DemucsArtifactUploadPathError("Demucs artifact upload path is invalid.")
        with plan.local_path.open("rb", buffering=0) as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != plan.content_length:
                raise DemucsArtifactUploadConsistencyError("Demucs artifact changed before upload.")
            body = _HashingUploadBody(source)
            client.put_object(
                Bucket=plan.bucket,
                Key=plan.object_key,
                Body=body,
                ContentLength=plan.content_length,
                ContentType=plan.content_type,
                Metadata=metadata,
            )
            after = os.fstat(source.fileno())
    except DemucsArtifactUploadError:
        raise
    except OSError as error:
        raise DemucsArtifactUploadPathError("Demucs artifact upload path is invalid.") from error
    except Exception as error:
        # Preserve only the bounded category at the worker boundary.  SDK
        # diagnostics can contain endpoints, object keys, or retry internals.
        raise DemucsArtifactUploadUnavailable("Demucs artifact upload is unavailable.") from error

    bytes_uploaded, uploaded_sha256 = body.completed_evidence()
    if (
        after.st_size != plan.content_length
        or bytes_uploaded != plan.content_length
        or uploaded_sha256 != metadata["sha256"]
    ):
        raise DemucsArtifactUploadConsistencyError("Demucs artifact changed during upload.")
    return UploadedDemucsStemObject(
        bucket=plan.bucket,
        object_key=plan.object_key,
        content_length=plan.content_length,
        sha256=uploaded_sha256,
    )
