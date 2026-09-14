"""Execute only the fixed local CPU ADTOF argv produced by the command builder.

This module is the private child-process entrypoint named by
``app.adtof_inference_command``. It rechecks the exact flag order, pinned model
revision, CPU configuration, and sibling scratch paths before it lazily imports
the reviewed ADTOF/audio libraries. It then writes only the reserved MIDI and
cloud-compatible tempo JSON files. The parent worker still must run the local
artifact verifier, MinIO uploader, stored-object verifier, and guarded task
completion in later separate boundaries.

No MinIO, PostgreSQL, RabbitMQ, Kubernetes, browser, or shell API is used
here. Model imports are lazy so source-level unit tests do not require ADTOF,
PyTorch, Librosa, NumPy, or PrettyMIDI to be installed.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.adtof_inference_command import (
    ADTOF_MIDI_OUTPUT_FILENAME,
    ADTOF_OUTPUT_DIRECTORY_NAME,
    ADTOF_TEMPO_OUTPUT_FILENAME,
)
from app.model_configuration import (
    ADTOF_CPU_DEVICE,
    ADTOF_FPS,
    ADTOF_MODEL_SOURCE_REVISION,
    ADTOF_THRESHOLDS,
    ADTOF_THRESHOLDS_ARGUMENT,
)
from app.output_artifact import parse_adtof_tempo_candidate


EXTRACTOR = "adtof"
MIN_DRUM_EVENT_COUNT = 8
MIN_BEAT_COUNT = 8
MIN_SHORT_CLIP_BEAT_COUNT = 4
MIN_BEAT_INTERVAL_CONSISTENCY = 0.70

_TEMPO_CANDIDATE_FIELDS = frozenset(
    {
        "bpm",
        "beat_count",
        "duration_seconds",
        "interval_consistency",
        "drum_event_count",
        "credible",
        "confidence",
        "source",
    }
)


class ADTOFCPUInferenceEntrypointError(RuntimeError):
    """Base safe category for a constrained ADTOF child-process outcome."""


class ADTOFCPUInferenceEntrypointContractError(ADTOFCPUInferenceEntrypointError):
    """The child argv/configuration does not equal the reviewed command shape."""


class ADTOFCPUInferenceEntrypointPathError(ADTOFCPUInferenceEntrypointError):
    """The expected private input/output scratch tree is unsafe or stale."""


class ADTOFCPUInferenceEntrypointUnavailable(ADTOFCPUInferenceEntrypointError):
    """The future worker image lacks a reviewed model/audio runtime dependency."""


class ADTOFCPUInferenceEntrypointModelError(ADTOFCPUInferenceEntrypointError):
    """ADTOF/model-adjacent work failed without exposing internal diagnostics."""


@dataclass(frozen=True)
class ADTOFCPUInferenceRequest:
    """The only runtime values admitted by the fixed command builder's argv."""

    input_wav_path: Path
    midi_output_path: Path
    tempo_output_path: Path


def _contract_error() -> ADTOFCPUInferenceEntrypointContractError:
    """Return a stable no-diagnostic category for malformed child arguments."""

    return ADTOFCPUInferenceEntrypointContractError("ADTOF CPU inference command is invalid.")


def _argument_value(value: object) -> str:
    """Accept one non-empty, NUL-free string exactly where fixed argv uses it."""

    if not isinstance(value, str) or not value or "\x00" in value:
        raise _contract_error()
    return value


