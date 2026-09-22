"""Run one bounded integration smoke test against the deployed Demucs worker.

The client deliberately owns only the stimulus, observation, artifact proof,
and safe cleanup for one fixed coordinate.  It does not hold a RabbitMQ
credential, publish a message, consume a queue, invoke a model, or make any
Kubernetes API call.  Those duties stay with the deployed dispatcher and
workers, which makes a successful run meaningful:

``controlled MinIO source -> PostgreSQL outbox -> dispatcher -> Demucs``
``-> durable stem/task evidence -> downstream-safe cleanup``.
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
from typing import Protocol
from uuid import UUID


# These literal coordinates are duplicated in the README, database functions,
# MinIO policy, and cleanup guard.  They are not environment settings: allowing
# a manifest to substitute an arbitrary Job/key would broaden this test's
# authority beyond its reviewed exact-coordinate identities.
SMOKE_JOB_ID = "12139966-5891-4d53-a817-f3ce1f264c61"
SMOKE_SOURCE_EVENT_ID = "e20cf942-ef7b-478e-89c4-f4795ed801ec"
SMOKE_OWNER_SUB = "clouddsp-demucs-worker-smoke"
UPLOADS_BUCKET = "clouddsp-uploads"
STEM_MODE = "2-stems"
SOURCE_KEY = f"uploads/{SMOKE_JOB_ID}/demucs-worker-smoke.wav"
STEM_NAMES = ("vocals", "no_vocals")
STEM_KEYS = tuple(f"stems/{SMOKE_JOB_ID}/{stem_name}.wav" for stem_name in STEM_NAMES)
MIDI_KEYS = tuple(f"midi/{SMOKE_JOB_ID}/{stem_name}.mid" for stem_name in STEM_NAMES)
FIXED_OBJECT_KEYS = frozenset((SOURCE_KEY, *STEM_KEYS, *MIDI_KEYS))

SOURCE_CONTENT_TYPE = "audio/wav"
STEM_CONTENT_TYPE = "audio/wav"
SOURCE_SAMPLE_RATE = 44_100
SOURCE_DURATION_SECONDS = 2
MAX_SOURCE_BYTES = 1 * 1024 * 1024
MAX_STEM_BYTES = 64 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 600
MAX_TIMEOUT_SECONDS = 720

_SOURCE_METADATA_NAMES = frozenset({"job-id", "stem-mode"})
_STEM_METADATA_NAMES = frozenset(
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


class DemucsWorkerSmokeConfigurationError(RuntimeError):
    """Report malformed settings without exposing endpoint or Secret values."""


class DemucsWorkerSmokeAssertionError(RuntimeError):
    """Report a durable/media contract failure using safe, compact wording."""


class DemucsWorkerSmokeInfrastructureError(RuntimeError):
    """Hide driver/S3 transport diagnostics that might contain credentials."""


class S3Client(Protocol):
    """The narrow S3-compatible surface needed for five literal keys only."""

    def put_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def head_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def delete_object(self, **kwargs: object) -> Mapping[str, object]: ...


class DatabaseCursor(Protocol):
    """Parameterized function calls only; the client has no raw-table SQL."""

    def execute(self, query: str, params: tuple[object, ...] = ()) -> object: ...

    def fetchone(self) -> Mapping[str, object] | None: ...

    def __enter__(self) -> "DatabaseCursor": ...

    def __exit__(self, *args: object) -> object: ...


class DatabaseConnection(Protocol):
    """The autocommit connection used solely for reviewed functions."""

    def cursor(self) -> DatabaseCursor: ...

    def close(self) -> object: ...


def _required_environment(name: str, *, default: str | None = None) -> str:
    """Read a nonempty environment setting without including its value in errors."""

    value = os.environ.get(name, default)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise DemucsWorkerSmokeConfigurationError(f"Required environment variable {name} is absent.")
    return value


def _bounded_integer(*, name: str, value: str, minimum: int, maximum: int) -> int:
    """Parse a port/timeout before it can affect a network connection or wait."""

    try:
        parsed = int(value)
    except ValueError as error:
        raise DemucsWorkerSmokeConfigurationError(f"{name} must be an integer.") from error
    if not minimum <= parsed <= maximum:
        raise DemucsWorkerSmokeConfigurationError(f"{name} is outside its reviewed range.")
    return parsed


@dataclass(frozen=True)
class SmokeSettings:
    """Private service routes plus the two separately constrained identities."""

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
        """Load only reviewed in-cluster routes; reject browser/host endpoints."""

        host = _required_environment(
            "DEMUCS_WORKER_SMOKE_DB_HOST", default="clouddsp-postgresql.clouddsp-data.svc"
        )
        if host == "localhost" or host.endswith(".localhost"):
            raise DemucsWorkerSmokeConfigurationError("Smoke database host must be private Service DNS.")
        endpoint = _required_environment(
            "DEMUCS_WORKER_SMOKE_MINIO_ENDPOINT_URL",
            default="http://clouddsp-minio.clouddsp-data.svc:9000",
        ).rstrip("/")
        if endpoint != "http://clouddsp-minio.clouddsp-data.svc:9000":
            raise DemucsWorkerSmokeConfigurationError("Smoke MinIO endpoint must be the reviewed private Service.")
        return cls(
            database_host=host,
            database_port=_bounded_integer(
                name="DEMUCS_WORKER_SMOKE_DB_PORT",
                value=os.environ.get("DEMUCS_WORKER_SMOKE_DB_PORT", "5432"),
                minimum=1,
                maximum=65_535,
            ),
            database_name=_required_environment("DEMUCS_WORKER_SMOKE_DB_NAME"),
            database_username=_required_environment("DEMUCS_WORKER_SMOKE_DB_USERNAME"),
            database_password=_required_environment("DEMUCS_WORKER_SMOKE_DB_PASSWORD"),
            minio_endpoint_url=endpoint,
            minio_access_key=_required_environment("DEMUCS_WORKER_SMOKE_S3_ACCESS_KEY"),
            minio_secret_key=_required_environment("DEMUCS_WORKER_SMOKE_S3_SECRET_KEY"),
            timeout_seconds=_bounded_integer(
                name="DEMUCS_WORKER_SMOKE_TIMEOUT_SECONDS",
                value=os.environ.get("DEMUCS_WORKER_SMOKE_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)),
                minimum=1,
                maximum=MAX_TIMEOUT_SECONDS,
            ),
        )


@dataclass(frozen=True)
class SmokeObservation:
    """The fixed function's intentionally compact durable status projection."""

    source_event_publication_status: str
    source_event_published_at: datetime | None
    demucs_task_id: str | None
    demucs_task_status: str | None
    demucs_task_attempt_count: int | None
    demucs_task_lease_is_clear: bool
    demucs_task_completed_at: datetime | None
    job_status: str
    stem_count: int
    downstream_event_count: int
    downstream_published_count: int
    basic_pitch_task_count: int
    basic_pitch_succeeded_first_attempt_count: int
    basic_pitch_active_task_count: int
    basic_pitch_failed_task_count: int


