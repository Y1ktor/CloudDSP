"""Validate and hash the one local MIDI artifact produced by Basic Pitch.

This is the narrow boundary immediately after :mod:`app.basic_pitch_process`.
It accepts only that adapter's deterministic ``stem_basic_pitch.mid`` path,
opens it as a current regular file without following a symlink, validates the
bounded Standard MIDI File container, and streams SHA-256 evidence.  The
returned evidence is still Pod-local: a later separate adapter must upload it
to MinIO and prove the upload before PostgreSQL records a task result.

The module does not invoke Basic Pitch, create a process, contact MinIO,
PostgreSQL, RabbitMQ, or Kubernetes, or change task/Job state.
"""

from __future__ import annotations

import hashlib
import os
import stat
import struct
from dataclasses import dataclass
from pathlib import Path

from app.basic_pitch_process import (
    BASIC_PITCH_EXECUTABLE,
    BASIC_PITCH_MIDI_SUFFIX,
    BASIC_PITCH_OUTPUT_DIRECTORY_NAME,
    BASIC_PITCH_SOURCE_FILENAME,
    BasicPitchInferenceCommand,
)
from app.basic_pitch_requested_message import MIDI_CONTENT_TYPE


# MIDI metadata is tiny compared with its WAV input.  A 16 MiB ceiling keeps a
# compromised/misconfigured model process from using the artifact path as an
# unbounded local-file or later object-storage capability.  Reads remain
# bounded even below that limit.
MAX_BASIC_PITCH_MIDI_BYTES = 16 * 1024 * 1024
BASIC_PITCH_MIDI_READ_CHUNK_BYTES = 64 * 1024
MAX_BASIC_PITCH_MIDI_TRACKS = 64

_MIDI_HEADER_ID = b"MThd"
_MIDI_TRACK_ID = b"MTrk"
_MIDI_HEADER_DATA_LENGTH = 6
_MIDI_CHUNK_HEADER_LENGTH = 8


class BasicPitchMidiArtifactError(RuntimeError):
    """Base safe category for a local generated-MIDI verification failure."""


class BasicPitchMidiArtifactContractError(BasicPitchMidiArtifactError):
    """A hand-built or widened inference coordinate reached the verifier."""


class BasicPitchMidiArtifactPathError(BasicPitchMidiArtifactError):
    """The local MIDI path cannot be opened as the expected regular output."""


class BasicPitchMidiArtifactFormatError(BasicPitchMidiArtifactError):
    """The bounded local file is not the expected Standard MIDI File shape."""


class BasicPitchMidiArtifactConsistencyError(BasicPitchMidiArtifactError):
    """The model output changed while its byte evidence was being established."""


@dataclass(frozen=True)
class VerifiedBasicPitchMidiArtifact:
    """Pod-local evidence for exactly one validated Basic Pitch MIDI output.

    ``inference`` retains the fixed command/path coordinate so the immediately
    following output-object contract can re-run this verifier and reject a
    stale same-size local-file replacement. ``path`` stays meaningful only
    within the surrounding temporary-stem context. ``size_bytes`` and
    ``sha256`` are the values a future restricted MinIO upload/verification
    boundary will carry forward; they are not yet proof of a stored object or
    permission to complete a database task.
    """

    inference: BasicPitchInferenceCommand
    path: Path
    size_bytes: int
    sha256: str
    content_type: str


