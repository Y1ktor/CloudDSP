"""Stream one reviewed Basic Pitch MIDI plan to private MinIO with byte proof.

The preceding :mod:`app.artifacts.midi_output_object` contract is the sole source of the
bucket, key, content type, metadata, and ephemeral local path. This adapter
revalidates that plan, repeats the local Standard MIDI File/hash proof, then
streams the file through one Boto3-compatible ``PutObject`` request while
calculating a second digest. It returns a receipt only when the client consumed
the exact planned byte count and SHA-256 value.

It does not construct a Boto3 client, list/delete objects, update PostgreSQL,
renew or complete a task, acknowledge RabbitMQ, run Basic Pitch, or call the
Kubernetes API. A later result boundary must verify the stored object and make
the lease-token-guarded completion decision.
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

from app.messaging.basic_pitch_requested_message import BASIC_PITCH_STEM_NAMES, LOCAL_UPLOADS_BUCKET, MIDI_CONTENT_TYPE
from app.artifacts.midi_artifact import VerifiedBasicPitchMidiArtifact, verify_and_hash_basic_pitch_midi
from app.artifacts.midi_output_object import (
    BASIC_PITCH_MIDI_METADATA_SCHEMA_VERSION,
    BASIC_PITCH_MIDI_PRODUCER,
    BasicPitchMidiOutputObject,
)
from app.db.task_lease import BASIC_PITCH_STEMS_BY_MODE


# The local MIDI verifier and request parser already impose their own bounds.
# This second, fixed streaming buffer keeps the upload request itself memory
# bounded even when Boto3 asks for a large read.
BASIC_PITCH_MIDI_UPLOAD_READ_CHUNK_BYTES = 64 * 1024


class BasicPitchMidiPutObjectClient(Protocol):
    """The one S3-compatible method required to upload a private MIDI object."""

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
        """Write one object; successful return means the request completed."""


class BasicPitchMidiUploadError(RuntimeError):
    """Base safe category for a reviewed Basic Pitch MIDI upload failure."""


class BasicPitchMidiUploadContractError(BasicPitchMidiUploadError):
    """A caller-built output plan is outside the reviewed private scope."""


class BasicPitchMidiUploadPathError(BasicPitchMidiUploadError):
    """The plan's ephemeral MIDI path cannot be opened as a regular file."""


class BasicPitchMidiUploadConsistencyError(BasicPitchMidiUploadError):
    """Current local bytes or client consumption differs from planned evidence."""


class BasicPitchMidiUploadUnavailable(BasicPitchMidiUploadError):
    """MinIO/S3 could not complete the bounded private PutObject request."""


@dataclass(frozen=True)
class UploadedBasicPitchMidiObject:
    """Non-sensitive upload evidence for a later guarded completion transition.

    An S3 ETag is deliberately omitted: it is not portable SHA-256 proof for
    multipart-compatible implementations. The exact streaming digest and the
    future ``HeadObject`` verification provide the stable evidence instead.
    """

    bucket: str
    object_key: str
    content_length: int
    sha256: str


def _contract_error() -> BasicPitchMidiUploadContractError:
    """Return one non-sensitive category for all invalid object-plan shapes."""

    return BasicPitchMidiUploadContractError("Basic Pitch MIDI upload contract is invalid.")


def _canonical_uuid(value: object) -> str:
    """Require canonical lower-case UUID metadata before it participates in a key."""

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
    """Convert the immutable no-duplicate metadata tuple into an S3 mapping."""

    if not isinstance(value, tuple):
        raise _contract_error()
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
            raise _contract_error()
        metadata[entry[0]] = entry[1]
    return metadata


def _current_artifact(value: object) -> VerifiedBasicPitchMidiArtifact:
    """Repeat framing/hash proof so an old result cannot authorize new local bytes."""

    if not isinstance(value, VerifiedBasicPitchMidiArtifact):
        raise _contract_error()
    try:
        current = verify_and_hash_basic_pitch_midi(value.inference)
    except Exception as error:
        raise _contract_error() from error
    if current != value:
        raise _contract_error()
    return current


def _validated_output_object(plan: object) -> tuple[BasicPitchMidiOutputObject, dict[str, str]]:
    """Require exact static scope/metadata and fresh local MIDI evidence before I/O."""

    if not isinstance(plan, BasicPitchMidiOutputObject):
        raise _contract_error()
    if (
        plan.bucket != LOCAL_UPLOADS_BUCKET
        or plan.content_type != MIDI_CONTENT_TYPE
        or type(plan.content_length) is not int
        or plan.content_length < 1
        or not isinstance(plan.local_path, Path)
    ):
        raise _contract_error()
    metadata = _exact_metadata(plan.s3_metadata)
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
        raise _contract_error()
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
        or metadata["size-bytes"] != str(plan.content_length)
        or not re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"])
        or not re.fullmatch(r"[0-9a-f]{64}", metadata["input-stem-sha256"])
        or plan.object_key != f"midi/{job_id}/{stem_name}.mid"
    ):
        raise _contract_error()
    artifact = _current_artifact(plan.artifact)
    if (
        artifact.path != plan.local_path
        or artifact.size_bytes != plan.content_length
        or artifact.sha256 != metadata["sha256"]
        or artifact.content_type != plan.content_type
    ):
        raise _contract_error()
    return plan, metadata


