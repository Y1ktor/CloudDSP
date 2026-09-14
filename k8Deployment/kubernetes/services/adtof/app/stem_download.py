"""Bounded, hash-verified MinIO download for one verified ADTOF drums stem.

The preceding ``HeadObject`` boundary proved the claimed private coordinate,
headers, and immutable Demucs metadata. This adapter makes one matching
``GetObject`` request, streams bytes into a generated child of the worker's
Pod-local scratch volume, and calculates SHA-256 while it writes.

The local WAV exists only inside this context manager. A later ADTOF runner may
use it in the same scope, but this module does not invoke a model, alter
PostgreSQL, acknowledge RabbitMQ, create a Boto3 client, or call Kubernetes.
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

from app.adtof_requested_message import ADTOF_STEM_NAME, LOCAL_UPLOADS_BUCKET, STEM_CONTENT_TYPE
from app.stem_object import VerifiedADTOFStemObject


# A worker takes only one drums stem at a time, but its Pod-local ``emptyDir``
# still needs a finite admission bound before audio bytes are written. This
# matches the Job API's preserved 256 MiB encoded-source ceiling and leaves
# space for a five-hundred-second 44.1 kHz stereo PCM WAV (~84 MiB).
MAX_ADTOF_STEM_SIZE_BYTES = 256 * 1024 * 1024
ADTOF_STEM_DOWNLOAD_CHUNK_BYTES = 64 * 1024


class ADTOFGetObjectClient(Protocol):
    """The one S3-compatible operation allowed after metadata verification."""

    def get_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        """Return response headers and a closeable binary streaming body."""


class ADTOFStemDownloadUnavailable(RuntimeError):
    """A retryable MinIO/connection failure without object-store diagnostics."""


class ADTOFStemDownloadProtocolError(RuntimeError):
    """An invalid client, stream, verified evidence, or scratch directory."""


class ADTOFStemDownloadConsistencyError(RuntimeError):
    """GetObject bytes/headers disagree with preceding HeadObject evidence."""


@dataclass(frozen=True)
class DownloadedADTOFStem:
    """One private WAV available only during the caller's ``with`` block.

    ``stem_path`` is a random generic name, never a MinIO key or user-provided
    filename. The random child directory is removed on normal and exceptional
    context exit; this path must never be logged or persisted as Job data.
    """

    stem_path: Path
    size_bytes: int
    sha256: str


def _validated_work_directory(work_directory: object) -> Path:
    """Require an existing non-symlink directory reserved for Pod scratch."""

    if not isinstance(work_directory, Path):
        raise ADTOFStemDownloadProtocolError("ADTOF stem work directory is invalid.")
    try:
        if work_directory.is_symlink():
            raise ADTOFStemDownloadProtocolError("ADTOF stem work directory is invalid.")
        resolved = work_directory.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ADTOFStemDownloadProtocolError("ADTOF stem work directory is invalid.") from error
    if not resolved.is_dir():
        raise ADTOFStemDownloadProtocolError("ADTOF stem work directory is invalid.")
    return resolved


def _canonical_uuid(value: object) -> str:
    """Require job UUID syntax before it participates in a local key check."""

    if not isinstance(value, str):
        raise ADTOFStemDownloadProtocolError("ADTOF verified stem evidence is invalid.")
    try:
        normalized = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise ADTOFStemDownloadProtocolError("ADTOF verified stem evidence is invalid.") from error
    if normalized != value:
        raise ADTOFStemDownloadProtocolError("ADTOF verified stem evidence is invalid.")
    return normalized


def _validated_verified_stem(source: object) -> VerifiedADTOFStemObject:
    """Revalidate HeadObject evidence before direct construction can trigger I/O."""

    if not isinstance(source, VerifiedADTOFStemObject):
        raise ADTOFStemDownloadProtocolError("ADTOF verified stem evidence is invalid.")
    if (
        source.bucket_name != LOCAL_UPLOADS_BUCKET
        or source.content_type != STEM_CONTENT_TYPE
        or isinstance(source.size_bytes, bool)
        or not isinstance(source.size_bytes, int)
        or not 1 <= source.size_bytes <= MAX_ADTOF_STEM_SIZE_BYTES
        or not isinstance(source.sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", source.sha256)
        or not isinstance(source.object_key, str)
    ):
        raise ADTOFStemDownloadProtocolError("ADTOF verified stem evidence is invalid.")

    # Reparse the exact three-component drum-key shape. A frozen evidence
    # dataclass can still be hand-built, so do not accept arbitrary bucket keys
    # even though the earlier HeadObject verifier normally produced this value.
    key_parts = source.object_key.split("/")
    if len(key_parts) != 3 or key_parts[0] != "stems":
        raise ADTOFStemDownloadProtocolError("ADTOF verified stem evidence is invalid.")
    job_id, filename = key_parts[1:]
    if _canonical_uuid(job_id) != job_id or filename != f"{ADTOF_STEM_NAME}.wav":
        raise ADTOFStemDownloadProtocolError("ADTOF verified stem evidence is invalid.")
    return source


def _get_object_or_raise(
    client: ADTOFGetObjectClient,
    *,
    source: VerifiedADTOFStemObject,
) -> Mapping[str, object]:
    """Make one streaming request without treating a transport fault as media."""

    try:
        response = client.get_object(Bucket=source.bucket_name, Key=source.object_key)
    except Exception as error:  # S3 SDKs expose incompatible concrete error classes.
        raise ADTOFStemDownloadUnavailable("ADTOF stem GetObject is unavailable.") from error
    if not isinstance(response, Mapping):
        raise ADTOFStemDownloadProtocolError("MinIO returned an invalid ADTOF GetObject response.")
    return response


def _response_headers(response: Mapping[str, object], *, source: VerifiedADTOFStemObject) -> None:
    """Require GetObject headers to agree with the immediately prior HeadObject."""

    content_length = response.get("ContentLength")
    content_type = response.get("ContentType")
    if isinstance(content_length, bool) or not isinstance(content_length, int) or content_length < 0:
        raise ADTOFStemDownloadProtocolError("MinIO returned an invalid ADTOF GetObject length.")
    if not isinstance(content_type, str) or not content_type or "\x00" in content_type:
        raise ADTOFStemDownloadProtocolError("MinIO returned an invalid ADTOF GetObject type.")
    if content_length != source.size_bytes or content_type != source.content_type:
        raise ADTOFStemDownloadConsistencyError("ADTOF stem changed after verification.")


def _response_body(response: Mapping[str, object]) -> object:
    """Require a readable/closeable body so the S3 HTTP connection is released."""

    body = response.get("Body")
    if not callable(getattr(body, "read", None)) or not callable(getattr(body, "close", None)):
        raise ADTOFStemDownloadProtocolError("MinIO returned an invalid ADTOF stem body.")
    return body


def _close_body(body: object) -> None:
    """Close a stream without masking the preceding safe transfer outcome."""

    try:
        body.close()  # type: ignore[union-attr]
    except Exception:
        # Scratch cleanup still removes partial audio. A close fault does not
        # turn incomplete/untrusted transfer data into a different category.
        return


def _stream_body_to_file(
    body: object,
    *,
    destination: Path,
    expected_size_bytes: int,
    expected_sha256: str,
) -> None:
    """Write/hash an exact-size stream without holding a full WAV in memory."""

    written = 0
    hasher = hashlib.sha256()
    try:
        try:
            # O_EXCL blocks local-file substitution. Explicit 0600 keeps
            # private audio inaccessible to a second process sharing the Pod.
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError as error:
            raise ADTOFStemDownloadProtocolError(
                "ADTOF stem temporary file could not be created."
            ) from error
        with os.fdopen(descriptor, "wb") as output:
            while True:
                # Read no more than one byte over the remaining expected count.
                # This detects both a short body and a post-HeadObject growth
                # before any temporary file is ever yielded to model code.
                read_size = min(
                    ADTOF_STEM_DOWNLOAD_CHUNK_BYTES,
                    expected_size_bytes - written + 1,
                )
                try:
                    chunk = body.read(read_size)  # type: ignore[union-attr]
                except Exception as error:
                    raise ADTOFStemDownloadUnavailable(
                        "ADTOF stem download is unavailable."
                    ) from error
                if not isinstance(chunk, bytes) or len(chunk) > read_size:
                    raise ADTOFStemDownloadProtocolError("MinIO returned invalid ADTOF stem bytes.")
                if not chunk:
                    break
                if written + len(chunk) > expected_size_bytes:
                    raise ADTOFStemDownloadConsistencyError(
                        "ADTOF stem changed during download."
                    )
                output.write(chunk)
                hasher.update(chunk)
                written += len(chunk)
    finally:
        _close_body(body)

    if written != expected_size_bytes or hasher.hexdigest() != expected_sha256:
        raise ADTOFStemDownloadConsistencyError("ADTOF stem changed during download.")


@contextmanager
def downloaded_verified_adtof_stem(
    client: ADTOFGetObjectClient,
    *,
    source: VerifiedADTOFStemObject,
    work_directory: Path,
) -> Iterator[DownloadedADTOFStem]:
    """Yield one verified local drums WAV, then remove its random scratch child.

    ``TemporaryDirectory`` is always created below the existing mounted scratch
    volume. It never derives a local filename from a MinIO key and removes only
    its own child—not the shared volume root—on success, transfer failure, or a
    later model exception. The model runner must finish within this context.
    """

    verified_source = _validated_verified_stem(source)
    resolved_work_directory = _validated_work_directory(work_directory)
    response = _get_object_or_raise(client, source=verified_source)
    body = _response_body(response)
    try:
        _response_headers(response, source=verified_source)
        with tempfile.TemporaryDirectory(
            prefix="adtof-stem-",
            dir=resolved_work_directory,
        ) as temporary_directory:
            stem_path = Path(temporary_directory) / "stem.wav"
            _stream_body_to_file(
                body,
                destination=stem_path,
                expected_size_bytes=verified_source.size_bytes,
                expected_sha256=verified_source.sha256,
            )
            yield DownloadedADTOFStem(
                stem_path=stem_path,
                size_bytes=verified_source.size_bytes,
                sha256=verified_source.sha256,
            )
    finally:
        # The streaming helper already closes on every stream path. This second
        # close covers malformed headers detected before byte streaming begins.
        _close_body(body)
