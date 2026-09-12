"""Build and run one bounded shell-free Basic Pitch CLI invocation.

The caller must already hold a committed ``running`` task lease and a verified
temporary ``stem.wav`` from :mod:`app.stem_task_start`. This module creates one
fresh private output directory beside that WAV, executes Basic Pitch's fixed
CLI without a shell, and returns only the expected local MIDI coordinate. A
zero exit still does not prove a safe MIDI artifact; a later output verifier
must inspect the generated file before MinIO upload.

It has no MinIO, PostgreSQL, RabbitMQ, model-result, or Kubernetes API code.
The actual Basic Pitch package is deliberately not imported: the separate
process lets a timeout terminate all child helpers in one process group and
keeps package dependencies isolated in the future worker image.
"""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.stem_task_start import RunningBasicPitchStem


# The future image installs the reviewed Basic Pitch package's console script
# at this absolute location. Avoiding PATH lookup prevents a Deployment
# environment override from replacing the inference executable. Basic Pitch's
# documented command shape is: `basic-pitch <output-directory> <input-audio>`.
BASIC_PITCH_EXECUTABLE = "/usr/local/bin/basic-pitch"
BASIC_PITCH_SOURCE_FILENAME = "stem.wav"
BASIC_PITCH_OUTPUT_DIRECTORY_NAME = "midi-output"
BASIC_PITCH_MIDI_SUFFIX = "_basic_pitch.mid"

# Basic Pitch is local CPU work. Five minutes bounds an ordinary bounded stem
# while the ten-minute maximum leaves a five-minute margin within the current
# 15-minute task lease for renewal/recovery/output cleanup in a later runtime.
DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS = 5 * 60
MIN_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS = 1
MAX_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS = 10 * 60


class BasicPitchProcessError(RuntimeError):
    """Base safe operational category for a Basic Pitch child process outcome."""


class BasicPitchProcessContractError(BasicPitchProcessError):
    """A caller supplied a hand-built/widened process request or timeout."""


class BasicPitchProcessPathError(BasicPitchProcessError):
    """The local running stem/output tree is unsafe for the non-root worker."""


class BasicPitchProcessUnavailable(BasicPitchProcessError):
    """The reviewed worker image did not provide the Basic Pitch executable."""


class BasicPitchProcessTimedOut(BasicPitchProcessError):
    """Basic Pitch exceeded its bounded local CPU execution budget."""


class BasicPitchProcessFailed(BasicPitchProcessError):
    """Basic Pitch exited unsuccessfully without exposing child diagnostics."""


@dataclass(frozen=True)
class BasicPitchInferenceCommand:
    """The one fixed CLI request and deterministic local MIDI location.

    The caller must not trust ``expected_midi_path`` merely because this object
    exists or the command returns zero. It is a private Pod-local coordinate
    that a later verifier will independently require to be a regular MIDI file
    before it can form an upload contract.
    """

    command: tuple[str, ...]
    source_path: Path
    output_directory: Path
    expected_midi_path: Path
    work_directory: Path


class BasicPitchProcessRunner(Protocol):
    """Injectable process surface that accepts no arbitrary shell command."""

    def run(
        self,
        *,
        inference: BasicPitchInferenceCommand,
        timeout_seconds: int,
    ) -> None:
        """Run one approved Basic Pitch inference command or raise a safe category."""


def _validated_timeout_seconds(value: object) -> int:
    """Require a finite whole-second deadline before spawning a child process."""

    if (
        type(value) is not int
        or not MIN_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS <= value <= MAX_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS
    ):
        raise BasicPitchProcessContractError("Basic Pitch process timeout is invalid.")
    return value


def _resolved_worker_directory(directory: object) -> Path:
    """Require an existing non-symlink directory mounted as Pod scratch."""

    if not isinstance(directory, Path):
        raise BasicPitchProcessPathError("Basic Pitch worker directory is invalid.")
    try:
        if directory.is_symlink():
            raise BasicPitchProcessPathError("Basic Pitch worker directory is invalid.")
        resolved = directory.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise BasicPitchProcessPathError("Basic Pitch worker directory is invalid.") from error
    if not resolved.is_dir():
        raise BasicPitchProcessPathError("Basic Pitch worker directory is invalid.")
    return resolved