@dataclass(frozen=True)
class VerifiedStem:
    """Independent object-store proof retained only as smoke success evidence."""

    stem_name: str
    size_bytes: int
    sha256: str


PREPARE_SQL = """
    SELECT smoke_job_id::text AS smoke_job_id, smoke_event_id::text AS smoke_event_id
    FROM public.clouddsp_demucs_worker_smoke_prepare(%s, %s)
"""
OBSERVE_SQL = "SELECT * FROM public.clouddsp_demucs_worker_smoke_observe()"
CLEANUP_SQL = "SELECT public.clouddsp_demucs_worker_smoke_cleanup() AS cleaned"


def _canonical_uuid(value: object, *, optional: bool = False) -> str | None:
    """Normalize Psycopg UUID/text values and reject alternate spellings."""

    if value is None and optional:
        return None
    if isinstance(value, UUID):
        return str(value)
    if not isinstance(value, str):
        raise DemucsWorkerSmokeAssertionError("Smoke database returned an invalid UUID field.")
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise DemucsWorkerSmokeAssertionError("Smoke database returned an invalid UUID field.") from error
    if canonical != value:
        raise DemucsWorkerSmokeAssertionError("Smoke database returned a noncanonical UUID field.")
    return canonical


def build_controlled_wav() -> bytes:
    """Build a deterministic, small stereo PCM WAV wholly in memory.

    A two-second musical input is long enough for normal Demucs padding/window
    handling while remaining tiny enough that storage transfer is never the
    dominant part of this worker smoke test.
    """

    frames = bytearray()
    for index in range(SOURCE_SAMPLE_RATE * SOURCE_DURATION_SECONDS):
        left = int(0.22 * 32767 * math.sin(2 * math.pi * 220 * index / SOURCE_SAMPLE_RATE))
        right = int(0.18 * 32767 * math.sin(2 * math.pi * 440 * index / SOURCE_SAMPLE_RATE))
        frames.extend(struct.pack("<hh", left, right))
    destination = io.BytesIO()
    with wave.open(destination, "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(SOURCE_SAMPLE_RATE)
        writer.writeframes(bytes(frames))
    result = destination.getvalue()
    if not 44 <= len(result) <= MAX_SOURCE_BYTES or result[:4] != b"RIFF" or result[8:12] != b"WAVE":
        raise DemucsWorkerSmokeAssertionError("Controlled Demucs source WAV construction is invalid.")
    return result


def _sha256(data: bytes) -> str:
    """Return the lower-case SHA-256 spelling used in object metadata."""

    return hashlib.sha256(data).hexdigest()


def controlled_source_metadata() -> dict[str, str]:
    """Return exactly the metadata enforced by normal direct-upload intake."""

    return {"job-id": SMOKE_JOB_ID, "stem-mode": STEM_MODE}


def _s3_error_code(error: BaseException) -> str | None:
    """Extract a conventional S3 error category without logging server details."""

    response = getattr(error, "response", None)
    detail = response.get("Error") if isinstance(response, Mapping) else None
    code = detail.get("Code") if isinstance(detail, Mapping) else None
    return code if isinstance(code, str) else None


def _assert_fixed_key(object_key: str) -> None:
    """Keep every S3 operation confined to the static reviewed key set."""

    if object_key not in FIXED_OBJECT_KEYS:
        raise DemucsWorkerSmokeConfigurationError("Smoke object key is outside the approved fixed set.")


def assert_object_absent(client: S3Client, *, object_key: str) -> None:
    """Require exact-key absence without granting the identity ListBucket."""

    _assert_fixed_key(object_key)
    try:
        client.head_object(Bucket=UPLOADS_BUCKET, Key=object_key)
    except Exception as error:  # SDK exception types are intentionally not imported.
        if _s3_error_code(error) in {"404", "NoSuchKey", "NoSuchObject", "NotFound"}:
            return
        raise DemucsWorkerSmokeInfrastructureError("MinIO absence check is unavailable.") from error
    raise DemucsWorkerSmokeAssertionError("Fixed smoke object coordinates are not clean.")


def _normalized_metadata(value: object, *, expected_names: frozenset[str]) -> dict[str, str]:
    """Require one nonempty case-insensitive spelling for each contract key."""

    if not isinstance(value, Mapping):
        raise DemucsWorkerSmokeAssertionError("MinIO metadata is invalid.")
    normalized: dict[str, str] = {}
    for raw_name, raw_value in value.items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, str) or not raw_name or not raw_value:
            raise DemucsWorkerSmokeAssertionError("MinIO metadata is invalid.")
        name = raw_name.lower()
        if name in normalized:
            raise DemucsWorkerSmokeAssertionError("MinIO metadata is ambiguous.")
        normalized[name] = raw_value
    if set(normalized) != expected_names:
        raise DemucsWorkerSmokeAssertionError("MinIO metadata does not match the smoke contract.")
    return normalized


