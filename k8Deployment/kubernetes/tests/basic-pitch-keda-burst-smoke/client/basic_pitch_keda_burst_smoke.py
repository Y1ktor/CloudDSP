"""Pure contract for the controlled three-request Basic Pitch KEDA burst.

This first client layer deliberately has **no** Boto3, Psycopg, RabbitMQ, or
Kubernetes dependency. It defines the exact evidence a later adapter may
upload and send to the three PostgreSQL ``SECURITY DEFINER`` functions.

Keeping the contract pure makes it inexpensive to test that this integration
fixture cannot drift into a general-purpose client before an image or Pod is
introduced. The next task will add network adapters around this module; those
adapters must not add caller-selectable object, Job, event, or queue
coordinates.
"""

from __future__ import annotations

import hashlib
import io
import math
import os
import struct
import wave
from dataclasses import dataclass, field
from typing import Final
from uuid import UUID


# These identifiers are intentionally repeated from the README, PostgreSQL
# bootstrap functions, and MinIO policy. They are source constants—not
# environment variables—so the future Job cannot substitute a real user's
# durable work or object path.
UPLOADS_BUCKET: Final = "clouddsp-uploads"
TEST_OWNER_SUB: Final = "clouddsp-basic-pitch-keda-burst-smoke"
STEM_MODE: Final = "4-stems"
DATABASE_NAME: Final = "clouddsp_job_api"
DATABASE_USERNAME: Final = "clouddsp-basic-pitch-keda-burst-smoke"
MINIO_ACCESS_KEY: Final = "clouddsp-basic-pitch-keda-burst-smoke"
DATABASE_HOST: Final = "clouddsp-postgresql.clouddsp-data.svc"
DATABASE_PORT: Final = 5432
MINIO_ENDPOINT_URL: Final = "http://clouddsp-minio.clouddsp-data.svc:9000"

# The durable wait belongs to the client, not to the KEDA controller. It is
# long enough for scale-from-zero, scheduling, worker start, and all three
# short CPU inferences, yet guarantees a stuck smoke Job terminates.
COMPLETION_TIMEOUT_SECONDS: Final = 300

# A 12-second mono, 44.1 kHz, signed-16-bit PCM WAV has 529,200 frames. The
# standard 44-byte header makes this exact byte size deterministic. It is
# below the dedicated two-MiB MinIO policy and exercises a real audio decoder.
WAV_SAMPLE_RATE: Final = 44_100
WAV_DURATION_SECONDS: Final = 12
WAV_SAMPLE_WIDTH_BYTES: Final = 2
WAV_CHANNELS: Final = 1
WAV_AMPLITUDE: Final = 0.2
EXPECTED_WAV_SIZE_BYTES: Final = 44 + (
    WAV_SAMPLE_RATE * WAV_DURATION_SECONDS * WAV_SAMPLE_WIDTH_BYTES * WAV_CHANNELS
)
MAX_WAV_SIZE_BYTES: Final = 2 * 1024 * 1024
MAX_MIDI_SIZE_BYTES: Final = 2 * 1024 * 1024
WAV_CONTENT_TYPE: Final = "audio/wav"
MIDI_CONTENT_TYPE: Final = "audio/midi"


class BasicPitchKedaBurstConfigurationError(RuntimeError):
    """Report invalid configuration without exposing a credential value."""


class BasicPitchKedaBurstContractError(RuntimeError):
    """Report an internal coordinate/evidence invariant failure safely."""


@dataclass(frozen=True)
class BurstCoordinate:
    """One non-drum Basic Pitch request in the fixed three-message backlog."""

    label: str
    job_id: str
    event_id: str
    synthetic_demucs_task_id: str
    stem_name: str
    frequency_hz: float

    @property
    def stem_key(self) -> str:
        """Return the only input-object key this coordinate may upload."""

        return f"stems/{self.job_id}/{self.stem_name}.wav"

    @property
    def midi_key(self) -> str:
        """Return the only MIDI-object key this coordinate may later verify."""

        return f"midi/{self.job_id}/{self.stem_name}.mid"


