"""Validate the exact local stem inventory produced by one Demucs separation.

The cloud Demucs worker uploads every file it finds. The Kubernetes worker uses
a narrower contract before any object-storage write: each supported stem mode
has one exact expected set of non-empty WAV files. This prevents a damaged or
unexpected local process from turning helper files, duplicate stems, symlinks,
or a missing output directory into durable MinIO artifacts.

This boundary validates only a completed local output tree. It does not run
Demucs, inspect audio samples, calculate hashes, upload files, alter a task,
or communicate with PostgreSQL, RabbitMQ, MinIO, Docker, or Kubernetes. Those
are intentionally separate later steps.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.processing.demucs_command import DemucsSeparationCommand


# These names follow the CloudDSP Demucs model contract. `--two-stems vocals`
# creates the selected `vocals` stem and its complementary `no_vocals` stem;
# normal htdemucs produces four stems; htdemucs_6s adds guitar and piano.
DEMUCS_STEMS_BY_MODE: dict[str, tuple[str, tuple[str, ...]]] = {
    "2-stems": ("htdemucs", ("vocals", "no_vocals")),
    "4-stems": ("htdemucs", ("drums", "bass", "other", "vocals")),
    "6-stems": ("htdemucs_6s", ("drums", "bass", "other", "vocals", "guitar", "piano")),
}
DEMUCS_STEM_FILE_EXTENSION = ".wav"


class DemucsArtifactInventoryError(RuntimeError):
    """Base safe category for local output-tree validation failures."""


class DemucsArtifactContractError(DemucsArtifactInventoryError):
    """The caller supplied a command record that cannot represent reviewed work."""


class DemucsArtifactPathError(DemucsArtifactInventoryError):
    """The worker output tree is missing, escaped scratch, or contains unsafe paths."""


class DemucsArtifactInventoryMismatch(DemucsArtifactInventoryError):
    """Demucs output lacks an expected stem or contains an unexpected entry."""


@dataclass(frozen=True)
class ValidatedDemucsStemArtifact:
    """One non-empty, regular local WAV artifact safe for the next upload boundary.

    ``path`` remains Pod-local and ephemeral. It must never be persisted in
    PostgreSQL or exposed through the browser; a later upload adapter maps the
    reviewed ``stem_name`` to a stable private MinIO key instead.
    """

    stem_name: str
    path: Path
    size_bytes: int


@dataclass(frozen=True)
class ValidatedDemucsStemInventory:
    """Deterministic, complete local artifact evidence for one separation request."""

    separation: DemucsSeparationCommand
    artifacts: tuple[ValidatedDemucsStemArtifact, ...]


def _mode_contract(separation: object) -> tuple[str, tuple[str, ...]]:
    """Validate the mode/model/output relationship without re-running Demucs."""

    if not isinstance(separation, DemucsSeparationCommand):
        raise DemucsArtifactContractError("Demucs artifact command is invalid.")
    try:
        model_name, stems = DEMUCS_STEMS_BY_MODE[separation.stem_mode]
    except (KeyError, TypeError) as error:
        raise DemucsArtifactContractError("Demucs artifact command is invalid.") from error
    if separation.model_name != model_name:
        raise DemucsArtifactContractError("Demucs artifact command is invalid.")
    if not all(isinstance(path, Path) for path in (separation.work_directory, separation.output_directory)):
        raise DemucsArtifactContractError("Demucs artifact command is invalid.")
    expected_output_directory = separation.output_directory / model_name / "source"
    if separation.expected_output_directory != expected_output_directory:
        raise DemucsArtifactContractError("Demucs artifact command is invalid.")
    return model_name, stems


def _resolved_expected_output_directory(separation: DemucsSeparationCommand, *, model_name: str) -> Path:
    """Require the known result directory to remain a normal child of scratch."""

    work_directory = separation.work_directory
    output_directory = separation.output_directory
    expected_directory = separation.expected_output_directory
    try:
        if work_directory.is_symlink() or output_directory.is_symlink() or expected_directory.is_symlink():
            raise DemucsArtifactPathError("Demucs artifact output path is invalid.")
        resolved_work_directory = work_directory.resolve(strict=True)
        resolved_output_directory = output_directory.resolve(strict=True)
        resolved_expected_directory = expected_directory.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise DemucsArtifactPathError("Demucs artifact output path is invalid.") from error
    if (
        not resolved_work_directory.is_dir()
        or not resolved_output_directory.is_dir()
        or not resolved_expected_directory.is_dir()
        or resolved_expected_directory != resolved_output_directory / model_name / "source"
    ):
        raise DemucsArtifactPathError("Demucs artifact output path is invalid.")
    try:
        resolved_output_directory.relative_to(resolved_work_directory)
        resolved_expected_directory.relative_to(resolved_output_directory)
    except ValueError as error:
        raise DemucsArtifactPathError("Demucs artifact output path is invalid.") from error
    return resolved_expected_directory


def validate_demucs_stem_inventory(
    separation: DemucsSeparationCommand,
) -> ValidatedDemucsStemInventory:
    """Return exact non-empty expected stems, rejecting every other local entry.

    The process runner's zero exit is not enough to call the work successful.
    This function requires exactly the expected WAV files for the stem mode,
    with no directories, symlinks, hidden files, or extra output. Each artifact
    is resolved below the expected private output directory and has a positive
    regular-file size before it becomes available to a later MinIO upload step.
    """

    model_name, expected_stems = _mode_contract(separation)
    expected_directory = _resolved_expected_output_directory(separation, model_name=model_name)
    expected_files = {f"{stem_name}{DEMUCS_STEM_FILE_EXTENSION}": stem_name for stem_name in expected_stems}
    try:
        entries = {entry.name: entry for entry in expected_directory.iterdir()}
    except OSError as error:
        raise DemucsArtifactPathError("Demucs artifact output path is invalid.") from error
    if set(entries) != set(expected_files):
        raise DemucsArtifactInventoryMismatch("Demucs stem inventory is incomplete or unexpected.")

    artifacts: list[ValidatedDemucsStemArtifact] = []
    for file_name, stem_name in expected_files.items():
        candidate = entries[file_name]
        try:
            if candidate.is_symlink() or not candidate.is_file():
                raise DemucsArtifactPathError("Demucs stem artifact path is invalid.")
            resolved_candidate = candidate.resolve(strict=True)
            resolved_candidate.relative_to(expected_directory)
            size_bytes = resolved_candidate.stat().st_size
        except (OSError, RuntimeError, ValueError) as error:
            raise DemucsArtifactPathError("Demucs stem artifact path is invalid.") from error
        if size_bytes < 1:
            raise DemucsArtifactInventoryMismatch("Demucs stem artifact is empty.")
        artifacts.append(
            ValidatedDemucsStemArtifact(
                stem_name=stem_name,
                path=resolved_candidate,
                size_bytes=size_bytes,
            )
        )
    return ValidatedDemucsStemInventory(
        separation=separation,
        artifacts=tuple(artifacts),
    )