def parse_adtof_cpu_inference_argv(argv: Sequence[str]) -> ADTOFCPUInferenceRequest:
    """Parse only the exact no-shell flag sequence emitted by the command builder.

    ``python -m`` removes the interpreter/module prefix before this function
    sees ``sys.argv[1:]``. Fixed flags and fixed configuration values are not
    merely defaults: a child launched outside the parent command builder still
    cannot select a different model revision, threshold, device, or output
    option by reordering/adding flags.
    """

    if not isinstance(argv, Sequence) or isinstance(argv, (str, bytes)):
        raise _contract_error()
    values = tuple(_argument_value(value) for value in argv)
    fixed = (
        "--input-wav",
        "--midi-output",
        "--tempo-output",
        "--model-revision",
        "--fps",
        "--thresholds",
        "--device",
    )
    if len(values) != len(fixed) * 2 or any(values[index * 2] != flag for index, flag in enumerate(fixed)):
        raise _contract_error()
    (
        _input_flag,
        input_wav,
        _midi_flag,
        midi_output,
        _tempo_flag,
        tempo_output,
        _revision_flag,
        model_revision,
        _fps_flag,
        fps,
        _thresholds_flag,
        thresholds,
        _device_flag,
        device,
    ) = values
    if (
        model_revision != ADTOF_MODEL_SOURCE_REVISION
        or fps != str(ADTOF_FPS)
        or thresholds != ADTOF_THRESHOLDS_ARGUMENT
        or device != ADTOF_CPU_DEVICE
    ):
        raise _contract_error()
    return ADTOFCPUInferenceRequest(
        input_wav_path=Path(input_wav),
        midi_output_path=Path(midi_output),
        tempo_output_path=Path(tempo_output),
    )


def _resolved_regular_input(path: Path) -> Path:
    """Require the model input's exact random stem filename below Pod scratch."""

    if not path.is_absolute():
        raise ADTOFCPUInferenceEntrypointPathError("ADTOF CPU inference paths are invalid.")
    try:
        if path.is_symlink():
            raise ADTOFCPUInferenceEntrypointPathError("ADTOF CPU inference paths are invalid.")
        resolved = path.resolve(strict=True)
        status = resolved.stat()
    except ADTOFCPUInferenceEntrypointPathError:
        raise
    except (OSError, RuntimeError) as error:
        raise ADTOFCPUInferenceEntrypointPathError("ADTOF CPU inference paths are invalid.") from error
    if (
        not stat.S_ISREG(status.st_mode)
        or resolved.name != "stem.wav"
        or not resolved.parent.name.startswith("adtof-stem-")
    ):
        raise ADTOFCPUInferenceEntrypointPathError("ADTOF CPU inference paths are invalid.")
    return resolved


def _validated_output_paths(
    *,
    input_wav_path: Path,
    midi_output_path: Path,
    tempo_output_path: Path,
) -> tuple[Path, Path, Path]:
    """Require one fresh sibling output directory with only the two fixed names."""

    resolved_input = _resolved_regular_input(input_wav_path)
    if not midi_output_path.is_absolute() or not tempo_output_path.is_absolute():
        raise ADTOFCPUInferenceEntrypointPathError("ADTOF CPU inference paths are invalid.")
    expected_directory = resolved_input.parent / ADTOF_OUTPUT_DIRECTORY_NAME
    try:
        if expected_directory.is_symlink():
            raise ADTOFCPUInferenceEntrypointPathError("ADTOF CPU inference paths are invalid.")
        resolved_directory = expected_directory.resolve(strict=True)
    except ADTOFCPUInferenceEntrypointPathError:
        raise
    except (OSError, RuntimeError) as error:
        raise ADTOFCPUInferenceEntrypointPathError("ADTOF CPU inference paths are invalid.") from error
    if not resolved_directory.is_dir() or resolved_directory != expected_directory:
        raise ADTOFCPUInferenceEntrypointPathError("ADTOF CPU inference paths are invalid.")

    expected_midi = resolved_directory / ADTOF_MIDI_OUTPUT_FILENAME
    expected_tempo = resolved_directory / ADTOF_TEMPO_OUTPUT_FILENAME
    if midi_output_path != expected_midi or tempo_output_path != expected_tempo:
        raise ADTOFCPUInferenceEntrypointPathError("ADTOF CPU inference paths are invalid.")
    try:
        if any(resolved_directory.iterdir()) or expected_midi.exists() or expected_tempo.exists():
            raise ADTOFCPUInferenceEntrypointPathError("ADTOF CPU inference output is not fresh.")
    except ADTOFCPUInferenceEntrypointPathError:
        raise
    except (OSError, RuntimeError) as error:
        raise ADTOFCPUInferenceEntrypointPathError("ADTOF CPU inference paths are invalid.") from error
    return resolved_input, expected_midi, expected_tempo


