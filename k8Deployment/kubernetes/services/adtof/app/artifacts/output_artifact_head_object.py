"""Verify MinIO stored ADTOF's complete uploaded output pair.

The preceding uploader proves which local bytes its S3-compatible client
consumed. This boundary performs metadata-only ``HeadObject`` requests for the
deterministic drum MIDI and tempo-candidate JSON keys. Each current stored
object must agree with its immutable upload plan and upload receipt: bucket,
key, content type, byte count, output SHA-256, and all provenance metadata.

The two objects are verified as one matching task pair. In particular, the
Job, task, request-event, input-stem digest, model configuration, and stem
mode must match across MIDI and tempo evidence; independently valid objects
from different tasks cannot be mixed into a completion result.

This module does not read output bytes, construct a MinIO client, re-upload or
delete objects, mutate PostgreSQL, acknowledge RabbitMQ, invoke ADTOF, or use
the Kubernetes API. A later lease-token-guarded completion boundary owns every
durable state change.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.messaging.adtof_requested_message import ADTOF_STEM_NAME, LOCAL_UPLOADS_BUCKET
from app.artifacts.minio_upload import UploadedADTOFObject, UploadedADTOFObjects
from app.processing.model_configuration import ADTOF_MODEL_CONFIGURATION_ID
from app.artifacts.output_artifact import VerifiedADTOFOutputArtifact
from app.artifacts.output_object_plan import (
    ADTOF_MIDI_CONTENT_TYPE,
    ADTOF_OUTPUT_METADATA_SCHEMA_VERSION,
    ADTOF_OUTPUT_PRODUCER,
    ADTOF_TEMPO_CONTENT_TYPE,
    ADTOFOutputArtifactKind,
)
from app.db.task_claim import ADTOF_STEM_MODES
from app.artifacts.upload_object import ADTOFUploadObject, ADTOFUploadObjects


class ADTOFOutputHeadObjectClient(Protocol):
    """The one S3-compatible operation needed to prove stored ADTOF outputs."""

    def head_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        """Return current headers/user metadata without reading an object body."""


class ADTOFOutputHeadObjectUnavailable(RuntimeError):
    """A retryable MinIO/client failure with no endpoint or SDK detail leak."""


class ADTOFOutputHeadObjectProtocolError(RuntimeError):
    """A plan/receipt/response is malformed before a durable decision is possible."""


class ADTOFOutputHeadObjectFailureCode(StrEnum):
    """Bounded permanent stored-object disagreement categories for later policy."""

    OBJECT_MISSING = "adtof_output_object_missing"
    SIZE_MISMATCH = "adtof_output_object_size_mismatch"
    CONTENT_TYPE_MISMATCH = "adtof_output_object_content_type_mismatch"
    METADATA_MISMATCH = "adtof_output_object_metadata_mismatch"


class ADTOFPermanentOutputHeadObjectError(RuntimeError):
    """Carry one reviewed mismatch code rather than raw private MinIO details."""

    def __init__(self, failure_code: ADTOFOutputHeadObjectFailureCode) -> None:
        self.failure_code = failure_code
        super().__init__(failure_code.value)


@dataclass(frozen=True)
class VerifiedStoredADTOFObject:
    """Stable stored evidence for one later guarded completion statement.

    This value deliberately omits local scratch paths, MinIO response objects,
    ETags, credential values, and raw metadata. The exact type/size/digest
    remain because the later completion statement must bind durable results to
    the same private bytes that this HeadObject boundary just observed.
    """

    bucket: str
    object_key: str
    content_type: str
    content_length: int
    sha256: str


@dataclass(frozen=True)
class VerifiedStoredADTOFObjects:
    """The matching stored drum-MIDI and tempo-candidate evidence pair."""

    midi: VerifiedStoredADTOFObject
    tempo_candidate: VerifiedStoredADTOFObject


def _protocol_error() -> ADTOFOutputHeadObjectProtocolError:
    """Return one non-sensitive category for invalid evidence shapes."""

    return ADTOFOutputHeadObjectProtocolError("ADTOF output HeadObject evidence is invalid.")


def _canonical_uuid(value: object) -> str:
    """Require canonical lower-case durable UUID text before deriving a key."""

    if not isinstance(value, str):
        raise _protocol_error()
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise _protocol_error() from error
    if canonical != value:
        raise _protocol_error()
    return canonical


def _metadata_mapping(value: object) -> dict[str, str]:
    """Read a frozen metadata tuple as an exact, duplicate-free mapping."""

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


def _validated_object_and_receipt(
    *,
    upload_object: object,
    upload_receipt: object,
) -> tuple[ADTOFUploadObject, UploadedADTOFObject, dict[str, str], ADTOFOutputArtifactKind]:
    """Require one plan/receipt to describe exactly one fixed private result."""

    if not isinstance(upload_object, ADTOFUploadObject) or not isinstance(upload_receipt, UploadedADTOFObject):
        raise _protocol_error()
    if (
        upload_object.bucket != LOCAL_UPLOADS_BUCKET
        or not isinstance(upload_object.local_path, Path)
        or type(upload_object.content_length) is not int
        or upload_object.content_length < 1
        or upload_receipt.bucket != upload_object.bucket
        or upload_receipt.object_key != upload_object.object_key
        or upload_receipt.content_length != upload_object.content_length
        or not isinstance(upload_receipt.sha256, str)
    ):
        raise _protocol_error()
    metadata = _metadata_mapping(upload_object.s3_metadata)
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
        raise _protocol_error()
    job_id = _canonical_uuid(metadata["job-id"])
    _canonical_uuid(metadata["task-id"])
    _canonical_uuid(metadata["request-event-id"])
    try:
        artifact_kind = ADTOFOutputArtifactKind(metadata["artifact-kind"])
    except (TypeError, ValueError) as error:
        raise _protocol_error() from error
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
        or metadata["size-bytes"] != str(upload_object.content_length)
        or not re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"])
        or not re.fullmatch(r"[0-9a-f]{64}", metadata["input-stem-sha256"])
        or upload_object.object_key != expected_key
        or upload_object.content_type != expected_content_type
        or upload_receipt.sha256 != metadata["sha256"]
    ):
        raise _protocol_error()
    artifact = upload_object.artifact
    if (
        not isinstance(artifact, VerifiedADTOFOutputArtifact)
        or artifact.output_plan.artifact_kind is not artifact_kind
        or artifact.path != upload_object.local_path
        or artifact.size_bytes != upload_object.content_length
        or artifact.sha256 != upload_receipt.sha256
    ):
        raise _protocol_error()
    return upload_object, upload_receipt, metadata, artifact_kind


def _validated_pair(
    *,
    upload_objects: object,
    upload_receipts: object,
) -> tuple[
    tuple[ADTOFUploadObject, UploadedADTOFObject, dict[str, str]],
    tuple[ADTOFUploadObject, UploadedADTOFObject, dict[str, str]],
]:
    """Ensure the two independently stored artifacts are one ADTOF task pair."""

    if not isinstance(upload_objects, ADTOFUploadObjects) or not isinstance(upload_receipts, UploadedADTOFObjects):
        raise _protocol_error()
    midi_plan, midi_receipt, midi_metadata, midi_kind = _validated_object_and_receipt(
        upload_object=upload_objects.midi,
        upload_receipt=upload_receipts.midi,
    )
    tempo_plan, tempo_receipt, tempo_metadata, tempo_kind = _validated_object_and_receipt(
        upload_object=upload_objects.tempo_candidate,
        upload_receipt=upload_receipts.tempo_candidate,
    )
    if (
        midi_kind is not ADTOFOutputArtifactKind.MIDI
        or tempo_kind is not ADTOFOutputArtifactKind.TEMPO_CANDIDATE
    ):
        raise _protocol_error()
    # The artifacts legitimately differ in kind, key, size, and output digest.
    # Every remaining provenance value identifies the same one ADTOF task.
    shared_metadata_names = (
        "schema-version",
        "producer",
        "job-id",
        "task-id",
        "request-event-id",
        "stem-name",
        "stem-mode",
        "input-stem-sha256",
        "model-config-id",
    )
    if any(midi_metadata[name] != tempo_metadata[name] for name in shared_metadata_names):
        raise _protocol_error()
    return (
        (midi_plan, midi_receipt, midi_metadata),
        (tempo_plan, tempo_receipt, tempo_metadata),
    )


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
    client: ADTOFOutputHeadObjectClient,
    *,
    bucket: str,
    object_key: str,
) -> Mapping[str, object]:
    """Perform one HeadObject request and separate absence from an outage."""

    try:
        response = client.head_object(Bucket=bucket, Key=object_key)
    except Exception as error:  # S3 SDK exception classes are implementation-specific.
        if _error_code(error) in {"404", "NoSuchKey", "NoSuchObject", "NotFound"}:
            raise ADTOFPermanentOutputHeadObjectError(
                ADTOFOutputHeadObjectFailureCode.OBJECT_MISSING
            ) from error
        raise ADTOFOutputHeadObjectUnavailable("ADTOF output HeadObject is unavailable.") from error
    if not isinstance(response, Mapping):
        raise _protocol_error()
    return response


def _content_length(response: Mapping[str, object], *, expected: int) -> int:
    """Require current stored bytes to match the immediately preceding receipt."""

    value = response.get("ContentLength")
    if type(value) is not int or value < 1:
        raise _protocol_error()
    if value != expected:
        raise ADTOFPermanentOutputHeadObjectError(ADTOFOutputHeadObjectFailureCode.SIZE_MISMATCH)
    return value


def _content_type(response: Mapping[str, object], *, expected: str) -> str:
    """Require the plan's exact MIME type before completion can observe output."""

    value = response.get("ContentType")
    if not isinstance(value, str) or not value or "\x00" in value:
        raise _protocol_error()
    if value != expected:
        raise ADTOFPermanentOutputHeadObjectError(
            ADTOFOutputHeadObjectFailureCode.CONTENT_TYPE_MISMATCH
        )
    return value