def _hash_current_regular_file(path: Path, *, expected_size: int) -> str:
    """Hash one current regular MIDI file before an upload request begins."""

    try:
        if path.is_symlink():
            raise BasicPitchMidiUploadPathError("Basic Pitch MIDI upload path is invalid.")
        with path.open("rb", buffering=0) as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
                raise BasicPitchMidiUploadConsistencyError("Basic Pitch MIDI changed before upload.")
            hasher = hashlib.sha256()
            bytes_read = 0
            while chunk := source.read(BASIC_PITCH_MIDI_UPLOAD_READ_CHUNK_BYTES):
                hasher.update(chunk)
                bytes_read += len(chunk)
            after = os.fstat(source.fileno())
    except BasicPitchMidiUploadError:
        raise
    except OSError as error:
        raise BasicPitchMidiUploadPathError("Basic Pitch MIDI upload path is invalid.") from error
    if bytes_read != expected_size or after.st_size != expected_size:
        raise BasicPitchMidiUploadConsistencyError("Basic Pitch MIDI changed before upload.")
    return hasher.hexdigest()


class _HashingUploadBody:
    """File-like request body that records exactly what a Boto3-like client reads."""

    def __init__(self, source: object) -> None:
        self._source = source
        self._hasher = hashlib.sha256()
        self._bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        """Read/hash bytes while preserving the ordinary file-like ``read`` contract.

        An SDK may legitimately call ``read()`` without a size and expect the
        remaining body, so this method must not silently turn that request into
        a partial read. The prior MIDI verifier caps the complete file at
        16 MiB; explicit-size calls retain the SDK's requested chunk size.
        """

        if type(size) is not int:
            raise BasicPitchMidiUploadConsistencyError("Basic Pitch MIDI upload body is invalid.")
        try:
            chunk = self._source.read(size)
        except OSError as error:
            raise BasicPitchMidiUploadPathError("Basic Pitch MIDI upload path is invalid.") from error
        if not isinstance(chunk, bytes):
            raise BasicPitchMidiUploadConsistencyError("Basic Pitch MIDI upload body is invalid.")
        self._hasher.update(chunk)
        self._bytes_read += len(chunk)
        return chunk

    def tell(self) -> int:
        """Expose current offset for Boto3 retry/checksum behavior."""

        try:
            position = self._source.tell()
        except OSError as error:
            raise BasicPitchMidiUploadPathError("Basic Pitch MIDI upload path is invalid.") from error
        if type(position) is not int:
            raise BasicPitchMidiUploadConsistencyError("Basic Pitch MIDI upload body is invalid.")
        return position

    def seek(self, offset: int, whence: int = 0) -> int:
        """Allow only a full rewind, resetting body evidence for an SDK retry."""

        if type(offset) is not int or type(whence) is not int:
            raise BasicPitchMidiUploadConsistencyError("Basic Pitch MIDI upload body is invalid.")
        try:
            position = self._source.seek(offset, whence)
        except OSError as error:
            raise BasicPitchMidiUploadPathError("Basic Pitch MIDI upload path is invalid.") from error
        if position != 0:
            raise BasicPitchMidiUploadConsistencyError("Basic Pitch MIDI upload body is invalid.")
        self._hasher = hashlib.sha256()
        self._bytes_read = 0
        return position

    def completed_evidence(self) -> tuple[int, str]:
        """Return only final-request consumption evidence after a client returns."""

        return self._bytes_read, self._hasher.hexdigest()


def upload_basic_pitch_midi_object(
    *,
    client: BasicPitchMidiPutObjectClient,
    output_object: BasicPitchMidiOutputObject,
) -> UploadedBasicPitchMidiObject:
    """Put exactly one reviewed private MIDI file and return only stable receipt evidence.

    The first local hash detects a byte change since object-plan construction.
    The second hash is calculated as the client consumes the body. A successful
    SDK return is insufficient by itself: complete byte and digest agreement is
    required before the caller can proceed to a later MinIO verification and
    PostgreSQL completion boundary.
    """

    if not callable(getattr(client, "put_object", None)):
        raise TypeError("client must provide put_object.")
    plan, metadata = _validated_output_object(output_object)
    initial_sha256 = _hash_current_regular_file(plan.local_path, expected_size=plan.content_length)
    if initial_sha256 != metadata["sha256"]:
        raise BasicPitchMidiUploadConsistencyError("Basic Pitch MIDI changed before upload.")

    try:
        if plan.local_path.is_symlink():
            raise BasicPitchMidiUploadPathError("Basic Pitch MIDI upload path is invalid.")
        with plan.local_path.open("rb", buffering=0) as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != plan.content_length:
                raise BasicPitchMidiUploadConsistencyError("Basic Pitch MIDI changed before upload.")
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
    except BasicPitchMidiUploadError:
        raise
    except OSError as error:
        raise BasicPitchMidiUploadPathError("Basic Pitch MIDI upload path is invalid.") from error
    except Exception as error:
        # Boto3/MinIO errors can contain endpoints, object keys, and SDK retry
        # internals. Preserve only a bounded retryable category at the worker.
        raise BasicPitchMidiUploadUnavailable("Basic Pitch MIDI upload is unavailable.") from error

    bytes_uploaded, uploaded_sha256 = body.completed_evidence()
    if (
        after.st_size != plan.content_length
        or bytes_uploaded != plan.content_length
        or uploaded_sha256 != metadata["sha256"]
    ):
        raise BasicPitchMidiUploadConsistencyError("Basic Pitch MIDI changed during upload.")
    return UploadedBasicPitchMidiObject(
        bucket=plan.bucket,
        object_key=plan.object_key,
        content_length=plan.content_length,
        sha256=uploaded_sha256,
    )
