"""Bounded private MinIO download for one previously verified Demucs source.

This adapter follows :mod:`app.source_object`, which established the private
bucket/key, MIME type, metadata, and exact object size with ``HeadObject``. It
uses that verified evidence to stream one ``GetObject`` response into an
unpredictably named directory below the worker's Pod-local scratch volume.

The downloaded file lives only for the context-manager scope.  That lets the
later FFprobe and Demucs calls share the same local bytes while ensuring every
normal, failed, and exception path removes the temporary directory.  It has no
Boto3 import, PostgreSQL operation, RabbitMQ acknowledgement, lease mutation,
FFprobe process, model invocation, or Kubernetes API call.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.source_object import MAX_DEMUCS_SOURCE_SIZE_BYTES, VerifiedDemucsSourceObject


# A streamed GetObject body should ordinarily return up to the requested byte
# count. This modest cap avoids holding an audio source in RAM while also making
# progress predictable for a future lease-renewal composition loop.
DEMUCS_SOURCE_DOWNLOAD_CHUNK_BYTES = 64 * 1024


class DemucsGetObjectClient(Protocol):
    """The one S3-compatible operation needed after HeadObject authorization."""

    def get_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        """Return response headers and a readable streaming object body."""


class DemucsSourceDownloadUnavailable(RuntimeError):
    """A retryable MinIO/connection failure without raw object-store details."""


class DemucsSourceDownloadProtocolError(RuntimeError):
    """An invalid client/stream response that cannot safely create local input."""


class DemucsSourceDownloadConsistencyError(RuntimeError):
    """GetObject disagreed with the already-authoritative HeadObject evidence."""


@dataclass(frozen=True)
class DownloadedDemucsSource:
    """One complete ephemeral local source available during the ``with`` scope.

    ``source_path`` is deliberately a generated generic name rather than the
    user-provided upload filename. The path is safe to pass to the FFprobe
    adapter, but must not be persisted in PostgreSQL or included in a normal
    log: the directory is removed when the context exits.
    """

    source_path: Path
    size_bytes: int


def _validated_work_directory(work_directory: object) -> Path:
    """Require an existing ordinary directory used exclusively as Pod scratch."""

    if not isinstance(work_directory, Path):
        raise DemucsSourceDownloadProtocolError("Demucs source work directory is invalid.")
    try:
        if work_directory.is_symlink():
            raise DemucsSourceDownloadProtocolError("Demucs source work directory is invalid.")
        resolved = work_directory.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise DemucsSourceDownloadProtocolError("Demucs source work directory is invalid.") from error
    if not resolved.is_dir():
        raise DemucsSourceDownloadProtocolError("Demucs source work directory is invalid.")
    return resolved


def _validated_verified_source(source: object) -> VerifiedDemucsSourceObject:
    """Defend the download boundary against a hand-built/invalid evidence object."""

    if not isinstance(source, VerifiedDemucsSourceObject):
        raise DemucsSourceDownloadProtocolError("Demucs verified source evidence is invalid.")
    if (
        isinstance(source.size_bytes, bool)
        or not isinstance(source.size_bytes, int)
        or not 1 <= source.size_bytes <= MAX_DEMUCS_SOURCE_SIZE_BYTES
    ):
        raise DemucsSourceDownloadProtocolError("Demucs verified source evidence is invalid.")
    if not isinstance(source.bucket_name, str) or not isinstance(source.object_key, str):
        raise DemucsSourceDownloadProtocolError("Demucs verified source evidence is invalid.")
    return source


def _get_object_or_raise(
    client: DemucsGetObjectClient,
    *,
    source: VerifiedDemucsSourceObject,
) -> Mapping[str, object]:
    """Make exactly one GetObject request without converting storage errors to media facts."""

    try:
        response = client.get_object(Bucket=source.bucket_name, Key=source.object_key)
    except Exception as error:  # SDK error types vary; never expose their text.
        raise DemucsSourceDownloadUnavailable("Demucs source GetObject is unavailable.") from error
    if not isinstance(response, Mapping):
        raise DemucsSourceDownloadProtocolError("MinIO returned an invalid Demucs GetObject response.")
    return response


def _response_length(response: Mapping[str, object], *, expected_size_bytes: int) -> None:
    """Require GetObject's own declared size to match the preceding HeadObject."""

    response_length = response.get("ContentLength")
    if isinstance(response_length, bool) or not isinstance(response_length, int) or response_length < 0:
        raise DemucsSourceDownloadProtocolError("MinIO returned an invalid Demucs GetObject length.")
    if response_length != expected_size_bytes:
        # The object could have changed, a proxy could be inconsistent, or the
        # later client could be wrong. Do not let potentially different bytes
        # enter FFprobe; a future task adapter can reverify/recover safely.
        raise DemucsSourceDownloadConsistencyError("Demucs source changed after verification.")