def _default_transcriber(
    input_wav_path: Path,
    midi_output_path: Path,
    *,
    thresholds: tuple[float, ...],
    fps: int,
    device: str,
) -> None:
    """Lazily import the pinned ADTOF package and call its documented CPU API."""

    try:
        from adtof_pytorch import transcribe_to_midi
    except ImportError as error:
        raise ADTOFCPUInferenceEntrypointUnavailable("ADTOF CPU runtime is unavailable.") from error
    transcribe_to_midi(
        input_wav_path,
        midi_output_path,
        thresholds=thresholds,
        fps=fps,
        device=device,
    )


def _default_drum_event_count(midi_output_path: Path) -> int:
    """Count model drum notes; preserve cloud's safe zero-event fallback."""

    try:
        from pretty_midi import PrettyMIDI

        midi = PrettyMIDI(str(midi_output_path))
        return sum(len(instrument.notes) for instrument in midi.instruments)
    except Exception:
        # A MIDI framing failure will still be caught by the later artifact
        # verifier. Here a count failure simply means tempo cannot be credible.
        return 0


def _default_tempo_candidate(
    input_wav_path: Path,
    drum_event_count: int,
) -> dict[str, object]:
    """Calculate the cloud-compatible drum-derived tempo observation or fallback."""

    try:
        import librosa
        import numpy as np

        audio, sample_rate = librosa.load(str(input_wav_path), sr=None, mono=True)
        tempo, beat_times = librosa.beat.beat_track(y=audio, sr=sample_rate, units="time")
        tempo_values = np.asarray(tempo).reshape(-1)
        bpm = float(tempo_values[0]) if tempo_values.size else 0.0
        beats = np.asarray(beat_times, dtype=float).reshape(-1)
        duration_seconds = len(audio) / sample_rate if sample_rate else 0.0
        intervals = np.diff(beats)
        if intervals.size:
            median_interval = float(np.median(intervals))
            tolerance = max(0.04, median_interval * 0.12)
            interval_consistency = float(np.mean(np.abs(intervals - median_interval) <= tolerance))
        else:
            interval_consistency = 0.0
        minimum_beats = MIN_SHORT_CLIP_BEAT_COUNT if duration_seconds < 20 else MIN_BEAT_COUNT
        credible = bool(
            np.isfinite(bpm)
            and bpm > 0
            and drum_event_count >= MIN_DRUM_EVENT_COUNT
            and beats.size >= minimum_beats
            and interval_consistency >= MIN_BEAT_INTERVAL_CONSISTENCY
        )
        return {
            "bpm": round(bpm, 2) if np.isfinite(bpm) and bpm > 0 else None,
            "beat_count": int(beats.size),
            "duration_seconds": round(duration_seconds, 2),
            "interval_consistency": round(interval_consistency, 3),
            "drum_event_count": drum_event_count,
            "credible": credible,
            "confidence": "high" if credible else "low",
            "source": "adtof_drums",
        }
    except Exception:
        return {
            "bpm": None,
            "beat_count": 0,
            "duration_seconds": 0.0,
            "interval_consistency": 0.0,
            "drum_event_count": drum_event_count,
            "credible": False,
            "confidence": "low",
            "source": "adtof_drums",
        }