def upload_and_verify_controlled_source(client: S3Client, *, wav_bytes: bytes, sha256: str) -> None:
    """Write and immediately HeadObject-check only the fixed normal-source key."""

    if len(wav_bytes) < 1 or len(wav_bytes) > MAX_SOURCE_BYTES or _sha256(wav_bytes) != sha256:
        raise DemucsWorkerSmokeAssertionError("Controlled source byte evidence is invalid.")
    try:
        client.put_object(
            Bucket=UPLOADS_BUCKET,
            Key=SOURCE_KEY,
            Body=wav_bytes,
            ContentLength=len(wav_bytes),
            ContentType=SOURCE_CONTENT_TYPE,
            Metadata=controlled_source_metadata(),
        )
        head = client.head_object(Bucket=UPLOADS_BUCKET, Key=SOURCE_KEY)
    except Exception as error:
        raise DemucsWorkerSmokeInfrastructureError("Controlled MinIO source upload is unavailable.") from error
    if not isinstance(head, Mapping) or head.get("ContentLength") != len(wav_bytes) or head.get("ContentType") != SOURCE_CONTENT_TYPE:
        raise DemucsWorkerSmokeAssertionError("Stored controlled source headers are invalid.")
    if _normalized_metadata(head.get("Metadata"), expected_names=_SOURCE_METADATA_NAMES) != controlled_source_metadata():
        raise DemucsWorkerSmokeAssertionError("Stored controlled source metadata is invalid.")


