"""Read and validate the two fixed ADTOF smoke outputs through MinIO.

This source-only adapter accepts an injected, S3-shaped client.  It imports no
Boto3 and does not construct a network client, delete an object, call
PostgreSQL/RabbitMQ, invoke ADTOF, build an image, or create a Kubernetes
resource.  A later composition root will obtain a *successful* fixed database
observation, create a restricted S3 client, and inject that client here.

The reader does more than trust a successful ``HeadObject`` response.  It
checks both the current stored metadata and a bounded streamed ``GetObject``
body.  The body hash must equal the stored SHA-256 metadata, so the smoke test
has evidence for the exact bytes the ADTOF worker wrote rather than a filename
or a model-quality assumption.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from collections.abc import Mapping
from contextlib import suppress
from typing import Any, Protocol
from uuid import UUID

from adtof_worker_smoke_contract import (
    ADTOFWorkerSmokeContractError,
    FixedObjectEvidence,
    VerifiedADTOFWorkerSmokeOutputs,
    fixed_storage_bucket,
)
from adtof_worker_smoke_fixture import (
    MIDI_CONTENT_TYPE,
    MIDI_KEY,
    SMOKE_EVENT_ID,
    SMOKE_JOB_ID,
    STEM_KEY,
    STEM_MODE,
    STEM_NAME,
    TEMPO_CONTENT_TYPE,
    TEMPO_KEY,
)


# These source-controlled values mirror the deployed ADTOF worker's output
# plan.  They are deliberately not environment variables: changing them at
# runtime would let a smoke client mislabel a result produced by another model
# configuration as this reviewed local ADTOF result.
_OUTPUT_SCHEMA_VERSION = "1"
_OUTPUT_PRODUCER = "adtof"
_MODEL_CONFIGURATION_ID = (
    "adtof-pytorch@85c192e78f716ea0b111cc8a5ee4a8f6a3a4f8a9"
    ";fps=100;thresholds=0.22,0.24,0.32,0.22,0.30;device=cpu"
)
_MIDI_ARTIFACT_KIND = "drum-midi"
_TEMPO_ARTIFACT_KIND = "tempo-candidate"

# Result objects are deliberately bounded before a byte stream is opened.  The
# limits match the worker's local output verifier: MIDI can contain up to 64
# tracks, while the compact tempo candidate must remain a small JSON document.
_MAX_MIDI_BYTES = 16 * 1024 * 1024
_MAX_TEMPO_BYTES = 64 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_MAX_MIDI_TRACKS = 64
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_MISSING_S3_CODES = frozenset({"404", "NoSuchKey", "NoSuchObject", "NotFound"})


class ADTOFWorkerSmokeOutputClient(Protocol):
    """The smallest S3 surface needed to read the two fixed worker outputs."""

    def head_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        """Return metadata for one private object without reading its body."""

    def get_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        """Return one private object response with its bounded stream body."""


class ADTOFWorkerSmokeOutputInfrastructureError(RuntimeError):
    """Hide SDK/endpoint diagnostics when the private object service is unavailable."""


class ADTOFWorkerSmokeOutputProtocolError(RuntimeError):
    """Reject missing, malformed, mismatched, or invalid fixed output evidence."""


class ADTOFWorkerSmokeOutputStream(Protocol):
    """The bounded response-body methods this reader needs from an SDK stream."""

    def read(self, amount: int = -1) -> bytes:
        """Return no more than the requested next body bytes."""

    def close(self) -> object:
        """Release the SDK response stream once the exact bytes were inspected."""


def _s3_error_code(error: BaseException) -> str | None:
    """Read a vendor-neutral S3 error code without importing an SDK exception."""

    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return None
    error_mapping = response.get("Error")
    if not isinstance(error_mapping, Mapping):
        return None
    code = error_mapping.get("Code")
    return code if isinstance(code, str) else None


def _require_mapping(value: object) -> Mapping[str, object]:
    """Reject a malformed fake/SDK response before any evidence is trusted."""

    if not isinstance(value, Mapping):
        raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke output response is invalid.")
    return value


def _canonical_dynamic_task_id(value: object) -> str:
    """Require the worker-created task ID to use canonical durable UUID text."""

    if not isinstance(value, str):
        raise ADTOFWorkerSmokeContractError("ADTOF smoke task ID is invalid.")
    try:
        canonical = str(UUID(value))
    except (TypeError, ValueError) as error:
        raise ADTOFWorkerSmokeContractError("ADTOF smoke task ID is invalid.") from error
    if canonical != value:
        raise ADTOFWorkerSmokeContractError("ADTOF smoke task ID is invalid.")
    return value


def _validated_input_stem(value: object) -> FixedObjectEvidence:
    """Admit only the prior fixed-WAV proof, not caller-supplied output evidence."""

    if (
        not isinstance(value, FixedObjectEvidence)
        or value.key != STEM_KEY
        or value.content_type != "audio/wav"
    ):
        raise ADTOFWorkerSmokeContractError("ADTOF smoke input evidence is invalid.")
    return value


def _normalized_exact_metadata(response: Mapping[str, object]) -> dict[str, str]:
    """Normalize S3 metadata names and reject null, duplicate, or unsafe entries."""

    raw_metadata = response.get("Metadata")
    if not isinstance(raw_metadata, Mapping):
        raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke output metadata is invalid.")
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
            raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke output metadata is invalid.")
        name = raw_name.lower()
        if name in normalized:
            raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke output metadata is ambiguous.")
        normalized[name] = raw_value
    return normalized


def _positive_bounded_length(value: object, *, maximum: int) -> int:
    """Require a non-empty bounded response length before allocating or streaming."""

    if type(value) is not int or not 1 <= value <= maximum:
        raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke output size is invalid.")
    return value


def _expected_metadata(
    *,
    task_id: str,
    input_stem_sha256: str,
    artifact_kind: str,
    size_bytes: int,
    sha256: str,
) -> dict[str, str]:
    """Return the complete immutable ADTOF metadata inventory for one output."""

    return {
        "schema-version": _OUTPUT_SCHEMA_VERSION,
        "producer": _OUTPUT_PRODUCER,
        "job-id": SMOKE_JOB_ID,
        "task-id": task_id,
        "request-event-id": SMOKE_EVENT_ID,
        "stem-name": STEM_NAME,
        "stem-mode": STEM_MODE,
        "artifact-kind": artifact_kind,
        "input-stem-sha256": input_stem_sha256,
        "model-config-id": _MODEL_CONFIGURATION_ID,
        "size-bytes": str(size_bytes),
        "sha256": sha256,
    }


def _response_headers(
    response: Mapping[str, object],
    *,
    content_type: str,
    maximum_bytes: int,
    task_id: str,
    input_stem_sha256: str,
    artifact_kind: str,
) -> tuple[int, str]:
    """Validate a current fixed-object header response without reading its body."""

    size_bytes = _positive_bounded_length(response.get("ContentLength"), maximum=maximum_bytes)
    if response.get("ContentType") != content_type:
        raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke output content type is invalid.")
    metadata = _normalized_exact_metadata(response)
    sha256 = metadata.get("sha256")
    if not isinstance(sha256, str) or _SHA256_PATTERN.fullmatch(sha256) is None:
        raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke output checksum is invalid.")
    if metadata != _expected_metadata(
        task_id=task_id,
        input_stem_sha256=input_stem_sha256,
        artifact_kind=artifact_kind,
        size_bytes=size_bytes,
        sha256=sha256,
    ):
        raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke output provenance is invalid.")
    return size_bytes, sha256


def _read_exact_stream(body: object, *, expected_size_bytes: int) -> bytes:
    """Read one bounded SDK stream, detect truncation/extra bytes, then close it."""

    if not hasattr(body, "read") or not hasattr(body, "close"):
        raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke output stream is invalid.")
    stream = body  # Retain duck typing so no vendor response type enters this module.
    chunks: list[bytes] = []
    remaining = expected_size_bytes
    try:
        while remaining:
            chunk = stream.read(min(_READ_CHUNK_BYTES, remaining))
            if not isinstance(chunk, bytes) or not chunk or len(chunk) > remaining:
                raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke output stream is invalid.")
            chunks.append(chunk)
            remaining -= len(chunk)
        if stream.read(1):
            raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke output stream is invalid.")
    except ADTOFWorkerSmokeOutputProtocolError:
        raise
    except Exception as error:
        raise ADTOFWorkerSmokeOutputInfrastructureError(
            "ADTOF smoke output download is unavailable."
        ) from error
    finally:
        with suppress(Exception):
            stream.close()
    return b"".join(chunks)


def _verify_standard_midi_file(body: bytes) -> None:
    """Verify complete Standard MIDI framing, not musical quality or note count."""

    if len(body) < 14 or body[:4] != b"MThd" or struct.unpack(">I", body[4:8])[0] != 6:
        raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke MIDI output is invalid.")
    format_type, track_count, division = struct.unpack(">HHH", body[8:14])
    if (
        format_type not in {0, 1, 2}
        or not 1 <= track_count <= _MAX_MIDI_TRACKS
        or (format_type == 0 and track_count != 1)
        or division == 0
    ):
        raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke MIDI output is invalid.")

    position = 14
    for _ in range(track_count):
        if position + 8 > len(body) or body[position : position + 4] != b"MTrk":
            raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke MIDI output is invalid.")
        track_size = struct.unpack(">I", body[position + 4 : position + 8])[0]
        position += 8
        if track_size < 1 or track_size > len(body) - position:
            raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke MIDI output is invalid.")
        position += track_size
    if position != len(body):
        raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke MIDI output is invalid.")


def _unique_json_pairs(pairs: list[tuple[object, object]]) -> dict[str, object]:
    """Reject duplicate tempo fields instead of accepting a last-value override."""

    parsed: dict[str, object] = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in parsed:
            raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke tempo output is invalid.")
        parsed[key] = value
    return parsed


def _reject_json_constant(_value: str) -> Any:
    """Reject non-standard NaN/Infinity spellings accepted by Python's JSON parser."""

    raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke tempo output is invalid.")


