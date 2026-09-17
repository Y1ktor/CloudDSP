"""Run one fixed CPU ADTOF child process under a bounded process-group policy.

The preceding command builder has reserved a fresh private output directory;
this adapter revalidates every field immediately before it starts the child.
It executes only the fixed Python-module argv without a shell, gives the child
a private session, and on timeout terminates then force-kills the whole process
group. This prevents a spawned numerical/audio helper from surviving after the
parent ADTOF process is stopped.

A zero exit proves only that the child returned successfully. The caller must
still verify the local MIDI/tempo files, upload them through restricted MinIO,
verify stored-object metadata, and make a guarded PostgreSQL completion. This
module has no MinIO, PostgreSQL, RabbitMQ, Kubernetes, or browser API.
"""

from __future__ import annotations

import os
import signal
import stat
import subprocess
from pathlib import Path
from typing import Protocol

from app.adtof_inference_command import (
    ADTOF_CPU_ENTRYPOINT_MODULE,
    ADTOF_MIDI_OUTPUT_FILENAME,
    ADTOF_OUTPUT_DIRECTORY_NAME,
    ADTOF_PYTHON_EXECUTABLE,
    ADTOF_TEMPO_OUTPUT_FILENAME,
    ADTOFCPUInferenceCommand,
)
from app.model_configuration import (
    ADTOF_CPU_DEVICE,
    ADTOF_FPS,
    ADTOF_MODEL_CONFIGURATION_ID,
    ADTOF_MODEL_SOURCE_REVISION,
    ADTOF_THRESHOLDS_ARGUMENT,
)


# The local ADTOF task's lease is currently fifteen minutes. Ten minutes is the
# normal CPU budget; twelve is an absolute upper bound that preserves at least
# three minutes for artifact verification, two private uploads, HeadObject
# checks, completion, and shutdown/recovery before the original lease expires.
DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS = 10 * 60
MIN_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS = 1
MAX_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS = 12 * 60

# The worker image copies the `app` package into this fixed, read-only location
# and starts its own PID 1 from here. The model child uses ``python -m app...``;
# it must therefore keep this directory on Python's module-search path. Source
# and output paths are absolute reviewed arguments, so the child never needs a
# writable current working directory to locate its media files.
ADTOF_CPU_PROCESS_WORKING_DIRECTORY = "/app"


class ADTOFCPUProcessError(RuntimeError):
    """Base safe operational category for an ADTOF CPU child-process outcome."""


class ADTOFCPUProcessContractError(ADTOFCPUProcessError):
    """A hand-built/widened command or timeout cannot be executed."""


class ADTOFCPUProcessPathError(ADTOFCPUProcessError):
    """The private source/output scratch tree is unsafe or no longer fresh."""


class ADTOFCPUProcessUnavailable(ADTOFCPUProcessError):
    """The reviewed Python entrypoint is absent from the future worker image."""


class ADTOFCPUProcessTimedOut(ADTOFCPUProcessError):
    """ADTOF exceeded its finite local CPU execution deadline."""


class ADTOFCPUProcessFailed(ADTOFCPUProcessError):
    """ADTOF exited unsuccessfully without exposing child diagnostics."""


class ADTOFCPUProcessRunner(Protocol):
    """Injectable no-shell child-process surface for source-level tests."""

    def run(
        self,
        *,
        inference: ADTOFCPUInferenceCommand,
        timeout_seconds: int,
    ) -> None:
        """Run one approved request or raise a reviewed process category."""


def _validated_timeout_seconds(value: object) -> int:
    """Require a finite whole-second timeout within the remaining lease budget."""

    if (
        type(value) is not int
        or not MIN_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS <= value <= MAX_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS
    ):
        raise ADTOFCPUProcessContractError("ADTOF CPU process timeout is invalid.")
    return value