def _one_mapping(cursor: DatabaseCursor, *, missing_message: str) -> Mapping[str, object]:
    """Require one dictionary row from a fixed function, never a table result."""

    row = cursor.fetchone()
    if not isinstance(row, Mapping):
        raise DemucsWorkerSmokeAssertionError(missing_message)
    return row


def assert_database_is_clean(connection: DatabaseConnection) -> None:
    """Refuse to overwrite durable evidence from an earlier fixed-coordinate run."""

    try:
        with connection.cursor() as cursor:
            cursor.execute(OBSERVE_SQL)
            if cursor.fetchone() is not None:
                raise DemucsWorkerSmokeAssertionError("Fixed smoke database coordinates are not clean.")
    except DemucsWorkerSmokeAssertionError:
        raise
    except Exception as error:
        raise DemucsWorkerSmokeInfrastructureError("Smoke database preflight is unavailable.") from error


def prepare_durable_smoke_event(connection: DatabaseConnection, *, size_bytes: int, sha256: str) -> None:
    """Create the one source-uploaded Job/event after its source object exists."""

    try:
        with connection.cursor() as cursor:
            cursor.execute(PREPARE_SQL, (size_bytes, sha256))
            row = _one_mapping(cursor, missing_message="Smoke database prepare returned no fixed event.")
    except DemucsWorkerSmokeAssertionError:
        raise
    except Exception as error:
        raise DemucsWorkerSmokeInfrastructureError("Smoke database prepare is unavailable.") from error
    if _canonical_uuid(row.get("smoke_job_id")) != SMOKE_JOB_ID or _canonical_uuid(row.get("smoke_event_id")) != SMOKE_SOURCE_EVENT_ID:
        raise DemucsWorkerSmokeAssertionError("Smoke database prepare returned unexpected coordinates.")


def _nonnegative_count(value: object) -> int:
    """Validate one aggregate count before it can determine a success/cleanup path."""

    if type(value) is not int or value < 0:
        raise DemucsWorkerSmokeAssertionError("Smoke database observation contains an invalid count.")
    return value


def _observation_from_row(row: Mapping[str, object]) -> SmokeObservation:
    """Validate the complete fixed observer projection before acting on it."""

    expected_names = {
        "source_event_publication_status", "source_event_published_at", "demucs_task_id",
        "demucs_task_status", "demucs_task_attempt_count", "demucs_task_lease_is_clear",
        "demucs_task_completed_at", "job_status", "stem_count", "downstream_event_count",
        "downstream_published_count", "basic_pitch_task_count",
        "basic_pitch_succeeded_first_attempt_count", "basic_pitch_active_task_count",
        "basic_pitch_failed_task_count",
    }
    if set(row) != expected_names:
        raise DemucsWorkerSmokeAssertionError("Smoke database observation has an incompatible shape.")
    source_status = row.get("source_event_publication_status")
    job_status = row.get("job_status")
    task_status = row.get("demucs_task_status")
    attempts = row.get("demucs_task_attempt_count")
    if not isinstance(source_status, str) or not isinstance(job_status, str):
        raise DemucsWorkerSmokeAssertionError("Smoke database observation is invalid.")
    if task_status is not None and not isinstance(task_status, str):
        raise DemucsWorkerSmokeAssertionError("Smoke database task status is invalid.")
    if attempts is not None and (type(attempts) is not int or attempts < 0):
        raise DemucsWorkerSmokeAssertionError("Smoke database task attempt is invalid.")
    if type(row.get("demucs_task_lease_is_clear")) is not bool:
        raise DemucsWorkerSmokeAssertionError("Smoke database lease state is invalid.")
    for timestamp_name in ("source_event_published_at", "demucs_task_completed_at"):
        value = row.get(timestamp_name)
        if value is not None and not isinstance(value, datetime):
            raise DemucsWorkerSmokeAssertionError("Smoke database timestamp is invalid.")
    return SmokeObservation(
        source_event_publication_status=source_status,
        source_event_published_at=row["source_event_published_at"],
        demucs_task_id=_canonical_uuid(row.get("demucs_task_id"), optional=True),
        demucs_task_status=task_status,
        demucs_task_attempt_count=attempts,
        demucs_task_lease_is_clear=row["demucs_task_lease_is_clear"],
        demucs_task_completed_at=row["demucs_task_completed_at"],
        job_status=job_status,
        stem_count=_nonnegative_count(row.get("stem_count")),
        downstream_event_count=_nonnegative_count(row.get("downstream_event_count")),
        downstream_published_count=_nonnegative_count(row.get("downstream_published_count")),
        basic_pitch_task_count=_nonnegative_count(row.get("basic_pitch_task_count")),
        basic_pitch_succeeded_first_attempt_count=_nonnegative_count(row.get("basic_pitch_succeeded_first_attempt_count")),
        basic_pitch_active_task_count=_nonnegative_count(row.get("basic_pitch_active_task_count")),
        basic_pitch_failed_task_count=_nonnegative_count(row.get("basic_pitch_failed_task_count")),
    )