def _stored_metadata(response: Mapping[str, object], *, expected: Mapping[str, str]) -> None:
    """Require complete, case-normalized MinIO user metadata to agree exactly."""

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
    # Boto3 normally lower-cases MinIO user-metadata names. Normalizing the
    # returned names permits equivalent SDK capitalization without allowing
    # the reviewed plan names or values to drift.
    if normalized != dict(expected):
        raise ADTOFPermanentOutputHeadObjectError(
            ADTOFOutputHeadObjectFailureCode.METADATA_MISMATCH
        )


def _verify_one_stored_object(
    client: ADTOFOutputHeadObjectClient,
    *,
    upload_object: ADTOFUploadObject,
    upload_receipt: UploadedADTOFObject,
    metadata: Mapping[str, str],
) -> VerifiedStoredADTOFObject:
    """Prove one already-uploaded object has current exact MinIO metadata."""

    response = _head_object_or_raise(client, bucket=upload_object.bucket, object_key=upload_object.object_key)
    size_bytes = _content_length(response, expected=upload_receipt.content_length)
    content_type = _content_type(response, expected=upload_object.content_type)
    _stored_metadata(response, expected=metadata)
    return VerifiedStoredADTOFObject(
        bucket=upload_receipt.bucket,
        object_key=upload_receipt.object_key,
        content_type=content_type,
        content_length=size_bytes,
        sha256=upload_receipt.sha256,
    )


def verify_uploaded_adtof_output_head_objects(
    client: ADTOFOutputHeadObjectClient,
    *,
    upload_objects: ADTOFUploadObjects,
    upload_receipts: UploadedADTOFObjects,
) -> VerifiedStoredADTOFObjects:
    """Verify the current MinIO drum-MIDI/tempo pair with two HeadObject calls.

    Both plan/receipt pairs must be valid and share all task provenance before
    the MIDI request begins. Successful return proves present stored metadata,
    not a cross-object transaction and not current PostgreSQL lease ownership.
    A later completion transaction must still check that task lease token and
    expiry while it commits the result exactly once.
    """

    (midi_plan, midi_receipt, midi_metadata), (tempo_plan, tempo_receipt, tempo_metadata) = _validated_pair(
        upload_objects=upload_objects,
        upload_receipts=upload_receipts,
    )
    return VerifiedStoredADTOFObjects(
        midi=_verify_one_stored_object(
            client,
            upload_object=midi_plan,
            upload_receipt=midi_receipt,
            metadata=midi_metadata,
        ),
        tempo_candidate=_verify_one_stored_object(
            client,
            upload_object=tempo_plan,
            upload_receipt=tempo_receipt,
            metadata=tempo_metadata,
        ),
    )