def _approved_inference_or_raise(value: object) -> ADTOFCPUInferenceCommand:
    """Revalidate every fixed field immediately before the child is launched."""

    if not isinstance(value, ADTOFCPUInferenceCommand):
        raise ADTOFCPUProcessContractError("ADTOF CPU process command is invalid.")
    if not all(
        isinstance(path, Path)
        for path in (
            value.work_directory,
            value.source_path,
            value.output_directory,
            value.midi_output_path,
            value.tempo_output_path,
        )
    ):
        raise ADTOFCPUProcessContractError("ADTOF CPU process command is invalid.")
    try:
        if (
            value.work_directory.is_symlink()
            or value.source_path.is_symlink()
            or value.output_directory.is_symlink()
        ):
            raise ADTOFCPUProcessPathError("ADTOF CPU process paths are invalid.")
        work_directory = value.work_directory.resolve(strict=True)
        source_path = value.source_path.resolve(strict=True)
        output_directory = value.output_directory.resolve(strict=True)
    except ADTOFCPUProcessPathError:
        raise
    except (OSError, RuntimeError) as error:
        raise ADTOFCPUProcessPathError("ADTOF CPU process paths are invalid.") from error
    try:
        # An ``emptyDir`` prepared with the Pod ``fsGroup`` is set-group-ID
        # (mode ``02777``). POSIX then propagates that *group-ID* bit to the
        # private child we create here, producing ``02700`` instead of bare
        # ``0700``. The group has no read, write, or execute permissions, so
        # this does not widen access to model inputs or outputs. Check the
        # permission bits separately, allow only the inherited setgid bit,
        # and continue rejecting setuid/sticky special modes.
        output_directory_mode = stat.S_IMODE(output_directory.stat().st_mode)
        output_directory_permissions = output_directory_mode & 0o777
        has_disallowed_special_mode = bool(output_directory_mode & (stat.S_ISUID | stat.S_ISVTX))
    except OSError as error:
        raise ADTOFCPUProcessPathError("ADTOF CPU process paths are invalid.") from error
    if (
        value.model_configuration_id != ADTOF_MODEL_CONFIGURATION_ID
        or not work_directory.is_dir()
        or not source_path.is_file()
        or source_path.name != "stem.wav"
        or not source_path.parent.name.startswith("adtof-stem-")
        or not output_directory.is_dir()
        or output_directory.name != ADTOF_OUTPUT_DIRECTORY_NAME
        or output_directory.parent != source_path.parent
        # ``02700`` is the expected Kubernetes ``fsGroup`` variant of this
        # otherwise owner-only directory. Any group/other permission remains
        # forbidden, as do setuid and sticky bits.
        or output_directory_permissions != 0o700
        or has_disallowed_special_mode
        or value.midi_output_path != output_directory / ADTOF_MIDI_OUTPUT_FILENAME
        or value.tempo_output_path != output_directory / ADTOF_TEMPO_OUTPUT_FILENAME
        or value.command
        != (
            ADTOF_PYTHON_EXECUTABLE,
            "-m",
            ADTOF_CPU_ENTRYPOINT_MODULE,
            "--input-wav",
            str(source_path),
            "--midi-output",
            str(output_directory / ADTOF_MIDI_OUTPUT_FILENAME),
            "--tempo-output",
            str(output_directory / ADTOF_TEMPO_OUTPUT_FILENAME),
            "--model-revision",
            ADTOF_MODEL_SOURCE_REVISION,
            "--fps",
            str(ADTOF_FPS),
            "--thresholds",
            ADTOF_THRESHOLDS_ARGUMENT,
            "--device",
            ADTOF_CPU_DEVICE,
        )
    ):
        raise ADTOFCPUProcessContractError("ADTOF CPU process command is invalid.")
    try:
        source_path.relative_to(work_directory)
        output_directory.relative_to(work_directory)
        if any(output_directory.iterdir()):
            raise ADTOFCPUProcessPathError("ADTOF CPU output directory is not empty.")
    except ADTOFCPUProcessPathError:
        raise
    except ValueError as error:
        raise ADTOFCPUProcessPathError("ADTOF CPU process paths are invalid.") from error
    except (OSError, RuntimeError) as error:
        raise ADTOFCPUProcessPathError("ADTOF CPU process paths are invalid.") from error
    return ADTOFCPUInferenceCommand(
        command=value.command,
        source_path=source_path,
        output_directory=output_directory,
        midi_output_path=output_directory / ADTOF_MIDI_OUTPUT_FILENAME,
        tempo_output_path=output_directory / ADTOF_TEMPO_OUTPUT_FILENAME,
        work_directory=work_directory,
        model_configuration_id=ADTOF_MODEL_CONFIGURATION_ID,
    )


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    """Best-effort stop of ADTOF and any child helpers in one private session."""

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
        # Kubernetes eventually terminates a pathological Pod. Preserve the
        # original reviewed timeout rather than emitting process details.
        return


class SubprocessADTOFCPUProcessRunner:
    """Production runner for exactly one private, no-shell CPU entrypoint call."""

    def run(
        self,
        *,
        inference: ADTOFCPUInferenceCommand,
        timeout_seconds: int,
    ) -> None:
        """Start the fixed command and terminate its full group if it overruns."""

        approved = _approved_inference_or_raise(inference)
        bounded_timeout_seconds = _validated_timeout_seconds(timeout_seconds)
        try:
            process = subprocess.Popen(
                approved.command,
                # Do not use the private output directory as CWD. Python puts
                # the CWD on ``sys.path`` for ``-m`` execution, and that
                # scratch directory cannot import the image's `/app/app`
                # package. All audio/output paths are explicit absolute argv
                # values, while this fixed read-only CWD keeps module import
                # deterministic and prevents a scratch path from becoming a
                # code-import location.
                cwd=ADTOF_CPU_PROCESS_WORKING_DIRECTORY,
                stdin=subprocess.DEVNULL,
                # Model/audio libraries can mention private scratch paths in
                # diagnostics. Later task state uses safe categories only.
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                start_new_session=True,
            )
        except FileNotFoundError as error:
            raise ADTOFCPUProcessUnavailable("ADTOF CPU executable is unavailable.") from error
        except OSError as error:
            raise ADTOFCPUProcessError("ADTOF CPU process could not start.") from error
        try:
            process.wait(timeout=bounded_timeout_seconds)
        except subprocess.TimeoutExpired as error:
            _stop_process_group(process)
            raise ADTOFCPUProcessTimedOut("ADTOF CPU process timed out.") from error
        if process.returncode != 0:
            raise ADTOFCPUProcessFailed("ADTOF CPU process failed.")


def run_adtof_cpu_inference_process(
    inference: ADTOFCPUInferenceCommand,
    *,
    timeout_seconds: int = DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS,
    runner: ADTOFCPUProcessRunner | None = None,
) -> ADTOFCPUInferenceCommand:
    """Run only an approved ADTOF command and return it after exit status zero.

    The caller must immediately pass the returned private paths to the local
    artifact verifier. No result is durable and no uploaded-object/task success
    is implied by a zero exit. The injectable runner gives source tests all
    ordering/validation coverage without requiring an ADTOF image or audio.
    """

    approved = _approved_inference_or_raise(inference)
    bounded_timeout_seconds = _validated_timeout_seconds(timeout_seconds)
    selected_runner = runner if runner is not None else SubprocessADTOFCPUProcessRunner()
    selected_runner.run(inference=approved, timeout_seconds=bounded_timeout_seconds)
    return approved
