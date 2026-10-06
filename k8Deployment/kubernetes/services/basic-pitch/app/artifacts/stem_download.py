"""Bounded, hash-verified MinIO download for one verified Basic Pitch stem.

The preceding ``HeadObject`` boundary proved the claimed private coordinate,
headers, and immutable Demucs metadata. This adapter makes one matching
``GetObject`` request, streams it into a generated child of the worker's
Pod-local scratch volume, and calculates SHA-256 as every byte is written.

The local WAV exists only within this context manager. A later Basic Pitch
runner may consume it in the same scope, but this module does not invoke a
model, alter PostgreSQL, acknowledge RabbitMQ, create a Boto3 client, or make
any Kubernetes API request.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.messaging.basic_pitch_requested_message import BASIC_PITCH_STEM_NAMES, LOCAL_UPLOADS_BUCKET, STEM_CONTENT_TYPE
from app.artifacts.stem_object import VerifiedBasicPitchStemObject


# A Basic Pitch worker processes one stem at a time, but its Pod-local
# ``emptyDir`` must still have a finite admission bound before it writes bytes.
# This matches the preserved 256 MiB encoded-source ceiling while comfortably
# covering a five-hundred-second 44.1 kHz stereo PCM WAV stem (~84 MiB).
MAX_BASIC_PITCH_STEM_SIZE_BYTES = 256 * 1024 * 1024
BASIC_PITCH_STEM_DOWNLOAD_CHUNK_BYTES = 64 * 1024


class BasicPitchGetObjectClient(Protocol):
    """The one S3-compatible operation allowed after metadata verification."""

    def get_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        """Return response headers and a closeable binary streaming body."""


class BasicPitchStemDownloadUnavailable(RuntimeError):
    """A retryable MinIO/connection failure with no raw object-store details."""


class BasicPitchStemDownloadProtocolError(RuntimeError):
    """An invalid client, stream, verified evidence, or scratch directory."""


class BasicPitchStemDownloadConsistencyError(RuntimeError):
    """GetObject bytes/headers differ from the preceding HeadObject evidence."""


@dataclass(frozen=True)
class DownloadedBasicPitchStem:
    """One complete private WAV available only within the caller's ``with`` block.

    ``stem_path`` is a randomly generated generic name, never a MinIO key or
    user-provided filename. The directory is removed on every normal or
    exceptional context exit, so this path must not be persisted or logged as
    durable Job data.
    """

    stem_path: Path
    size_bytes: int
    sha256: str


def _validated_work_directory(work_directory: object) -> Path:
    """Require an existing real directory reserved for Pod-local scratch."""

    if not isinstance(work_directory, Path):
        raise BasicPitchStemDownloadProtocolError("Basic Pitch stem work directory is invalid.")
    try:
        if work_directory.is_symlink():
            raise BasicPitchStemDownloadProtocolError("Basic Pitch stem work directory is invalid.")
        resolved = work_directory.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise BasicPitchStemDownloadProtocolError("Basic Pitch stem work directory is invalid.") from error
    if not resolved.is_dir():
        raise BasicPitchStemDownloadProtocolError("Basic Pitch stem work directory is invalid.")
    return resolved


def _canonical_uuid(value: object) -> str:
    """Require canonical job UUID syntax before it participates in a local path check."""

    if not isinstance(value, str):
        raise BasicPitchStemDownloadProtocolError("Basic Pitch verified stem evidence is invalid.")
    try:
        normalized = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise BasicPitchStemDownloadProtocolError("Basic Pitch verified stem evidence is invalid.") from error
    if normalized != value:
        raise BasicPitchStemDownloadProtocolError("Basic Pitch verified stem evidence is invalid.")
    return normalized


def _validated_verified_stem(source: object) -> VerifiedBasicPitchStemObject:
    """Revalidate immutable HeadObject evidence before a caller can trigger I/O."""

    if not isinstance(source, VerifiedBasicPitchStemObject):
        raise BasicPitchStemDownloadProtocolError("Basic Pitch verified stem evidence is invalid.")
    if (
        source.bucket_name != LOCAL_UPLOADS_BUCKET
        or source.content_type != STEM_CONTENT_TYPE
        or isinstance(source.size_bytes, bool)
        or not isinstance(source.size_bytes, int)
        or not 1 <= source.size_bytes <= MAX_BASIC_PITCH_STEM_SIZE_BYTES
        or not isinstance(source.sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", source.sha256)
        or not isinstance(source.object_key, str)
    ):
        raise BasicPitchStemDownloadProtocolError("Basic Pitch verified stem evidence is invalid.")

    # A frozen evidence dataclass can still be directly constructed. Parse the
    # exact three-part key again instead of accepting an arbitrary bucket path.
    key_parts = source.object_key.split("/")
    if len(key_parts) != 3 or key_parts[0] != "stems":
        raise BasicPitchStemDownloadProtocolError("Basic Pitch verified stem evidence is invalid.")
    job_id, filename = key_parts[1:]
    stem_name = filename.removesuffix(".wav")
    if (
        _canonical_uuid(job_id) != job_id
        or filename != f"{stem_name}.wav"
        or stem_name not in BASIC_PITCH_STEM_NAMES
    ):
        raise BasicPitchStemDownloadProtocolError("Basic Pitch verified stem evidence is invalid.")
    return source


def _get_object_or_raise(
    client: BasicPitchGetObjectClient,
    *,
    source: VerifiedBasicPitchStemObject,
) -> Mapping[str, object]:
    """Perform one streaming request without classifying a transport failure as media."""

    try:
        response = client.get_object(Bucket=source.bucket_name, Key=source.object_key)
    except Exception as error:  # S3 SDKs expose incompatible concrete failures.
        raise BasicPitchStemDownloadUnavailable("Basic Pitch stem GetObject is unavailable.") from error
    if not isinstance(response, Mapping):
        raise BasicPitchStemDownloadProtocolError("MinIO returned an invalid Basic Pitch GetObject response.")
    return response


def _response_headers(response: Mapping[str, object], *, source: VerifiedBasicPitchStemObject) -> None:
    """Require GetObject headers to agree with the immediately prior head evidence."""

    content_length = response.get("ContentLength")
    content_type = response.get("ContentType")
    if isinstance(content_length, bool) or not isinstance(content_length, int) or content_length < 0:
        raise BasicPitchStemDownloadProtocolError("MinIO returned an invalid Basic Pitch GetObject length.")
    if not isinstance(content_type, str) or not content_type or "\x00" in content_type:
        raise BasicPitchStemDownloadProtocolError("MinIO returned an invalid Basic Pitch GetObject type.")
    if content_length != source.size_bytes or content_type != source.content_type:
        raise BasicPitchStemDownloadConsistencyError("Basic Pitch stem changed after verification.")


def _response_body(response: Mapping[str, object]) -> object:
    """Require a readable/closeable body so the S3 HTTP connection is released."""

    body = response.get("Body")
    if not callable(getattr(body, "read", None)) or not callable(getattr(body, "close", None)):
        raise BasicPitchStemDownloadProtocolError("MinIO returned an invalid Basic Pitch stem body.")
    return body


def _close_body(body: object) -> None:
    """Close a streaming HTTP body without masking the preceding safe error."""

    try:
        body.close()  # type: ignore[union-attr]
    except Exception:
        # Temporary directory cleanup still removes local partial data. A close
        # failure must not replace the verified transfer failure/category.
        return


def _stream_body_to_file(
    body: object,
    *,
    destination: Path,
    expected_size_bytes: int,
    expected_sha256: str,
) -> None:
    """Write/hash an exact-size stream without retaining a full WAV in memory."""

    written = 0
    hasher = hashlib.sha256()
    try:
        try:
            # O_EXCL stops local file substitution. Explicit 0600 keeps private
            # audio inaccessible to another process sharing the Pod filesystem.
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError as error:
            raise BasicPitchStemDownloadProtocolError(
                "Basic Pitch stem temporary file could not be created."
            ) from error
        with os.fdopen(descriptor, "wb") as output:
            while True:
                # Request at most one byte beyond remaining data so both a
                # short body and an object that grew after HeadObject are
                # distinguishable before the local WAV is yielded.
                read_size = min(
                    BASIC_PITCH_STEM_DOWNLOAD_CHUNK_BYTES,
                    expected_size_bytes - written + 1,
                )
                try:
                    chunk = body.read(read_size)  # type: ignore[union-attr]
                except Exception as error:
                    raise BasicPitchStemDownloadUnavailable(
                        "Basic Pitch stem download is unavailable."
                    ) from error
                if not isinstance(chunk, bytes) or len(chunk) > read_size:
                    raise BasicPitchStemDownloadProtocolError("MinIO returned invalid Basic Pitch stem bytes.")
                if not chunk:
                    break
                if written + len(chunk) > expected_size_bytes:
                    raise BasicPitchStemDownloadConsistencyError(
                        "Basic Pitch stem changed during download."
                    )
                output.write(chunk)
                hasher.update(chunk)
                written += len(chunk)
    finally:
        _close_body(body)

    if written != expected_size_bytes or hasher.hexdigest() != expected_sha256:
        raise BasicPitchStemDownloadConsistencyError("Basic Pitch stem changed during download.")


@contextmanager
def downloaded_verified_basic_pitch_stem(
    client: BasicPitchGetObjectClient,
    *,
    source: VerifiedBasicPitchStemObject,
    work_directory: Path,
) -> Iterator[DownloadedBasicPitchStem]:
    """Yield one verified local WAV, then reliably remove its random scratch child.

    ``TemporaryDirectory`` is always a child of the already-mounted worker
    scratch volume. It never uses an object key as a local filename and deletes
    only its own child, not the shared volume root. The later model runner must
    finish inside the context; on exit, success, exception, or interruption,
    no private stem path remains for a later unrelated task.
    """

    verified_source = _validated_verified_stem(source)
    resolved_work_directory = _validated_work_directory(work_directory)
    response = _get_object_or_raise(client, source=verified_source)
    body = _response_body(response)
    try:
        _response_headers(response, source=verified_source)
        with tempfile.TemporaryDirectory(
            prefix="basic-pitch-stem-",
            dir=resolved_work_directory,
        ) as temporary_directory:
            stem_path = Path(temporary_directory) / "stem.wav"
            _stream_body_to_file(
                body,
                destination=stem_path,
                expected_size_bytes=verified_source.size_bytes,
                expected_sha256=verified_source.sha256,
            )
            yield DownloadedBasicPitchStem(
                stem_path=stem_path,
                size_bytes=verified_source.size_bytes,
                sha256=verified_source.sha256,
            )
    finally:
        # `_stream_body_to_file` closes on every streaming path. This second
        # close covers a malformed header detected before streaming begins.
        _close_body(body)
