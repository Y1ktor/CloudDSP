"""Build one fixed, shell-free local-CPU Demucs separation command.

The preserved cloud worker chooses ``htdemucs`` for two/four stems and
``htdemucs_6s`` for six stems. This Kubernetes-local boundary keeps those model
choices while adding the local image's explicit CPU device and baked offline
model repository. It returns arguments only: no child process is started here.

The caller must have already acquired a committed ``running`` task lease and
created a private empty output directory below the Pod's bounded scratch
volume. The source downloader deliberately writes ``source.media`` under that
same worker-owned volume; requiring that generic filename makes the later
output tree predictable and prevents a browser filename or MinIO key from
becoming a local path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


# The child must enter Demucs through this reviewed Python module rather than
# the package's ``demucs`` console script.  On the Linux/ARM64 virtual CPU used
# by Docker Desktop and k3d on this Mac, the default Torch oneDNN/MKLDNN model
# path can terminate with SIGILL during real Demucs inference.  The launcher
# disables only that backend *before* it imports ``demucs.separate``.  Keeping
# an absolute Python path prevents a future Deployment ``PATH`` setting from
# selecting a different interpreter or bypassing the local CPU safeguard.
#
# The process runner deliberately changes its child working directory to a
# freshly allocated output directory. A ``python -m app.demucs_cpu_cli`` child
# would then search that empty directory rather than `/app` and fail before
# loading Demucs. Give Python the reviewed absolute script path instead, so the
# launcher works from the isolated output directory without adding a broad
# ``PYTHONPATH`` environment setting to the worker Pod.
DEMUCS_PYTHON_EXECUTABLE = "/usr/local/bin/python"
DEMUCS_CPU_CLI_SCRIPT = "/app/app/demucs_cpu_cli.py"
DEMUCS_DEVICE = "cpu"
DEMUCS_MODEL_REPOSITORY = "/opt/clouddsp/demucs-models"
DEMUCS_SOURCE_FILENAME = "source.media"


class DemucsCommandContractError(ValueError):
    """The requested stem mode cannot select a reviewed local model profile."""


class DemucsCommandPathError(RuntimeError):
    """A local source/output path is not safe for the non-root worker process."""


@dataclass(frozen=True)
class DemucsSeparationCommand:
    """Fixed command and deterministic output location for one local separation.

    ``command`` is a tuple for a future ``subprocess.Popen(..., shell=False)``
    call. ``expected_output_directory`` is not created or trusted by this
    builder—it merely documents where the Demucs CLI will place stems when a
    later bounded process adapter succeeds. The next artifact verifier must
    independently validate files below this directory before any upload.
    """

    command: tuple[str, ...]
    stem_mode: str
    model_name: str
    source_path: Path
    output_directory: Path
    work_directory: Path
    expected_output_directory: Path


def _model_arguments(stem_mode: object) -> tuple[str, tuple[str, ...]]:
    """Return only the CloudDSP-reviewed model/option set for one stem mode."""

    if stem_mode == "2-stems":
        # The existing cloud task defines two stems as vocals versus accompaniment
        # on the normal four-stem hybrid model; preserve that product contract.
        return "htdemucs", ("--two-stems", "vocals")
    if stem_mode == "4-stems":
        return "htdemucs", ()
    if stem_mode == "6-stems":
        return "htdemucs_6s", ()
    raise DemucsCommandContractError("Demucs stem mode is invalid.")


def resolved_demucs_work_directory(directory: object) -> Path:
    """Return one existing non-symlink directory allocated as Pod scratch.

    Command construction uses this before it inspects source/output paths.  The
    running-separation workspace also calls it *before* it creates a private
    temporary output directory, so a symlinked or host-derived directory can
    never receive even a short-lived model artifact.  The returned path is the
    canonical scratch root used in every later ``relative_to`` ownership check.
    """

    if not isinstance(directory, Path):
        raise DemucsCommandPathError("Demucs worker directory is invalid.")
    try:
        if directory.is_symlink():
            raise DemucsCommandPathError("Demucs worker directory is invalid.")
        resolved = directory.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise DemucsCommandPathError("Demucs worker directory is invalid.") from error
    if not resolved.is_dir():
        raise DemucsCommandPathError("Demucs worker directory is invalid.")
    return resolved


def _worker_owned_source_and_output(
    *,
    source_path: object,
    output_directory: object,
    work_directory: object,
) -> tuple[Path, Path]:
    """Validate the generic local source and fresh output directory in scratch.

    This is a local-process safety check, not authorization: PostgreSQL, MinIO,
    and FFprobe have already established task ownership and source validity.
    It prevents a future model process from receiving a host path, symlink,
    FIFO, user-derived filename, or non-empty output tree that could mix one
    task's artifact files with another task's data.
    """

    if not isinstance(source_path, Path) or not isinstance(output_directory, Path):
        raise DemucsCommandPathError("Demucs local paths are invalid.")
    resolved_work_directory = resolved_demucs_work_directory(work_directory)
    try:
        if source_path.is_symlink() or output_directory.is_symlink():
            raise DemucsCommandPathError("Demucs local paths are invalid.")
        resolved_source_path = source_path.resolve(strict=True)
        resolved_output_directory = output_directory.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise DemucsCommandPathError("Demucs local paths are invalid.") from error

    if (
        not resolved_source_path.is_file()
        or not resolved_output_directory.is_dir()
        or resolved_source_path.name != DEMUCS_SOURCE_FILENAME
    ):
        raise DemucsCommandPathError("Demucs local paths are invalid.")
    try:
        resolved_source_path.relative_to(resolved_work_directory)
        resolved_output_directory.relative_to(resolved_work_directory)
    except ValueError as error:
        raise DemucsCommandPathError("Demucs local paths are invalid.") from error
    try:
        # Output must be a distinct empty directory. In particular, it cannot
        # be the source's parent or contain the source file itself.
        resolved_source_path.relative_to(resolved_output_directory)
    except ValueError:
        # This expected case proves the source is not inside the output tree.
        pass
    else:
        raise DemucsCommandPathError("Demucs local paths are invalid.")
    try:
        if any(resolved_output_directory.iterdir()):
            raise DemucsCommandPathError("Demucs local output directory is not empty.")
    except (OSError, RuntimeError) as error:
        raise DemucsCommandPathError("Demucs local paths are invalid.") from error
    return resolved_source_path, resolved_output_directory


def build_demucs_separation_command(
    *,
    stem_mode: str,
    source_path: Path,
    output_directory: Path,
    work_directory: Path,
) -> DemucsSeparationCommand:
    """Build the one approved CPU-only Demucs command without executing it.

    The returned tuple contains no shell syntax, environment interpolation,
    browser filename, S3 key, user model name, or arbitrary Demucs option. A
    future process adapter may run it only after source preflight and the
    lease-token-guarded ``running`` transition have both committed. It must add
    bounded child-process lifetime/output handling and artifact verification in
    separate reviewed steps.
    """

    model_name, mode_arguments = _model_arguments(stem_mode)
    validated_work_directory = resolved_demucs_work_directory(work_directory)
    validated_source_path, validated_output_directory = _worker_owned_source_and_output(
        source_path=source_path,
        output_directory=output_directory,
        work_directory=validated_work_directory,
    )
    command = (
        DEMUCS_PYTHON_EXECUTABLE,
        DEMUCS_CPU_CLI_SCRIPT,
        "--device",
        DEMUCS_DEVICE,
        "--repo",
        DEMUCS_MODEL_REPOSITORY,
        "--name",
        model_name,
        "--out",
        str(validated_output_directory),
        *mode_arguments,
        str(validated_source_path),
    )
    return DemucsSeparationCommand(
        command=command,
        stem_mode=stem_mode,
        model_name=model_name,
        source_path=validated_source_path,
        output_directory=validated_output_directory,
        work_directory=validated_work_directory,
        expected_output_directory=validated_output_directory / model_name / "source",
    )