def _validated_running_stem(
    *,
    running: object,
    work_directory: object,
) -> tuple[RunningBasicPitchStem, Path, Path]:
    """Validate one temporary worker-owned WAV and its fresh output coordinate."""

    if not isinstance(running, RunningBasicPitchStem):
        raise BasicPitchProcessContractError("Basic Pitch running stem is invalid.")
    resolved_work_directory = _resolved_worker_directory(work_directory)
    source_path = running.stem.stem_path
    if not isinstance(source_path, Path):
        raise BasicPitchProcessPathError("Basic Pitch running stem path is invalid.")
    try:
        if source_path.is_symlink():
            raise BasicPitchProcessPathError("Basic Pitch running stem path is invalid.")
        resolved_source_path = source_path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise BasicPitchProcessPathError("Basic Pitch running stem path is invalid.") from error
    if (
        not resolved_source_path.is_file()
        or resolved_source_path.name != BASIC_PITCH_SOURCE_FILENAME
        or not resolved_source_path.parent.name.startswith("basic-pitch-stem-")
    ):
        raise BasicPitchProcessPathError("Basic Pitch running stem path is invalid.")
    try:
        resolved_source_path.relative_to(resolved_work_directory)
    except ValueError as error:
        raise BasicPitchProcessPathError("Basic Pitch running stem path is invalid.") from error

    # The per-stem download scope owns this parent. A fixed fresh sibling
    # prevents the Basic Pitch CLI from overwriting a prior run or receiving a
    # MinIO/browser-derived path as an output location.
    output_directory = resolved_source_path.parent / BASIC_PITCH_OUTPUT_DIRECTORY_NAME
    if output_directory.exists() or output_directory.is_symlink():
        raise BasicPitchProcessPathError("Basic Pitch output directory is not fresh.")
    return running, resolved_source_path, resolved_work_directory


def build_basic_pitch_inference_command(
    *,
    running: RunningBasicPitchStem,
    work_directory: Path,
) -> BasicPitchInferenceCommand:
    """Allocate an empty private output directory and build the one CLI tuple.

    This allocation occurs only within the caller's temporary download scope.
    The directory uses mode 0700 and cannot exist already. The returned command
    contains no user input, shell syntax, configurable model choice, browser
    URL, bucket/key, or output format selection—Basic Pitch's default CLI mode
    writes the single MIDI transcription we verify in the next task.
    """

    _running, source_path, resolved_work_directory = _validated_running_stem(
        running=running,
        work_directory=work_directory,
    )
    output_directory = source_path.parent / BASIC_PITCH_OUTPUT_DIRECTORY_NAME
    try:
        output_directory.mkdir(mode=0o700)
    except OSError as error:
        raise BasicPitchProcessPathError("Basic Pitch output directory could not be created.") from error

    expected_midi_path = output_directory / f"{source_path.stem}{BASIC_PITCH_MIDI_SUFFIX}"
    command = (BASIC_PITCH_EXECUTABLE, str(output_directory), str(source_path))
    return BasicPitchInferenceCommand(
        command=command,
        source_path=source_path,
        output_directory=output_directory,
        expected_midi_path=expected_midi_path,
        work_directory=resolved_work_directory,
    )


