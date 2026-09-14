"""Build the fixed, CPU-only ADTOF inference command and scratch output paths.

The caller must already hold a ``RunningADTOFStem``: its drums WAV was streamed
from MinIO with SHA-256 verification and its exact task moved to ``running`` in
a committed PostgreSQL transaction. This boundary reserves one empty sibling
output directory and returns the no-shell Python command that a *later*
execution boundary may run.

No model package is imported here, and this module never executes the command.
It does not upload to MinIO, mutate PostgreSQL, acknowledge RabbitMQ, build an
image, or use Kubernetes. A zero exit from the future runner will still require
the separate local MIDI/tempo artifact verifier before any upload is possible.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.model_configuration import (
    ADTOF_CPU_DEVICE,
    ADTOF_FPS,
    ADTOF_MODEL_CONFIGURATION_ID,
    ADTOF_MODEL_SOURCE_REVISION,
    ADTOF_THRESHOLDS_ARGUMENT,
)
from app.output_object_plan import build_adtof_output_object_plans
from app.stem_task_start import RunningADTOFStem


# The worker image provides the Python runtime at this fixed location. Invoking
# it directly avoids PATH lookup, and ``-m`` lets the future internal entrypoint
# import the pinned model package inside a child process without shell parsing.
ADTOF_PYTHON_EXECUTABLE = "/usr/local/bin/python"
ADTOF_CPU_ENTRYPOINT_MODULE = "app.adtof_cpu_inference_entrypoint"
ADTOF_OUTPUT_DIRECTORY_NAME = "adtof-output"
ADTOF_MIDI_OUTPUT_FILENAME = "drums.mid"
ADTOF_TEMPO_OUTPUT_FILENAME = "drums_bpm.json"


class ADTOFInferenceCommandError(RuntimeError):
    """Base safe category for an invalid fixed ADTOF inference request."""


class ADTOFInferenceCommandContractError(ADTOFInferenceCommandError):
    """A direct/widened running-task value cannot form a CPU command."""


class ADTOFInferenceCommandPathError(ADTOFInferenceCommandError):
    """The worker scratch/stem/output path is unsafe for non-root model work."""


@dataclass(frozen=True)
class ADTOFCPUInferenceCommand:
    """One deterministic no-shell request and its private local output paths.

    ``model_configuration_id`` is duplicated from the output plan's immutable
    provenance so a future runner can prove that its fixed argv and the planned
    stored-artifact metadata name exactly the same ADTOF configuration.
    """

    command: tuple[str, ...]
    source_path: Path
    output_directory: Path
    midi_output_path: Path
    tempo_output_path: Path
    work_directory: Path
    model_configuration_id: str


def _resolved_worker_directory(directory: object) -> Path:
    """Require an existing non-symlink directory mounted as Pod scratch."""

    if not isinstance(directory, Path):
        raise ADTOFInferenceCommandPathError("ADTOF worker directory is invalid.")
    try:
        if directory.is_symlink():
            raise ADTOFInferenceCommandPathError("ADTOF worker directory is invalid.")
        resolved = directory.resolve(strict=True)
    except ADTOFInferenceCommandPathError:
        raise
    except (OSError, RuntimeError) as error:
        raise ADTOFInferenceCommandPathError("ADTOF worker directory is invalid.") from error
    if not resolved.is_dir():
        raise ADTOFInferenceCommandPathError("ADTOF worker directory is invalid.")
    return resolved


def _validated_running_stem(
    *,
    running: object,
    work_directory: object,
) -> tuple[RunningADTOFStem, Path, Path]:
    """Require a current running drums handoff and its exact temporary WAV path."""

    if not isinstance(running, RunningADTOFStem):
        raise ADTOFInferenceCommandContractError("ADTOF running stem is invalid.")
    # Reuse the output planner's independent UUID/lease/stem-mode/input-digest
    # checks before touching a local path. The return value is intentionally
    # discarded: this boundary only needs its revalidation guarantee.
    try:
        build_adtof_output_object_plans(running=running)
    except Exception as error:
        raise ADTOFInferenceCommandContractError("ADTOF running stem is invalid.") from error

    resolved_work_directory = _resolved_worker_directory(work_directory)
    source_path = running.stem.stem_path
    if not isinstance(source_path, Path):
        raise ADTOFInferenceCommandPathError("ADTOF running stem path is invalid.")
    try:
        if source_path.is_symlink():
            raise ADTOFInferenceCommandPathError("ADTOF running stem path is invalid.")
        resolved_source_path = source_path.resolve(strict=True)
    except ADTOFInferenceCommandPathError:
        raise
    except (OSError, RuntimeError) as error:
        raise ADTOFInferenceCommandPathError("ADTOF running stem path is invalid.") from error
    if (
        not resolved_source_path.is_file()
        or resolved_source_path.name != "stem.wav"
        or not resolved_source_path.parent.name.startswith("adtof-stem-")
    ):
        raise ADTOFInferenceCommandPathError("ADTOF running stem path is invalid.")
    try:
        resolved_source_path.relative_to(resolved_work_directory)
    except ValueError as error:
        raise ADTOFInferenceCommandPathError("ADTOF running stem path is invalid.") from error
    return running, resolved_source_path, resolved_work_directory


def build_adtof_cpu_inference_command(
    *,
    running: RunningADTOFStem,
    work_directory: Path,
) -> ADTOFCPUInferenceCommand:
    """Reserve empty private paths and construct the one reviewed CPU-only argv.

    The output directory lives beside the random download directory, so the
    outer running-stem context removes both input and outputs together. It is
    created mode 0700 and may not already exist; a stale model run therefore
    cannot overwrite previous evidence. Every argument is source-controlled:
    no browser value, MinIO coordinate, shell syntax, model selection, device,
    threshold, FPS, or output filename is configurable at this boundary.
    """

    _running, source_path, resolved_work_directory = _validated_running_stem(
        running=running,
        work_directory=work_directory,
    )
    output_directory = source_path.parent / ADTOF_OUTPUT_DIRECTORY_NAME
    if output_directory.exists() or output_directory.is_symlink():
        raise ADTOFInferenceCommandPathError("ADTOF output directory is not fresh.")
    try:
        output_directory.mkdir(mode=0o700)
    except OSError as error:
        raise ADTOFInferenceCommandPathError("ADTOF output directory could not be created.") from error

    midi_output_path = output_directory / ADTOF_MIDI_OUTPUT_FILENAME
    tempo_output_path = output_directory / ADTOF_TEMPO_OUTPUT_FILENAME
    command = (
        ADTOF_PYTHON_EXECUTABLE,
        "-m",
        ADTOF_CPU_ENTRYPOINT_MODULE,
        "--input-wav",
        str(source_path),
        "--midi-output",
        str(midi_output_path),
        "--tempo-output",
        str(tempo_output_path),
        "--model-revision",
        ADTOF_MODEL_SOURCE_REVISION,
        "--fps",
        str(ADTOF_FPS),
        "--thresholds",
        ADTOF_THRESHOLDS_ARGUMENT,
        "--device",
        ADTOF_CPU_DEVICE,
    )
    return ADTOFCPUInferenceCommand(
        command=command,
        source_path=source_path,
        output_directory=output_directory,
        midi_output_path=midi_output_path,
        tempo_output_path=tempo_output_path,
        work_directory=resolved_work_directory,
        model_configuration_id=ADTOF_MODEL_CONFIGURATION_ID,
    )
