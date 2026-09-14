"""Stream complete ADTOF output plans to private MinIO with byte proof.

This adapter receives the two complete local-file-to-object plans created by
``upload_object.py``. Before an S3 request it repeats each artifact validator
and hashes the regular local file. While MinIO consumes the request body it
calculates a second SHA-256. A receipt exists only when both independent local
checks and the bytes consumed by the S3-compatible client exactly match the
immutable upload plan.

The two writes are deliberately sequential rather than falsely described as a
cross-object transaction. If MIDI writes and tempo fails, a later safe retry
overwrites the same deterministic private MIDI key and then retries tempo. No
PostgreSQL result is changed here, so neither object becomes browser-visible or
a completed task until later stored-object verification and a guarded commit.
This module creates no client, lists/deletes no object, handles no RabbitMQ
delivery, invokes no model, and calls no Kubernetes API.
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

from app.adtof_requested_message import ADTOF_STEM_NAME, LOCAL_UPLOADS_BUCKET
from app.model_configuration import ADTOF_MODEL_CONFIGURATION_ID
from app.output_artifact import VerifiedADTOFOutputArtifact, verify_and_hash_adtof_output_artifact
from app.output_object_plan import (
    ADTOF_MIDI_CONTENT_TYPE,
    ADTOF_OUTPUT_METADATA_SCHEMA_VERSION,
    ADTOF_OUTPUT_PRODUCER,
    ADTOF_TEMPO_CONTENT_TYPE,
    ADTOFOutputArtifactKind,
)
from app.task_claim import ADTOF_STEM_MODES
from app.upload_object import ADTOFUploadObject, ADTOFUploadObjects


ADTOF_UPLOAD_READ_CHUNK_BYTES = 64 * 1024


class ADTOFPutObjectClient(Protocol):
    """The single S3-compatible operation used by the restricted ADTOF identity."""

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
        """Store one private object; a return alone is not upload evidence."""


class ADTOFUploadError(RuntimeError):
    """Base safe category for a reviewed ADTOF MinIO upload outcome."""


class ADTOFUploadContractError(ADTOFUploadError):
    """A direct object plan is outside the fixed private ADTOF scope."""


class ADTOFUploadPathError(ADTOFUploadError):
    """The temporary local file cannot be safely opened for a stream upload."""


class ADTOFUploadConsistencyError(ADTOFUploadError):
    """Current local bytes or client body consumption differs from planned proof."""


class ADTOFUploadUnavailable(ADTOFUploadError):
    """MinIO/S3 could not complete the bounded request without diagnostic leaks."""


@dataclass(frozen=True)
class UploadedADTOFObject:
    """Non-sensitive receipt for one client-consumed private upload request."""

    bucket: str
    object_key: str
    content_length: int
    sha256: str


@dataclass(frozen=True)
class UploadedADTOFObjects:
    """Receipts for both deterministic outputs after two successful client calls."""

    midi: UploadedADTOFObject
    tempo_candidate: UploadedADTOFObject


def _contract_error() -> ADTOFUploadContractError:
    """Return one non-sensitive category for invalid plans/metadata/evidence."""

    return ADTOFUploadContractError("ADTOF upload contract is invalid.")


def _canonical_uuid(value: object) -> str:
    """Require canonical durable UUID text before rebuilding a private object key."""

    if not isinstance(value, str):
        raise _contract_error()
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise _contract_error() from error
    if canonical != value:
        raise _contract_error()
    return canonical


def _metadata_mapping(value: object) -> dict[str, str]:
    """Convert an immutable tuple into an exact no-duplicate S3 metadata mapping."""

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


def _current_artifact(value: object) -> VerifiedADTOFOutputArtifact:
    """Repeat format/SHA proof so a stale scratch path cannot authorize an upload."""

    if not isinstance(value, VerifiedADTOFOutputArtifact):
        raise _contract_error()
    try:
        current = verify_and_hash_adtof_output_artifact(
            output_plan=value.output_plan,
            artifact_path=value.path,
        )
    except Exception as error:
        raise _contract_error() from error
    if current != value:
        raise _contract_error()
    return current


def _validated_upload_object(value: object) -> tuple[ADTOFUploadObject, dict[str, str]]:
    """Validate static MinIO scope, complete metadata, and current local evidence."""

    if not isinstance(value, ADTOFUploadObject):
        raise _contract_error()
    if (
        value.bucket != LOCAL_UPLOADS_BUCKET
        or not isinstance(value.local_path, Path)
        or type(value.content_length) is not int
        or value.content_length < 1
    ):
        raise _contract_error()
    metadata = _metadata_mapping(value.s3_metadata)
    required_names = {
        "schema-version",
        "producer",
        "job-id",
        "task-id",
        "request-event-id",
        "stem-name",
        "stem-mode",
        "artifact-kind",
        "input-stem-sha256",
        "model-config-id",
        "size-bytes",
        "sha256",
    }
    if set(metadata) != required_names:
        raise _contract_error()
    job_id = _canonical_uuid(metadata["job-id"])
    _canonical_uuid(metadata["task-id"])
    _canonical_uuid(metadata["request-event-id"])
    try:
        artifact_kind = ADTOFOutputArtifactKind(metadata["artifact-kind"])
    except (TypeError, ValueError) as error:
        raise _contract_error() from error
    expected_coordinates = {
        ADTOFOutputArtifactKind.MIDI: (f"midi/{job_id}/drums.mid", ADTOF_MIDI_CONTENT_TYPE),
        ADTOFOutputArtifactKind.TEMPO_CANDIDATE: (
            f"midi/{job_id}/drums_bpm.json",
            ADTOF_TEMPO_CONTENT_TYPE,
        ),
    }
    expected_key, expected_content_type = expected_coordinates[artifact_kind]
    if (
        metadata["schema-version"] != ADTOF_OUTPUT_METADATA_SCHEMA_VERSION
        or metadata["producer"] != ADTOF_OUTPUT_PRODUCER
        or metadata["stem-name"] != ADTOF_STEM_NAME
        or metadata["stem-mode"] not in ADTOF_STEM_MODES
        or metadata["model-config-id"] != ADTOF_MODEL_CONFIGURATION_ID
        or metadata["size-bytes"] != str(value.content_length)
        or not re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"])
        or not re.fullmatch(r"[0-9a-f]{64}", metadata["input-stem-sha256"])
        or value.object_key != expected_key
        or value.content_type != expected_content_type
    ):
        raise _contract_error()

    artifact = _current_artifact(value.artifact)
    expected_metadata = artifact.output_plan.base_s3_metadata + (
        ("size-bytes", str(artifact.size_bytes)),
        ("sha256", artifact.sha256),
    )
    if (
        artifact.output_plan.artifact_kind is not artifact_kind
        or artifact.path != value.local_path
        or artifact.size_bytes != value.content_length
        or artifact.sha256 != metadata["sha256"]
        or value.s3_metadata != expected_metadata
    ):
        raise _contract_error()
    return value, metadata


def _hash_current_regular_file(path: Path, *, expected_size: int) -> str:
    """Hash one current non-symlink local artifact before the upload begins."""

    try:
        if path.is_symlink():
            raise ADTOFUploadPathError("ADTOF upload path is invalid.")
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        with os.fdopen(descriptor, "rb", buffering=0) as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
                raise ADTOFUploadConsistencyError("ADTOF output changed before upload.")
            hasher = hashlib.sha256()
            bytes_read = 0
            while chunk := source.read(ADTOF_UPLOAD_READ_CHUNK_BYTES):
                hasher.update(chunk)
                bytes_read += len(chunk)
            after = os.fstat(source.fileno())
    except ADTOFUploadError:
        raise
    except OSError as error:
        raise ADTOFUploadPathError("ADTOF upload path is invalid.") from error
    if bytes_read != expected_size or after.st_size != expected_size:
        raise ADTOFUploadConsistencyError("ADTOF output changed before upload.")
    return hasher.hexdigest()


class _HashingUploadBody:
    """File-like S3 request body that records exactly what the client consumes."""

    def __init__(self, source: object) -> None:
        self._source = source
        self._hasher = hashlib.sha256()
        self._bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        """Read/hash bytes while preserving ordinary file-like ``read`` behavior."""

        if type(size) is not int:
            raise ADTOFUploadConsistencyError("ADTOF upload body is invalid.")
        try:
            chunk = self._source.read(size)
        except OSError as error:
            raise ADTOFUploadPathError("ADTOF upload path is invalid.") from error
        if not isinstance(chunk, bytes):
            raise ADTOFUploadConsistencyError("ADTOF upload body is invalid.")
        self._hasher.update(chunk)
        self._bytes_read += len(chunk)
        return chunk

    def tell(self) -> int:
        """Expose the body position required by an S3 SDK retry implementation."""

        try:
            position = self._source.tell()
        except OSError as error:
            raise ADTOFUploadPathError("ADTOF upload path is invalid.") from error
        if type(position) is not int:
            raise ADTOFUploadConsistencyError("ADTOF upload body is invalid.")
        return position

    def seek(self, offset: int, whence: int = 0) -> int:
        """Allow only a whole-request rewind and reset evidence for an SDK retry."""

        if type(offset) is not int or type(whence) is not int:
            raise ADTOFUploadConsistencyError("ADTOF upload body is invalid.")
        try:
            position = self._source.seek(offset, whence)
        except OSError as error:
            raise ADTOFUploadPathError("ADTOF upload path is invalid.") from error
        if position != 0:
            raise ADTOFUploadConsistencyError("ADTOF upload body is invalid.")
        self._hasher = hashlib.sha256()
        self._bytes_read = 0
        return position

    def completed_evidence(self) -> tuple[int, str]:
        """Return final request consumption evidence after ``put_object`` returns."""

        return self._bytes_read, self._hasher.hexdigest()


def upload_adtof_object(
    *,
    client: ADTOFPutObjectClient,
    upload_object: ADTOFUploadObject,
) -> UploadedADTOFObject:
    """Upload exactly one revalidated ADTOF artifact and return a stable receipt."""

    if not callable(getattr(client, "put_object", None)):
        raise TypeError("client must provide put_object.")
    plan, metadata = _validated_upload_object(upload_object)
    initial_sha256 = _hash_current_regular_file(plan.local_path, expected_size=plan.content_length)
    if initial_sha256 != metadata["sha256"]:
        raise ADTOFUploadConsistencyError("ADTOF output changed before upload.")

    try:
        if plan.local_path.is_symlink():
            raise ADTOFUploadPathError("ADTOF upload path is invalid.")
        descriptor = os.open(
            plan.local_path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        with os.fdopen(descriptor, "rb", buffering=0) as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != plan.content_length:
                raise ADTOFUploadConsistencyError("ADTOF output changed before upload.")
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
    except ADTOFUploadError:
        raise
    except OSError as error:
        raise ADTOFUploadPathError("ADTOF upload path is invalid.") from error
    except Exception as error:
        # S3/MinIO clients can expose endpoint/key/retry details. Keep a
        # bounded retryable category at the worker boundary instead.
        raise ADTOFUploadUnavailable("ADTOF MinIO upload is unavailable.") from error

    bytes_uploaded, uploaded_sha256 = body.completed_evidence()
    if (
        after.st_size != plan.content_length
        or bytes_uploaded != plan.content_length
        or uploaded_sha256 != metadata["sha256"]
    ):
        raise ADTOFUploadConsistencyError("ADTOF output changed during upload.")
    return UploadedADTOFObject(
        bucket=plan.bucket,
        object_key=plan.object_key,
        content_length=plan.content_length,
        sha256=uploaded_sha256,
    )


def upload_adtof_objects(
    *,
    client: ADTOFPutObjectClient,
    upload_objects: ADTOFUploadObjects,
) -> UploadedADTOFObjects:
    """Sequentially upload the fixed MIDI/tempo pair after both plans validate.

    Both plans are validated before the first put. This avoids writing MIDI for
    a malformed tempo plan. A transport failure after the first successful put
    remains a non-atomic, retryable condition described in the module header.
    """

    if not isinstance(upload_objects, ADTOFUploadObjects):
        raise _contract_error()
    midi_plan, _ = _validated_upload_object(upload_objects.midi)
    tempo_plan, _ = _validated_upload_object(upload_objects.tempo_candidate)
    if (
        midi_plan.artifact.output_plan.artifact_kind is not ADTOFOutputArtifactKind.MIDI
        or tempo_plan.artifact.output_plan.artifact_kind is not ADTOFOutputArtifactKind.TEMPO_CANDIDATE
    ):
        raise _contract_error()
    return UploadedADTOFObjects(
        midi=upload_adtof_object(client=client, upload_object=midi_plan),
        tempo_candidate=upload_adtof_object(client=client, upload_object=tempo_plan),
    )