def _tempo_payload(candidate: object) -> bytes:
    """Use the shared verifier parser before writing one tempo JSON file."""

    if not isinstance(candidate, Mapping) or set(candidate) != _TEMPO_CANDIDATE_FIELDS:
        raise ADTOFCPUInferenceEntrypointModelError("ADTOF tempo calculation failed.")
    try:
        payload = json.dumps(
            {"extractor": EXTRACTOR, **candidate},
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        # Validate producer output before it becomes a local file. The later
        # file verifier repeats this after a safe read/hash to detect changes.
        parse_adtof_tempo_candidate(payload)
    except Exception as error:
        raise ADTOFCPUInferenceEntrypointModelError("ADTOF tempo calculation failed.") from error
    return payload


def _write_new_tempo_payload(path: Path, payload: bytes) -> None:
    """Create exactly the planned JSON file without following an existing link."""

    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb", buffering=0) as destination:
            written = destination.write(payload)
            if written != len(payload):
                raise OSError("short private tempo write")
            destination.flush()
            os.fsync(destination.fileno())
    except OSError as error:
        raise ADTOFCPUInferenceEntrypointPathError("ADTOF tempo output could not be written.") from error


ADTOFDrumEventCounter = Callable[[Path], int]
ADTOFTempoEstimator = Callable[[Path, int], Mapping[str, object]]


def run_adtof_cpu_inference_entrypoint(
    argv: Sequence[str],
    *,
    transcriber: Callable[..., None] | None = None,
    drum_event_counter: ADTOFDrumEventCounter | None = None,
    tempo_estimator: ADTOFTempoEstimator | None = None,
) -> ADTOFCPUInferenceRequest:
    """Run one parsed CPU model request and write only its two reserved outputs.

    Callers normally execute this module through the fixed child-process command
    rather than importing it. Injectable functions make the contract testable
    without an ADTOF image. Failures intentionally return only reviewed local
    categories; outer process supervision decides timeout/retry/task policy.
    """

    request = parse_adtof_cpu_inference_argv(argv)
    input_wav_path, midi_output_path, tempo_output_path = _validated_output_paths(
        input_wav_path=request.input_wav_path,
        midi_output_path=request.midi_output_path,
        tempo_output_path=request.tempo_output_path,
    )
    selected_transcriber = transcriber or _default_transcriber
    try:
        selected_transcriber(
            input_wav_path,
            midi_output_path,
            thresholds=ADTOF_THRESHOLDS,
            fps=ADTOF_FPS,
            device=ADTOF_CPU_DEVICE,
        )
    except ADTOFCPUInferenceEntrypointError:
        raise
    except Exception as error:
        raise ADTOFCPUInferenceEntrypointModelError("ADTOF CPU inference failed.") from error

    selected_counter = drum_event_counter or _default_drum_event_count
    selected_estimator = tempo_estimator or _default_tempo_candidate
    try:
        drum_event_count = selected_counter(midi_output_path)
        if type(drum_event_count) is not int or drum_event_count < 0:
            raise ValueError("invalid drum event count")
        candidate = selected_estimator(input_wav_path, drum_event_count)
        payload = _tempo_payload(candidate)
    except ADTOFCPUInferenceEntrypointError:
        raise
    except Exception as error:
        raise ADTOFCPUInferenceEntrypointModelError("ADTOF tempo calculation failed.") from error
    _write_new_tempo_payload(tempo_output_path, payload)
    return ADTOFCPUInferenceRequest(
        input_wav_path=input_wav_path,
        midi_output_path=midi_output_path,
        tempo_output_path=tempo_output_path,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Return a generic process status without printing model/path diagnostics."""

    try:
        run_adtof_cpu_inference_entrypoint(tuple(sys.argv[1:] if argv is None else argv))
    except ADTOFCPUInferenceEntrypointError:
        # The parent process runner intentionally suppresses child stderr. This
        # one generic line nevertheless makes direct local troubleshooting
        # possible without exposing a private path, raw model output, or secret.
        print("ADTOF CPU inference failed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the image later.
    raise SystemExit(main())
