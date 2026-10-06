"""Unit tests for in-scope Demucs stem hashing after exact inventory validation.

The fake process writes small placeholder WAV files and the tests calculate
only local SHA-256 digests. They never run Demucs/Torch, upload to MinIO, change
PostgreSQL, contact RabbitMQ, build an image, or use Kubernetes resources.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.artifacts.demucs_artifact_hash import DemucsArtifactHashContractError, HashedDemucsStemInventory
from app.artifacts.demucs_artifacts import DEMUCS_STEMS_BY_MODE
from app.processing.demucs_command import DemucsSeparationCommand
from app.processing.executed_separation_workspace import opened_executed_demucs_separation_workspace
from app.artifacts.hashed_stem_inventory_workspace import (
    DemucsHashedStemInventoryWorkspace,
    DemucsHashedStemInventoryWorkspaceProtocolError,
    opened_hashed_demucs_stem_inventory_workspace,
)
from app.db.preflight_task_start import DemucsRunningSource
from app.processing.running_source_workspace import DemucsRunningSourceWorkspace
from app.processing.source_preflight import ValidatedDemucsSource
from app.db.task_lease import DemucsTaskLease
from app.artifacts.validated_stem_inventory_workspace import opened_validated_demucs_stem_inventory_workspace


JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"


class WritingStemRunner:
    """Write one controlled complete output tree instead of invoking Demucs."""

    def run(self, *, separation: DemucsSeparationCommand, timeout_seconds: int) -> None:
        """Create every fixed four-stem name in the command's private output tree."""

        separation.expected_output_directory.mkdir(parents=True)
        for stem_name in DEMUCS_STEMS_BY_MODE[separation.stem_mode][1]:
            (separation.expected_output_directory / f"{stem_name}.wav").write_bytes(
                f"private-{stem_name}-wav-bytes".encode("ascii")
            )


def running_workspace(scratch: Path) -> DemucsRunningSourceWorkspace:
    """Create one minimal committed-running workspace with a generic source file."""

    source_path = scratch / "demucs-source-random" / "source.media"
    source_path.parent.mkdir()
    source_path.write_bytes(b"earlier-preflight-validated-source-bytes")
    lease = DemucsTaskLease(
        task_id="c21a7035-6ee2-495c-8f77-a2d2b8d33a8d",
        job_id=JOB_ID,
        request_event_id="93b31df9-ea8c-46bb-b2c0-19e9db5365d5",
        input_bucket="clouddsp-uploads",
        input_object_key=f"uploads/{JOB_ID}/mix.wav",
        stem_mode="4-stems",
        attempt_count=1,
        lease_token="49d78c86-9591-4fcb-85d8-694c15808a65",
        lease_expires_at=datetime(2026, 9, 8, tzinfo=UTC),
    )
    return DemucsRunningSourceWorkspace(
        running=DemucsRunningSource(
            lease=lease,
            source=ValidatedDemucsSource(
                source_object=MagicMock(),
                audio_probe=MagicMock(),
            ),
            started_at=datetime(2026, 9, 8, 12, 20, tzinfo=UTC),
        ),
        source_path=source_path,
    )


class HashedStemInventoryWorkspaceTests(unittest.TestCase):
    """Prove current hashes remain tied to the exact in-scope inventory."""

    def test_hashes_complete_inventory_before_outer_workspace_cleanup(self) -> None:
        """The next object-plan boundary receives ordered name/path/size/digest proof."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            scratch = Path(temporary_directory) / "scratch"
            scratch.mkdir()

            with opened_executed_demucs_separation_workspace(
                running_workspace(scratch),
                work_directory=scratch,
                runner=WritingStemRunner(),
            ) as executed:
                with opened_validated_demucs_stem_inventory_workspace(executed) as validated:
                    with opened_hashed_demucs_stem_inventory_workspace(validated) as hashed:
                        self.assertIsInstance(hashed, DemucsHashedStemInventoryWorkspace)
                        self.assertIs(hashed.validated_workspace, validated)
                        self.assertIs(hashed.running, executed.running)
                        self.assertIs(
                            hashed.hashed_inventory.separation,
                            validated.inventory.separation,
                        )
                        self.assertEqual(
                            tuple(artifact.stem_name for artifact in hashed.hashed_inventory.artifacts),
                            DEMUCS_STEMS_BY_MODE["4-stems"][1],
                        )
                        for artifact in hashed.hashed_inventory.artifacts:
                            self.assertEqual(
                                artifact.sha256,
                                hashlib.sha256(artifact.path.read_bytes()).hexdigest(),
                            )
                        output_directory = executed.separation.output_directory

                    self.assertTrue(output_directory.is_dir())

            self.assertFalse(output_directory.exists())

    def test_post_inventory_file_change_fails_before_hash_evidence_is_yielded(self) -> None:
        """The hashing layer repeats validation instead of trusting old byte counts."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            scratch = Path(temporary_directory) / "scratch"
            scratch.mkdir()

            with opened_executed_demucs_separation_workspace(
                running_workspace(scratch),
                work_directory=scratch,
                runner=WritingStemRunner(),
            ) as executed:
                output_directory = executed.separation.output_directory
                with opened_validated_demucs_stem_inventory_workspace(executed) as validated:
                    validated.inventory.artifacts[0].path.write_bytes(b"new-sized-private-content")
                    with self.assertRaises(DemucsArtifactHashContractError):
                        with opened_hashed_demucs_stem_inventory_workspace(validated):
                            self.fail("Changed bytes must not gain SHA-256 evidence.")

            self.assertFalse(output_directory.exists())

    def test_hand_built_workspace_never_reaches_local_hashing(self) -> None:
        """Hashing cannot bypass the executed-output and exact-inventory gates."""

        with self.assertRaises(TypeError):
            with opened_hashed_demucs_stem_inventory_workspace(object()):  # type: ignore[arg-type]
                self.fail("An arbitrary object must not become hash evidence.")

    def test_equal_but_substituted_hash_record_is_rejected(self) -> None:
        """A replacement adapter cannot pair cloned command evidence with live stems."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            scratch = Path(temporary_directory) / "scratch"
            scratch.mkdir()

            with opened_executed_demucs_separation_workspace(
                running_workspace(scratch),
                work_directory=scratch,
                runner=WritingStemRunner(),
            ) as executed:
                with opened_validated_demucs_stem_inventory_workspace(executed) as validated:
                    substituted_hashes = HashedDemucsStemInventory(
                        separation=replace(validated.inventory.separation),
                        artifacts=(),
                    )
                    with patch(
                        "app.artifacts.hashed_stem_inventory_workspace.hash_validated_demucs_stem_inventory",
                        return_value=substituted_hashes,
                    ):
                        with self.assertRaises(DemucsHashedStemInventoryWorkspaceProtocolError):
                            with opened_hashed_demucs_stem_inventory_workspace(validated):
                                self.fail("A cloned command must not be paired with hash evidence.")


if __name__ == "__main__":
    unittest.main()