def _response_body(response: Mapping[str, object]) -> object:
    """Require a closeable, readable stream so its HTTP connection is released."""

    body = response.get("Body")
    if not callable(getattr(body, "read", None)) or not callable(getattr(body, "close", None)):
        raise DemucsSourceDownloadProtocolError("MinIO returned an invalid Demucs source body.")
    return body


def _close_body(body: object) -> None:
    """Close the streaming HTTP body without replacing the original safe error."""

    try:
        body.close()  # type: ignore[union-attr]
    except Exception:
        # Network clients can fail while closing an already-failed transfer.
        # The requested data is not trusted either way, and temporary-file
        # cleanup below remains responsible for local bytes.
        return


def _stream_body_to_file(
    body: object,
    *,
    destination: Path,
    expected_size_bytes: int,
) -> None:
    """Write an exact-size binary stream without retaining its body in memory."""

    written = 0
    try:
        try:
            # ``Path.open('xb')`` relies on the process umask for permissions.
            # Set 0600 explicitly because source audio is private even inside
            # a Pod-local volume; O_EXCL also prevents local-file substitution.
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except OSError as error:
            raise DemucsSourceDownloadProtocolError(
                "Demucs source temporary file could not be created."
            ) from error
        with os.fdopen(descriptor, "wb") as output:
            while True:
                # Asking for one byte past the remaining count distinguishes an
                # exact final chunk from a body that has silently grown since
                # HeadObject. A compliant streaming body returns no more than
                # requested, but the explicit check also protects fake/SDK bugs.
                read_size = min(
                    DEMUCS_SOURCE_DOWNLOAD_CHUNK_BYTES,
                    expected_size_bytes - written + 1,
                )
                try:
                    chunk = body.read(read_size)  # type: ignore[union-attr]
                except Exception as error:
                    raise DemucsSourceDownloadUnavailable(
                        "Demucs source download is unavailable."
                    ) from error
                if not isinstance(chunk, bytes):
                    raise DemucsSourceDownloadProtocolError("MinIO returned invalid Demucs source bytes.")
                if not chunk:
                    break
                if len(chunk) > read_size:
                    raise DemucsSourceDownloadProtocolError("MinIO returned invalid Demucs source bytes.")
                if written + len(chunk) > expected_size_bytes:
                    raise DemucsSourceDownloadConsistencyError(
                        "Demucs source changed during download."
                    )
                output.write(chunk)
                written += len(chunk)
    finally:
        _close_body(body)

    if written != expected_size_bytes:
        raise DemucsSourceDownloadConsistencyError("Demucs source changed during download.")


@contextmanager
def downloaded_verified_demucs_source(
    client: DemucsGetObjectClient,
    *,
    source: VerifiedDemucsSourceObject,
    work_directory: Path,
) -> Iterator[DownloadedDemucsSource]:
    """Stream one verified object into ephemeral worker scratch and remove it reliably.

    The returned file remains valid only within the caller's ``with`` block:

    .. code-block:: python

        with downloaded_verified_demucs_source(client, source=source, work_directory=scratch) as local:
            probe = run_verified_demucs_audio_probe(
                source_path=local.source_path,
                work_directory=scratch,
            )

    ``TemporaryDirectory`` creates a private random child below the existing
    bounded Pod scratch volume. It is never based on an S3 key or upload name,
    and removes only that child—not the volume root—after FFprobe/Demucs work
    finishes or raises. The direct file is created with exclusive mode so no
    pre-existing local file can be substituted into the analysis path.
    """

    verified_source = _validated_verified_source(source)
    resolved_work_directory = _validated_work_directory(work_directory)
    response = _get_object_or_raise(client, source=verified_source)
    body = _response_body(response)
    try:
        _response_length(response, expected_size_bytes=verified_source.size_bytes)
        with tempfile.TemporaryDirectory(
            prefix="demucs-source-",
            dir=resolved_work_directory,
        ) as temporary_directory:
            source_path = Path(temporary_directory) / "source.media"
            _stream_body_to_file(
                body,
                destination=source_path,
                expected_size_bytes=verified_source.size_bytes,
            )
            yield DownloadedDemucsSource(
                source_path=source_path,
                size_bytes=verified_source.size_bytes,
            )
    finally:
        # `_stream_body_to_file` closes the body itself. This second close is
        # harmless for boto-style bodies and covers a length mismatch that was
        # detected before streaming started.
        _close_body(body)