def _approved_output_path(value: object) -> Path:
    """Revalidate the exact local coordinate, without requiring an empty output.

    The process adapter required an empty directory before it ran.  This
    post-process boundary instead requires the same immutable command/path
    shape while allowing the one expected MIDI file to exist.
    """

    if not isinstance(value, BasicPitchInferenceCommand):
        raise BasicPitchMidiArtifactContractError("Basic Pitch MIDI request is invalid.")
    if not all(
        isinstance(path, Path)
        for path in (
            value.source_path,
            value.output_directory,
            value.expected_midi_path,
            value.work_directory,
        )
    ):
        raise BasicPitchMidiArtifactContractError("Basic Pitch MIDI request is invalid.")
    try:
        if any(
            path.is_symlink()
            for path in (
                value.source_path,
                value.output_directory,
                value.expected_midi_path,
                value.work_directory,
            )
        ):
            raise BasicPitchMidiArtifactPathError("Basic Pitch MIDI path is invalid.")
        work_directory = value.work_directory.resolve(strict=True)
        source_path = value.source_path.resolve(strict=True)
        output_directory = value.output_directory.resolve(strict=True)
        expected_midi_path = value.expected_midi_path.resolve(strict=True)
    except BasicPitchMidiArtifactError:
        raise
    except (OSError, RuntimeError) as error:
        raise BasicPitchMidiArtifactPathError("Basic Pitch MIDI path is invalid.") from error

    if (
        not work_directory.is_dir()
        or not source_path.is_file()
        or source_path.name != BASIC_PITCH_SOURCE_FILENAME
        or not source_path.parent.name.startswith("basic-pitch-stem-")
        or not output_directory.is_dir()
        or output_directory.name != BASIC_PITCH_OUTPUT_DIRECTORY_NAME
        or output_directory.parent != source_path.parent
        or expected_midi_path != output_directory / f"{source_path.stem}{BASIC_PITCH_MIDI_SUFFIX}"
        or value.command
        != (BASIC_PITCH_EXECUTABLE, str(output_directory), str(source_path))
    ):
        raise BasicPitchMidiArtifactContractError("Basic Pitch MIDI request is invalid.")
    try:
        source_path.relative_to(work_directory)
        output_directory.relative_to(work_directory)
        expected_midi_path.relative_to(work_directory)
    except ValueError as error:
        raise BasicPitchMidiArtifactPathError("Basic Pitch MIDI path is invalid.") from error
    return expected_midi_path


def _open_current_regular_midi(path: Path) -> tuple[object, os.stat_result]:
    """Open one non-symlink regular file and capture its size for stable hashing."""

    try:
        # O_NOFOLLOW is available on the Linux worker image and macOS local
        # tests.  The explicit lstat check preserves the no-symlink rule on a
        # future supported platform that lacks that flag.
        before_open = path.lstat()
        if stat.S_ISLNK(before_open.st_mode):
            raise BasicPitchMidiArtifactPathError("Basic Pitch MIDI path is invalid.")
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        source = os.fdopen(descriptor, "rb", buffering=0)
    except BasicPitchMidiArtifactError:
        raise
    except OSError as error:
        raise BasicPitchMidiArtifactPathError("Basic Pitch MIDI path is invalid.") from error
    try:
        opened = os.fstat(source.fileno())
    except OSError as error:
        source.close()
        raise BasicPitchMidiArtifactPathError("Basic Pitch MIDI path is invalid.") from error
    if not stat.S_ISREG(opened.st_mode):
        source.close()
        raise BasicPitchMidiArtifactPathError("Basic Pitch MIDI path is invalid.")
    if opened.st_size < _MIDI_HEADER_DATA_LENGTH + _MIDI_CHUNK_HEADER_LENGTH + 8:
        source.close()
        raise BasicPitchMidiArtifactFormatError("Basic Pitch MIDI output is invalid.")
    if opened.st_size > MAX_BASIC_PITCH_MIDI_BYTES:
        source.close()
        raise BasicPitchMidiArtifactFormatError("Basic Pitch MIDI output is too large.")
    return source, opened


class _HashingMidiReader:
    """Stream exact sections while preserving a bounded byte counter and digest."""

    def __init__(self, source: object, expected_size_bytes: int) -> None:
        self._source = source
        self._expected_size_bytes = expected_size_bytes
        self._hasher = hashlib.sha256()
        self.bytes_read = 0

    def _record(self, chunk: bytes) -> bytes:
        self.bytes_read += len(chunk)
        if self.bytes_read > self._expected_size_bytes:
            raise BasicPitchMidiArtifactConsistencyError("Basic Pitch MIDI output changed while reading.")
        self._hasher.update(chunk)
        return chunk

    def exact(self, length: int) -> bytes:
        """Read and hash precisely ``length`` bytes or reject a truncated file."""

        pieces: list[bytes] = []
        remaining = length
        while remaining:
            chunk = self._source.read(min(BASIC_PITCH_MIDI_READ_CHUNK_BYTES, remaining))
            if not isinstance(chunk, bytes) or not chunk:
                raise BasicPitchMidiArtifactFormatError("Basic Pitch MIDI output is invalid.")
            pieces.append(self._record(chunk))
            remaining -= len(chunk)
        return b"".join(pieces)

    def discard_exact(self, length: int) -> None:
        """Hash a bounded track body without retaining its musical events in memory."""

        remaining = length
        while remaining:
            chunk = self._source.read(min(BASIC_PITCH_MIDI_READ_CHUNK_BYTES, remaining))
            if not isinstance(chunk, bytes) or not chunk:
                raise BasicPitchMidiArtifactFormatError("Basic Pitch MIDI output is invalid.")
            self._record(chunk)
            remaining -= len(chunk)

    def digest(self) -> str:
        """Return the hash only after every byte of the fixed-size file was read."""

        if self.bytes_read != self._expected_size_bytes:
            raise BasicPitchMidiArtifactConsistencyError("Basic Pitch MIDI output changed while reading.")
        return self._hasher.hexdigest()


