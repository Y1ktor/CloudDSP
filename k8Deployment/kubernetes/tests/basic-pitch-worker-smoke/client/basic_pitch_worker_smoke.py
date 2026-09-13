"""Run one bounded, least-privilege Basic Pitch worker integration smoke test.

This client proves the real durable path, not a mock shortcut:

``controlled MinIO WAV -> PostgreSQL outbox -> dispatcher -> deployed worker``
``-> PostgreSQL task success + private MinIO MIDI``.

Its credentials deliberately cannot publish/consume RabbitMQ, read application
tables, create arbitrary jobs, list a bucket, or access normal user objects.
The three PostgreSQL security-definer functions and MinIO policy independently
restrict it to the fixed test coordinates declared below.  On failure after a
durable event may exist, it preserves the exact test evidence for diagnosis.
"""

from __future__ import annotations

import hashlib
import io
import math
import os
import struct
import sys
import time
import wave
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID


# Fixed coordinates match README.md, the PostgreSQL functions, and the MinIO
# policy. They are source constants rather than environment variables so a
# future manifest cannot broaden this test to another user's object/job.
SMOKE_JOB_ID = "7ea5ee7e-c726-4af8-8f21-4d0e227bdd0b"
SMOKE_EVENT_ID = "66d8637f-4e80-4b53-aed3-4e4ea54bd110"
SYNTHETIC_DEMUCS_TASK_ID = "3ed3ddfa-3165-4365-af60-5ae4f7f656ba"
UPLOADS_BUCKET = "clouddsp-uploads"
STEM_NAME = "vocals"
STEM_MODE = "2-stems"
STEM_KEY = f"stems/{SMOKE_JOB_ID}/{STEM_NAME}.wav"
MIDI_KEY = f"midi/{SMOKE_JOB_ID}/{STEM_NAME}.mid"
WAV_CONTENT_TYPE = "audio/wav"
MIDI_CONTENT_TYPE = "audio/midi"
FIXED_OBJECT_KEYS = frozenset({STEM_KEY, MIDI_KEY})

# A one-second PCM tone is deterministic, tiny, and a real WAV file. It is
# input evidence only—not an attempt to emulate a Demucs model output.
WAV_SAMPLE_RATE = 44_100
WAV_DURATION_SECONDS = 1
WAV_FREQUENCY_HZ = 440
WAV_AMPLITUDE = 0.2
MAX_SMOKE_WAV_BYTES = 1 * 1024 * 1024
MAX_MIDI_BYTES = 16 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 180
MAX_TIMEOUT_SECONDS = 240