def read_observation(connection: DatabaseConnection) -> SmokeObservation:
    """Read only the safe fixed observation; no raw SQL/table values escape."""

    try:
        with connection.cursor() as cursor:
            cursor.execute(OBSERVE_SQL)
            row = _one_mapping(cursor, missing_message="Smoke database observation is absent.")
    except DemucsWorkerSmokeAssertionError:
        raise
    except Exception as error:
        raise DemucsWorkerSmokeInfrastructureError("Smoke database observation is unavailable.") from error
    return _observation_from_row(row)


def _demucs_completed(observation: SmokeObservation) -> bool:
    """Recognize the one durable Demucs stage result this smoke requires."""

    return (
        observation.source_event_publication_status == "published"
        and observation.source_event_published_at is not None
        and observation.demucs_task_id is not None
        and observation.demucs_task_status == "succeeded"
        and observation.demucs_task_attempt_count == 1
        and observation.demucs_task_lease_is_clear
        and observation.demucs_task_completed_at is not None
        and observation.job_status == "midi_processing"
        and observation.stem_count == len(STEM_NAMES)
        and observation.downstream_event_count == len(STEM_NAMES)
    )


def wait_for_demucs_completion(
    connection: DatabaseConnection,
    *,
    timeout_seconds: int,
    sleep_function: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> SmokeObservation:
    """Poll PostgreSQL authority rather than racing the worker's AMQP queue."""

    if not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise DemucsWorkerSmokeConfigurationError("Smoke timeout is outside the reviewed range.")
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        observation = read_observation(connection)
        if observation.demucs_task_status == "failed":
            raise DemucsWorkerSmokeAssertionError("Demucs worker recorded a terminal task failure.")
        if _demucs_completed(observation):
            return observation
        sleep_function(2)
    raise DemucsWorkerSmokeAssertionError("Timed out waiting for deployed Demucs worker completion.")


def _stream_and_hash_body(body: object, *, size_bytes: int, limit: int) -> tuple[bytes, str]:
    """Read exactly one bounded S3 body and prove its metadata checksum truthfully."""

    if size_bytes < 1 or size_bytes > limit or not callable(getattr(body, "read", None)):
        raise DemucsWorkerSmokeAssertionError("MinIO object body is invalid.")
    content = bytearray()
    hasher = hashlib.sha256()
    try:
        while len(content) < size_bytes:
            chunk = body.read(min(READ_CHUNK_BYTES, size_bytes - len(content)))
            if not isinstance(chunk, bytes) or not chunk:
                raise DemucsWorkerSmokeAssertionError("MinIO object body is incomplete.")
            content.extend(chunk)
            hasher.update(chunk)
        if body.read(1) or len(content) != size_bytes:
            raise DemucsWorkerSmokeAssertionError("MinIO object body length changed while reading.")
    finally:
        with suppress(Exception):
            body.close()
    return bytes(content), hasher.hexdigest()


def verify_stored_stem(client: S3Client, *, observation: SmokeObservation, stem_name: str) -> VerifiedStem:
    """Independently validate one real Demucs WAV's headers, metadata, and bytes."""

    if stem_name not in STEM_NAMES or observation.demucs_task_id is None:
        raise DemucsWorkerSmokeAssertionError("Demucs stem verification input is invalid.")
    object_key = f"stems/{SMOKE_JOB_ID}/{stem_name}.wav"
    try:
        head = client.head_object(Bucket=UPLOADS_BUCKET, Key=object_key)
    except Exception as error:
        raise DemucsWorkerSmokeInfrastructureError("Demucs stem HeadObject is unavailable.") from error
    if not isinstance(head, Mapping):
        raise DemucsWorkerSmokeAssertionError("Demucs stem HeadObject is invalid.")
    size_bytes = head.get("ContentLength")
    if type(size_bytes) is not int or not 44 <= size_bytes <= MAX_STEM_BYTES or head.get("ContentType") != STEM_CONTENT_TYPE:
        raise DemucsWorkerSmokeAssertionError("Demucs stem headers are invalid.")
    metadata = _normalized_metadata(head.get("Metadata"), expected_names=_STEM_METADATA_NAMES)
    if (
        metadata["schema-version"] != "1"
        or metadata["producer"] != "demucs"
        or metadata["job-id"] != SMOKE_JOB_ID
        or _canonical_uuid(metadata["task-id"]) != observation.demucs_task_id
        or metadata["stem-name"] != stem_name
        or metadata["stem-mode"] != STEM_MODE
        or metadata["size-bytes"] != str(size_bytes)
        or len(metadata["sha256"]) != 64
    ):
        raise DemucsWorkerSmokeAssertionError("Demucs stem metadata is invalid.")
    try:
        response = client.get_object(Bucket=UPLOADS_BUCKET, Key=object_key)
        body = response.get("Body") if isinstance(response, Mapping) else None
        content, digest = _stream_and_hash_body(body, size_bytes=size_bytes, limit=MAX_STEM_BYTES)
    except DemucsWorkerSmokeAssertionError:
        raise
    except Exception as error:
        raise DemucsWorkerSmokeInfrastructureError("Demucs stem download is unavailable.") from error
    # RIFF/WAVE framing demonstrates this is a WAV file without turning a
    # pipeline smoke test into a model-quality or full audio-decoding benchmark.
    if content[:4] != b"RIFF" or content[8:12] != b"WAVE" or digest != metadata["sha256"]:
        raise DemucsWorkerSmokeAssertionError("Demucs stem content/hash is invalid.")
    return VerifiedStem(stem_name=stem_name, size_bytes=size_bytes, sha256=digest)


def _downstream_cleanup_is_safe(observation: SmokeObservation) -> bool:
    """Permit cleanup only after both normal Basic Pitch consumers are idle/safe."""

    return (
        observation.downstream_published_count == len(STEM_NAMES)
        and observation.basic_pitch_task_count == len(STEM_NAMES)
        and observation.basic_pitch_succeeded_first_attempt_count == len(STEM_NAMES)
        and observation.basic_pitch_active_task_count == 0
        and observation.basic_pitch_failed_task_count == 0
    )


def wait_for_downstream_cleanup_barrier(
    connection: DatabaseConnection,
    *,
    timeout_seconds: int,
    sleep_function: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> SmokeObservation:
    """Wait only to avoid deleting stems still used by real downstream workers."""

    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        observation = read_observation(connection)
        if observation.basic_pitch_failed_task_count:
            raise DemucsWorkerSmokeAssertionError("Downstream cleanup barrier recorded a terminal task failure.")
        if _downstream_cleanup_is_safe(observation):
            return observation
        sleep_function(2)
    raise DemucsWorkerSmokeAssertionError("Timed out waiting for safe downstream cleanup barrier.")


def _delete_exact_object(client: S3Client, *, object_key: str) -> None:
    """Delete one fixed coordinate only after all required durable proofs exist."""

    _assert_fixed_key(object_key)
    try:
        client.delete_object(Bucket=UPLOADS_BUCKET, Key=object_key)
    except Exception as error:
        raise DemucsWorkerSmokeInfrastructureError("Smoke MinIO cleanup is unavailable.") from error


def cleanup_successful_smoke(connection: DatabaseConnection, client: S3Client) -> None:
    """Delete five literal objects, then invoke guarded fixed database cleanup."""

    # Delete downstream result objects first. The database guard below confirms
    # their workers are complete; a storage cleanup error deliberately leaves
    # the Job/event/task evidence intact for diagnosis.
    for object_key in (*MIDI_KEYS, *STEM_KEYS, SOURCE_KEY):
        _delete_exact_object(client, object_key=object_key)
    try:
        with connection.cursor() as cursor:
            cursor.execute(CLEANUP_SQL)
            row = _one_mapping(cursor, missing_message="Smoke database cleanup returned no result.")
    except DemucsWorkerSmokeAssertionError:
        raise
    except Exception as error:
        raise DemucsWorkerSmokeInfrastructureError("Smoke database cleanup is unavailable.") from error
    if row.get("cleaned") is not True:
        raise DemucsWorkerSmokeAssertionError("Smoke database refused successful cleanup.")


def run_smoke(
    connection: DatabaseConnection,
    client: S3Client,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    report: Callable[[str], None] = print,
    sleep_function: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[VerifiedStem, ...]:
    """Execute the only safe Demucs smoke ordering and return stem evidence."""

    report("Demucs worker smoke: checking fixed coordinates")
    assert_database_is_clean(connection)
    for object_key in FIXED_OBJECT_KEYS:
        assert_object_absent(client, object_key=object_key)

    source = build_controlled_wav()
    source_sha256 = _sha256(source)
    report("Demucs worker smoke: uploading controlled source WAV")
    try:
        upload_and_verify_controlled_source(client, wav_bytes=source, sha256=source_sha256)
    except Exception:
        # Before `prepare` starts, a failed upload may have left only this
        # source. Remove exactly it; no worker can legitimately need it yet.
        with suppress(Exception):
            _delete_exact_object(client, object_key=SOURCE_KEY)
        raise

    report("Demucs worker smoke: creating durable source event")
    # Once `prepare` begins, preserve source/evidence even if the caller loses
    # the DB response: PostgreSQL may have committed and Demucs may need it.
    prepare_durable_smoke_event(connection, size_bytes=len(source), sha256=source_sha256)

    report("Demucs worker smoke: waiting for deployed Demucs completion")
    demucs_observation = wait_for_demucs_completion(
        connection,
        timeout_seconds=timeout_seconds,
        sleep_function=sleep_function,
        monotonic=monotonic,
    )
    report("Demucs worker smoke: validating private stem artifacts")
    verified_stems = tuple(
        verify_stored_stem(client, observation=demucs_observation, stem_name=stem_name)
        for stem_name in STEM_NAMES
    )

    report("Demucs worker smoke: waiting for safe downstream cleanup barrier")
    wait_for_downstream_cleanup_barrier(
        connection,
        timeout_seconds=timeout_seconds,
        sleep_function=sleep_function,
        monotonic=monotonic,
    )
    report("Demucs worker smoke: cleaning exact successful evidence")
    cleanup_successful_smoke(connection, client)
    report("Demucs worker smoke passed")
    return verified_stems


def open_database_connection(settings: SmokeSettings) -> DatabaseConnection:
    """Open a short autocommit dictionary-row connection for fixed functions only."""

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as error:
        raise DemucsWorkerSmokeInfrastructureError("Pinned PostgreSQL smoke dependency is unavailable.") from error
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
        raise DemucsWorkerSmokeInfrastructureError("Smoke PostgreSQL connection is unavailable.") from error


def open_minio_client(settings: SmokeSettings) -> S3Client:
    """Create path-style MinIO client with explicit restricted credentials only."""

    try:
        import boto3
        from botocore.config import Config
    except ImportError as error:
        raise DemucsWorkerSmokeInfrastructureError("Pinned S3 smoke dependency is unavailable.") from error
    try:
        return boto3.client(
            "s3",
            endpoint_url=settings.minio_endpoint_url,
            aws_access_key_id=settings.minio_access_key,
            aws_secret_access_key=settings.minio_secret_key,
            region_name="us-east-1",
            config=Config(
                connect_timeout=3,
                read_timeout=20,
                retries={"max_attempts": 2, "mode": "standard"},
                s3={"addressing_style": "path"},
            ),
        )
    except Exception as error:
        raise DemucsWorkerSmokeInfrastructureError("Smoke MinIO client is unavailable.") from error


def main() -> int:
    """Run the bounded smoke process as PID 1 and emit only safe milestones."""

    connection: DatabaseConnection | None = None
    try:
        settings = SmokeSettings.from_environment()
        connection = open_database_connection(settings)
        client = open_minio_client(settings)
        run_smoke(connection, client, timeout_seconds=settings.timeout_seconds)
        return 0
    except (DemucsWorkerSmokeConfigurationError, DemucsWorkerSmokeAssertionError, DemucsWorkerSmokeInfrastructureError) as error:
        print(f"Demucs worker smoke failed: {error}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            with suppress(Exception):
                connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
