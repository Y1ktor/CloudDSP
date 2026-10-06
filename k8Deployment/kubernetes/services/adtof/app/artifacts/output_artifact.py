"""Validate bounded local ADTOF MIDI/tempo artifacts and establish SHA-256 proof.

The preceding output-object planner has fixed the private MinIO coordinate and
base provenance for each ADTOF result, but it cannot honestly know properties
of bytes that the model has not produced yet. This module fills that narrow
gap: it accepts one fixed output plan and one local regular file, validates the
file's format, and returns its byte count/SHA-256 plus a parsed tempo candidate
when applicable.

The caller is responsible for supplying the future model runner's controlled
scratch-output path. This verifier protects the final path from symlink and
regular-file confusion, requires its fixed filename, and binds it to the plan's
artifact kind. It does not create paths, invoke ADTOF, upload to MinIO, mutate
PostgreSQL, acknowledge RabbitMQ, build an image, or use Kubernetes.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from app.messaging.adtof_requested_message import ADTOF_STEM_NAME, LOCAL_UPLOADS_BUCKET
from app.processing.model_configuration import ADTOF_MODEL_CONFIGURATION_ID
from app.artifacts.output_object_plan import (
    ADTOF_MIDI_CONTENT_TYPE,
    ADTOF_OUTPUT_METADATA_SCHEMA_VERSION,
    ADTOF_OUTPUT_PRODUCER,
    ADTOF_TEMPO_CONTENT_TYPE,
    ADTOFOutputArtifactKind,
    ADTOFOutputObjectPlan,
)
from app.db.task_claim import ADTOF_STEM_MODES


# MIDI results are compact metadata compared with their WAV inputs. The finite
# ceiling prevents an unexpected model output from turning this trusted scratch
# path into an unbounded read or later MinIO-upload capability.
MAX_ADTOF_MIDI_BYTES = 16 * 1024 * 1024
MAX_ADTOF_TEMPO_JSON_BYTES = 64 * 1024
ADTOF_ARTIFACT_READ_CHUNK_BYTES = 64 * 1024
MAX_ADTOF_MIDI_TRACKS = 64

# These preserve the cloud worker's boundary for declaring a drum-derived tempo
# credible. A low-confidence candidate is still stored as valid evidence, but
# no caller may label weak model/beat data "high" by changing only one field.
MIN_ADTOF_DRUM_EVENT_COUNT = 8
MIN_ADTOF_BEAT_COUNT = 8
MIN_ADTOF_SHORT_CLIP_BEAT_COUNT = 4
MIN_ADTOF_BEAT_INTERVAL_CONSISTENCY = 0.70

_MIDI_HEADER_ID = b"MThd"
_MIDI_TRACK_ID = b"MTrk"
_MIDI_HEADER_DATA_LENGTH = 6
_MIDI_CHUNK_HEADER_LENGTH = 8
_MIDI_FILENAME = "drums.mid"
_TEMPO_FILENAME = "drums_bpm.json"


class ADTOFOutputArtifactError(RuntimeError):
    """Base safe category for local generated-output verification failures."""


class ADTOFOutputArtifactContractError(ADTOFOutputArtifactError):
    """A hand-built/widened output plan cannot authorize a local artifact read."""


class ADTOFOutputArtifactPathError(ADTOFOutputArtifactError):
    """The fixed local output path is missing, a symlink, or not a regular file."""


class ADTOFOutputArtifactFormatError(ADTOFOutputArtifactError):
    """The bounded output bytes are not the expected MIDI or tempo JSON shape."""


class ADTOFOutputArtifactConsistencyError(ADTOFOutputArtifactError):
    """The local model output changed while evidence was being calculated."""


@dataclass(frozen=True)
class ADTOFTempoCandidate:
    """The cloud-compatible tempo observation that later completion may persist.

    A low-confidence candidate is still valid ADTOF output. It is not a model
    error and the later job-level aggregation decides whether to prefer it.
    """

    bpm: float | None
    beat_count: int
    duration_seconds: float
    interval_consistency: float
    drum_event_count: int
    credible: bool
    confidence: str
    source: str


@dataclass(frozen=True)
class VerifiedADTOFOutputArtifact:
    """Pod-local evidence for exactly one validated ADTOF output object.

    ``path`` remains meaningful only inside the future worker's scratch scope.
    It must not enter PostgreSQL or a browser response. ``tempo_candidate`` is
    set only for the JSON plan; it retains the reviewed cloud-compatible data
    needed by a later guarded completion boundary.
    """

    output_plan: ADTOFOutputObjectPlan
    path: Path
    size_bytes: int
    sha256: str
    tempo_candidate: ADTOFTempoCandidate | None


def _contract_error() -> ADTOFOutputArtifactContractError:
    """Return one non-sensitive category without private metadata or paths."""

    return ADTOFOutputArtifactContractError("ADTOF output artifact contract is invalid.")


def _canonical_uuid(value: object) -> str:
    """Require canonical durable identifiers before rebuilding an output key."""

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
    """Read the frozen base metadata as one exact, duplicate-free mapping."""

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


def _validated_output_plan(value: object) -> ADTOFOutputObjectPlan:
    """Rebuild both private object coordinates from the plan's base provenance."""

    if not isinstance(value, ADTOFOutputObjectPlan):
        raise _contract_error()
    if not isinstance(value.artifact_kind, ADTOFOutputArtifactKind):
        raise _contract_error()
    metadata = _metadata_mapping(value.base_s3_metadata)
    expected_keys = {
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
    }
    if set(metadata) != expected_keys:
        raise _contract_error()

    job_id = _canonical_uuid(metadata["job-id"])
    _canonical_uuid(metadata["task-id"])
    _canonical_uuid(metadata["request-event-id"])
    if (
        value.bucket != LOCAL_UPLOADS_BUCKET
        or metadata["schema-version"] != ADTOF_OUTPUT_METADATA_SCHEMA_VERSION
        or metadata["producer"] != ADTOF_OUTPUT_PRODUCER
        or metadata["stem-name"] != ADTOF_STEM_NAME
        or metadata["stem-mode"] not in ADTOF_STEM_MODES
        or metadata["artifact-kind"] != value.artifact_kind.value
        or not re.fullmatch(r"[0-9a-f]{64}", metadata["input-stem-sha256"])
        or metadata["model-config-id"] != ADTOF_MODEL_CONFIGURATION_ID
    ):
        raise _contract_error()

    expected_coordinates = {
        ADTOFOutputArtifactKind.MIDI: (f"midi/{job_id}/drums.mid", ADTOF_MIDI_CONTENT_TYPE),
        ADTOFOutputArtifactKind.TEMPO_CANDIDATE: (
            f"midi/{job_id}/drums_bpm.json",
            ADTOF_TEMPO_CONTENT_TYPE,
        ),
    }
    expected_key, expected_content_type = expected_coordinates[value.artifact_kind]
    if value.object_key != expected_key or value.content_type != expected_content_type:
        raise _contract_error()
    return value


