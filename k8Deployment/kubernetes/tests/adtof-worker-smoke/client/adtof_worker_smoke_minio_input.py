"""Implement fixed-key MinIO clean-state checks and controlled drums-WAV upload.

This source-only adapter accepts an injected S3-compatible client, but it does
not import Boto3, construct a network client, read output objects, delete an
object, touch PostgreSQL/RabbitMQ, invoke ADTOF, build an image, or create a
Kubernetes resource. A later composition root must create the restricted Boto3
client from :mod:`adtof_worker_smoke_contract` settings and inject it here.

The adapter deliberately exposes high-level operations rather than a generic
``bucket, key`` method. Its only write is the deterministic fixture's one drums
WAV; its only reads are metadata checks for the same WAV and two expected ADTOF
outputs. That shape mirrors the reviewed fixed-key MinIO policy.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Protocol

from adtof_worker_smoke_contract import (
    ADTOFWorkerSmokeContractError,
    FixedObjectEvidence,
    fixed_storage_bucket,
)
from adtof_worker_smoke_fixture import (
    FIXED_OBJECT_KEYS,
    MIDI_KEY,
    STEM_KEY,
    TEMPO_KEY,
    WAV_CONTENT_TYPE,
    ControlledDrumWav,
    controlled_stem_metadata,
)


# Keep the ordered S3 operations deterministic for tests and diagnostics. The
# list is not a configurable prefix and contains exactly the policy-granted
# object coordinates in their workflow order: input, MIDI, then tempo evidence.
_FIXED_OBJECT_KEYS_IN_ORDER = (STEM_KEY, MIDI_KEY, TEMPO_KEY)
_MISSING_S3_CODES = frozenset({"404", "NoSuchKey", "NoSuchObject", "NotFound"})


class ADTOFWorkerSmokeS3Client(Protocol):
    """The minimal injected S3 surface for this input-only smoke boundary."""

    def head_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        """Return S3-compatible metadata for one exact private object key."""

    def put_object(self, **kwargs: object) -> Mapping[str, object]:
        """Store the controlled WAV at the one reviewed input coordinate."""


class ADTOFWorkerSmokeObjectStoreInfrastructureError(RuntimeError):
    """Hide S3/SDK details that could reveal endpoint, object, or key material."""


class ADTOFWorkerSmokeDirtyStateError(RuntimeError):
    """Refuse to overwrite evidence from a previous fixed-coordinate smoke run."""


class ADTOFWorkerSmokeObjectStoreProtocolError(RuntimeError):
    """Reject malformed client data or an input object that fails exact evidence checks."""


def _s3_error_code(error: BaseException) -> str | None:
    """Read an S3-shaped code without importing a vendor-specific exception type."""

    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return None
    error_mapping = response.get("Error")
    if not isinstance(error_mapping, Mapping):
        return None
    code = error_mapping.get("Code")
    return code if isinstance(code, str) else None


def _require_mapping(response: object) -> Mapping[str, object]:
    """Ensure a fake/SDK response cannot silently skip stored-object verification."""

    if not isinstance(response, Mapping):
        raise ADTOFWorkerSmokeObjectStoreProtocolError("MinIO response is invalid.")
    return response


def _normalized_exact_metadata(response: Mapping[str, object]) -> dict[str, str]:
    """Normalize case once and reject missing, extra, invalid, or duplicate user metadata."""

    raw_metadata = response.get("Metadata")
    if not isinstance(raw_metadata, Mapping):
        raise ADTOFWorkerSmokeObjectStoreProtocolError("MinIO metadata is invalid.")
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
            raise ADTOFWorkerSmokeObjectStoreProtocolError("MinIO metadata is invalid.")
        name = raw_name.lower()
        if name in normalized:
            raise ADTOFWorkerSmokeObjectStoreProtocolError("MinIO metadata is ambiguous.")
        normalized[name] = raw_value
    return normalized


def _validated_fixture(fixture: object) -> ControlledDrumWav:
    """Rebuild all input evidence so callers cannot upload arbitrary bytes/metadata."""

    if not isinstance(fixture, ControlledDrumWav):
        raise TypeError("fixture must be ControlledDrumWav.")
    if (
        type(fixture.size_bytes) is not int
        or fixture.size_bytes != len(fixture.wav_bytes)
        or fixture.size_bytes < 1
        or not isinstance(fixture.sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", fixture.sha256)
        or hashlib.sha256(fixture.wav_bytes).hexdigest() != fixture.sha256
    ):
        raise ADTOFWorkerSmokeObjectStoreProtocolError("Controlled smoke WAV evidence is invalid.")
    expected_metadata = controlled_stem_metadata(
        size_bytes=fixture.size_bytes,
        sha256=fixture.sha256,
    )
    if fixture.metadata != expected_metadata:
        raise ADTOFWorkerSmokeObjectStoreProtocolError("Controlled smoke WAV metadata is invalid.")
    return fixture


class FixedKeyADTOFWorkerSmokeMinIOInputAdapter:
    """Perform only the first two fixed-key storage phases of a future smoke run.

    The S3 client stays private to this object. No caller can use this adapter
    as a generic MinIO capability because no method accepts a bucket/key path,
    lists a bucket, creates a presigned URL, reads a produced output, or deletes
    evidence. The later output/read-cleanup task will be a separate adapter.
    """

    def __init__(self, client: ADTOFWorkerSmokeS3Client) -> None:
        """Retain one injected restricted S3 client without opening a connection."""

        if not hasattr(client, "head_object") or not hasattr(client, "put_object"):
            raise TypeError("client must expose the fixed smoke S3 operations.")
        self._client = client

    def _head_exact_key(self, key: str) -> Mapping[str, object]:
        """Head one known key and translate only transport-neutral failure categories."""

        if key not in FIXED_OBJECT_KEYS:
            raise ADTOFWorkerSmokeContractError("Smoke object key is outside the fixed policy.")
        try:
            response = self._client.head_object(Bucket=fixed_storage_bucket(), Key=key)
        except Exception as error:  # SDK concrete types intentionally remain outside this layer.
            if _s3_error_code(error) in _MISSING_S3_CODES:
                raise ADTOFWorkerSmokeDirtyStateError("Fixed smoke object is absent.") from error
            raise ADTOFWorkerSmokeObjectStoreInfrastructureError(
                "MinIO metadata request is unavailable."
            ) from error
        return _require_mapping(response)

    def assert_fixed_objects_absent(self) -> None:
        """Require all three reserved object keys to be absent before any write.

        A `HeadObject` success means prior evidence exists, so the method fails
        without overwriting it. A recognized S3 not-found response is the only
        clean result. Other failures, including permission issues, remain
        infrastructure failures rather than a dangerous assumption of absence.
        """

        for key in _FIXED_OBJECT_KEYS_IN_ORDER:
            try:
                self._head_exact_key(key)
            except ADTOFWorkerSmokeDirtyStateError:
                # This helper's "absent" marker is private to the head call;
                # absence is the one success case for preflight.
                continue
            raise ADTOFWorkerSmokeDirtyStateError("Fixed smoke object coordinates are not clean.")

    def upload_controlled_drum_wav(self, fixture: ControlledDrumWav) -> FixedObjectEvidence:
        """Put and Head-verify only the controlled input WAV and Demucs metadata.

        This call performs no database change. A future orchestrator must run
        clean-state preflight first, then call this method, then ask the scoped
        PostgreSQL function to create its durable ADTOF outbox event. A failed
        post-upload verification preserves the object as evidence; it does not
        delete, retry, or create a replacement key.
        """

        validated_fixture = _validated_fixture(fixture)
        try:
            put_response = self._client.put_object(
                Bucket=fixed_storage_bucket(),
                Key=STEM_KEY,
                Body=validated_fixture.wav_bytes,
                ContentLength=validated_fixture.size_bytes,
                ContentType=WAV_CONTENT_TYPE,
                Metadata=dict(validated_fixture.metadata),
            )
        except Exception as error:  # Do not reveal S3 details through smoke output.
            raise ADTOFWorkerSmokeObjectStoreInfrastructureError(
                "Controlled smoke WAV upload is unavailable."
            ) from error
        _require_mapping(put_response)

        # A successful PutObject return alone does not prove a gateway stored
        # the intended bytes/type/user metadata. Require one current HeadObject
        # response before the later PostgreSQL prepare function may be called.
        try:
            response = self._client.head_object(Bucket=fixed_storage_bucket(), Key=STEM_KEY)
        except Exception as error:
            raise ADTOFWorkerSmokeObjectStoreInfrastructureError(
                "Controlled smoke WAV verification is unavailable."
            ) from error
        stored = _require_mapping(response)
        stored_length = stored.get("ContentLength")
        if type(stored_length) is not int or stored_length != validated_fixture.size_bytes:
            raise ADTOFWorkerSmokeObjectStoreProtocolError("Stored smoke WAV size is invalid.")
        if stored.get("ContentType") != WAV_CONTENT_TYPE:
            raise ADTOFWorkerSmokeObjectStoreProtocolError("Stored smoke WAV content type is invalid.")
        if _normalized_exact_metadata(stored) != validated_fixture.metadata:
            raise ADTOFWorkerSmokeObjectStoreProtocolError("Stored smoke WAV metadata is invalid.")
        return FixedObjectEvidence(
            key=STEM_KEY,
            content_type=WAV_CONTENT_TYPE,
            size_bytes=validated_fixture.size_bytes,
            sha256=validated_fixture.sha256,
        )
