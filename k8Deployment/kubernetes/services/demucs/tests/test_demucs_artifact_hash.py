"""Unit tests for streaming SHA-256 evidence over validated local Demucs stems.

The tests create small temporary placeholder WAV files only. They do not run
Demucs/Torch, inspect waveform content, make an S3 request, or contact a
database, broker, Docker daemon, or Kubernetes cluster.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import tempfile
import unittest
from pathlib import Path

from app.artifacts.demucs_artifact_hash import (
    DemucsArtifactHashContractError,
    hash_validated_demucs_stem_inventory,
)
from app.artifacts.demucs_artifacts import (
    DEMUCS_STEMS_BY_MODE,
    ValidatedDemucsStemInventory,
    validate_demucs_stem_inventory,
)
from app.processing.demucs_command import DemucsSeparationCommand, build_demucs_separation_command


def complete_inventory(
    temporary_directory: Path,
) -> tuple[DemucsSeparationCommand, ValidatedDemucsStemInventory]:
    """Return one four-stem inventory with deterministic different placeholder bytes."""

    work_directory = temporary_directory / "scratch"
    # Some tests use a named child of the temporary root to keep two independent
    # inventories alive at once, so create all parents rather than assuming it.
    work_directory.mkdir(parents=True)
    source_path = work_directory / "input" / "source.media"
    source_path.parent.mkdir()
    source_path.write_bytes(b"source-was-accepted-before-model-execution")
    output_directory = work_directory / "output"
    output_directory.mkdir()
    separation = build_demucs_separation_command(
        stem_mode="4-stems",
        source_path=source_path,
        output_directory=output_directory,
        work_directory=work_directory,
    )
    separation.expected_output_directory.mkdir(parents=True)
    _, stems = DEMUCS_STEMS_BY_MODE["4-stems"]
    for stem_name in stems:
        (separation.expected_output_directory / f"{stem_name}.wav").write_bytes(
            f"private-{stem_name}-bytes".encode("ascii")
        )
    return separation, validate_demucs_stem_inventory(separation)


class DemucsArtifactHashTests(unittest.TestCase):
    """Prove only current validated files receive hash evidence for later upload."""

    def test_hashes_each_exact_inventory_stem_without_changing_order_or_size(self) -> None:
        """The future uploader can bind each stable stem name to its byte digest."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            separation, inventory = complete_inventory(Path(temporary_directory))

            hashed = hash_validated_demucs_stem_inventory(inventory)

            self.assertIs(hashed.separation, separation)
            self.assertEqual(
                tuple(artifact.stem_name for artifact in hashed.artifacts),
                DEMUCS_STEMS_BY_MODE["4-stems"][1],
            )
            for artifact in hashed.artifacts:
                contents = artifact.path.read_bytes()
                self.assertEqual(artifact.size_bytes, len(contents))
                self.assertEqual(artifact.sha256, hashlib.sha256(contents).hexdigest())

    def test_stale_size_or_symlink_change_is_rejected_before_hash_evidence(self) -> None:
        """No file altered after inventory validation can reach a future uploader."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _, inventory = complete_inventory(root)
            first = inventory.artifacts[0]
            first.path.write_bytes(b"this-file-now-has-a-different-size")
            with self.assertRaises(DemucsArtifactHashContractError):
                hash_validated_demucs_stem_inventory(inventory)

            _, second_inventory = complete_inventory(root / "second")
            second = second_inventory.artifacts[0]
            outside = root / "outside.wav"
            outside.write_bytes(b"not-a-worker-artifact")
            second.path.unlink()
            second.path.symlink_to(outside)
            with self.assertRaises(DemucsArtifactHashContractError):
                hash_validated_demucs_stem_inventory(second_inventory)

    def test_hand_built_inventory_cannot_change_recorded_artifact_metadata(self) -> None:
        """The hash boundary repeats inventory validation instead of trusting a dataclass."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            _, inventory = complete_inventory(Path(temporary_directory))
            altered_first = replace(inventory.artifacts[0], size_bytes=999)
            tampered = replace(
                inventory,
                artifacts=(altered_first, *inventory.artifacts[1:]),
            )

            with self.assertRaises(DemucsArtifactHashContractError):
                hash_validated_demucs_stem_inventory(tampered)


if __name__ == "__main__":
    unittest.main()
