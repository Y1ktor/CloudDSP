"""Unit tests for strict local Demucs stem-artifact inventory validation.

The tests create only temporary placeholder files in a simulated worker scratch
directory. They do not run Demucs/Torch, inspect audio content, upload MinIO
objects, contact PostgreSQL/RabbitMQ, or create Docker/Kubernetes resources.
"""

from __future__ import annotations

from dataclasses import replace
import tempfile
import unittest
from pathlib import Path

from app.demucs_artifacts import (
    DEMUCS_STEMS_BY_MODE,
    DemucsArtifactContractError,
    DemucsArtifactInventoryMismatch,
    DemucsArtifactPathError,
    validate_demucs_stem_inventory,
)
from app.demucs_command import DemucsSeparationCommand, build_demucs_separation_command


def separation_for_mode(
    temporary_directory: Path,
    *,
    stem_mode: str,
) -> DemucsSeparationCommand:
    """Build one legitimate post-process request with an initially empty output root."""

    work_directory = temporary_directory / f"scratch-{stem_mode}"
    work_directory.mkdir()
    source_path = work_directory / "input" / "source.media"
    source_path.parent.mkdir()
    source_path.write_bytes(b"source-was-validated-by-earlier-boundaries")
    output_directory = work_directory / "output"
    output_directory.mkdir()
    return build_demucs_separation_command(
        stem_mode=stem_mode,
        source_path=source_path,
        output_directory=output_directory,
        work_directory=work_directory,
    )


def write_expected_stems(separation: DemucsSeparationCommand) -> None:
    """Create the exact placeholder WAV names a successful CLI would produce."""

    _, stems = DEMUCS_STEMS_BY_MODE[separation.stem_mode]
    separation.expected_output_directory.mkdir(parents=True)
    for index, stem_name in enumerate(stems, start=1):
        # Give each placeholder a distinct positive length so the inventory
        # assertion proves it reports file metadata in the documented order.
        (separation.expected_output_directory / f"{stem_name}.wav").write_bytes(b"x" * index)


class DemucsArtifactInventoryTests(unittest.TestCase):
    """Prove no arbitrary local Demucs output can cross into an upload boundary."""

    def test_each_mode_requires_and_returns_its_exact_ordered_stem_set(self) -> None:
        """The output contract stays aligned with CloudDSP's Demucs mode mapping."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for stem_mode, (_, expected_stems) in DEMUCS_STEMS_BY_MODE.items():
                with self.subTest(stem_mode=stem_mode):
                    separation = separation_for_mode(root, stem_mode=stem_mode)
                    write_expected_stems(separation)

                    inventory = validate_demucs_stem_inventory(separation)

                    self.assertEqual(
                        tuple(artifact.stem_name for artifact in inventory.artifacts),
                        expected_stems,
                    )
                    self.assertEqual(
                        tuple(artifact.size_bytes for artifact in inventory.artifacts),
                        tuple(range(1, len(expected_stems) + 1)),
                    )
                    self.assertTrue(
                        all(artifact.path.parent == separation.expected_output_directory for artifact in inventory.artifacts)
                    )

    def test_missing_or_extra_stems_are_not_an_uploadable_inventory(self) -> None:
        """One zero-exit process cannot publish partial or helper-file outputs."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            missing = separation_for_mode(root, stem_mode="4-stems")
            missing.expected_output_directory.mkdir(parents=True)
            (missing.expected_output_directory / "drums.wav").write_bytes(b"drums")
            with self.assertRaises(DemucsArtifactInventoryMismatch):
                validate_demucs_stem_inventory(missing)

            extra = separation_for_mode(root, stem_mode="6-stems")
            write_expected_stems(extra)
            (extra.expected_output_directory / "demucs.log").write_bytes(b"helper output")
            with self.assertRaises(DemucsArtifactInventoryMismatch):
                validate_demucs_stem_inventory(extra)

    def test_symlink_or_empty_expected_stem_is_rejected(self) -> None:
        """A local path substitution or empty output is never treated as audio."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            symlinked = separation_for_mode(root, stem_mode="2-stems")
            write_expected_stems(symlinked)
            outside = root / "outside.wav"
            outside.write_bytes(b"not-an-owned-artifact")
            (symlinked.expected_output_directory / "vocals.wav").unlink()
            (symlinked.expected_output_directory / "vocals.wav").symlink_to(outside)
            with self.assertRaises(DemucsArtifactPathError):
                validate_demucs_stem_inventory(symlinked)

            empty = separation_for_mode(root, stem_mode="4-stems")
            write_expected_stems(empty)
            (empty.expected_output_directory / "bass.wav").write_bytes(b"")
            with self.assertRaises(DemucsArtifactInventoryMismatch):
                validate_demucs_stem_inventory(empty)

    def test_tampered_separation_record_is_rejected_before_output_inventory(self) -> None:
        """The model/mode/output relationship cannot be changed after execution."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            separation = separation_for_mode(Path(temporary_directory), stem_mode="4-stems")
            write_expected_stems(separation)
            tampered = replace(separation, model_name="htdemucs_6s")

            with self.assertRaises(DemucsArtifactContractError):
                validate_demucs_stem_inventory(tampered)


if __name__ == "__main__":
    unittest.main()