def _nonnegative_number(value: object) -> float:
    """Accept a finite non-negative JSON number while excluding booleans."""

    if type(value) not in {int, float} or not math.isfinite(float(value)) or float(value) < 0:
        raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke tempo output is invalid.")
    return float(value)


def _nonnegative_count(value: object) -> int:
    """Accept only a JSON integer count, not a truth value or decimal number."""

    if type(value) is not int or value < 0:
        raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke tempo output is invalid.")
    return value


def _verify_tempo_candidate(body: bytes) -> None:
    """Validate the complete cloud-compatible ADTOF tempo-candidate JSON shape."""

    try:
        parsed = json.loads(
            body.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_pairs,
            parse_constant=_reject_json_constant,
        )
    except ADTOFWorkerSmokeOutputProtocolError:
        raise
    except (UnicodeDecodeError, ValueError, TypeError) as error:
        raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke tempo output is invalid.") from error
    expected_fields = {
        "extractor",
        "bpm",
        "beat_count",
        "duration_seconds",
        "interval_consistency",
        "drum_event_count",
        "credible",
        "confidence",
        "source",
    }
    if not isinstance(parsed, Mapping) or set(parsed) != expected_fields:
        raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke tempo output is invalid.")
    bpm_raw = parsed["bpm"]
    bpm = None if bpm_raw is None else _nonnegative_number(bpm_raw)
    if bpm is not None and bpm <= 0:
        raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke tempo output is invalid.")
    if (
        parsed["extractor"] != _OUTPUT_PRODUCER
        or type(parsed["credible"]) is not bool
        or not isinstance(parsed["confidence"], str)
        or not isinstance(parsed["source"], str)
        or parsed["confidence"] not in {"high", "low"}
        or parsed["source"] != "adtof_drums"
    ):
        raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke tempo output is invalid.")
    beat_count = _nonnegative_count(parsed["beat_count"])
    duration_seconds = _nonnegative_number(parsed["duration_seconds"])
    interval_consistency = _nonnegative_number(parsed["interval_consistency"])
    drum_event_count = _nonnegative_count(parsed["drum_event_count"])
    credible = parsed["credible"]
    if (
        interval_consistency > 1
        or parsed["confidence"] != ("high" if credible else "low")
        or (credible and bpm is None)
    ):
        raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke tempo output is invalid.")
    # A credible short clip still needs enough evidence to make that label
    # meaningful.  Weak candidates remain valid only when labelled low.
    minimum_beats = 4 if duration_seconds < 20 else 8
    if credible and (drum_event_count < 8 or beat_count < minimum_beats or interval_consistency < 0.70):
        raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke tempo output is invalid.")