# Tuple ordering is part of the database function parameter order. Do not sort
# it at runtime: PostgreSQL expects vocals evidence, then bass, then other.
BURST_COORDINATES: Final[tuple[BurstCoordinate, ...]] = (
    BurstCoordinate(
        label="vocals",
        job_id="2a1e8097-7da6-4d06-8b27-c006b02e0d91",
        event_id="32e10a72-b5e7-4f11-8dfe-b922ec7ebbd9",
        synthetic_demucs_task_id="b1f9a310-9b6c-4707-84f7-ec1510c12201",
        stem_name="vocals",
        frequency_hz=440.0,
    ),
    BurstCoordinate(
        label="bass",
        job_id="8e27f4ad-6c19-4e3f-9b56-fa287429c0a2",
        event_id="c4b23de8-4a63-47e3-a96e-155abfd38c36",
        synthetic_demucs_task_id="e9875430-1df0-4389-a60c-48ad0fb21334",
        stem_name="bass",
        frequency_hz=493.883,
    ),
    BurstCoordinate(
        label="other",
        job_id="96ba2e39-e035-4fe8-a50a-b1e6db208ac3",
        event_id="47ae0c63-e1b1-4663-b47a-8efaa8f37bc7",
        synthetic_demucs_task_id="0b4970e2-6735-48be-8741-64a720be79ce",
        stem_name="other",
        frequency_hz=523.251,
    ),
)

# No prefix wildcard is allowed by the paired MinIO policy. A future storage
# adapter must ask for one of these six literal coordinates or fail before a
# network request is constructed.
FIXED_OBJECT_KEYS: Final[frozenset[str]] = frozenset(
    key
    for coordinate in BURST_COORDINATES
    for key in (coordinate.stem_key, coordinate.midi_key)
)

