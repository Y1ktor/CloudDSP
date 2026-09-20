"""Unit tests for exact Demucs stem inventory within an open output workspace.

The fake runner writes tiny placeholder WAV files only. These tests never run
Demucs/Torch, hash/upload a stem, contact PostgreSQL/MinIO/RabbitMQ, create an
image, or interact with a Kubernetes cluster.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.demucs_artifacts import (
    DEMUCS_STEMS_BY_MODE,
    DemucsArtifactInventoryMismatch,
    ValidatedDemucsStemInventory,
)
from app.demucs_command import DemucsSeparationCommand
from app.executed_separation_workspace import opened_executed_demucs_separation_workspace
from app.preflight_task_start import DemucsRunningSource
from app.running_source_workspace import DemucsRunningSourceWorkspace
from app.source_preflight import ValidatedDemucsSource
from app.task_lease import DemucsTaskLease
from app.validated_stem_inventory_workspace import (
    DemucsValidatedStemInventoryWorkspace,
    DemucsValidatedStemInventoryWorkspaceProtocolError,
    opened_validated_demucs_stem_inventory_workspace,
)


JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"


class WritingStemRunner:
    """Pretend a successful model process produced a controlled WAV tree."""

    def __init__(self, *, stem_count: int | None = None) -> None:
        """Allow one test to model a partial process output without real audio."""

        self.stem_count = stem_count
        self.calls: list[DemucsSeparationCommand] = []

    def run(self, *, separation: DemucsSeparationCommand, timeout_seconds: int) -> None:
        """Create only mode-approved placeholder names below the fixed output path."""

        self.calls.append(separation)
        separation.expected_output_directory.mkdir(parents=True)
        expected_stems = DEMUCS_STEMS_BY_MODE[separation.stem_mode][1]
        selected_stems = expected_stems if self.stem_count is None else expected_stems[: self.stem_count]
        for stem_name in selected_stems:
            (separation.expected_output_directory / f"{stem_name}.wav").write_bytes(
                f"private-{stem_name}-wav-bytes".encode("ascii")
            )


def running_workspace(scratch: Path) -> DemucsRunningSourceWorkspace:
    """Create a generic source paired with the minimum committed lease evidence."""

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


class ValidatedStemInventoryWorkspaceTests(unittest.TestCase):
    """Prove only the exact in-scope model output becomes artifact evidence."""

    def test_exact_inventory_is_yielded_before_outer_output_cleanup(self) -> None:
        """The next hashing boundary receives no stem unless every expected name exists."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            scratch = Path(temporary_directory) / "scratch"
            scratch.mkdir()
            runner = WritingStemRunner()

            with opened_executed_demucs_separation_workspace(
                running_workspace(scratch),
                work_directory=scratch,
                runner=runner,
            ) as executed:
                with opened_validated_demucs_stem_inventory_workspace(executed) as validated:
                    self.assertIsInstance(validated, DemucsValidatedStemInventoryWorkspace)
                    self.assertIs(validated.executed_workspace, executed)
                    self.assertIs(validated.running, executed.running)
                    self.assertIs(validated.inventory.separation, executed.separation)
                    self.assertEqual(
                        tuple(artifact.stem_name for artifact in validated.inventory.artifacts),
                        DEMUCS_STEMS_BY_MODE["4-stems"][1],
                    )
                    self.assertTrue(all(artifact.path.is_file() for artifact in validated.inventory.artifacts))
                    output_directory = executed.separation.output_directory

                # Exiting the inner handoff does not delete files: the outer
                # execution workspace owns cleanup after later in-scope stages.
                self.assertTrue(output_directory.is_dir())

            self.assertFalse(output_directory.exists())
            self.assertEqual(len(runner.calls), 1)

    def test_partial_model_output_is_rejected_before_any_inventory_is_yielded(self) -> None:
        """A zero process exit cannot convert an incomplete stem set into success."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            scratch = Path(temporary_directory) / "scratch"
            scratch.mkdir()

            with opened_executed_demucs_separation_workspace(
                running_workspace(scratch),
                work_directory=scratch,
                runner=WritingStemRunner(stem_count=1),
            ) as executed:
                output_directory = executed.separation.output_directory
                with self.assertRaises(DemucsArtifactInventoryMismatch):
                    with opened_validated_demucs_stem_inventory_workspace(executed):
                        self.fail("An incomplete output tree must not produce inventory evidence.")

            self.assertFalse(output_directory.exists())

    def test_hand_built_or_substituted_workspace_never_reaches_output_validation(self) -> None:
        """A caller cannot bypass the committed-running and model-process handoffs."""

        with self.assertRaises(TypeError):
            with opened_validated_demucs_stem_inventory_workspace(object()):  # type: ignore[arg-type]
                self.fail("An arbitrary object must not become a stem inventory workspace.")

    def test_equal_but_substituted_command_inventory_is_rejected(self) -> None:
        """A validator replacement cannot pair a cloned command with live local files."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            scratch = Path(temporary_directory) / "scratch"
            scratch.mkdir()

            with opened_executed_demucs_separation_workspace(
                running_workspace(scratch),
                work_directory=scratch,
                runner=WritingStemRunner(),
            ) as executed:
                substituted_inventory = ValidatedDemucsStemInventory(
                    separation=replace(executed.separation),
                    artifacts=(),
                )
                with patch(
                    "app.validated_stem_inventory_workspace.validate_demucs_stem_inventory",
                    return_value=substituted_inventory,
                ):
                    with self.assertRaises(DemucsValidatedStemInventoryWorkspaceProtocolError):
                        with opened_validated_demucs_stem_inventory_workspace(executed):
                            self.fail("A cloned command must not be paired with the live output tree.")


if __name__ == "__main__":
    unittest.main()
