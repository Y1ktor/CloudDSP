"""Narrow, injectable MinIO operations for the Basic Pitch KEDA burst smoke.

The adapter accepts an already-constructed S3-compatible client. It cannot
construct an endpoint, enumerate a bucket, choose a prefix, or read/write an
object outside the six source constants in ``basic_pitch_keda_burst_smoke``.
The later image task will provide a hash-pinned Boto3 factory; this module is
kept SDK-free so its behavior remains unit-testable without cluster access.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from basic_pitch_keda_burst_smoke import (
    BURST_COORDINATES,
    INPUT_METADATA_NAMES,
    MAX_MIDI_SIZE_BYTES,
    MIDI_CONTENT_TYPE,
    OUTPUT_METADATA_NAMES,
    STEM_MODE,
    UPLOADS_BUCKET,
    WAV_CONTENT_TYPE,
    BasicPitchKedaBurstConfigurationError,
    BasicPitchKedaBurstContractError,
    BurstCoordinate,
    ControlledWav,
    assert_fixed_object_key,
    prepare_function_parameters,
)


READ_CHUNK_BYTES = 64 * 1024


class BasicPitchKedaBurstMinioInfrastructureError(RuntimeError):
    """Hide endpoint, access-key, and driver details from smoke-job output."""


class S3Body(Protocol):
    """Minimal streaming response body used for an independent checksum pass."""

    def read(self, amount: int = -1) -> bytes: ...

    def close(self) -> object: ...


class S3Client(Protocol):
    """The exact S3-compatible calls this six-key client may make."""

    def put_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def head_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def delete_object(self, **kwargs: object) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class VerifiedMidi:
    """Safe evidence derived from a streamed, verified worker output object."""

    coordinate: BurstCoordinate
    size_bytes: int
    sha256: str


def _s3_error_code(error: BaseException) -> str | None:
    """Extract only conventional absence evidence from an SDK-shaped exception."""

    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return None
    detail = response.get("Error")
    if not isinstance(detail, Mapping):
        return None
    code = detail.get("Code")
    return code if isinstance(code, str) else None


def _canonical_uuid(value: object, *, optional: bool = False) -> str | None:
    """Accept only canonical UUID text from trusted database/object metadata."""

    if value is None and optional:
        return None
    if isinstance(value, UUID):
        return str(value)
    if not isinstance(value, str):
        raise BasicPitchKedaBurstContractError("Burst object metadata contains an invalid UUID.")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise BasicPitchKedaBurstContractError("Burst object metadata contains an invalid UUID.") from error
    if str(parsed) != value:
        raise BasicPitchKedaBurstContractError("Burst object metadata contains a noncanonical UUID.")
    return value


def _normalized_metadata(value: object, *, expected_names: frozenset[str]) -> dict[str, str]:
    """Reject missing, duplicate-after-lowercasing, or extra MinIO metadata."""

    if not isinstance(value, Mapping):
        raise BasicPitchKedaBurstContractError("Burst object metadata is invalid.")
    normalized: dict[str, str] = {}
    for name, item in value.items():
        if not isinstance(name, str) or not isinstance(item, str) or not name or not item:
            raise BasicPitchKedaBurstContractError("Burst object metadata is invalid.")
        lowered_name = name.lower()
        if lowered_name in normalized:
            raise BasicPitchKedaBurstContractError("Burst object metadata is ambiguous.")
        normalized[lowered_name] = item
    if set(normalized) != expected_names:
        raise BasicPitchKedaBurstContractError("Burst object metadata does not match its fixed contract.")
    return normalized


def _verify_standard_midi(data: bytes) -> None:
    """Validate bounded Standard MIDI framing before reporting an output success."""

    if len(data) < 22 or data[:4] != b"MThd" or struct.unpack(">I", data[4:8])[0] != 6:
        raise BasicPitchKedaBurstContractError("Burst MIDI object is not a valid Standard MIDI File.")
    format_type, track_count, division = struct.unpack(">HHH", data[8:14])
    if (
        format_type not in {0, 1, 2}
        or not 1 <= track_count <= 64
        or (format_type == 0 and track_count != 1)
        or division == 0
    ):
        raise BasicPitchKedaBurstContractError("Burst MIDI object is not a valid Standard MIDI File.")
    offset = 14
    for _ in range(track_count):
        if offset + 8 > len(data) or data[offset : offset + 4] != b"MTrk":
            raise BasicPitchKedaBurstContractError("Burst MIDI object is not a valid Standard MIDI File.")
        track_size = struct.unpack(">I", data[offset + 4 : offset + 8])[0]
        offset += 8
        if track_size < 1 or offset + track_size > len(data):
            raise BasicPitchKedaBurstContractError("Burst MIDI object is not a valid Standard MIDI File.")
        offset += track_size
    if offset != len(data):
        raise BasicPitchKedaBurstContractError("Burst MIDI object is not a valid Standard MIDI File.")


class MinioBurstAdapter:
    """Apply six-key MinIO policy constraints before every storage operation."""

    def __init__(self, client: S3Client) -> None:
        """Retain an injected client; no endpoint or credential is accepted here."""

        self._client = client

    def assert_all_coordinates_absent(self) -> None:
        """Require every future input/output coordinate to be clean without ListBucket."""

        # Preserve the documented input/output order. The set remains the
        # authorization boundary, while deterministic probes make a retained
        # coordinate easier to diagnose without listing the bucket.
        for coordinate in BURST_COORDINATES:
            self._assert_object_absent(coordinate.stem_key)
            self._assert_object_absent(coordinate.midi_key)

    def _assert_object_absent(self, object_key: str) -> None:
        """Treat only an explicit object-not-found response as clean evidence."""

        assert_fixed_object_key(object_key)
        try:
            self._client.head_object(Bucket=UPLOADS_BUCKET, Key=object_key)
        except Exception as error:  # Concrete SDK errors are intentionally not imported.
            if _s3_error_code(error) in {"404", "NoSuchKey", "NoSuchObject", "NotFound"}:
                return
            raise BasicPitchKedaBurstMinioInfrastructureError("Burst MinIO absence check is unavailable.") from error
        raise BasicPitchKedaBurstContractError("Burst MinIO coordinates are not clean.")

    def upload_controlled_wavs(self, wavs: tuple[ControlledWav, ...]) -> None:
        """Put exactly the three contract WAVs with all worker-required provenance."""

        expected_coordinates = BURST_COORDINATES
        if tuple(wav.coordinate for wav in wavs) != expected_coordinates:
            raise BasicPitchKedaBurstConfigurationError("Burst WAV upload order does not match the fixed contract.")
        # The same validation protects the later PostgreSQL call. Run it before
        # the first put so a tampered byte/hash pair cannot leave even a
        # pre-durable input object in MinIO.
        prepare_function_parameters(wavs)
        for wav in wavs:
            object_key = wav.coordinate.stem_key
            assert_fixed_object_key(object_key)
            metadata = wav.metadata()
            if set(metadata) != INPUT_METADATA_NAMES:
                raise BasicPitchKedaBurstContractError("Burst WAV metadata does not match its fixed contract.")
            try:
                self._client.put_object(
                    Bucket=UPLOADS_BUCKET,
                    Key=object_key,
                    Body=wav.body,
                    ContentLength=wav.size_bytes,
                    ContentType=WAV_CONTENT_TYPE,
                    Metadata=metadata,
                )
            except Exception as error:
                raise BasicPitchKedaBurstMinioInfrastructureError("Burst MinIO WAV upload is unavailable.") from error

    def verify_midi(
        self,
        *,
        coordinate: BurstCoordinate,
        task_id: str,
        input_sha256: str,
    ) -> VerifiedMidi:
        """Head and stream one exact MIDI object after its durable task succeeds."""

        if coordinate not in BURST_COORDINATES:
            raise BasicPitchKedaBurstConfigurationError("Burst MIDI coordinate is not approved.")
        if len(input_sha256) != 64 or any(character not in "0123456789abcdef" for character in input_sha256):
            raise BasicPitchKedaBurstConfigurationError("Burst MIDI input checksum is invalid.")
        expected_task_id = _canonical_uuid(task_id)
        object_key = coordinate.midi_key
        assert_fixed_object_key(object_key)
        try:
            head = self._client.head_object(Bucket=UPLOADS_BUCKET, Key=object_key)
        except Exception as error:
            raise BasicPitchKedaBurstMinioInfrastructureError("Burst MIDI HeadObject is unavailable.") from error
        if not isinstance(head, Mapping):
            raise BasicPitchKedaBurstContractError("Burst MIDI HeadObject is invalid.")
        size_bytes = head.get("ContentLength")
        if type(size_bytes) is not int or not 1 <= size_bytes <= MAX_MIDI_SIZE_BYTES:
            raise BasicPitchKedaBurstContractError("Burst MIDI object size is invalid.")
        if head.get("ContentType") != MIDI_CONTENT_TYPE:
            raise BasicPitchKedaBurstContractError("Burst MIDI object content type is invalid.")
        metadata = _normalized_metadata(head.get("Metadata"), expected_names=OUTPUT_METADATA_NAMES)
        if (
            metadata["schema-version"] != "1"
            or metadata["producer"] != "basic-pitch"
            or metadata["job-id"] != coordinate.job_id
            or _canonical_uuid(metadata["task-id"]) != expected_task_id
            or metadata["request-event-id"] != coordinate.event_id
            or metadata["stem-name"] != coordinate.stem_name
            or metadata["stem-mode"] != STEM_MODE
            or metadata["size-bytes"] != str(size_bytes)
            or len(metadata["sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in metadata["sha256"])
            or metadata["input-stem-sha256"] != input_sha256
        ):
            raise BasicPitchKedaBurstContractError("Burst MIDI metadata is invalid.")
        body: S3Body | None = None
        try:
            response = self._client.get_object(Bucket=UPLOADS_BUCKET, Key=object_key)
            body_candidate = response.get("Body") if isinstance(response, Mapping) else None
            if body_candidate is None or not callable(getattr(body_candidate, "read", None)):
                raise BasicPitchKedaBurstContractError("Burst MIDI body is invalid.")
            body = body_candidate  # Protocol check above establishes the required surface at runtime.
            content = bytearray()
            digest = hashlib.sha256()
            while len(content) < size_bytes:
                chunk = body.read(min(READ_CHUNK_BYTES, size_bytes - len(content)))
                if not isinstance(chunk, bytes) or not chunk:
                    raise BasicPitchKedaBurstContractError("Burst MIDI body is incomplete.")
                content.extend(chunk)
                digest.update(chunk)
            if body.read(1) or len(content) != size_bytes:
                raise BasicPitchKedaBurstContractError("Burst MIDI body length changed while reading.")
        except BasicPitchKedaBurstContractError:
            raise
        except Exception as error:
            raise BasicPitchKedaBurstMinioInfrastructureError("Burst MIDI download is unavailable.") from error
        finally:
            with suppress(Exception):
                if body is not None:
                    body.close()
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != metadata["sha256"]:
            raise BasicPitchKedaBurstContractError("Burst MIDI checksum is invalid.")
        _verify_standard_midi(bytes(content))
        return VerifiedMidi(coordinate=coordinate, size_bytes=size_bytes, sha256=actual_sha256)

    def delete_all_fixed_objects(self) -> None:
        """Delete only the six reviewed keys; a runner calls this after success."""

        # Outputs are deleted before inputs so an interrupted cleanup cannot
        # leave an output that appears valid while its input evidence is gone.
        for coordinate in BURST_COORDINATES:
            self._delete_exact_object(coordinate.midi_key)
        for coordinate in BURST_COORDINATES:
            self._delete_exact_object(coordinate.stem_key)

    def _delete_exact_object(self, object_key: str) -> None:
        """Delete one literal key without exposing a bucket-list capability."""

        assert_fixed_object_key(object_key)
        try:
            self._client.delete_object(Bucket=UPLOADS_BUCKET, Key=object_key)
        except Exception as error:
            raise BasicPitchKedaBurstMinioInfrastructureError("Burst MinIO cleanup is unavailable.") from error
