"""Strict interpretation of bounded FFprobe JSON for one Demucs source.

The future worker will download the already-claimed private object into a
bounded work directory, run FFprobe, and pass *only* its standard-output bytes
to this module.  Keeping that interpretation pure makes the media admission
rules testable without a subprocess, MinIO, PostgreSQL, RabbitMQ, a model, or
Kubernetes.

This module deliberately does not execute FFprobe or read a source file.  A
later process adapter owns command construction, timeouts, temporary-file
cleanup, and classification of an FFprobe process failure.  This parser owns
the successful-process result: it proves that at least one audio stream exists
and that the container-level duration is positive and no more than the
CloudDSP 500-second limit.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any


# FFprobe's ``-show_format -show_streams -of json`` output contains metadata,
# not source media bytes.  It is still untrusted process output, so bound it
# before JSON decoding.  One MiB leaves ample room for ordinary audio metadata
# while preventing an unexpected stream list from consuming worker memory.
MAX_FFPROBE_OUTPUT_BYTES = 1 * 1024 * 1024

# This is the durable CloudDSP source-duration rule shared with the cloud
# deployment.  Decimal avoids a binary floating-point rounding decision at the
# exact 500-second boundary.
MAX_DEMUCS_SOURCE_DURATION_SECONDS = Decimal("500")


class DemucsAudioProbeProtocolError(RuntimeError):
    """FFprobe output is malformed, incomplete, or incompatible with this parser."""


class DemucsAudioProbeFailureCode(StrEnum):
    """Bounded permanent categories for media that cannot enter Demucs work."""

    NO_AUDIO_STREAM = "source_audio_stream_missing"
    INVALID_DURATION = "source_duration_invalid"
    DURATION_LIMIT_EXCEEDED = "source_duration_limit_exceeded"


class DemucsPermanentAudioProbeError(RuntimeError):
    """Carry a reviewed media-failure category without exposing FFprobe output."""

    def __init__(self, failure_code: DemucsAudioProbeFailureCode) -> None:
        self.failure_code = failure_code
        super().__init__(failure_code.value)


@dataclass(frozen=True)
class VerifiedDemucsAudioProbe:
    """Minimal proof from FFprobe that a claimed source may reach Demucs.

    ``duration_seconds`` remains a Decimal so a later persistence adapter can
    make an exact policy/audit decision without silently changing the boundary
    through float serialization.  ``audio_stream_count`` is evidence only: the
    first Demucs implementation passes the whole source to Demucs rather than
    selecting a particular stream here.
    """

    duration_seconds: Decimal
    audio_stream_count: int


def _protocol_error() -> DemucsAudioProbeProtocolError:
    """Return the one safe public message for untrusted FFprobe output."""

    return DemucsAudioProbeProtocolError("Demucs FFprobe output is invalid.")


def _unique_json_object_pairs(pairs: list[tuple[object, object]]) -> dict[str, object]:
    """Build every JSON object while rejecting duplicate member names.

    JSON permits duplicates, but decoders disagree about whether the first or
    last value wins.  Rejecting duplicates prevents a later media parser from
    accepting a different duration/stream description than this boundary.
    """

    parsed: dict[str, object] = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in parsed:
            raise _protocol_error()
        parsed[key] = value
    return parsed


def _reject_json_constant(_constant: str) -> Any:
    """Reject non-standard JSON ``NaN`` and infinity tokens."""

    raise _protocol_error()


def _decode_probe_output(probe_output: object) -> Mapping[str, object]:
    """Decode bounded UTF-8 FFprobe JSON without permissive numeric handling."""

    if (
        not isinstance(probe_output, bytes)
        or not probe_output
        or len(probe_output) > MAX_FFPROBE_OUTPUT_BYTES
    ):
        raise _protocol_error()
    try:
        parsed = json.loads(
            probe_output.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object_pairs,
            # Native JSON numbers are read exactly rather than as binary floats.
            parse_float=Decimal,
            parse_int=Decimal,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise _protocol_error() from error
    if not isinstance(parsed, Mapping):
        raise _protocol_error()
    return parsed


def _audio_stream_count(probe: Mapping[str, object]) -> int:
    """Require a well-shaped stream list with at least one declared audio stream."""

    streams = probe.get("streams")
    if not isinstance(streams, list):
        raise _protocol_error()

    count = 0
    for stream in streams:
        if not isinstance(stream, Mapping):
            raise _protocol_error()
        codec_type = stream.get("codec_type")
        if not isinstance(codec_type, str) or not codec_type:
            raise _protocol_error()
        if codec_type == "audio":
            count += 1
    if count == 0:
        raise DemucsPermanentAudioProbeError(DemucsAudioProbeFailureCode.NO_AUDIO_STREAM)
    return count


def _duration_seconds(probe: Mapping[str, object]) -> Decimal:
    """Require an exact, finite, positive format duration within the hard limit."""

    format_details = probe.get("format")
    if not isinstance(format_details, Mapping):
        raise DemucsPermanentAudioProbeError(DemucsAudioProbeFailureCode.INVALID_DURATION)
    raw_duration = format_details.get("duration")

    # FFprobe normally emits a JSON string (for example ``"12.345000"``), but
    # accept its equivalent exact JSON number too.  Reject bool explicitly:
    # bool is an int subclass and must not turn into a one-second duration.
    if isinstance(raw_duration, bool) or not isinstance(raw_duration, (str, Decimal)):
        raise DemucsPermanentAudioProbeError(DemucsAudioProbeFailureCode.INVALID_DURATION)
    if isinstance(raw_duration, str) and (not raw_duration or raw_duration.strip() != raw_duration):
        raise DemucsPermanentAudioProbeError(DemucsAudioProbeFailureCode.INVALID_DURATION)
    try:
        duration = Decimal(raw_duration)
    except (InvalidOperation, ValueError) as error:
        raise DemucsPermanentAudioProbeError(
            DemucsAudioProbeFailureCode.INVALID_DURATION
        ) from error
    if not duration.is_finite() or duration <= 0:
        raise DemucsPermanentAudioProbeError(DemucsAudioProbeFailureCode.INVALID_DURATION)
    if duration > MAX_DEMUCS_SOURCE_DURATION_SECONDS:
        raise DemucsPermanentAudioProbeError(
            DemucsAudioProbeFailureCode.DURATION_LIMIT_EXCEEDED
        )
    return duration


def parse_verified_demucs_audio_probe(probe_output: object) -> VerifiedDemucsAudioProbe:
    """Validate successful FFprobe JSON before the worker allocates Demucs.

    A future adapter should call FFprobe with ``-show_format -show_streams``
    and JSON output, then call this function only when the command itself
    succeeded.  A successful return proves the source has an audio stream and
    a finite, positive duration no greater than 500 seconds.  It does not
    inspect audio samples, choose a stream, execute a process, alter a lease,
    download from MinIO, or acknowledge RabbitMQ.
    """

    probe = _decode_probe_output(probe_output)
    # Check the audio evidence first: a video-only file is a definitive source
    # rejection even if it happens to include a usable container duration.
    audio_stream_count = _audio_stream_count(probe)
    duration_seconds = _duration_seconds(probe)
    return VerifiedDemucsAudioProbe(
        duration_seconds=duration_seconds,
        audio_stream_count=audio_stream_count,
    )