INPUT_METADATA_NAMES: Final[frozenset[str]] = frozenset(
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

# A worker-produced MIDI object must bind back to both the upstream controlled
# stem and the downstream outbox request. The future MinIO adapter verifies
# this complete shape after streaming the object instead of trusting metadata.
OUTPUT_METADATA_NAMES: Final[frozenset[str]] = frozenset(
    {
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
)

# This SQL names only the reviewed function—not a table—so later adapter code
# cannot bypass the dedicated PostgreSQL role's narrow permission boundary.
PREPARE_SQL: Final = """
    SELECT request_label, smoke_job_id::text AS smoke_job_id, smoke_event_id::text AS smoke_event_id
    FROM public.clouddsp_basic_pitch_keda_burst_smoke_prepare(%s, %s, %s, %s, %s, %s)
"""
OBSERVE_SQL: Final = "SELECT * FROM public.clouddsp_basic_pitch_keda_burst_smoke_observe()"
CLEANUP_SQL: Final = "SELECT public.clouddsp_basic_pitch_keda_burst_smoke_cleanup() AS cleaned"


def _required_environment(name: str, *, default: str | None = None) -> str:
    """Read one setting while ensuring an error never repeats its value."""

    value = os.environ.get(name, default)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise BasicPitchKedaBurstConfigurationError(f"Required environment variable {name} is absent.")
    return value


def _fixed_value_environment(name: str, *, expected: str, default: str | None = None) -> str:
    """Accept exactly one reviewed non-secret route/identity setting."""

    value = _required_environment(name, default=default)
    if value != expected:
        raise BasicPitchKedaBurstConfigurationError(f"{name} must use the reviewed local burst-test value.")
    return value


def _fixed_port_environment(name: str, *, expected: int) -> int:
    """Reject a port override before it can reroute the later database client."""

    value = _required_environment(name, default=str(expected))
    try:
        parsed = int(value)
    except ValueError as error:
        raise BasicPitchKedaBurstConfigurationError(f"{name} must be an integer.") from error
    if parsed != expected:
        raise BasicPitchKedaBurstConfigurationError(f"{name} must use the reviewed local burst-test port.")
    return parsed


@dataclass(frozen=True)
class BurstSettings:
    """Private routes and dedicated credentials for the later one-shot client.

    The secret fields are omitted from ``repr`` so a normally safe diagnostic
    print of this dataclass cannot disclose a mounted password or access key.
    """

    database_host: str
    database_port: int
    database_name: str
    database_username: str
    database_password: str = field(repr=False)
    minio_endpoint_url: str = MINIO_ENDPOINT_URL
    minio_access_key: str = MINIO_ACCESS_KEY
    minio_secret_key: str = field(default="", repr=False)
    completion_timeout_seconds: int = COMPLETION_TIMEOUT_SECONDS

    @classmethod
    def from_environment(cls) -> "BurstSettings":
        """Load the later Job's settings without permitting route broadening."""

        return cls(
            database_host=_fixed_value_environment(
                "BASIC_PITCH_KEDA_BURST_SMOKE_DB_HOST",
                expected=DATABASE_HOST,
                default=DATABASE_HOST,
            ),
            database_port=_fixed_port_environment(
                "BASIC_PITCH_KEDA_BURST_SMOKE_DB_PORT",
                expected=DATABASE_PORT,
            ),
            database_name=_fixed_value_environment(
                "BASIC_PITCH_KEDA_BURST_SMOKE_DB_NAME",
                expected=DATABASE_NAME,
            ),
            database_username=_fixed_value_environment(
                "BASIC_PITCH_KEDA_BURST_SMOKE_DB_USERNAME",
                expected=DATABASE_USERNAME,
            ),
            database_password=_required_environment("BASIC_PITCH_KEDA_BURST_SMOKE_DB_PASSWORD"),
            minio_endpoint_url=_fixed_value_environment(
                "BASIC_PITCH_KEDA_BURST_SMOKE_MINIO_ENDPOINT_URL",
                expected=MINIO_ENDPOINT_URL,
                default=MINIO_ENDPOINT_URL,
            ),
            minio_access_key=_fixed_value_environment(
                "BASIC_PITCH_KEDA_BURST_SMOKE_S3_ACCESS_KEY",
                expected=MINIO_ACCESS_KEY,
            ),
            minio_secret_key=_required_environment("BASIC_PITCH_KEDA_BURST_SMOKE_S3_SECRET_KEY"),
        )


@dataclass(frozen=True)
class ControlledWav:
    """Validated byte/hash evidence for exactly one fixed input coordinate."""

    coordinate: BurstCoordinate
    body: bytes = field(repr=False)
    size_bytes: int
    sha256: str

    def metadata(self) -> dict[str, str]:
        """Return the complete Demucs-provenance shape Basic Pitch validates."""

        return {
            "schema-version": "1",
            "producer": "demucs",
            "job-id": self.coordinate.job_id,
            "task-id": self.coordinate.synthetic_demucs_task_id,
            "stem-name": self.coordinate.stem_name,
            "stem-mode": STEM_MODE,
            "size-bytes": str(self.size_bytes),
            "sha256": self.sha256,
        }


def _canonical_uuid(value: str, *, field_name: str) -> None:
    """Ensure fixed identifiers have canonical UUID spelling at import time."""

    try:
        parsed = UUID(value)
    except ValueError as error:
        raise BasicPitchKedaBurstContractError(f"Burst contract contains an invalid {field_name} UUID.") from error
    if str(parsed) != value:
        raise BasicPitchKedaBurstContractError(f"Burst contract contains a noncanonical {field_name} UUID.")


def _validate_contract_constants() -> None:
    """Fail closed if a later source edit breaks fixed-coordinate alignment."""

    if len(BURST_COORDINATES) != 3:
        raise BasicPitchKedaBurstContractError("Burst contract must contain exactly three requests.")
    if len(FIXED_OBJECT_KEYS) != 6:
        raise BasicPitchKedaBurstContractError("Burst contract must contain exactly six object keys.")
    seen_labels: set[str] = set()
    seen_stems: set[str] = set()
    seen_job_ids: set[str] = set()
    seen_event_ids: set[str] = set()
    seen_task_ids: set[str] = set()
    for coordinate in BURST_COORDINATES:
        if coordinate.label != coordinate.stem_name or coordinate.label not in {"vocals", "bass", "other"}:
            raise BasicPitchKedaBurstContractError("Burst contract contains an unsupported request label.")
        if coordinate.frequency_hz <= 0:
            raise BasicPitchKedaBurstContractError("Burst contract contains an invalid WAV frequency.")
        _canonical_uuid(coordinate.job_id, field_name="job")
        _canonical_uuid(coordinate.event_id, field_name="event")
        _canonical_uuid(coordinate.synthetic_demucs_task_id, field_name="synthetic task")
        seen_labels.add(coordinate.label)
        seen_stems.add(coordinate.stem_name)
        seen_job_ids.add(coordinate.job_id)
        seen_event_ids.add(coordinate.event_id)
        seen_task_ids.add(coordinate.synthetic_demucs_task_id)
    if not (
        len(seen_labels) == len(seen_stems) == len(seen_job_ids) == len(seen_event_ids) == len(seen_task_ids) == 3
    ):
        raise BasicPitchKedaBurstContractError("Burst contract coordinates must be unique.")


_validate_contract_constants()


def assert_fixed_object_key(object_key: str) -> None:
    """Forbid a future storage adapter from constructing prefix/arbitrary access."""

    if object_key not in FIXED_OBJECT_KEYS:
        raise BasicPitchKedaBurstConfigurationError("Burst smoke attempted an object key outside its fixed contract.")


def build_controlled_wav(coordinate: BurstCoordinate) -> ControlledWav:
    """Build deterministic, valid PCM evidence for one approved coordinate.

    No input path, random source, microphone, or user-provided sample is
    accepted. The controlled tone is only a compact stand-in for an upstream
    Demucs-produced stem; it does not emulate Demucs inference.
    """

    if coordinate not in BURST_COORDINATES:
        raise BasicPitchKedaBurstConfigurationError("Burst smoke requested an unknown fixed coordinate.")

    frame_count = WAV_SAMPLE_RATE * WAV_DURATION_SECONDS
    frames = bytearray(frame_count * WAV_SAMPLE_WIDTH_BYTES)
    for frame_index in range(frame_count):
        sample = int(
            WAV_AMPLITUDE
            * 32767
            * math.sin(2 * math.pi * coordinate.frequency_hz * frame_index / WAV_SAMPLE_RATE)
        )
        struct.pack_into("<h", frames, frame_index * WAV_SAMPLE_WIDTH_BYTES, sample)

    destination = io.BytesIO()
    with wave.open(destination, "wb") as writer:
        writer.setnchannels(WAV_CHANNELS)
        writer.setsampwidth(WAV_SAMPLE_WIDTH_BYTES)
        writer.setframerate(WAV_SAMPLE_RATE)
        writer.writeframes(frames)
    body = destination.getvalue()
    if (
        len(body) != EXPECTED_WAV_SIZE_BYTES
        or len(body) > MAX_WAV_SIZE_BYTES
        or body[:4] != b"RIFF"
        or body[8:12] != b"WAVE"
    ):
        raise BasicPitchKedaBurstContractError("Controlled WAV construction is invalid.")
    return ControlledWav(
        coordinate=coordinate,
        body=body,
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )


def build_all_controlled_wavs() -> tuple[ControlledWav, ...]:
    """Construct evidence in the documented PostgreSQL parameter order."""

    return tuple(build_controlled_wav(coordinate) for coordinate in BURST_COORDINATES)


def prepare_function_parameters(wavs: tuple[ControlledWav, ...]) -> tuple[int | str, ...]:
    """Return only the three size/hash pairs accepted by ``prepare``.

    The function receives no caller-selected IDs, object keys, owner, queue
    route, status, or payload. This helper rejects reordered, missing, or
    substituted evidence before an eventual database adapter can call it.
    """

    if len(wavs) != len(BURST_COORDINATES) or tuple(item.coordinate for item in wavs) != BURST_COORDINATES:
        raise BasicPitchKedaBurstConfigurationError("Burst smoke evidence does not match the fixed coordinate order.")
    parameters: list[int | str] = []
    for wav in wavs:
        if (
            wav.size_bytes != len(wav.body)
            or not 1 <= wav.size_bytes <= MAX_WAV_SIZE_BYTES
            or hashlib.sha256(wav.body).hexdigest() != wav.sha256
            or len(wav.sha256) != 64
            or any(character not in "0123456789abcdef" for character in wav.sha256)
        ):
            raise BasicPitchKedaBurstContractError("Controlled WAV evidence is inconsistent.")
        parameters.extend((wav.size_bytes, wav.sha256))
    return tuple(parameters)