def _verify_standard_midi_file(source: object, expected_size_bytes: int) -> str:
    """Validate the SMF container/chunk layout while calculating its SHA-256 hash.

    This intentionally validates the Standard MIDI File framing, not musical
    semantics such as notes or tempo.  Basic Pitch produces the musical data;
    later consumers may parse it for their own domain needs.  Requiring the
    exact declared track count and EOF rejects arbitrary bytes that merely
    start with ``MThd``.
    """

    reader = _HashingMidiReader(source, expected_size_bytes)
    header = reader.exact(8 + _MIDI_HEADER_DATA_LENGTH)
    if header[:4] != _MIDI_HEADER_ID or struct.unpack(">I", header[4:8])[0] != _MIDI_HEADER_DATA_LENGTH:
        raise BasicPitchMidiArtifactFormatError("Basic Pitch MIDI output is invalid.")
    format_type, track_count, division = struct.unpack(">HHH", header[8:])
    if (
        format_type not in {0, 1, 2}
        or track_count < 1
        or track_count > MAX_BASIC_PITCH_MIDI_TRACKS
        or (format_type == 0 and track_count != 1)
        or division == 0
    ):
        raise BasicPitchMidiArtifactFormatError("Basic Pitch MIDI output is invalid.")

    for _ in range(track_count):
        track_header = reader.exact(_MIDI_CHUNK_HEADER_LENGTH)
        if track_header[:4] != _MIDI_TRACK_ID:
            raise BasicPitchMidiArtifactFormatError("Basic Pitch MIDI output is invalid.")
        track_size = struct.unpack(">I", track_header[4:])[0]
        if track_size < 1 or track_size > expected_size_bytes - reader.bytes_read:
            raise BasicPitchMidiArtifactFormatError("Basic Pitch MIDI output is invalid.")
        reader.discard_exact(track_size)

    trailing = source.read(1)
    if trailing:
        raise BasicPitchMidiArtifactFormatError("Basic Pitch MIDI output is invalid.")
    return reader.digest()


def verify_and_hash_basic_pitch_midi(
    inference: BasicPitchInferenceCommand,
) -> VerifiedBasicPitchMidiArtifact:
    """Return local evidence only for the exact bounded MIDI output of one run.

    The caller invokes this after :func:`app.basic_pitch_process.run_basic_pitch_inference`
    returns successfully and before a future MinIO uploader.  It rechecks the
    fixed command/path contract because frozen dataclasses are constructible by
    arbitrary Python callers; it does not trust a filename alone as authority
    to read another file from worker scratch.
    """

    midi_path = _approved_output_path(inference)
    source, before = _open_current_regular_midi(midi_path)
    try:
        sha256 = _verify_standard_midi_file(source, before.st_size)
        after = os.fstat(source.fileno())
    except BasicPitchMidiArtifactError:
        raise
    except OSError as error:
        raise BasicPitchMidiArtifactPathError("Basic Pitch MIDI path is invalid.") from error
    finally:
        source.close()
    if (
        after.st_size != before.st_size
        or after.st_dev != before.st_dev
        or after.st_ino != before.st_ino
    ):
        raise BasicPitchMidiArtifactConsistencyError("Basic Pitch MIDI output changed while reading.")
    return VerifiedBasicPitchMidiArtifact(
        inference=inference,
        path=midi_path,
        size_bytes=before.st_size,
        sha256=sha256,
        content_type=MIDI_CONTENT_TYPE,
    )