def _approved_inference_or_raise(value: object) -> BasicPitchInferenceCommand:
    """Revalidate an immutable request immediately before child-process launch."""

    if not isinstance(value, BasicPitchInferenceCommand):
        raise BasicPitchProcessContractError("Basic Pitch process command is invalid.")
    try:
        if (
            value.work_directory.is_symlink()
            or value.source_path.is_symlink()
            or value.output_directory.is_symlink()
        ):
            raise BasicPitchProcessPathError("Basic Pitch process paths are invalid.")
        work_directory = value.work_directory.resolve(strict=True)
        source_path = value.source_path.resolve(strict=True)
        output_directory = value.output_directory.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise BasicPitchProcessPathError("Basic Pitch process paths are invalid.") from error
    if (
        not work_directory.is_dir()
        or not source_path.is_file()
        or source_path.name != BASIC_PITCH_SOURCE_FILENAME
        or not source_path.parent.name.startswith("basic-pitch-stem-")
        or not output_directory.is_dir()
        or output_directory.name != BASIC_PITCH_OUTPUT_DIRECTORY_NAME
        or output_directory.parent != source_path.parent
        or value.expected_midi_path != output_directory / f"{source_path.stem}{BASIC_PITCH_MIDI_SUFFIX}"
        or value.command != (BASIC_PITCH_EXECUTABLE, str(output_directory), str(source_path))
    ):
        raise BasicPitchProcessContractError("Basic Pitch process command is invalid.")
    try:
        source_path.relative_to(work_directory)
        output_directory.relative_to(work_directory)
        if any(output_directory.iterdir()):
            raise BasicPitchProcessPathError("Basic Pitch output directory is not empty.")
    except ValueError as error:
        raise BasicPitchProcessPathError("Basic Pitch process paths are invalid.") from error
    except (OSError, RuntimeError) as error:
        raise BasicPitchProcessPathError("Basic Pitch process paths are invalid.") from error
    return BasicPitchInferenceCommand(
        command=value.command,
        source_path=source_path,
        output_directory=output_directory,
        expected_midi_path=value.expected_midi_path,
        work_directory=work_directory,
    )


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    """Best-effort termination of Basic Pitch and helpers in one private session."""

    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=1)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        # Kubernetes ultimately terminates a pathological Pod. Preserve the
        # original safe timeout category rather than process-management detail.
        return


class SubprocessBasicPitchProcessRunner:
    """Production runner for the fixed CLI in private Pod-local scratch."""

    def run(
        self,
        *,
        inference: BasicPitchInferenceCommand,
        timeout_seconds: int,
    ) -> None:
        """Execute without shell/stdin/log capture and stop the full group on timeout."""

        approved = _approved_inference_or_raise(inference)
        bounded_timeout_seconds = _validated_timeout_seconds(timeout_seconds)
        try:
            process = subprocess.Popen(
                approved.command,
                cwd=str(approved.output_directory),
                stdin=subprocess.DEVNULL,
                # Child diagnostics may include local paths/model details. The
                # later result boundary uses reviewed categories, not raw logs.
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                start_new_session=True,
            )
        except FileNotFoundError as error:
            raise BasicPitchProcessUnavailable("Basic Pitch executable is unavailable.") from error
        except OSError as error:
            raise BasicPitchProcessError("Basic Pitch process could not start.") from error

        try:
            process.wait(timeout=bounded_timeout_seconds)
        except subprocess.TimeoutExpired as error:
            _stop_process_group(process)
            raise BasicPitchProcessTimedOut("Basic Pitch process timed out.") from error
        if process.returncode != 0:
            raise BasicPitchProcessFailed("Basic Pitch process failed.")


def run_basic_pitch_inference(
    inference: BasicPitchInferenceCommand,
    *,
    timeout_seconds: int = DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS,
    runner: BasicPitchProcessRunner | None = None,
) -> BasicPitchInferenceCommand:
    """Run only an approved Basic Pitch command and return its output plan on exit 0.

    A zero exit means solely that the process boundary completed. The next
    output verifier must still prove that ``expected_midi_path`` exists, is a
    safe bounded MIDI file, and agrees with the current task before MinIO sees
    any artifact. Tests inject a runner, so this module needs no Basic Pitch
    installation or real audio to validate its command/timeout contract.
    """

    approved = _approved_inference_or_raise(inference)
    bounded_timeout_seconds = _validated_timeout_seconds(timeout_seconds)
    selected_runner = runner if runner is not None else SubprocessBasicPitchProcessRunner()
    selected_runner.run(inference=approved, timeout_seconds=bounded_timeout_seconds)
    return approved