class FixedKeyADTOFWorkerSmokeMinIOOutputAdapter:
    """Read only verified MIDI/tempo evidence at the two ADTOF-owned fixed keys.

    No public method accepts a bucket, object key, output type, or metadata
    override.  The calling orchestration layer must pass the dynamic task ID
    from a completed first-attempt database observation and the exact input
    evidence returned by the controlled-WAV upload boundary.
    """

    def __init__(self, client: ADTOFWorkerSmokeOutputClient) -> None:
        """Keep one injected restricted client without opening a connection."""

        if not hasattr(client, "head_object") or not hasattr(client, "get_object"):
            raise TypeError("client must expose fixed smoke output read operations.")
        self._client = client

    def _head(self, *, key: str) -> Mapping[str, object]:
        """Request current metadata for one source-controlled output coordinate."""

        try:
            response = self._client.head_object(Bucket=fixed_storage_bucket(), Key=key)
        except Exception as error:
            if _s3_error_code(error) in _MISSING_S3_CODES:
                raise ADTOFWorkerSmokeOutputProtocolError("Expected ADTOF smoke output is absent.") from error
            raise ADTOFWorkerSmokeOutputInfrastructureError(
                "ADTOF smoke output metadata request is unavailable."
            ) from error
        return _require_mapping(response)

    def _get(self, *, key: str) -> Mapping[str, object]:
        """Open one source-controlled output stream without exposing a generic GET."""

        try:
            response = self._client.get_object(Bucket=fixed_storage_bucket(), Key=key)
        except Exception as error:
            if _s3_error_code(error) in _MISSING_S3_CODES:
                raise ADTOFWorkerSmokeOutputProtocolError("Expected ADTOF smoke output is absent.") from error
            raise ADTOFWorkerSmokeOutputInfrastructureError(
                "ADTOF smoke output download is unavailable."
            ) from error
        return _require_mapping(response)

    def _read_one(
        self,
        *,
        key: str,
        content_type: str,
        maximum_bytes: int,
        task_id: str,
        input_stem_sha256: str,
        artifact_kind: str,
    ) -> FixedObjectEvidence:
        """Head then stream one output, checking the same evidence on both responses."""

        header_size, header_sha256 = _response_headers(
            self._head(key=key),
            content_type=content_type,
            maximum_bytes=maximum_bytes,
            task_id=task_id,
            input_stem_sha256=input_stem_sha256,
            artifact_kind=artifact_kind,
        )
        get_response = self._get(key=key)
        get_size, get_sha256 = _response_headers(
            get_response,
            content_type=content_type,
            maximum_bytes=maximum_bytes,
            task_id=task_id,
            input_stem_sha256=input_stem_sha256,
            artifact_kind=artifact_kind,
        )
        if (get_size, get_sha256) != (header_size, header_sha256):
            raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke output changed while reading.")
        body = _read_exact_stream(get_response.get("Body"), expected_size_bytes=get_size)
        body_sha256 = hashlib.sha256(body).hexdigest()
        if body_sha256 != get_sha256:
            raise ADTOFWorkerSmokeOutputProtocolError("ADTOF smoke output checksum is invalid.")
        if key == MIDI_KEY:
            _verify_standard_midi_file(body)
        elif key == TEMPO_KEY:
            _verify_tempo_candidate(body)
        else:  # The two fixed call sites are source-controlled; keep this fail-closed.
            raise ADTOFWorkerSmokeContractError("ADTOF smoke output key is invalid.")
        return FixedObjectEvidence(
            key=key,
            content_type=content_type,
            size_bytes=get_size,
            sha256=body_sha256,
        )

    def read_verified_adtof_outputs(
        self, *, task_id: str, input_stem: FixedObjectEvidence
    ) -> VerifiedADTOFWorkerSmokeOutputs:
        """Return only a valid fixed MIDI/tempo pair for one observed worker task.

        This method is deliberately read-only.  A later orchestrator must first
        observe successful first-attempt task completion, then call this method,
        and only afterward invoke the separately guarded PostgreSQL/object
        cleanup capabilities.  An error leaves all worker outputs untouched.
        """

        expected_task_id = _canonical_dynamic_task_id(task_id)
        input_evidence = _validated_input_stem(input_stem)
        midi = self._read_one(
            key=MIDI_KEY,
            content_type=MIDI_CONTENT_TYPE,
            maximum_bytes=_MAX_MIDI_BYTES,
            task_id=expected_task_id,
            input_stem_sha256=input_evidence.sha256,
            artifact_kind=_MIDI_ARTIFACT_KIND,
        )
        tempo = self._read_one(
            key=TEMPO_KEY,
            content_type=TEMPO_CONTENT_TYPE,
            maximum_bytes=_MAX_TEMPO_BYTES,
            task_id=expected_task_id,
            input_stem_sha256=input_evidence.sha256,
            artifact_kind=_TEMPO_ARTIFACT_KIND,
        )
        return VerifiedADTOFWorkerSmokeOutputs(midi=midi, tempo=tempo)