_INPUT_METADATA_NAMES = frozenset(
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
_OUTPUT_METADATA_NAMES = frozenset(
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


class BasicPitchWorkerSmokeConfigurationError(RuntimeError):
    """Report invalid non-secret settings without exposing their values."""


class BasicPitchWorkerSmokeAssertionError(RuntimeError):
    """Report a failed durable-work assertion with no raw object/job data."""


class BasicPitchWorkerSmokeInfrastructureError(RuntimeError):
    """Hide S3/driver diagnostics that could disclose endpoints or credentials."""


class S3Client(Protocol):
    """The small S3-compatible surface this client needs for its two keys."""

    def put_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def head_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def delete_object(self, **kwargs: object) -> Mapping[str, object]: ...


class DatabaseCursor(Protocol):
    """The parameterized cursor operations used only for three functions."""

    def execute(self, query: str, params: tuple[object, ...] = ()) -> object: ...

    def fetchone(self) -> Mapping[str, object] | None: ...

    def __enter__(self) -> "DatabaseCursor": ...

    def __exit__(self, *args: object) -> object: ...


class DatabaseConnection(Protocol):
    """A connection that exposes a context-managed dictionary-row cursor."""

    def cursor(self) -> DatabaseCursor: ...

    def close(self) -> object: ...


def _required_environment(name: str, *, default: str | None = None) -> str:
    """Read one setting while ensuring failure text never includes its value."""

    value = os.environ.get(name, default)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise BasicPitchWorkerSmokeConfigurationError(f"Required environment variable {name} is absent.")
    return value


def _bounded_integer(*, name: str, value: str, minimum: int, maximum: int) -> int:
    """Parse a port/timeout before it can control a network operation."""

    try:
        parsed = int(value)
    except ValueError as error:
        raise BasicPitchWorkerSmokeConfigurationError(f"{name} must be an integer.") from error
    if not minimum <= parsed <= maximum:
        raise BasicPitchWorkerSmokeConfigurationError(
            f"{name} must be between {minimum} and {maximum}."
        )
    return parsed


@dataclass(frozen=True)
class SmokeSettings:
    """The future Job's private Service routes and restricted credentials."""

    database_host: str
    database_port: int
    database_name: str
    database_username: str
    database_password: str = field(repr=False)
    minio_endpoint_url: str = "http://clouddsp-minio.clouddsp-data.svc:9000"
    minio_access_key: str = ""
    minio_secret_key: str = field(default="", repr=False)
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_environment(cls) -> "SmokeSettings":
        """Load only the fixed in-cluster routes approved for this test."""

        host = _required_environment(
            "BASIC_PITCH_WORKER_SMOKE_DB_HOST",
            default="clouddsp-postgresql.clouddsp-data.svc",
        )
        if host == "localhost" or host.endswith(".localhost"):
            raise BasicPitchWorkerSmokeConfigurationError("Smoke database host must be private Service DNS.")
        endpoint = _required_environment(
            "BASIC_PITCH_WORKER_SMOKE_MINIO_ENDPOINT_URL",
            default="http://clouddsp-minio.clouddsp-data.svc:9000",
        ).rstrip("/")
        if endpoint != "http://clouddsp-minio.clouddsp-data.svc:9000":
            raise BasicPitchWorkerSmokeConfigurationError("Smoke MinIO endpoint must be the reviewed private Service.")
        return cls(
            database_host=host,
            database_port=_bounded_integer(
                name="BASIC_PITCH_WORKER_SMOKE_DB_PORT",
                value=os.environ.get("BASIC_PITCH_WORKER_SMOKE_DB_PORT", "5432"),
                minimum=1,
                maximum=65_535,
            ),
            database_name=_required_environment("BASIC_PITCH_WORKER_SMOKE_DB_NAME"),
            database_username=_required_environment("BASIC_PITCH_WORKER_SMOKE_DB_USERNAME"),
            database_password=_required_environment("BASIC_PITCH_WORKER_SMOKE_DB_PASSWORD"),
            minio_endpoint_url=endpoint,
            minio_access_key=_required_environment("BASIC_PITCH_WORKER_SMOKE_S3_ACCESS_KEY"),
            minio_secret_key=_required_environment("BASIC_PITCH_WORKER_SMOKE_S3_SECRET_KEY"),
            timeout_seconds=_bounded_integer(
                name="BASIC_PITCH_WORKER_SMOKE_TIMEOUT_SECONDS",
                value=os.environ.get("BASIC_PITCH_WORKER_SMOKE_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)),
                minimum=1,
                maximum=MAX_TIMEOUT_SECONDS,
            ),
        )


@dataclass(frozen=True)
class TaskObservation:
    """Safe durable status returned by the fixed `observe` database function."""

    publication_status: str
    published_at: datetime | None
    task_id: str | None
    task_status: str | None
    task_attempt_count: int | None
    task_lease_is_clear: bool
    task_completed_at: datetime | None
    job_status: str


@dataclass(frozen=True)
class VerifiedMidi:
    """Independent storage evidence that the smoke client observed on success."""

    size_bytes: int
    sha256: str


PREPARE_SQL = """
    SELECT smoke_job_id::text AS smoke_job_id, smoke_event_id::text AS smoke_event_id
    FROM public.clouddsp_basic_pitch_worker_smoke_prepare(%s, %s)
"""
OBSERVE_SQL = "SELECT * FROM public.clouddsp_basic_pitch_worker_smoke_observe()"
CLEANUP_SQL = "SELECT public.clouddsp_basic_pitch_worker_smoke_cleanup() AS cleaned"


def _canonical_uuid(value: object, *, optional: bool = False) -> str | None:
    """Normalize a driver UUID/text value without accepting another spelling."""

    if value is None and optional:
        return None
    if isinstance(value, UUID):
        return str(value)
    if not isinstance(value, str):
        raise BasicPitchWorkerSmokeAssertionError("Smoke database returned an invalid UUID field.")
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError) as error:
        raise BasicPitchWorkerSmokeAssertionError("Smoke database returned an invalid UUID field.") from error
    if str(parsed) != value:
        raise BasicPitchWorkerSmokeAssertionError("Smoke database returned a noncanonical UUID field.")
    return value


def build_controlled_wav() -> bytes:
    """Build one small valid PCM WAV without a file, library, or user input."""

    frames = bytearray()
    for index in range(WAV_SAMPLE_RATE * WAV_DURATION_SECONDS):
        sample = int(WAV_AMPLITUDE * 32767 * math.sin(2 * math.pi * WAV_FREQUENCY_HZ * index / WAV_SAMPLE_RATE))
        frames.extend(struct.pack("<h", sample))
    destination = io.BytesIO()
    with wave.open(destination, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(WAV_SAMPLE_RATE)
        writer.writeframes(bytes(frames))
    result = destination.getvalue()
    if not 44 <= len(result) <= MAX_SMOKE_WAV_BYTES or result[:4] != b"RIFF" or result[8:12] != b"WAVE":
        raise BasicPitchWorkerSmokeAssertionError("Controlled WAV construction is invalid.")
    return result


def _sha256(data: bytes) -> str:
    """Return the lower-case digest form required by database and S3 metadata."""

    return hashlib.sha256(data).hexdigest()


def controlled_stem_metadata(*, size_bytes: int, sha256: str) -> dict[str, str]:
    """Return the complete Demucs-compatible provenance Basic Pitch enforces."""

    if not 1 <= size_bytes <= MAX_SMOKE_WAV_BYTES or len(sha256) != 64:
        raise BasicPitchWorkerSmokeAssertionError("Controlled stem evidence is invalid.")
    return {
        "schema-version": "1",
        "producer": "demucs",
        "job-id": SMOKE_JOB_ID,
        "task-id": SYNTHETIC_DEMUCS_TASK_ID,
        "stem-name": STEM_NAME,
        "stem-mode": STEM_MODE,
        "size-bytes": str(size_bytes),
        "sha256": sha256,
    }


def _s3_error_code(error: BaseException) -> str | None:
    """Read only a conventional S3 error code; retain no server diagnostic."""

    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return None
    detail = response.get("Error")
    if not isinstance(detail, Mapping):
        return None
    code = detail.get("Code")
    return code if isinstance(code, str) else None


def assert_object_absent(client: S3Client, *, object_key: str) -> None:
    """Require one exact key to be absent without granting ListBucket access."""

    if object_key not in FIXED_OBJECT_KEYS:
        raise BasicPitchWorkerSmokeConfigurationError("Smoke object preflight key is not approved.")
    try:
        client.head_object(Bucket=UPLOADS_BUCKET, Key=object_key)
    except Exception as error:  # SDK exception types are deliberately not imported.
        if _s3_error_code(error) in {"404", "NoSuchKey", "NoSuchObject", "NotFound"}:
            return
        raise BasicPitchWorkerSmokeInfrastructureError("MinIO absence check is unavailable.") from error
    raise BasicPitchWorkerSmokeAssertionError("Fixed smoke object coordinates are not clean.")


def upload_controlled_stem(client: S3Client, *, wav_bytes: bytes, sha256: str) -> None:
    """Put only the fixed input key with exact type/metadata evidence."""

    try:
        client.put_object(
            Bucket=UPLOADS_BUCKET,
            Key=STEM_KEY,
            Body=wav_bytes,
            ContentLength=len(wav_bytes),
            ContentType=WAV_CONTENT_TYPE,
            Metadata=controlled_stem_metadata(size_bytes=len(wav_bytes), sha256=sha256),
        )
    except Exception as error:
        raise BasicPitchWorkerSmokeInfrastructureError("Controlled MinIO WAV upload is unavailable.") from error


def _one_mapping(cursor: DatabaseCursor, *, missing_message: str) -> Mapping[str, object]:
    """Require one dictionary-shaped function result, never a raw table row."""

    row = cursor.fetchone()
    if not isinstance(row, Mapping):
        raise BasicPitchWorkerSmokeAssertionError(missing_message)
    return row


def assert_database_is_clean(connection: DatabaseConnection) -> None:
    """Refuse to overwrite durable evidence from an earlier failed smoke run."""

    try:
        with connection.cursor() as cursor:
            cursor.execute(OBSERVE_SQL)
            if cursor.fetchone() is not None:
                raise BasicPitchWorkerSmokeAssertionError("Fixed smoke database coordinates are not clean.")
    except BasicPitchWorkerSmokeAssertionError:
        raise
    except Exception as error:
        raise BasicPitchWorkerSmokeInfrastructureError("Smoke database preflight is unavailable.") from error


def prepare_durable_smoke_event(connection: DatabaseConnection, *, size_bytes: int, sha256: str) -> None:
    """Call only the fixed `prepare` function after its MinIO source exists."""

    try:
        with connection.cursor() as cursor:
            cursor.execute(PREPARE_SQL, (size_bytes, sha256))
            row = _one_mapping(cursor, missing_message="Smoke database prepare returned no fixed event.")
    except BasicPitchWorkerSmokeAssertionError:
        raise
    except Exception as error:
        raise BasicPitchWorkerSmokeInfrastructureError("Smoke database prepare is unavailable.") from error
    if _canonical_uuid(row.get("smoke_job_id")) != SMOKE_JOB_ID or _canonical_uuid(row.get("smoke_event_id")) != SMOKE_EVENT_ID:
        raise BasicPitchWorkerSmokeAssertionError("Smoke database prepare returned unexpected coordinates.")


def _observation_from_row(row: Mapping[str, object]) -> TaskObservation:
    """Validate the compact projection before it controls a success decision."""

    expected_names = {
        "publication_status",
        "published_at",
        "task_id",
        "task_status",
        "task_attempt_count",
        "task_lease_is_clear",
        "task_completed_at",
        "job_status",
    }
    if set(row) != expected_names:
        raise BasicPitchWorkerSmokeAssertionError("Smoke database observation has an incompatible shape.")
    publication_status = row.get("publication_status")
    job_status = row.get("job_status")
    if not isinstance(publication_status, str) or not isinstance(job_status, str):
        raise BasicPitchWorkerSmokeAssertionError("Smoke database observation is invalid.")
    task_status = row.get("task_status")
    task_attempt_count = row.get("task_attempt_count")
    if task_status is not None and not isinstance(task_status, str):
        raise BasicPitchWorkerSmokeAssertionError("Smoke database task status is invalid.")
    if task_attempt_count is not None and (type(task_attempt_count) is not int or task_attempt_count < 0):
        raise BasicPitchWorkerSmokeAssertionError("Smoke database task attempt is invalid.")
    if type(row.get("task_lease_is_clear")) is not bool:
        raise BasicPitchWorkerSmokeAssertionError("Smoke database task lease state is invalid.")
    return TaskObservation(
        publication_status=publication_status,
        published_at=row.get("published_at") if isinstance(row.get("published_at"), datetime) else None,
        task_id=_canonical_uuid(row.get("task_id"), optional=True),
        task_status=task_status,
        task_attempt_count=task_attempt_count,
        task_lease_is_clear=row["task_lease_is_clear"],
        task_completed_at=row.get("task_completed_at") if isinstance(row.get("task_completed_at"), datetime) else None,
        job_status=job_status,
    )


def read_observation(connection: DatabaseConnection) -> TaskObservation:
    """Read only the fixed status projection from the restricted function."""

    try:
        with connection.cursor() as cursor:
            cursor.execute(OBSERVE_SQL)
            row = _one_mapping(cursor, missing_message="Smoke database observation is absent.")
    except BasicPitchWorkerSmokeAssertionError:
        raise
    except Exception as error:
        raise BasicPitchWorkerSmokeInfrastructureError("Smoke database observation is unavailable.") from error
    return _observation_from_row(row)


def _worker_completed(observation: TaskObservation) -> bool:
    """Require durable publication and the exact one-attempt task completion."""

    return (
        observation.publication_status == "published"
        and observation.published_at is not None
        and observation.task_id is not None
        and observation.task_status == "succeeded"
        and observation.task_attempt_count == 1
        and observation.task_lease_is_clear
        and observation.task_completed_at is not None
        and observation.job_status == "midi_processing"
    )


def wait_for_worker_completion(
    connection: DatabaseConnection,
    *,
    timeout_seconds: int,
    sleep_function: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> TaskObservation:
    """Poll durable state; RabbitMQ consumption is never observed directly."""

    if not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise BasicPitchWorkerSmokeConfigurationError("Smoke timeout is outside the reviewed range.")
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        observation = read_observation(connection)
        if _worker_completed(observation):
            return observation
        sleep_function(1)
    raise BasicPitchWorkerSmokeAssertionError("Timed out waiting for the Basic Pitch worker completion.")


def _normalized_metadata(value: object, *, expected_names: frozenset[str]) -> dict[str, str]:
    """Require complete non-ambiguous lower-case S3 metadata from MinIO."""

    if not isinstance(value, Mapping):
        raise BasicPitchWorkerSmokeAssertionError("MinIO metadata is invalid.")
    normalized: dict[str, str] = {}
    for name, item in value.items():
        if not isinstance(name, str) or not isinstance(item, str) or not name or not item:
            raise BasicPitchWorkerSmokeAssertionError("MinIO metadata is invalid.")
        lowered = name.lower()
        if lowered in normalized:
            raise BasicPitchWorkerSmokeAssertionError("MinIO metadata is ambiguous.")
        normalized[lowered] = item
    if set(normalized) != expected_names:
        raise BasicPitchWorkerSmokeAssertionError("MinIO metadata does not match the smoke contract.")
    return normalized


def _verify_standard_midi(data: bytes) -> None:
    """Independently validate bounded Standard MIDI File framing and all tracks."""

    if len(data) < 22 or data[:4] != b"MThd" or struct.unpack(">I", data[4:8])[0] != 6:
        raise BasicPitchWorkerSmokeAssertionError("Stored Basic Pitch MIDI is invalid.")
    format_type, track_count, division = struct.unpack(">HHH", data[8:14])
    if format_type not in {0, 1, 2} or not 1 <= track_count <= 64 or (format_type == 0 and track_count != 1) or division == 0:
        raise BasicPitchWorkerSmokeAssertionError("Stored Basic Pitch MIDI is invalid.")
    offset = 14
    for _ in range(track_count):
        if offset + 8 > len(data) or data[offset : offset + 4] != b"MTrk":
            raise BasicPitchWorkerSmokeAssertionError("Stored Basic Pitch MIDI is invalid.")
        track_size = struct.unpack(">I", data[offset + 4 : offset + 8])[0]
        offset += 8
        if track_size < 1 or offset + track_size > len(data):
            raise BasicPitchWorkerSmokeAssertionError("Stored Basic Pitch MIDI is invalid.")
        offset += track_size
    if offset != len(data):
        raise BasicPitchWorkerSmokeAssertionError("Stored Basic Pitch MIDI is invalid.")


def verify_stored_midi(client: S3Client, *, observation: TaskObservation, input_sha256: str) -> VerifiedMidi:
    """Head and stream-read the exact private MIDI with independent evidence."""

    if observation.task_id is None:
        raise BasicPitchWorkerSmokeAssertionError("Completed smoke task identity is missing.")
    try:
        head = client.head_object(Bucket=UPLOADS_BUCKET, Key=MIDI_KEY)
    except Exception as error:
        raise BasicPitchWorkerSmokeInfrastructureError("Smoke MIDI HeadObject is unavailable.") from error
    if not isinstance(head, Mapping):
        raise BasicPitchWorkerSmokeAssertionError("Smoke MIDI HeadObject is invalid.")
    size_bytes = head.get("ContentLength")
    if type(size_bytes) is not int or not 1 <= size_bytes <= MAX_MIDI_BYTES or head.get("ContentType") != MIDI_CONTENT_TYPE:
        raise BasicPitchWorkerSmokeAssertionError("Stored Basic Pitch MIDI headers are invalid.")
    metadata = _normalized_metadata(head.get("Metadata"), expected_names=_OUTPUT_METADATA_NAMES)
    if (
        metadata["schema-version"] != "1"
        or metadata["producer"] != "basic-pitch"
        or metadata["job-id"] != SMOKE_JOB_ID
        or _canonical_uuid(metadata["task-id"]) != observation.task_id
        or metadata["request-event-id"] != SMOKE_EVENT_ID
        or metadata["stem-name"] != STEM_NAME
        or metadata["stem-mode"] != STEM_MODE
        or metadata["size-bytes"] != str(size_bytes)
        or len(metadata["sha256"]) != 64
        or metadata["input-stem-sha256"] != input_sha256
    ):
        raise BasicPitchWorkerSmokeAssertionError("Stored Basic Pitch MIDI metadata is invalid.")
    try:
        response = client.get_object(Bucket=UPLOADS_BUCKET, Key=MIDI_KEY)
        body = response.get("Body") if isinstance(response, Mapping) else None
        if body is None or not callable(getattr(body, "read", None)):
            raise BasicPitchWorkerSmokeAssertionError("Smoke MIDI body is invalid.")
        content = bytearray()
        hasher = hashlib.sha256()
        while len(content) < size_bytes:
            chunk = body.read(min(READ_CHUNK_BYTES, size_bytes - len(content)))
            if not isinstance(chunk, bytes) or not chunk:
                raise BasicPitchWorkerSmokeAssertionError("Smoke MIDI body is incomplete.")
            content.extend(chunk)
            hasher.update(chunk)
        if body.read(1) or len(content) != size_bytes:
            raise BasicPitchWorkerSmokeAssertionError("Smoke MIDI body length changed while reading.")
    except BasicPitchWorkerSmokeAssertionError:
        raise
    except Exception as error:
        raise BasicPitchWorkerSmokeInfrastructureError("Smoke MIDI download is unavailable.") from error
    finally:
        with suppress(Exception):
            if "body" in locals() and body is not None:
                body.close()
    digest = hasher.hexdigest()
    if digest != metadata["sha256"]:
        raise BasicPitchWorkerSmokeAssertionError("Stored Basic Pitch MIDI checksum is invalid.")
    _verify_standard_midi(bytes(content))
    return VerifiedMidi(size_bytes=size_bytes, sha256=digest)


def _delete_exact_object(client: S3Client, *, object_key: str) -> None:
    """Delete only a completed test coordinate; never enumerate a prefix."""

    if object_key not in FIXED_OBJECT_KEYS:
        raise BasicPitchWorkerSmokeConfigurationError("Smoke cleanup key is not approved.")
    try:
        client.delete_object(Bucket=UPLOADS_BUCKET, Key=object_key)
    except Exception as error:
        raise BasicPitchWorkerSmokeInfrastructureError("Smoke MinIO cleanup is unavailable.") from error


def cleanup_successful_smoke(connection: DatabaseConnection, client: S3Client) -> None:
    """Remove exact objects first, then let fixed SQL delete its succeeded Job."""

    _delete_exact_object(client, object_key=MIDI_KEY)
    _delete_exact_object(client, object_key=STEM_KEY)
    try:
        with connection.cursor() as cursor:
            cursor.execute(CLEANUP_SQL)
            row = _one_mapping(cursor, missing_message="Smoke database cleanup returned no result.")
    except BasicPitchWorkerSmokeAssertionError:
        raise
    except Exception as error:
        raise BasicPitchWorkerSmokeInfrastructureError("Smoke database cleanup is unavailable.") from error
    if row.get("cleaned") is not True:
        raise BasicPitchWorkerSmokeAssertionError("Smoke database refused successful cleanup.")


def run_smoke(
    connection: DatabaseConnection,
    client: S3Client,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    report: Callable[[str], None] = print,
    sleep_function: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> VerifiedMidi:
    """Execute the contract in its only safe order and return success evidence."""

    report("Basic Pitch worker smoke: checking fixed coordinates")
    assert_database_is_clean(connection)
    assert_object_absent(client, object_key=STEM_KEY)
    assert_object_absent(client, object_key=MIDI_KEY)
    wav_bytes = build_controlled_wav()
    stem_sha256 = _sha256(wav_bytes)
    report("Basic Pitch worker smoke: uploading controlled WAV")
    try:
        upload_controlled_stem(client, wav_bytes=wav_bytes, sha256=stem_sha256)
    except Exception:
        # An upload error can occur after MinIO accepted bytes. It is still
        # before a durable event exists, so remove only this fixed input key.
        with suppress(Exception):
            _delete_exact_object(client, object_key=STEM_KEY)
        raise
    report("Basic Pitch worker smoke: creating durable request")
    # Do not delete input after this call begins: a lost DB response could mean
    # PostgreSQL committed the event and the real worker may soon need the WAV.
    prepare_durable_smoke_event(connection, size_bytes=len(wav_bytes), sha256=stem_sha256)
    report("Basic Pitch worker smoke: waiting for deployed worker completion")
    observation = wait_for_worker_completion(
        connection,
        timeout_seconds=timeout_seconds,
        sleep_function=sleep_function,
        monotonic=monotonic,
    )
    report("Basic Pitch worker smoke: verifying stored MIDI")
    verified = verify_stored_midi(client, observation=observation, input_sha256=stem_sha256)
    report("Basic Pitch worker smoke: cleaning exact successful evidence")
    cleanup_successful_smoke(connection, client)
    report("Basic Pitch worker smoke passed")
    return verified


def open_database_connection(settings: SmokeSettings) -> DatabaseConnection:
    """Create an autocommit dictionary-row connection only for fixed functions."""

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as error:
        raise BasicPitchWorkerSmokeInfrastructureError("Pinned PostgreSQL smoke dependency is unavailable.") from error
    try:
        return psycopg.connect(
            host=settings.database_host,
            port=settings.database_port,
            dbname=settings.database_name,
            user=settings.database_username,
            password=settings.database_password,
            connect_timeout=3,
            autocommit=True,
            row_factory=dict_row,
            options="-c statement_timeout=5000",
        )
    except Exception as error:
        raise BasicPitchWorkerSmokeInfrastructureError("Smoke PostgreSQL connection is unavailable.") from error


def open_minio_client(settings: SmokeSettings) -> S3Client:
    """Create a private path-style S3 client without ambient AWS credentials."""

    try:
        import boto3
        from botocore.config import Config
    except ImportError as error:
        raise BasicPitchWorkerSmokeInfrastructureError("Pinned S3 smoke dependency is unavailable.") from error
    try:
        return boto3.client(
            "s3",
            endpoint_url=settings.minio_endpoint_url,
            aws_access_key_id=settings.minio_access_key,
            aws_secret_access_key=settings.minio_secret_key,
            region_name="us-east-1",
            config=Config(
                connect_timeout=3,
                read_timeout=10,
                retries={"max_attempts": 2, "mode": "standard"},
                s3={"addressing_style": "path"},
            ),
        )
    except Exception as error:
        raise BasicPitchWorkerSmokeInfrastructureError("Smoke MinIO client is unavailable.") from error


def main() -> int:
    """Run the smoke client as a future PID-1 Job process with safe output."""

    connection: DatabaseConnection | None = None
    try:
        settings = SmokeSettings.from_environment()
        connection = open_database_connection(settings)
        client = open_minio_client(settings)
        run_smoke(connection, client, timeout_seconds=settings.timeout_seconds)
        return 0
    except (BasicPitchWorkerSmokeConfigurationError, BasicPitchWorkerSmokeAssertionError, BasicPitchWorkerSmokeInfrastructureError) as error:
        print(f"Basic Pitch worker smoke failed: {error}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            with suppress(Exception):
                connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