def _expected_filename(kind: ADTOFOutputArtifactKind) -> str:
    """Keep the local filename tied to the artifact kind, not a caller choice."""

    return _MIDI_FILENAME if kind is ADTOFOutputArtifactKind.MIDI else _TEMPO_FILENAME


def _open_current_regular_output(
    path_value: object,
    *,
    plan: ADTOFOutputObjectPlan,
) -> tuple[Path, object, os.stat_result]:
    """Open the one expected bounded output without following its final symlink."""

    if not isinstance(path_value, Path) or path_value.name != _expected_filename(plan.artifact_kind):
        raise ADTOFOutputArtifactPathError("ADTOF output artifact path is invalid.")
    maximum_size = (
        MAX_ADTOF_MIDI_BYTES
        if plan.artifact_kind is ADTOFOutputArtifactKind.MIDI
        else MAX_ADTOF_TEMPO_JSON_BYTES
    )
    try:
        # ``O_NOFOLLOW`` blocks final-component link swaps on Linux and macOS.
        # The lstat is retained for future platforms that do not expose it.
        before_open = path_value.lstat()
        if stat.S_ISLNK(before_open.st_mode):
            raise ADTOFOutputArtifactPathError("ADTOF output artifact path is invalid.")
        descriptor = os.open(
            path_value,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        source = os.fdopen(descriptor, "rb", buffering=0)
    except ADTOFOutputArtifactPathError:
        raise
    except OSError as error:
        raise ADTOFOutputArtifactPathError("ADTOF output artifact path is invalid.") from error
    try:
        opened = os.fstat(source.fileno())
    except OSError as error:
        source.close()
        raise ADTOFOutputArtifactPathError("ADTOF output artifact path is invalid.") from error
    if not stat.S_ISREG(opened.st_mode):
        source.close()
        raise ADTOFOutputArtifactPathError("ADTOF output artifact path is invalid.")
    if not 1 <= opened.st_size <= maximum_size:
        source.close()
        raise ADTOFOutputArtifactFormatError("ADTOF output artifact size is invalid.")
    return path_value, source, opened


class _HashingReader:
    """Read exact bounded sections while retaining one SHA-256 and byte counter."""

    def __init__(self, source: object, expected_size_bytes: int) -> None:
        self._source = source
        self._expected_size_bytes = expected_size_bytes
        self._hasher = hashlib.sha256()
        self.bytes_read = 0

    def _record(self, chunk: bytes) -> bytes:
        self.bytes_read += len(chunk)
        if self.bytes_read > self._expected_size_bytes:
            raise ADTOFOutputArtifactConsistencyError("ADTOF output artifact changed while reading.")
        self._hasher.update(chunk)
        return chunk

    def exact(self, length: int) -> bytes:
        """Read/hash precisely ``length`` bytes or reject a truncated output."""

        pieces: list[bytes] = []
        remaining = length
        while remaining:
            chunk = self._source.read(min(ADTOF_ARTIFACT_READ_CHUNK_BYTES, remaining))
            if not isinstance(chunk, bytes) or not chunk:
                raise ADTOFOutputArtifactFormatError("ADTOF output artifact is invalid.")
            pieces.append(self._record(chunk))
            remaining -= len(chunk)
        return b"".join(pieces)

    def discard_exact(self, length: int) -> None:
        """Hash a MIDI track body without retaining its musical events in RAM."""

        remaining = length
        while remaining:
            chunk = self._source.read(min(ADTOF_ARTIFACT_READ_CHUNK_BYTES, remaining))
            if not isinstance(chunk, bytes) or not chunk:
                raise ADTOFOutputArtifactFormatError("ADTOF output artifact is invalid.")
            self._record(chunk)
            remaining -= len(chunk)

    def all_bytes(self) -> bytes:
        """Read the already-bounded JSON output exactly once while calculating SHA-256."""

        return self.exact(self._expected_size_bytes)

    def digest(self) -> str:
        """Return SHA-256 only after every byte from the opened file was hashed."""

        if self.bytes_read != self._expected_size_bytes:
            raise ADTOFOutputArtifactConsistencyError("ADTOF output artifact changed while reading.")
        return self._hasher.hexdigest()


def _verify_standard_midi_file(source: object, expected_size_bytes: int) -> str:
    """Validate Standard MIDI File framing while producing SHA-256 evidence.

    This verifies a bounded, complete MIDI container—not musical note semantics.
    The future model adapter owns ADTOF invocation and its own model-level
    checks; later browser/editor code remains free to parse the musical events.
    """

    reader = _HashingReader(source, expected_size_bytes)
    header = reader.exact(8 + _MIDI_HEADER_DATA_LENGTH)
    if header[:4] != _MIDI_HEADER_ID or struct.unpack(">I", header[4:8])[0] != _MIDI_HEADER_DATA_LENGTH:
        raise ADTOFOutputArtifactFormatError("ADTOF MIDI artifact is invalid.")
    format_type, track_count, division = struct.unpack(">HHH", header[8:])
    if (
        format_type not in {0, 1, 2}
        or track_count < 1
        or track_count > MAX_ADTOF_MIDI_TRACKS
        or (format_type == 0 and track_count != 1)
        or division == 0
    ):
        raise ADTOFOutputArtifactFormatError("ADTOF MIDI artifact is invalid.")

    for _ in range(track_count):
        track_header = reader.exact(_MIDI_CHUNK_HEADER_LENGTH)
        if track_header[:4] != _MIDI_TRACK_ID:
            raise ADTOFOutputArtifactFormatError("ADTOF MIDI artifact is invalid.")
        track_size = struct.unpack(">I", track_header[4:])[0]
        if track_size < 1 or track_size > expected_size_bytes - reader.bytes_read:
            raise ADTOFOutputArtifactFormatError("ADTOF MIDI artifact is invalid.")
        reader.discard_exact(track_size)

    if source.read(1):
        # Declared track chunks must consume the complete Standard MIDI File.
        # A later fstat still detects a concurrent replacement/append; bytes
        # already present beyond the chunks are simply a malformed container.
        raise ADTOFOutputArtifactFormatError("ADTOF MIDI artifact is invalid.")
    return reader.digest()


def _unique_json_pairs(pairs: list[tuple[object, object]]) -> dict[str, object]:
    """Reject duplicate JSON fields instead of accepting a last-value override."""

    parsed: dict[str, object] = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in parsed:
            raise ADTOFOutputArtifactFormatError("ADTOF tempo artifact is invalid.")
        parsed[key] = value
    return parsed


def _reject_json_constant(_constant: str) -> Any:
    """Reject Python JSON's non-standard NaN/Infinity spellings."""

    raise ADTOFOutputArtifactFormatError("ADTOF tempo artifact is invalid.")


def _finite_nonnegative_number(value: object) -> float:
    """Convert one JSON number without admitting booleans or non-finite values."""

    if type(value) not in {int, float} or not math.isfinite(float(value)) or float(value) < 0:
        raise ADTOFOutputArtifactFormatError("ADTOF tempo artifact is invalid.")
    return float(value)


def _nonnegative_count(value: object) -> int:
    """Accept only JSON integer counters; booleans are deliberately excluded."""

    if type(value) is not int or value < 0:
        raise ADTOFOutputArtifactFormatError("ADTOF tempo artifact is invalid.")
    return value


def parse_adtof_tempo_candidate(body: bytes) -> ADTOFTempoCandidate:
    """Parse exactly the cloud-compatible ADTOF tempo-candidate JSON shape.

    The CPU inference entrypoint uses this pure parser before it writes its
    JSON. The file verifier uses the same parser after a local read. Sharing it
    prevents the producer and later storage boundary from disagreeing about
    which tempo observations are safe to call ADTOF output.
    """

    try:
        parsed = json.loads(
            body.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_pairs,
            parse_constant=_reject_json_constant,
        )
    except ADTOFOutputArtifactFormatError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise ADTOFOutputArtifactFormatError("ADTOF tempo artifact is invalid.") from error
    expected_keys = {
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
    if not isinstance(parsed, Mapping) or set(parsed) != expected_keys:
        raise ADTOFOutputArtifactFormatError("ADTOF tempo artifact is invalid.")

    bpm_raw = parsed["bpm"]
    bpm = None if bpm_raw is None else _finite_nonnegative_number(bpm_raw)
    if bpm is not None and bpm <= 0:
        raise ADTOFOutputArtifactFormatError("ADTOF tempo artifact is invalid.")
    if (
        parsed["extractor"] != ADTOF_OUTPUT_PRODUCER
        or not isinstance(parsed["extractor"], str)
        or type(parsed["credible"]) is not bool
        or not isinstance(parsed["confidence"], str)
        or not isinstance(parsed["source"], str)
    ):
        raise ADTOFOutputArtifactFormatError("ADTOF tempo artifact is invalid.")
    candidate = ADTOFTempoCandidate(
        bpm=bpm,
        beat_count=_nonnegative_count(parsed["beat_count"]),
        duration_seconds=_finite_nonnegative_number(parsed["duration_seconds"]),
        interval_consistency=_finite_nonnegative_number(parsed["interval_consistency"]),
        drum_event_count=_nonnegative_count(parsed["drum_event_count"]),
        credible=parsed["credible"],
        confidence=parsed["confidence"],
        source=parsed["source"],
    )
    if (
        candidate.confidence not in {"high", "low"}
        or candidate.source != "adtof_drums"
        or candidate.interval_consistency > 1
        or candidate.confidence != ("high" if candidate.credible else "low")
        or (candidate.credible and candidate.bpm is None)
    ):
        raise ADTOFOutputArtifactFormatError("ADTOF tempo artifact is invalid.")
    minimum_beats = (
        MIN_ADTOF_SHORT_CLIP_BEAT_COUNT
        if candidate.duration_seconds < 20
        else MIN_ADTOF_BEAT_COUNT
    )
    if candidate.credible and (
        candidate.drum_event_count < MIN_ADTOF_DRUM_EVENT_COUNT
        or candidate.beat_count < minimum_beats
        or candidate.interval_consistency < MIN_ADTOF_BEAT_INTERVAL_CONSISTENCY
    ):
        raise ADTOFOutputArtifactFormatError("ADTOF tempo artifact is invalid.")
    return candidate


def verify_and_hash_adtof_output_artifact(
    *,
    output_plan: ADTOFOutputObjectPlan,
    artifact_path: Path,
) -> VerifiedADTOFOutputArtifact:
    """Return local evidence only for one exact bounded planned ADTOF artifact.

    A later model adapter supplies its controlled output path after a successful
    model run. A later uploader may add this result's ``size_bytes`` and
    ``sha256`` to the plan's base metadata, but neither stage may treat this as
    proof that MinIO has stored an object or that PostgreSQL may complete work.
    """

    plan = _validated_output_plan(output_plan)
    path, source, before = _open_current_regular_output(artifact_path, plan=plan)
    try:
        if plan.artifact_kind is ADTOFOutputArtifactKind.MIDI:
            sha256 = _verify_standard_midi_file(source, before.st_size)
            tempo_candidate = None
        else:
            reader = _HashingReader(source, before.st_size)
            tempo_candidate = parse_adtof_tempo_candidate(reader.all_bytes())
            if source.read(1):
                raise ADTOFOutputArtifactConsistencyError("ADTOF output artifact changed while reading.")
            sha256 = reader.digest()
        after = os.fstat(source.fileno())
    except ADTOFOutputArtifactError:
        raise
    except OSError as error:
        raise ADTOFOutputArtifactPathError("ADTOF output artifact path is invalid.") from error
    finally:
        source.close()
    if (
        after.st_size != before.st_size
        or after.st_dev != before.st_dev
        or after.st_ino != before.st_ino
    ):
        raise ADTOFOutputArtifactConsistencyError("ADTOF output artifact changed while reading.")
    return VerifiedADTOFOutputArtifact(
        output_plan=plan,
        path=path,
        size_bytes=before.st_size,
        sha256=sha256,
        tempo_candidate=tempo_candidate,
    )
